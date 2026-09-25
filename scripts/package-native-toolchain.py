#!/usr/bin/env python3
"""Assemble a minimal, versioned Rust toolchain that runs inside Scarlet.

The inputs are already-validated Actions artifacts downloaded with
fetch-native-host.sh and fetch-native-linker.sh.  This step deliberately does
not package Scarlet's runtime dynamic linker: /bin/scarlet-ld belongs to
the matching Scarlet image and is recorded as a compatibility requirement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Optional


TARGETS = {
    "aarch64-unknown-scarlet": {"arch": "aarch64", "machine": 183},
    "riscv64gc-unknown-scarlet": {"arch": "riscv64", "machine": 243},
}
VERSION_RE = re.compile(r"v[0-9A-Za-z][0-9A-Za-z._+-]*\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
RUN_ID_RE = re.compile(r"[1-9][0-9]*\Z")
STATIC_SUFFIXES = {".a", ".o", ".rlib"}
INTERPRETER = "/bin/scarlet-ld"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{description} must be a regular file: {path}")
    return path


def one_file(paths: Iterable[Path], description: str) -> Path:
    candidates = sorted(paths)
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one {description}, found {len(candidates)}"
        )
    return regular_file(candidates[0], description)


def read_json(path: Path, description: str) -> Dict[str, Any]:
    regular_file(path, description)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {description}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must be a JSON object: {path}")
    return value


def validate_run_id(value: Any, description: str) -> str:
    result = str(value or "")
    if not RUN_ID_RE.fullmatch(result):
        raise ValueError(f"{description} has no valid GitHub run ID")
    return result


def validate_commit(value: Any, description: str) -> str:
    result = str(value or "")
    if not COMMIT_RE.fullmatch(result):
        raise ValueError(f"{description} must be a full lowercase Git commit")
    return result


def validate_manifests(
    native_host: Path, native_linker: Path, target: str
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    host = read_json(
        native_host / "native-host-manifest.json", "native host manifest"
    )
    linker = read_json(native_linker / "manifest.json", "native linker manifest")
    if (
        host.get("schema") != 1
        or host.get("status") != "built-not-guest-verified"
        or host.get("native_host") != target
        or host.get("backend") != "cranelift"
        or host.get("exit_code") != 0
        or host.get("capabilities", {}).get("codegen") is not True
        or host.get("capabilities", {}).get("cargo") is not True
    ):
        raise ValueError("native host manifest is not a successful Cranelift build")
    if (
        linker.get("schema") != 1
        or linker.get("status") != "built-not-guest-verified"
        or linker.get("target") != target
    ):
        raise ValueError("native linker manifest is not a successful matching build")
    rust_commit = validate_commit(host.get("rust_commit"), "native host rust_commit")
    if linker.get("rust_toolchain_revision") != rust_commit:
        raise ValueError("native host and Wild were built with different Rust revisions")
    validate_run_id(host.get("github_run_id"), "native host manifest")
    validate_run_id(linker.get("github_run_id"), "native linker manifest")
    return host, linker


def elf_identity(path: Path, expected_machine: int) -> Dict[str, Any]:
    regular_file(path, "native ELF")
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError(f"not a little-endian ELF64 file: {path}")
        elf_type, machine = struct.unpack_from("<HH", header, 16)
        if machine != expected_machine:
            raise ValueError(f"wrong ELF machine in {path}: {machine}")
        if elf_type not in (2, 3) or header[7] != 0x53:
            raise ValueError(f"not a Scarlet executable/shared object: {path}")
        phoff = struct.unpack_from("<Q", header, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", header, 54)
        if phentsize != 56 or not 0 < phnum < 65535:
            raise ValueError(f"invalid ELF program header table: {path}")
        size = path.stat().st_size
        if phoff + phentsize * phnum > size:
            raise ValueError(f"out-of-bounds ELF program header table: {path}")
        interpreter: Optional[str] = None
        for index in range(phnum):
            stream.seek(phoff + index * phentsize)
            kind, _, offset, _, _, file_size, memory_size, _ = struct.unpack(
                "<IIQQQQQQ", stream.read(56)
            )
            if offset + file_size > size or (kind == 1 and file_size > memory_size):
                raise ValueError(f"out-of-bounds ELF segment: {path}")
            if kind == 3:
                if not 0 < file_size <= 4096:
                    raise ValueError(f"invalid ELF interpreter: {path}")
                stream.seek(offset)
                interpreter = stream.read(file_size).rstrip(b"\0").decode("utf-8")
    return {"type": elf_type, "machine": machine, "interpreter": interpreter}


def dynamic_needed(path: Path) -> List[str]:
    """Read DT_NEEDED without depending on a host binutils installation."""
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError(f"not a little-endian ELF64 file: {path}")
        phoff = struct.unpack_from("<Q", header, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", header, 54)
        size = path.stat().st_size
        if phentsize != 56 or not 0 < phnum < 65535 or phoff + phentsize * phnum > size:
            raise ValueError(f"invalid ELF program header table: {path}")
        loads = []
        dynamic = None
        for index in range(phnum):
            stream.seek(phoff + index * phentsize)
            kind, _, offset, virtual, _, file_size, memory_size, _ = struct.unpack(
                "<IIQQQQQQ", stream.read(56)
            )
            if offset + file_size > size or (kind == 1 and file_size > memory_size):
                raise ValueError(f"out-of-bounds ELF segment: {path}")
            if kind == 1:
                loads.append((virtual, offset, file_size))
            elif kind == 2:
                dynamic = (offset, file_size)
        if dynamic is None:
            return []
        dynamic_offset, dynamic_size = dynamic
        if dynamic_size % 16:
            raise ValueError(f"misaligned ELF dynamic table: {path}")
        string_table_address = None
        string_table_size = None
        needed_offsets = []
        for offset in range(dynamic_offset, dynamic_offset + dynamic_size, 16):
            stream.seek(offset)
            tag, value = struct.unpack("<qQ", stream.read(16))
            if tag == 0:
                break
            if tag == 1:
                needed_offsets.append(value)
            elif tag == 5:
                string_table_address = value
            elif tag == 10:
                string_table_size = value
        if not needed_offsets:
            return []
        if string_table_address is None or string_table_size is None:
            raise ValueError(f"ELF dependencies have no valid string table: {path}")
        string_table_offset = None
        for virtual, offset, file_size in loads:
            if virtual <= string_table_address < virtual + file_size:
                string_table_offset = offset + string_table_address - virtual
                break
        if (
            string_table_offset is None
            or string_table_offset + string_table_size > size
        ):
            raise ValueError(f"out-of-bounds ELF dynamic string table: {path}")
        stream.seek(string_table_offset)
        strings = stream.read(string_table_size)
    names = []
    for offset in needed_offsets:
        if offset >= len(strings):
            raise ValueError(f"out-of-bounds DT_NEEDED string: {path}")
        end = strings.find(b"\0", offset)
        if end < 0:
            raise ValueError(f"unterminated DT_NEEDED string: {path}")
        name = strings[offset:end].decode("utf-8")
        if not name or "/" in name or name in (".", ".."):
            raise ValueError(f"unsafe DT_NEEDED name in {path}: {name!r}")
        if name not in names:
            names.append(name)
    return names


def copy_file(source: Path, destination: Path, mode: int) -> None:
    regular_file(source, "package input")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    destination.chmod(mode)


def copy_rust_licenses(package: Path) -> None:
    # Bootstrap's installed sysroot omits these files. Keep the exact license
    # set from the pinned Rust fork in the packaging repository so release
    # candidates remain complete and do not need network access.
    source = Path(__file__).resolve().parents[1] / "native-toolchain/licenses/rust"
    for name in ("COPYRIGHT", "LICENSE-APACHE", "LICENSE-MIT"):
        copy_file(source / name, package / "share/licenses/rust" / name, 0o644)


def payload_inventory(package: Path) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package).as_posix()
        if relative == "manifest.json" or path.is_dir():
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            files.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "mode": f"{mode:04o}",
                    "target": os.readlink(path),
                }
            )
        elif path.is_file():
            files.append(
                {
                    "path": relative,
                    "kind": "file",
                    "mode": f"{mode:04o}",
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        else:
            raise ValueError(f"unsupported package entry: {path}")
    return files


def packaging_commit(explicit: Optional[str]) -> str:
    if explicit is not None:
        return validate_commit(explicit, "packaging commit")
    repo = Path(__file__).resolve().parents[1]
    value = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    return validate_commit(value, "packaging commit")


def assemble(
    native_host: Path,
    native_linker: Path,
    target: str,
    version: str,
    scarlet_commit: str,
    destination: Path,
    package_commit: Optional[str] = None,
) -> Path:
    if target not in TARGETS:
        raise ValueError(f"unsupported target: {target}")
    if not VERSION_RE.fullmatch(version):
        raise ValueError("version must start with v and contain only release-safe characters")
    scarlet_commit = validate_commit(scarlet_commit, "required Scarlet commit")
    package_commit = packaging_commit(package_commit)
    native_host = native_host.resolve()
    native_linker = native_linker.resolve()
    if not native_host.is_dir() or not native_linker.is_dir():
        raise ValueError("native host and linker inputs must be directories")
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"destination already exists: {destination}")

    host_manifest, linker_manifest = validate_manifests(
        native_host, native_linker, target
    )
    target_info = TARGETS[target]
    rustc = regular_file(native_host / "bin/rustc", "native rustc")
    cargo = regular_file(native_host / "bin/cargo", "native Cargo")
    wild = regular_file(native_linker / "bin/wild", "native Wild")
    driver = one_file(
        native_host.glob("lib/librustc_driver-*.so"), "native rustc_driver"
    )
    backend = one_file(
        native_host.glob(
            f"lib/rustlib/{target}/codegen-backends/"
            "librustc_codegen_cranelift-*.so"
        ),
        "native Cranelift backend",
    )
    target_lib = native_host / "lib/rustlib" / target / "lib"
    if target_lib.is_symlink() or not target_lib.is_dir():
        raise ValueError(f"matching target library directory is missing: {target_lib}")
    static_inputs = sorted(
        path
        for path in target_lib.iterdir()
        if path.suffix in STATIC_SUFFIXES and path.is_file() and not path.is_symlink()
    )
    for crate in ("core", "alloc", "std"):
        if not any(path.name.startswith(f"lib{crate}-") and path.suffix == ".rlib" for path in static_inputs):
            raise ValueError(f"target sysroot has no static {crate} rlib")

    rustc_elf = elf_identity(rustc, target_info["machine"])
    if rustc_elf["interpreter"] != INTERPRETER:
        raise ValueError(f"native rustc does not request {INTERPRETER}")
    cargo_elf = elf_identity(cargo, target_info["machine"])
    if cargo_elf["interpreter"] != INTERPRETER:
        raise ValueError(f"native Cargo does not request {INTERPRETER}")
    wild_elf = elf_identity(wild, target_info["machine"])
    if wild_elf["interpreter"] not in (None, INTERPRETER):
        raise ValueError("native Wild requests an incompatible dynamic loader")
    for shared in (driver, backend):
        identity = elf_identity(shared, target_info["machine"])
        if identity["type"] != 3 or identity["interpreter"] is not None:
            raise ValueError(f"expected a shared object without PT_INTERP: {shared}")

    available_libraries = {
        path.name: path
        for path in native_host.glob("lib/*.so")
        if path.is_file() and not path.is_symlink()
    }
    runtime_libraries = {driver.name: driver}
    dependency_work = [
        (rustc, "bin/rustc"),
        (cargo, "bin/cargo"),
        (driver, f"lib/{driver.name}"),
        (
            backend,
            f"lib/rustlib/{target}/codegen-backends/{backend.name}",
        ),
        (wild, "bin/wild"),
    ]
    dynamic_dependencies: Dict[str, List[str]] = {}
    checked = set()
    while dependency_work:
        source, packaged_path = dependency_work.pop(0)
        if source in checked:
            continue
        checked.add(source)
        dependencies = dynamic_needed(source)
        dynamic_dependencies[packaged_path] = dependencies
        for name in dependencies:
            library = available_libraries.get(name)
            if library is None:
                raise ValueError(f"unpackaged DT_NEEDED dependency {name} in {source}")
            if name not in runtime_libraries:
                runtime_libraries[name] = library
                dependency_work.append((library, f"lib/{name}"))

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".native-rust-package-", dir=destination.parent)
    )
    package = temporary / "package"
    try:
        package.mkdir()
        copy_file(rustc, package / "bin/rustc", 0o755)
        copy_file(cargo, package / "bin/cargo", 0o755)
        copy_file(wild, package / "bin/wild", 0o755)
        for name, source in sorted(runtime_libraries.items()):
            copy_file(source, package / "lib" / name, 0o755)
        copy_file(
            backend,
            package
            / "lib/rustlib"
            / target
            / "codegen-backends"
            / backend.name,
            0o755,
        )
        for source in static_inputs:
            copy_file(
                source, package / "lib/rustlib" / target / "lib" / source.name, 0o644
            )

        # Keep canonical runtime DSOs under lib. scarlet-ld resolves each
        # DT_NEEDED beside the requesting object before its global directories,
        # so add a relative link there rather than copying toolchain-private
        # libraries into /system/lib. This covers both rustc and the backend.
        (package / "bin/rust-lld").symlink_to("wild")
        for requester, dependencies in sorted(dynamic_dependencies.items()):
            requester_directory = (package / requester).parent
            for name in dependencies:
                library = package / "lib" / name
                link = requester_directory / name
                if link == library or link.exists() or link.is_symlink():
                    continue
                link.symlink_to(os.path.relpath(library, requester_directory))

        for license_name in ("LICENSE-APACHE", "LICENSE-MIT"):
            copy_file(
                native_linker / license_name,
                package / "share/licenses/wild" / license_name,
                0o644,
            )
        copy_rust_licenses(package)

        provenance = package / "share/provenance"
        provenance.mkdir(parents=True, exist_ok=True)
        (provenance / "native-host.json").write_text(
            json.dumps(host_manifest, indent=2, sort_keys=True) + "\n"
        )
        (provenance / "native-linker.json").write_text(
            json.dumps(linker_manifest, indent=2, sort_keys=True) + "\n"
        )
        for path in provenance.iterdir():
            path.chmod(0o644)

        install_prefix = f"/opt/scarlet/toolchains/rust/{version}"
        readme = package / "README.txt"
        readme.write_text(
            f"Scarlet native Rust toolchain {version} ({target})\n\n"
            f"Install at {install_prefix} and add {install_prefix}/bin to PATH.\n"
            f"This package requires {INTERPRETER} from Scarlet commit "
            f"{scarlet_commit}.\n"
            "Scarlet owns the runtime loader; it is intentionally absent here.\n"
            "The target standard library is linked statically by default.\n"
            "Cargo is included; guest execution is not claimed by this build.\n"
            "Procedural-macro execution is not claimed by this bundle.\n"
        )
        readme.chmod(0o644)

        guest_verified = bool(
            host_manifest.get("guest_verified")
            and linker_manifest.get("guest_verified")
        )
        manifest = {
            "schema": 1,
            "kind": "scarlet-native-rust-toolchain",
            "status": "guest-verified" if guest_verified else "built-not-guest-verified",
            "version": version,
            "target": target,
            "architecture": target_info["arch"],
            "install_prefix": install_prefix,
            "packaging_commit": package_commit,
            "requires": {
                "scarlet_commit": scarlet_commit,
                "dynamic_loader": INTERPRETER,
            },
            "rust": {
                "revision": host_manifest["rust_commit"],
                "backend": "cranelift",
                "native_host_run_id": validate_run_id(
                    host_manifest.get("github_run_id"), "native host manifest"
                ),
                "native_host_packaging_commit": host_manifest.get(
                    "packaging_commit"
                ),
            },
            "wild": {
                "version": linker_manifest.get("version"),
                "revision": linker_manifest.get("revision"),
                "native_linker_run_id": validate_run_id(
                    linker_manifest.get("github_run_id"), "native linker manifest"
                ),
                "native_linker_packaging_commit": linker_manifest.get(
                    "packaging_commit"
                ),
            },
            "capabilities": {
                "rustc": True,
                "codegen": True,
                "linker": True,
                "static_target_std": True,
                "cargo": True,
                "proc_macro": False,
                "guest_verified": guest_verified,
            },
            "components": {
                "scarlet_ld": {
                    "included": False,
                    "owner": "petitstrawberry/Scarlet",
                    "path": INTERPRETER,
                }
            },
            "dynamic_dependencies": dynamic_dependencies,
            "files": payload_inventory(package),
        }
        manifest_path = package / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        manifest_path.chmod(0o644)

        forbidden = [
            path
            for path in package.rglob("*")
            if path.name == "scarlet-ld" or path.suffix == ".rmeta"
        ]
        if forbidden:
            raise ValueError(f"forbidden files entered package: {forbidden}")
        os.rename(package, destination)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-host", required=True, type=Path)
    parser.add_argument("--native-linker", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--version", required=True)
    parser.add_argument("--scarlet-commit", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--packaging-commit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        destination = assemble(
            native_host=args.native_host,
            native_linker=args.native_linker,
            target=args.target,
            version=args.version,
            scarlet_commit=args.scarlet_commit,
            destination=args.destination,
            package_commit=args.packaging_commit,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(destination)


if __name__ == "__main__":
    main()
