#!/usr/bin/env python3
"""Protect Scarlet consumers before publishing new toolchain cache entries."""

import argparse
import base64
import json
import os
import re
import subprocess
import urllib.request

SYSTEMS = ("x86_64-linux", "aarch64-linux", "aarch64-darwin")
REPOSITORY = "petitstrawberry/scarlet-rust-nix"


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def api(endpoint):
    return json.loads(run("gh", "api", endpoint))


def locked_revision(lock):
    nodes = lock["nodes"]
    node = nodes[nodes[lock["root"]]["inputs"]["scarlet-rust-toolchain"]]["locked"]
    if (node.get("type"), node.get("owner"), node.get("repo")) != (
        "github", "petitstrawberry", "scarlet-rust-nix"
    ):
        raise ValueError("Scarlet must reference the expected toolchain repository")
    revision = node["rev"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid toolchain revision in Scarlet's lockfile")
    return revision


def consumers():
    refs = {"scarlet-dev": "dev", "scarlet-main": "main"}
    # Release tags are distinct from the latest GitHub release (which may be a kernel).
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", "repos/petitstrawberry/Scarlet/releases"))
    releases = [
        release for page in pages for release in page
        if not release["draft"] and not release["prerelease"]
        and re.fullmatch(r"distro-v[0-9][A-Za-z0-9.+-]*", release["tag_name"])
    ]
    if not releases:
        raise ValueError("No published Scarlet distro release found; retaining existing pins")
    refs["scarlet-distro"] = max(releases, key=lambda r: r["published_at"])["tag_name"]
    result = {}
    for name, ref in refs.items():
        data = api(f"repos/petitstrawberry/Scarlet/contents/flake.lock?ref={ref}")
        lock = json.loads(base64.b64decode(data["content"]))
        result[name] = locked_revision(lock)
    return result


def plan_pins(revisions, evaluate):
    paths = {}
    pins = []
    for name, revision in revisions.items():
        for system in SYSTEMS:
            key = (revision, system)
            if key not in paths:
                paths[key] = evaluate(revision, system)
            path = paths[key]
            if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-scarlet-rust-toolchain-[0-9a-f]{12}", path):
                raise ValueError(f"Unexpected toolchain output: {path}")
            pins.append((f"{name}-{system}", path))
    return pins


def pin(cache, name, path):
    # The API pins a cached path directly. The CLI requires the complete path
    # to be registered in the local Nix store, which is unnecessary here.
    payload = {"name": name, "storePath": path, "artifacts": [],
               "keep": {"tag": "Revisions", "contents": 1}}
    request = urllib.request.Request(
        f"https://app.cachix.org/api/v1/cache/{cache}/pin",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {os.environ['CACHIX_AUTH_TOKEN']}",
                 "Content-Type": "application/json", "User-Agent": "scarlet-rust-nix"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60):
        pass
    print(f"Protected {name}: {path}", flush=True)


def validate_plan(plan):
    for entry in plan:
        system = entry["system"]
        if system not in SYSTEMS or entry["name"] not in [
            f"{name}-{system}" for name in ("scarlet-dev", "scarlet-main", "scarlet-distro", "latest")
        ]:
            raise ValueError("Invalid pin name or host")
        if not re.fullmatch(r"[0-9a-f]{40}", entry["revision"]):
            raise ValueError("Invalid pin revision")
        if not re.fullmatch(r"/nix/store/[a-z0-9]{32}-scarlet-rust-toolchain-[0-9a-f]{12}", entry["path"]):
            raise ValueError("Invalid pin path")


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", metavar="PLAN")
    mode.add_argument("--apply", metavar="PLAN")
    args = parser.parse_args()
    cache = os.environ["CACHIX_CACHE_NAME"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", cache) or not os.environ.get("CACHIX_AUTH_TOKEN"):
        raise ValueError("Cachix credentials are required before publishing toolchains")

    if args.apply:
        with open(args.apply) as file:
            plan = json.load(file)
        validate_plan(plan)
        current = consumers()
        for entry in plan:
            if not entry["name"].startswith("latest-"):
                name = entry["name"].removesuffix(f"-{entry['system']}")
                if current.get(name) != entry["revision"]:
                    raise RuntimeError("Scarlet consumers changed during restoration; rerun to refresh the pin plan")
        for entry in plan:
            if entry["name"].startswith("latest-"):
                if api(f"repos/{REPOSITORY}/commits/main")["sha"] != entry["revision"]:
                    print("Skipping latest pin from a superseded main build", flush=True)
                    continue
            pin(cache, entry["name"], entry["path"])
        return

    revisions = consumers()
    latest = os.environ.get("LATEST_REV", "")
    if latest:
        if not re.fullmatch(r"[0-9a-f]{40}", latest):
            raise ValueError("Invalid latest revision")
        if api(f"repos/{REPOSITORY}/commits/main")["sha"] == latest:
            revisions["latest"] = latest

    def evaluate(revision, system):
        return run(
            "nix", "eval", "--raw", "--accept-flake-config",
            f"github:{REPOSITORY}/{revision}#packages.{system}.scarlet-rust-toolchain.outPath",
        )

    paths = dict(plan_pins(revisions, evaluate))
    plan = [{"name": f"{name}-{system}", "path": paths[f"{name}-{system}"],
             "system": system, "revision": revision}
            for name, revision in revisions.items() for system in SYSTEMS]
    validate_plan(plan)
    available = {}
    missing = {}
    runners = {"x86_64-linux": "ubuntu-latest", "aarch64-linux": "ubuntu-24.04-arm", "aarch64-darwin": "macos-14"}
    for entry in plan:
        path = entry["path"]
        if path not in available:
            store_hash = path.split("/")[-1].split("-")[0]
            status = run("curl", "--retry", "3", "--silent", "--show-error", "--output", "/dev/null",
                         "--write-out", "%{http_code}", f"https://{cache}.cachix.org/{store_hash}.narinfo")
            if status not in ("200", "404"):
                raise RuntimeError(f"Cannot check cache availability: HTTP {status}")
            available[path] = status == "200"
        if available[path]:
            # Protect every available consumer before any recovery/candidate uploads.
            if not entry["name"].startswith("latest-"):
                pin(cache, entry["name"], path)
        else:
            missing[path] = {**entry, "os": runners[entry["system"]]}
    with open(args.prepare, "w") as file:
        json.dump(plan, file)
    with open(os.environ["GITHUB_OUTPUT"], "a") as file:
        file.write("has_missing=" + str(bool(missing)).lower() + "\n")
        file.write("matrix=" + json.dumps({"include": list(missing.values())}) + "\n")


if __name__ == "__main__":
    main()
