#!/usr/bin/env python3
"""Prepare vendored dependencies for a native Scarlet compiler build.

The Rust compiler and standard-library changes live in the Scarlet Rust fork.
This changes only --source; it never modifies registry caches or an installed
compiler. Dependency inputs are recorded by exact Git commits.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib


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
    if not (source / ".cargo/config.toml").is_file() or not (source / "vendor").is_dir():
        raise ValueError("source must contain the published vendored dependencies")
    recipe = json.loads((inputs / "recipe.json").read_text())
    records = []
    for package in recipe["packages"]:
        if "git" not in package or package.get("operations"):
            raise ValueError(f'expected one pinned fork without local operations for {package["name"]}')
        destination = source / "native-host-deps" / f'{package["name"]}-{package["version"]}'
        records.append(fetch_package(destination, package))
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
