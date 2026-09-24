#!/usr/bin/env python3
"""Package a selectable Scarlet bundle from the two native release candidates."""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile


TARGETS = {
    "aarch64": "aarch64-unknown-scarlet",
    "riscv64": "riscv64gc-unknown-scarlet",
}
PREFIX = "/opt/scarlet/toolchains/rust/"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_inputs(directory, packaging_commit, rust_revision):
    if not re.fullmatch(r"[0-9a-f]{40}", packaging_commit):
        raise ValueError("Expected a full packaging commit")
    version = f"v0.1.0-dev.{packaging_commit[:12]}"
    hashes = {}
    scarlet_commit = None
    for arch, target in TARGETS.items():
        stem = f"native-rust-{arch}-{version}"
        manifest = json.loads((directory / f"{stem}.manifest.json").read_text())
        required = manifest.get("requires", {})
        if (
            manifest.get("schema") != 1
            or manifest.get("kind") != "scarlet-native-rust-toolchain"
            or manifest.get("version") != version
            or manifest.get("packaging_commit") != packaging_commit
            or manifest.get("target") != target
            or manifest.get("architecture") != arch
            or manifest.get("install_prefix") != PREFIX + version
            or manifest.get("rust", {}).get("revision") != rust_revision
            or manifest.get("rust", {}).get("backend") != "cranelift"
            or required.get("dynamic_loader") != "/bin/scarlet-ld"
            or manifest.get("components", {}).get("scarlet_ld", {}).get("included") is not False
            or not re.fullmatch(r"[0-9a-f]{40}", required.get("scarlet_commit", ""))
            or not all(manifest.get("capabilities", {}).get(key) is True for key in (
                "rustc", "codegen", "linker", "static_target_std"
            ))
        ):
            raise ValueError(f"Invalid native release manifest for {arch}")
        if scarlet_commit and scarlet_commit != required["scarlet_commit"]:
            raise ValueError("The native packages require different Scarlet revisions")
        scarlet_commit = required["scarlet_commit"]
        fields = (directory / f"{stem}.tar.zst.sha256").read_text().split()
        if (len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0])
                or fields[1] != f"{stem}.tar.zst"):
            raise ValueError(f"Invalid native release checksum for {arch}")
        if sha256(directory / fields[1]) != fields[0]:
            raise ValueError(f"Native archive checksum differs for {arch}")
        hashes[arch] = fields[0]
    return version, hashes, scarlet_commit


def package_bundle(directory, packaging_commit, rust_revision,
                   repository="petitstrawberry/scarlet-rust-nix"):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be an owner/name pair")
    version, hashes, scarlet_commit = release_inputs(directory, packaging_commit, rust_revision)
    archive = directory / f"rust-toolchain-bundle-{version}.tar.gz"
    checksum = archive.with_name(archive.name + ".sha256")
    if archive.exists() or checksum.exists():
        raise ValueError("Refusing to replace an existing bundle")
    manifest = (
        f"# Native Rust {version}; requires /bin/scarlet-ld from Scarlet {scarlet_commit}.\n"
        "# Select this release explicitly when building a Scarlet image.\n"
        "[[layers]]\n"
        'kind = "archive"\n'
        f'url = "https://github.com/{repository}/releases/download/{version}/native-rust-{{arch}}-{version}.tar.zst"\n'
        f'sha256 = {{ aarch64 = "sha256:{hashes["aarch64"]}", riscv64 = "sha256:{hashes["riscv64"]}" }}\n'
        'format = "tar-zst"\n'
        'strip_components = 1\n'
        f'to = "{PREFIX}{version}"\n\n'
        '[[layers]]\nkind = "copy"\nsource = "fs"\nto = "/"\n'
    ).encode()
    # Fixed headers make this tiny bundle independent of the runner and clock.
    with archive.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                entry = tarfile.TarInfo("rust-toolchain/bundle.toml")
                entry.mode = 0o644
                entry.size = len(manifest)
                bundle.addfile(entry, io.BytesIO(manifest))
                current = tarfile.TarInfo(f"rust-toolchain/fs{PREFIX}current")
                current.type = tarfile.SYMTYPE
                current.mode = 0o777
                current.linkname = version
                bundle.addfile(current)
    checksum.write_text(f"{sha256(archive)}  {archive.name}\n")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--packaging-commit", required=True)
    args = parser.parse_args()
    source = (Path(__file__).resolve().parent.parent / "flake.nix").read_text()
    revision = re.search(r'^\s*rustRev = "([0-9a-f]{40})";', source, re.MULTILINE)
    if not revision:
        raise SystemExit("Missing pinned Rust revision")
    try:
        print(package_bundle(args.directory, args.packaging_commit, revision[1],
                             os.environ.get("GITHUB_REPOSITORY", "petitstrawberry/scarlet-rust-nix")))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
