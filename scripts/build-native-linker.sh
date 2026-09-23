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
# Exercise the same buffered input/output and Scarlet ELF header path on Linux.
"$cargo" build --manifest-path "$source_dir/Cargo.toml" --locked --profile opt \
    -p wild-linker --no-default-features --features scarlet 2>&1 | tee "$output/host-build.log"
ln -sf wild "$CARGO_TARGET_DIR/opt/rust-lld"
"$CARGO_TARGET_DIR/opt/rust-lld" --help > "$output/rust-lld-help.txt"
grep -Fq '    rust-lld [OPTIONS] [FILES...]' "$output/rust-lld-help.txt"
grep -Fq 'rust-lld: supported targets:' "$output/rust-lld-help.txt"
if grep -Fiq 'wild' "$output/rust-lld-help.txt"; then
    echo 'rust-lld help exposes the Wild implementation identity' >&2
    exit 1
fi
"$CARGO_TARGET_DIR/opt/rust-lld" --version > "$output/rust-lld-version.txt"
grep -Fq 'rust-lld (Wild ' "$output/rust-lld-version.txt"
if "$CARGO_TARGET_DIR/opt/rust-lld" --definitely-invalid > "$output/rust-lld-error.txt" 2>&1; then
    echo 'rust-lld unexpectedly accepted an invalid option' >&2
    exit 1
fi
grep -Fq 'rust-lld: error:' "$output/rust-lld-error.txt"
"$CARGO_TARGET_DIR/opt/wild" --help > "$output/wild-help.txt"
grep -Fq '    wild [OPTIONS] [FILES...]' "$output/wild-help.txt"
grep -Fq -- '--wild-experiments' "$output/wild-help.txt"
python3 "$repo_root/native-linker/check.py" --target "$target" \
    --output "$output/fixtures" --linker "$CARGO_TARGET_DIR/opt/wild" 2>&1 | tee "$output/host-check.log"
# Apply these flags only to target crates, leaving build scripts/proc macros on Linux.
target_env="${target//-/_}"
target_env="${target_env^^}"
export "CARGO_TARGET_${target_env}_RUSTFLAGS=-Cpanic=abort -Clinker=$SCARLET_NATIVE_LINKER -Clink-arg=-z -Clink-arg=max-page-size=4096"
"$cargo" build --manifest-path "$source_dir/Cargo.toml" --locked --profile opt \
    -p wild-linker --no-default-features --features scarlet --target "$target" 2>&1 | tee "$output/native-build.log"
package="$output/package/native-linker"
mkdir -p "$package/bin" "$package/fixtures"
cp "$source_dir/LICENSE-MIT" "$source_dir/LICENSE-APACHE" "$package/"
cp "$CARGO_TARGET_DIR/$target/opt/wild" "$package/bin/wild"
cp "$output/fixtures/"*.o "$output/fixtures/"*.a "$package/fixtures/"
cp -R "$output/fixtures/rust" "$package/fixtures/rust"
"$RUSTC" --edition 2024 "$repo_root/native-linker/probe.rs" --target "$target" \
    -Cpanic=abort -Copt-level=1 -Clinker="$SCARLET_NATIVE_LINKER" \
    -Clink-arg=-z -Clink-arg=max-page-size=4096 -o "$package/bin/native-linker-probe"
export NATIVE_LINKER_TARGET="$target" NATIVE_LINKER_OUTPUT="$output"
python3 - <<'PY'
import hashlib, importlib.util, json, os, pathlib, subprocess, tarfile, tomllib
repo = pathlib.Path.cwd()
spec = importlib.util.spec_from_file_location('native_linker_check', repo / 'native-linker/check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
out = pathlib.Path(os.environ['NATIVE_LINKER_OUTPUT'])
package = out / 'package/native-linker'
target = os.environ['NATIVE_LINKER_TARGET']
for binary in (package / 'bin').iterdir():
    check.audit(binary, target)
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
print('Native Scarlet linker packaged. Guest link and execution tests remain required.')
PY
