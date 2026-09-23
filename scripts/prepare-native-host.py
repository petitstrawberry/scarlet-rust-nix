#!/usr/bin/env python3
"""Prepare vendored dependencies for a native Scarlet compiler build.

The Rust compiler and standard-library changes live in the Scarlet Rust fork.
This changes only --source; it never patches registry caches or an installed
compiler. Dependency inputs are recorded by SHA-256 or an exact Git commit.
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
        # Some dependency patches contain exact, context-free hunks.
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


def fetch_package(destination, package):
    source = package["git"]
    destination.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "remote", "add", "origin", source["url"]],
                   check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "--depth", "1", "origin",
                    source["revision"]], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "-q", "--detach", "FETCH_HEAD"],
                   check=True)
    revision = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"],
                                      text=True).strip()
    if revision != source["revision"]:
        raise ValueError(f'wrong {package["name"]} fork revision: {revision}')
    manifest = tomllib.loads((destination / "Cargo.toml").read_text())["package"]
    if (manifest["name"], manifest["version"]) != (package["name"], package["version"]):
        raise ValueError(f'wrong {package["name"]} fork package/version')
    shutil.rmtree(destination / ".git")
    return {"repository": source["url"], "revision": revision}


def prepare(source, inputs, cargo=None):
    source = source.resolve(strict=True)
    inputs = inputs.resolve(strict=True)
    marker = source / ".scarlet-native-host-prepared.json"
    if marker.exists() or (source / "native-host-deps").exists():
        raise ValueError("source already prepared; use a fresh writable copy of the Rust source")
    if not (source / "compiler/rustc_target/src/spec/targets/aarch64_unknown_scarlet.rs").is_file():
        raise ValueError("source must be the Scarlet Rust fork")
    if not (source / "library/std/src/sys/scarlet_errno_abi_test.py").is_file():
        raise ValueError("Scarlet Rust fork lacks the integrated native-host runtime")
    if not (source / ".cargo/config.toml").is_file() or not (source / "vendor").is_dir():
        raise ValueError("source must contain the published vendored dependencies")
    recipe = json.loads((inputs / "recipe.json").read_text())
    records = []
    for package in recipe["packages"]:
        if "git" not in package:
            package["source"] = find_package(source / "vendor", package["name"], package["version"])
    for package in recipe["packages"]:
        destination = source / "native-host-deps" / f'{package["name"]}-{package["version"]}'
        if "git" in package:
            records.append(fetch_package(destination, package))
        else:
            shutil.copytree(package["source"], destination,
                            ignore=shutil.ignore_patterns(".git", ".cargo-checksum.json",
                                                                  ".cargo-ok", ".cargo_vcs_info.json"))
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
    manifest = source / "Cargo.toml"
    if "[patch.crates-io]" in [line.strip() for line in manifest.read_text().splitlines()]:
        raise ValueError("unexpected existing root patch table; adapt preparation explicitly")
    with manifest.open("a") as stream:
        stream.write("\n[patch.crates-io]\n")
        for package in recipe["packages"]:
            if package.get("root_patch"):
                stream.write(f'{package["root_patch"]} = {{ package = "{package["name"]}", path = "native-host-deps/{package["name"]}-{package["version"]}" }}\n')
    # Cranelift is an independent Cargo workspace, so the root patch table
    # does not apply to its target-lexicon dependency.
    lexicon = next(package for package in recipe["packages"] if package["name"] == "target-lexicon")
    backend_manifest = source / "compiler/rustc_codegen_cranelift/Cargo.toml"
    backend_text = backend_manifest.read_text()
    section = "[patch.crates-io]\n"
    if backend_text.count(section) != 1 or "target-lexicon = { path =" in backend_text:
        raise ValueError("unexpected Cranelift patch table; adapt preparation explicitly")
    backend_text = backend_text.replace(
        section,
        section + f'target-lexicon = {{ path = "../../native-host-deps/target-lexicon-{lexicon["version"]}" }}\n',
        1,
    )
    backend_manifest.write_text(backend_text)
    if cargo:
        command = [str(cargo), "update", "--offline"]
        for package in recipe["packages"]:
            if package.get("root_patch"):
                command.extend(["-p", f'{package["name"]}@{package["version"]}'])
        environment = dict(os.environ, RUSTC=str(cargo.parent / "rustc"))
        subprocess.run(command, cwd=source, env=environment, check=True)
        subprocess.run([str(cargo), "update", "--offline", "-p",
                        f'target-lexicon@{lexicon["version"]}'],
                       cwd=backend_manifest.parent, env=environment, check=True)
    marker.write_text(json.dumps({"schema": 2, "rust_revision": os.environ.get("SCARLET_RUST_REV"),
                                  "inputs": records, "native_compiler_built": False,
                                  "guest_execution_verified": False}, indent=2) + "\n")
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
