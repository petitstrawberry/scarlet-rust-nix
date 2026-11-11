#!/usr/bin/env python3
"""Merge a separately vendored workspace without conflating Cargo sources.

Keep the main vendor layout (native builds consume vendor/scarlet-libc).
Registry packages and Git sources shared with the main workspace are merged
there. Git sources unique to the extra workspace retain their own directory.
Run from the Rust source root, before installing .cargo/config.toml.
"""
import argparse
import json
from pathlib import Path
import shutil
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit


def read_toml(path):
    return tomllib.loads(Path(path).read_text())


def inventory(directory):
    packages = {}
    for path in sorted(directory.iterdir()):
        manifest = read_toml(path / "Cargo.toml")["package"]
        key = manifest["name"], manifest["version"]
        if key in packages:
            raise ValueError(f"duplicate vendored package {key} in {directory}")
        packages[key] = path
    return packages


def source_key(source, sources):
    if source == "registry+https://github.com/rust-lang/crates.io-index":
        return "crates-io"
    if not source.startswith("git+"):
        raise ValueError(f"unsupported Cargo source: {source}")
    parsed = urlsplit(source[4:])
    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    query = parse_qs(parsed.query)
    matches = [key for key, value in sources.items()
               if value.get("git") == url
               and all(value.get(field) == query.get(field, [None])[0]
                       for field in ("rev", "branch", "tag"))]
    if len(matches) != 1:
        raise ValueError(f"expected one vendor configuration for {source}: {matches}")
    return matches[0]


def merge(main_config, extra_config, extra_lock):
    main = read_toml(main_config)["source"]
    extra = read_toml(extra_config)["source"]
    main_directory = Path(main[main["crates-io"]["replace-with"]]["directory"])
    extra_directory = Path(extra[extra["crates-io"]["replace-with"]]["directory"])
    existing = inventory(main_directory)
    incoming = inventory(extra_directory)
    locked = {}
    for package in read_toml(extra_lock)["package"]:
        if "source" not in package:
            continue
        key = package["name"], package["version"]
        if key in locked:
            raise ValueError(f"ambiguous extra workspace package: {key}")
        locked[key] = package["source"]

    extra_replacement = "vendored-rust-analyzer"
    if extra_replacement in main:
        raise ValueError(f"replacement name already exists: {extra_replacement}")
    unique_sources = set()
    for key, path in incoming.items():
        source = source_key(locked[key], extra)
        if source not in main:
            # Do not put a different-source home 0.5.12 beside the registry one.
            unique_sources.add(source)
            continue
        if {k: v for k, v in main[source].items() if k != "replace-with"} != {
                k: v for k, v in extra[source].items() if k != "replace-with"}:
            raise ValueError(f"source definition changed: {source}")
        destination_directory = Path(main[main[source]["replace-with"]]["directory"])
        if destination_directory != main_directory:
            raise ValueError(f"unexpected main vendor layout for {source}")
        if key in existing:
            # A registry checksum identifies the archive; Git checksum entries
            # identify the vendored files. Never silently overwrite either.
            old = json.loads((existing[key] / ".cargo-checksum.json").read_text())
            new = json.loads((path / ".cargo-checksum.json").read_text())
            if old != new:
                raise ValueError(f"different contents for shared package {key}")
            shutil.rmtree(path)
        else:
            destination = main_directory / path.name
            if destination.exists():
                destination = main_directory / f"{key[0]}-{key[1]}"
            if destination.exists():
                raise ValueError(f"vendor directory collision: {destination}")
            shutil.move(str(path), destination)
            existing[key] = destination

    if unique_sources:
        main[extra_replacement] = {"directory": str(extra_directory)}
        for source in sorted(unique_sources):
            main[source] = dict(extra[source], **{"replace-with": extra_replacement})
    else:
        extra_directory.rmdir()
    # cargo vendor emits only source tables with string fields. JSON quoting
    # is valid TOML for these ASCII keys/values and escapes URLs safely.
    lines = []
    for source, fields in sorted(main.items()):
        lines.append(f"[source.{json.dumps(source)}]")
        for name, value in sorted(fields.items()):
            if not isinstance(value, str):
                raise ValueError(f"unexpected Cargo configuration value: {name}")
            lines.append(f"{name} = {json.dumps(value)}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("main_config", type=Path)
    parser.add_argument("extra_config", type=Path)
    parser.add_argument("extra_lock", type=Path)
    args = parser.parse_args()
    print(merge(args.main_config, args.extra_config, args.extra_lock))
