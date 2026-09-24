#!/usr/bin/env bash
# Heavy compilation belongs in Actions; the cross compiler must already exist.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
target="${1:?usage: build-native-linker.sh TARGET}"
case "$target" in aarch64-unknown-scarlet|riscv64gc-unknown-scarlet) ;; *) exit 2 ;; esac
: "${SCARLET_TOOLCHAIN:?enter native-linker/shell.nix}"
: "${SCARLET_NATIVE_LINKER:?missing native LLD cross linker}"
work="$repo_root/.native-linker-work"
output="$repo_root/native-linker-output"
mkdir -p "$work" "$output"
if [[ -e "$work/source" && ! -f "$work/.scarlet-native-linker-work" ]]; then
    echo 'Refusing to replace an unowned source directory' >&2
    exit 2
fi
touch "$work/.scarlet-native-linker-work"
rm -rf "$work/source" "$output/package"
rm -f "$output/native-linker.tar.xz" "$output/native-linker.tar.xz.sha256"
source_dir="$work/source"
revision="$(python3 -c 'import json; print(json.load(open("native-linker/recipe.json"))["revision"])')"
source_url="$(python3 -c 'import json; print(json.load(open("native-linker/recipe.json"))["upstream"])')"
git init -q "$source_dir"
git -C "$source_dir" remote add origin "$source_url"
git -C "$source_dir" fetch --depth 1 origin "$revision"
git -C "$source_dir" checkout -q --detach FETCH_HEAD
test "$(git -C "$source_dir" rev-parse HEAD)" = "$revision"
git -C "$source_dir" apply --check --unidiff-zero "$repo_root/native-linker/rust-lld-identity.patch"
git -C "$source_dir" apply --unidiff-zero "$repo_root/native-linker/rust-lld-identity.patch"
export CARGO_TARGET_DIR="$work/target" RUSTC="$SCARLET_TOOLCHAIN/bin/rustc"
export CARGO_PROFILE_OPT_OPT_LEVEL=2 CARGO_NET_OFFLINE=false
cargo="$SCARLET_TOOLCHAIN/bin/cargo"
# Apply these flags only to target crates, leaving build scripts/proc macros on Linux.
target_env="${target//-/_}"
target_env="${target_env^^}"
export "CARGO_TARGET_${target_env}_RUSTFLAGS=-Cpanic=abort -Clinker=$SCARLET_NATIVE_LINKER -Clink-arg=-z -Clink-arg=max-page-size=4096"
"$cargo" build --manifest-path "$source_dir/Cargo.toml" --locked --profile opt \
    -p wild-linker --no-default-features --features scarlet --target "$target" 2>&1 | tee "$output/native-build.log"
package="$output/package/native-linker"
mkdir -p "$package/bin"
cp "$source_dir/LICENSE-MIT" "$source_dir/LICENSE-APACHE" "$package/"
cp "$CARGO_TARGET_DIR/$target/opt/wild" "$package/bin/wild"
export NATIVE_LINKER_TARGET="$target" NATIVE_LINKER_OUTPUT="$output"
python3 - <<'PY'
import hashlib, json, os, pathlib, struct, subprocess, tarfile, tomllib
repo = pathlib.Path.cwd()
out = pathlib.Path(os.environ['NATIVE_LINKER_OUTPUT'])
package = out / 'package/native-linker'
target = os.environ['NATIVE_LINKER_TARGET']
binary = package / 'bin/wild'
data = binary.read_bytes()
machine = {'aarch64-unknown-scarlet': 183, 'riscv64gc-unknown-scarlet': 243}[target]
if len(data) < 64 or data[:8] != b'\x7fELF\x02\x01\x01\x53':
    raise SystemExit(f'wrong native Scarlet linker ELF identity: {binary}')
kind, actual_machine, version = struct.unpack_from('<HHI', data, 16)
if kind not in (2, 3) or (actual_machine, version) != (machine, 1):
    raise SystemExit(f'wrong native Scarlet linker ELF identity: {binary}')
manifest = json.loads((repo / 'native-linker/recipe.json').read_text())
with (pathlib.Path(os.environ['SCARLET_TOOLCHAIN']) / 'manifest.toml').open('rb') as stream:
    rust_revision = tomllib.load(stream)['rust_commit']
identity_patch = repo / 'native-linker/rust-lld-identity.patch'
manifest.update(target=target, github_run_id=os.environ.get('GITHUB_RUN_ID'),
                rust_toolchain_revision=rust_revision,
                packaging_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                status='built-not-guest-verified', guest_verified=False,
                rust_lld_identity_patch_sha256=hashlib.sha256(identity_patch.read_bytes()).hexdigest(),
                files={str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in package.rglob('*') if p.is_file()})
for path in (out / 'manifest.json', package / 'manifest.json'):
    path.write_text(json.dumps(manifest, indent=2) + '\n')
archive = out / 'native-linker.tar.xz'
with tarfile.open(archive, 'w:xz') as tar:
    tar.add(package, arcname='native-linker')
(out / 'native-linker.tar.xz.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  native-linker.tar.xz\n')
print('Native Scarlet linker packaged; runtime behavior is not asserted by this build.')
PY
