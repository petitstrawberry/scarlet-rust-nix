#!/usr/bin/env python3
"""Prepare an isolated, vendored Rust source tree for the native Scarlet host.

This changes only --source; it never patches registry caches or an installed
compiler. All input patches and added source files are recorded by SHA-256.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_patch(source, patch, *, check_only=False):
    # Vendored trees live below the packaging checkout in Actions. Without this
    # boundary Git discovers that parent repository and silently skips patches
    # whose paths are outside the current repository-relative subdirectory.
    environment = {key: value for key, value in os.environ.items()
                   if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    environment["GIT_CEILING_DIRECTORIES"] = str(source.resolve().parent)
    for options in ([["--check"]] if check_only else [["--check"], []]):
        # Source overlays include exact, context-free hunks generated against
        # recipe.base_rust_revision. Git otherwise rejects interior zero-context
        # hunks even when their old lines match the pinned source exactly.
        subprocess.run(["git", "apply", "--unidiff-zero", *options, str(patch)], cwd=source,
                       env=environment, check=True)


def find_package(vendor, name, version):
    matches = []
    for candidate in sorted(vendor.glob(f"{name}*")):
        manifest = candidate / "Cargo.toml"
        if not manifest.is_file():
            continue
        data = tomllib.loads(manifest.read_text()).get("package", {})
        if data.get("name") == name and data.get("version") == version:
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one vendored {name} {version}, found {matches}")
    return matches[0]


def prepare(source, inputs, cargo=None):
    source = source.resolve(strict=True)
    inputs = inputs.resolve(strict=True)
    marker = source / ".scarlet-native-host-prepared.json"
    if marker.exists() or (source / "native-host-deps").exists():
        raise ValueError("source already prepared; use a fresh writable copy of the pinned source")
    if not (source / "compiler/rustc_target/src/spec/targets/aarch64_unknown_scarlet.rs").is_file():
        raise ValueError("source must be the Scarlet Rust fork")
    if not (source / ".cargo/config.toml").is_file() or not (source / "vendor").is_dir():
        raise ValueError("source must contain the published vendored dependencies")
    recipe = json.loads((inputs / "recipe.json").read_text())
    if os.environ.get("SCARLET_RUST_REV", recipe["base_rust_revision"]) != recipe["base_rust_revision"]:
        raise ValueError("native host patches require their exact pinned Rust source revision")
    records = []
    for package in recipe["packages"]:
        package["source"] = find_package(source / "vendor", package["name"], package["version"])
    # Check source patch applicability before creating any local dependency copies.
    source_patch = inputs / "patches/rust-native-host.patch"
    apply_patch(source, source_patch, check_only=True)
    for package in recipe["packages"]:
        destination = source / "native-host-deps" / f'{package["name"]}-{package["version"]}'
        shutil.copytree(package["source"], destination, ignore=shutil.ignore_patterns(".git", ".cargo-checksum.json", ".cargo-ok", ".cargo_vcs_info.json"))
        for operation in package["operations"]:
            input_file = inputs / operation["input"]
            records.append({"path": str(input_file.relative_to(inputs)), "sha256": sha256(input_file)})
            if operation["kind"] == "patch":
                apply_patch(destination, input_file)
            elif operation["kind"] == "copy":
                target = destination / operation["destination"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(input_file, target)
            else:
                raise ValueError(f"unknown operation {operation}")
    apply_patch(source, source_patch)
    records.append({"path": str(source_patch.relative_to(inputs)), "sha256": sha256(source_patch)})
    manifest = source / "Cargo.toml"
    if "[patch.crates-io]" in [line.strip() for line in manifest.read_text().splitlines()]:
        raise ValueError("unexpected existing root patch table; adapt preparation explicitly")
    with manifest.open("a") as stream:
        stream.write("\n[patch.crates-io]\n")
        for package in recipe["packages"]:
            if package.get("root_patch"):
                stream.write(f'{package["root_patch"]} = {{ package = "{package["name"]}", path = "native-host-deps/{package["name"]}-{package["version"]}" }}\n')
    if cargo:
        command = [str(cargo), "update", "--offline"]
        for package in recipe["packages"]:
            if package.get("root_patch"):
                command.extend(["-p", f'{package["name"]}@{package["version"]}'])
        environment = dict(os.environ, RUSTC=str(cargo.parent / "rustc"))
        subprocess.run(command, cwd=source, env=environment, check=True)
    marker.write_text(json.dumps({"schema": 1, "base_rust_revision": recipe["base_rust_revision"], "inputs": records, "native_compiler_built": False, "guest_execution_verified": False}, indent=2) + "\n")
    print(f"Prepared native Scarlet source: {source}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, default=Path(__file__).resolve().parents[1] / "native-host")
    parser.add_argument("--cargo", type=Path, help="Pinned bootstrap Cargo; refresh path-patched lock entries offline")
    args = parser.parse_args()
    prepare(args.source, args.inputs, args.cargo.absolute() if args.cargo else None)


if __name__ == "__main__":
    main()
