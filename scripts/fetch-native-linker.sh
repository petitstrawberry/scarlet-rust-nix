#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: scripts/fetch-native-linker.sh RUN_ID [TARGET] [DESTINATION]
Download a native Scarlet Wild linker build artifact.
TARGET defaults to aarch64-unknown-scarlet. DESTINATION must not exist.
Requires authenticated GitHub CLI and Python 3; no compiler build is performed.
Override SCARLET_NATIVE_LINKER_REPOSITORY to use a development fork.
USAGE
}
if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then usage; exit 0; fi
run_id="${1:-}"
target="${2:-aarch64-unknown-scarlet}"
destination="${3:-native-linker-${target}-${run_id}}"
if [[ ! "$run_id" =~ ^[0-9]+$ || $# -gt 3 ]]; then usage >&2; exit 2; fi
case "$target" in aarch64-unknown-scarlet|riscv64gc-unknown-scarlet) ;; *) usage >&2; exit 2 ;; esac
if [[ -e "$destination" || -L "$destination" ]]; then
    echo "Destination already exists: $destination" >&2; exit 2
fi
repo="${SCARLET_NATIVE_LINKER_REPOSITORY:-petitstrawberry/scarlet-rust-nix}"
download="$(mktemp -d)"
trap 'rm -rf "$download"' EXIT
gh run download "$run_id" --repo "$repo" --name "native-linker-${target}" --dir "$download"
python3 - "$download" "$destination" "$target" "$run_id" <<'PY'
import hashlib, json, os, pathlib, shutil, sys, tarfile, tempfile
source, destination = map(pathlib.Path, sys.argv[1:3])
target, run_id = sys.argv[3:5]
archive = source / 'native-linker.tar.xz'

def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()

checksum = (source / 'native-linker.tar.xz.sha256').read_text().split()
if len(checksum) != 2 or checksum[1] != archive.name or sha256(archive) != checksum[0]:
    raise SystemExit('Artifact archive checksum mismatch')
manifest = json.loads((source / 'manifest.json').read_text())
if (manifest.get('schema') != 1 or manifest.get('status') != 'built-not-guest-verified'
        or manifest.get('target') != target or str(manifest.get('github_run_id')) != run_id):
    raise SystemExit('Artifact manifest target, run or build status mismatch')
destination = destination.absolute()
destination.parent.mkdir(parents=True, exist_ok=True)
if destination.exists() or destination.is_symlink():
    raise SystemExit('Destination already exists')
staging = pathlib.Path(tempfile.mkdtemp(prefix='.native-linker-download-', dir=destination.parent))
try:
    with tarfile.open(archive, 'r:xz') as tar:
        seen = set()
        for member in tar:
            name = pathlib.PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'native-linker'
                    or not (member.isdir() or member.isfile()) or name in seen):
                raise SystemExit('Unsafe or duplicate archive path: ' + member.name)
            seen.add(name)
            path = staging.joinpath(*name.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, path.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                path.chmod(member.mode & 0o777)
    package = staging / 'native-linker'
    if json.loads((package / 'manifest.json').read_text()) != manifest:
        raise SystemExit('Inner and outer artifact manifests differ')
    files = manifest.get('files', {})
    actual = {str(path.relative_to(package)) for path in package.rglob('*')
              if path.is_file() and path != package / 'manifest.json'}
    if set(files) != actual or 'bin/wild' not in actual:
        raise SystemExit('Artifact file inventory mismatch')
    for name, digest in files.items():
        if sha256(package / name) != digest:
            raise SystemExit('Artifact file checksum mismatch: ' + name)
    os.rename(package, destination)
finally:
    shutil.rmtree(staging)
print(destination)
print('Native linker downloaded and checksums verified.')
PY
