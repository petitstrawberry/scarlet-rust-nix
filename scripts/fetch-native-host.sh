#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: scripts/fetch-native-host.sh RUN_ID [TARGET] [DESTINATION]
Download a successful native-host artifact from petitstrawberry/scarlet-rust-nix.
TARGET defaults to aarch64-unknown-scarlet. DESTINATION must not exist.
Requires GitHub CLI authentication and Python 3. No compiler build is performed.
Only Cranelift artifacts with Cargo and code generation are accepted; download frontend-only
dummy artifacts explicitly with `gh run download` for diagnostic work.
Override SCARLET_NATIVE_HOST_REPOSITORY to download from a development fork.
USAGE
}
if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then usage; exit 0; fi
run_id="${1:-}"
target="${2:-aarch64-unknown-scarlet}"
destination="${3:-native-host-${target}-${run_id}}"
if [[ ! "$run_id" =~ ^[0-9]+$ || $# -gt 3 ]]; then usage >&2; exit 2; fi
case "$target" in aarch64-unknown-scarlet|riscv64gc-unknown-scarlet) ;; *) usage >&2; exit 2 ;; esac
if [[ -e "$destination" || -L "$destination" ]]; then
    echo "Destination already exists: $destination" >&2; exit 2
fi
repo="${SCARLET_NATIVE_HOST_REPOSITORY:-petitstrawberry/scarlet-rust-nix}"
download="$(mktemp -d)"
trap 'rm -rf "$download"' EXIT
gh run download "$run_id" --repo "$repo" --name "native-host-${target}" --dir "$download"
python3 - "$download" "$destination" "$target" "$run_id" <<'PY'
import hashlib, json, os, pathlib, shutil, sys, tarfile, tempfile
source, destination = map(pathlib.Path, sys.argv[1:3])
target, run_id = sys.argv[3:5]
archive = source / 'native-host.tar.xz'
checksum_fields = (source / 'native-host.tar.xz.sha256').read_text().split()
if len(checksum_fields) != 2 or checksum_fields[1] != archive.name:
    raise SystemExit('Unexpected artifact checksum format')
digest = hashlib.sha256()
with archive.open('rb') as file:
    for chunk in iter(lambda: file.read(1024 * 1024), b''):
        digest.update(chunk)
if digest.hexdigest() != checksum_fields[0]:
    raise SystemExit('Artifact SHA-256 mismatch')
manifest = json.loads((source / 'manifest.json').read_text())
if (manifest.get('schema') != 1 or manifest.get('status') != 'built-not-guest-verified'
        or manifest.get('native_host') != target or str(manifest.get('github_run_id')) != run_id
        or manifest.get('exit_code') != 0):
    raise SystemExit('Artifact manifest does not identify a successful build for this target/run')
if manifest.get('backend') != 'cranelift' or manifest.get('capabilities', {}).get('codegen') is not True:
    raise SystemExit('Artifact has no Cranelift code generator. Frontend-only dummy artifacts must be downloaded explicitly with gh run download.')
if manifest.get('capabilities', {}).get('cargo') is not True:
    raise SystemExit('Artifact has no native Cargo')
destination = destination.absolute()
destination.parent.mkdir(parents=True, exist_ok=True)
if destination.exists() or destination.is_symlink():
    raise SystemExit('Destination already exists')
staging = pathlib.Path(tempfile.mkdtemp(prefix='.native-host-download-', dir=destination.parent))
try:
    with tarfile.open(archive, 'r:xz') as tar:
        seen = set()
        for member in tar:
            name = pathlib.PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'native-host'
                    or not (member.isdir() or member.isfile()) or name in seen):
                raise SystemExit('Unsafe or duplicate path in native-host archive: ' + member.name)
            seen.add(name)
            path = staging.joinpath(*name.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, path.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                path.chmod(member.mode & 0o777)
    sysroot = staging / 'native-host'
    for tool in ('rustc', 'cargo'):
        if not (sysroot / 'bin' / tool).is_file():
            raise SystemExit(f'Downloaded sysroot has no native {tool}')
    shutil.copy2(source / 'manifest.json', sysroot / 'native-host-manifest.json')
    os.rename(sysroot, destination)
finally:
    shutil.rmtree(staging)
print(destination)
print('Scarlet native artifact downloaded. Guest execution and compilation are not yet verified.')
PY
