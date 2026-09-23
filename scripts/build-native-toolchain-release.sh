#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: scripts/build-native-toolchain-release.sh \
  --native-host DIR --native-linker DIR \
  --target aarch64-unknown-scarlet|riscv64gc-unknown-scarlet \
  --version vX.Y.Z --scarlet-commit FULL_SHA [--output DIR]

Create a deterministic tar.zst release asset, its checksum and a Scarlet bundle
manifest fragment. The input directories must come from the validated fetch
helpers. /bin/scarlet-ld is a required Scarlet component and is never
included in this toolchain archive.
USAGE
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
native_host=
native_linker=
target=
version=
scarlet_commit=
output="$repo_root/native-toolchain-output"
while (($#)); do
    case "$1" in
        --native-host) native_host="${2:?missing native host directory}"; shift 2 ;;
        --native-linker) native_linker="${2:?missing native linker directory}"; shift 2 ;;
        --target) target="${2:?missing target}"; shift 2 ;;
        --version) version="${2:?missing version}"; shift 2 ;;
        --scarlet-commit) scarlet_commit="${2:?missing Scarlet commit}"; shift 2 ;;
        --output) output="${2:?missing output directory}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
if [[ -z "$native_host" || -z "$native_linker" || -z "$target" || -z "$version" || -z "$scarlet_commit" ]]; then
    usage >&2
    exit 2
fi
if [[ ! "$version" =~ ^v[0-9A-Za-z][0-9A-Za-z._+-]*$ || ! "$scarlet_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid release version or Scarlet commit" >&2
    exit 2
fi
case "$target" in
    aarch64-unknown-scarlet) arch=aarch64 ;;
    riscv64gc-unknown-scarlet) arch=riscv64 ;;
    *) usage >&2; exit 2 ;;
esac
command -v python3 >/dev/null
command -v tar >/dev/null
command -v zstd >/dev/null

mkdir -p "$output"
output="$(cd "$output" && pwd)"
root_name="native-rust-${arch}-${version}"
package="$output/$root_name"
archive="$output/$root_name.tar.zst"
checksum="$archive.sha256"
sidecar="$output/$root_name.manifest.json"
layer="$output/manifest-native-rust-${arch}.toml"
for path in "$package" "$archive" "$checksum" "$sidecar" "$layer"; do
    if [[ -e "$path" || -L "$path" ]]; then
        echo "Refusing to replace existing output: $path" >&2
        exit 2
    fi
done

python3 "$repo_root/scripts/package-native-toolchain.py" \
    --native-host "$native_host" --native-linker "$native_linker" \
    --target "$target" --version "$version" --scarlet-commit "$scarlet_commit" \
    --destination "$package"
cp "$package/manifest.json" "$sidecar"

# GNU tar plus single-threaded zstd makes the same inputs byte-for-byte stable.
tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
    --pax-option=delete=atime,delete=ctime \
    -C "$output" -cf - "$root_name" | \
    zstd -q --no-progress -19 -T1 -f -o "$archive"

ARCHIVE="$archive" CHECKSUM="$checksum" LAYER="$layer" PACKAGE="$package" VERSION="$version" \
ARCH="$arch" ROOT_NAME="$root_name" REPOSITORY="${GITHUB_REPOSITORY:-petitstrawberry/scarlet-rust-nix}" \
python3 - <<'PY'
import hashlib, os, pathlib, re, shutil

archive = pathlib.Path(os.environ['ARCHIVE'])
digest = hashlib.sha256()
with archive.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
digest = digest.hexdigest()
pathlib.Path(os.environ['CHECKSUM']).write_text(f'{digest}  {archive.name}\n')
repository = os.environ['REPOSITORY']
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
    raise SystemExit('GITHUB_REPOSITORY is not an owner/name pair')
url = (f"https://github.com/{repository}/releases/download/"
       f"{os.environ['VERSION']}/{archive.name}")
prefix = f"/opt/scarlet/toolchains/rust/{os.environ['VERSION']}"
pathlib.Path(os.environ['LAYER']).write_text(
    '[[layers]]\n'
    'kind = "archive"\n'
    f'url = "{url}"\n'
    f'sha256 = "sha256:{digest}"\n'
    'format = "tar-zst"\n'
    'strip_components = 1\n'
    f'to = "{prefix}"\n'
)
shutil.rmtree(os.environ['PACKAGE'])
PY

echo "$archive"
echo "$sidecar"
echo "$layer"
