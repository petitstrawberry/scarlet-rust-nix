#!/usr/bin/env bash
# Intentionally run this expensive bootstrap in GitHub Actions, not on consumers.
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: scripts/build-native-host.sh [--target aarch64-unknown-scarlet|riscv64gc-unknown-scarlet]
       [--work-dir DIR] [--output DIR] [--backend dummy|cranelift]
Run inside `nix develop .#native-host`. Builds a native Scarlet compiler using
prebuilt build-host LLVM. A successful artifact is not a guest execution test.
USAGE
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target=aarch64-unknown-scarlet
backend=cranelift
work_dir="${repo_root}/.native-host-work"
output_dir="${repo_root}/native-host-output"
while (($#)); do
    case "$1" in
        --target) target="${2:?missing target}"; shift 2 ;;
        --backend) backend="${2:?missing backend}"; shift 2 ;;
        --work-dir) work_dir="${2:?missing work directory}"; shift 2 ;;
        --output) output_dir="${2:?missing output directory}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$target" in
    aarch64-unknown-scarlet) clang_target=aarch64-none-elf; clang_arch_flags='' ;;
    riscv64gc-unknown-scarlet) clang_target=riscv64-unknown-elf; clang_arch_flags='-march=rv64gc -mabi=lp64d' ;;
    *) echo "Unsupported native host: $target" >&2; exit 2 ;;
esac
case "$backend" in dummy|cranelift) ;; *) echo "Unsupported backend: $backend" >&2; exit 2 ;; esac
mkdir -p "$work_dir" "$output_dir"
work_dir="$(cd "$work_dir" && pwd)"
output_dir="$(cd "$output_dir" && pwd)"
export NATIVE_HOST_OUTPUT="$output_dir" NATIVE_HOST_TARGET="$target" NATIVE_HOST_BACKEND="$backend"
export NATIVE_HOST_REPO="$repo_root"
# Never let a previous successful tarball masquerade as this attempt's output.
rm -f "$output_dir/native-host.tar.xz" "$output_dir/native-host.tar.xz.sha256"
python3 - <<'PY'
import datetime, json, os, pathlib, subprocess
out = pathlib.Path(os.environ['NATIVE_HOST_OUTPUT'])
manifest = {
    'schema': 1, 'status': 'preparing', 'guest_verified': False,
    'native_host': os.environ['NATIVE_HOST_TARGET'], 'backend': os.environ['NATIVE_HOST_BACKEND'],
    'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'packaging_commit': subprocess.check_output(['git', '-C', os.environ['NATIVE_HOST_REPO'], 'rev-parse', 'HEAD'], text=True).strip(),
    'rust_commit': os.environ.get('SCARLET_RUST_REV'),
    'github_run_id': os.environ.get('GITHUB_RUN_ID'), 'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
finish() {
    local status=$?
    trap - EXIT
    NATIVE_HOST_EXIT="$status" python3 - <<'PY'
import datetime, json, os, pathlib
path = pathlib.Path(os.environ['NATIVE_HOST_OUTPUT']) / 'manifest.json'
data = json.loads(path.read_text())
data['exit_code'] = int(os.environ['NATIVE_HOST_EXIT'])
data['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
if data['exit_code']:
    data['status'] = 'failed'
path.write_text(json.dumps(data, indent=2) + '\n')
PY
    exit "$status"
}
trap finish EXIT
for name in SCARLET_BOOTSTRAP SCARLET_VENDORED_SOURCE SCARLET_LLVM_CONFIG SCARLET_CLANG SCARLET_CLANGXX SCARLET_LLVM_AR SCARLET_NATIVE_LINKER SCARLET_RUST_REV; do
    if [[ -z "${!name:-}" ]]; then
        echo "$name missing; enter nix develop .#native-host first" >&2
        exit 2
    fi
done
# Check build-host LLVM's system libraries before starting the long bootstrap.
python3 "$repo_root/scripts/check-native-host-llvm.py" 2>&1 | tee "$output_dir/host-llvm.log"
# Refuse to remove a caller's existing directory; this marker identifies our source copy.
if [[ ( -d "$work_dir/source" || -d "$work_dir/prepared-source" ) && ! -f "$work_dir/.scarlet-native-host-work" ]]; then
    echo "Existing unowned source directory: $work_dir/source" >&2
    exit 2
fi
touch "$work_dir/.scarlet-native-host-work"
rm -rf "$work_dir/prepared-source"
cp -a "$SCARLET_VENDORED_SOURCE" "$work_dir/prepared-source"
chmod -R u+w "$work_dir/prepared-source"
mkdir -p "$work_dir/build" "$work_dir/wrappers" "$work_dir/cargo-home"
export CARGO_HOME="$work_dir/cargo-home" CARGO_NET_OFFLINE=true RUSTC_BOOTSTRAP=1
python3 "$repo_root/scripts/prepare-native-host.py" --source "$work_dir/prepared-source" \
    --cargo "$SCARLET_BOOTSTRAP/bin/cargo" 2>&1 | tee "$output_dir/prepare.log"
# Exercise the patched allocator's actual placement logic before the expensive
# cross bootstrap. Compile and execute only a host test, never a native binary.
python3 "$repo_root/scripts/check-native-host-allocator.py" \
    "$work_dir/prepared-source/library/std/src/sys/alloc/scarlet.rs" \
    --rustc "$SCARLET_BOOTSTRAP/bin/rustc" 2>&1 | tee "$output_dir/allocator-regression.log"
# Do not restore source mtimes on equal content: Cargo must reuse unchanged
# patched files, and must notice changed patches. Vendor files are read-only
# baseline data from Nix, excluded from the Actions cache to save space.
mkdir -p "$work_dir/source"
rsync -rpl --checksum --delete --exclude /vendor/ "$work_dir/prepared-source/" "$work_dir/source/"
rm -rf "$work_dir/source/vendor"
cp -a "$work_dir/prepared-source/vendor" "$work_dir/source/vendor"
rm -rf "$work_dir/prepared-source"
cp "$work_dir/source/.scarlet-native-host-prepared.json" "$output_dir/source-preparation.json"
# The native C target has no host libc. Keep the host compiler's wrapped cc/c++
# available for bootstrap itself; only native compilations use these wrappers.
export NATIVE_HOST_WRAPPERS="$work_dir/wrappers" NATIVE_HOST_CLANG_TARGET="$clang_target" NATIVE_HOST_CLANG_ARCH_FLAGS="$clang_arch_flags"
python3 - <<'PY'
import os, pathlib, shlex
out = pathlib.Path(os.environ['NATIVE_HOST_WRAPPERS'])
for name, env in [('cc', 'SCARLET_CLANG'), ('cxx', 'SCARLET_CLANGXX')]:
    path = out / name
    flags = ['--target=' + os.environ['NATIVE_HOST_CLANG_TARGET'], '-ffreestanding', '-fPIC']
    flags += shlex.split(os.environ['NATIVE_HOST_CLANG_ARCH_FLAGS'])
    # Last flags win if cc-rs also passes the Rust target's OS spelling.
    path.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(os.environ[env]) + ' "$@" ' + shlex.join(flags) + '\n')
    path.chmod(0o755)
PY
python3 "$repo_root/native-host/prepare_host_build.py" \
    --rust-source "$work_dir/source" --target "$target" --backend "$backend" \
    --stage0-rustc "$SCARLET_BOOTSTRAP/bin/rustc" --stage0-cargo "$SCARLET_BOOTSTRAP/bin/cargo" \
    --host-llvm-config "$SCARLET_LLVM_CONFIG" \
    --native-cc "$work_dir/wrappers/cc" --native-cxx "$work_dir/wrappers/cxx" \
    --native-ar "$SCARLET_LLVM_AR" --native-linker "$SCARLET_NATIVE_LINKER" \
    --build-dir "$work_dir/build" --output "$output_dir/recipe" \
    --vendor --offline --nix --skip-stage0-validation
export NATIVE_HOST_SOURCE="$work_dir/source" NATIVE_HOST_BUILD="$work_dir/build"
python3 - <<'PY' 2>&1 | tee "$output_dir/build.log"
import json, os, pathlib, subprocess
out = pathlib.Path(os.environ['NATIVE_HOST_OUTPUT'])
data = json.loads((out / 'recipe/build-command.json').read_text())
subprocess.run(data['argv'], cwd=data['cwd'], env=dict(os.environ, **data['env']), check=True)
PY
python3 - <<'PY' 2>&1 | tee "$output_dir/package.log"
import hashlib, json, os, pathlib, shutil, struct, subprocess, tarfile
out = pathlib.Path(os.environ['NATIVE_HOST_OUTPUT'])
target = os.environ['NATIVE_HOST_TARGET']
build = pathlib.Path(os.environ['NATIVE_HOST_BUILD'])
recipe = json.loads((out / 'recipe/build-command.json').read_text())
build_host = recipe['build_host']
sysroot = pathlib.Path(recipe['native_sysroot'])
std = pathlib.Path(recipe['stdlib_source'])
if not (sysroot / 'bin/rustc').is_file() or not list(std.glob('libstd-*.rlib')):
    raise SystemExit('Native stage2 compiler or matching stage1 native standard library is missing')
package = out / 'sysroot'
if package.exists():
    shutil.rmtree(package)
# Bootstrap uses both `rustlib/src` and `rustlib/rustc-src` layouts for links
# back into the complete Rust checkout. Following either one would include
# source-only test fixtures, including foreign-architecture ELF samples from
# gcc/libgo. Native rustc does not need those components at runtime.
def runtime_sysroot_ignores(directory, names):
    if pathlib.Path(directory) == sysroot / 'lib/rustlib':
        return {'src', 'rustc-src'}.intersection(names)
    return set()

shutil.copytree(sysroot, package, symlinks=False, ignore=runtime_sysroot_ignores)
stdlib = package / 'lib/rustlib' / target / 'lib'
shutil.copytree(std, stdlib, dirs_exist_ok=True, symlinks=False)
if not list((package / 'lib').glob('librustc_driver*.so')):
    raise SystemExit('Native rustc_driver shared library is missing')
if os.environ['NATIVE_HOST_BACKEND'] == 'cranelift' and not list(
        (package / 'lib/rustlib' / target / 'codegen-backends').glob('librustc_codegen_cranelift*.so')):
    raise SystemExit('Requested native Cranelift codegen backend is missing')
expected_machine = {'aarch64-unknown-scarlet': 183, 'riscv64gc-unknown-scarlet': 243}[target]
elfs = []
for path in sorted(package.rglob('*')):
    if not path.is_file():
        continue
    with path.open('rb') as f:
        header = f.read(64)
        if header[:4] != b'\x7fELF':
            if path == package / 'bin/rustc' or path.suffix in ('.so', '.o'):
                raise SystemExit(f'Not ELF: {path}')
            continue
        if len(header) != 64 or header[4:7] != bytes([2, 1, 1]):
            raise SystemExit(f'Not ELF64 little endian: {path}')
        elf_type, machine = struct.unpack_from('<HH', header, 16)
        if machine != expected_machine:
            raise SystemExit(f'Wrong native architecture/type: {path}')
        interp, has_tls = None, False
        file_size = path.stat().st_size
        if elf_type == 1 and path.suffix == '.o':
            # Clang's freestanding CRT objects use generic SYSV OSABI. They are
            # link inputs, never executable programs or shared libraries.
            if header[7] not in (0, 0x53):
                raise SystemExit(f'Non-native relocatable OSABI: {path}')
            shoff = struct.unpack_from('<Q', header, 40)[0]
            shentsize, shnum, shstrndx = struct.unpack_from('<HHH', header, 58)
            if shentsize != 64 or not 0 < shnum < 65535 or shstrndx >= shnum or shoff + shnum * 64 > file_size:
                raise SystemExit(f'Invalid ELF section headers: {path}')
            for index in range(shnum):
                f.seek(shoff + index * shentsize)
                _, kind, flags, _, offset, size, _, _, _, _ = struct.unpack('<IIQQQQIIQQ', f.read(64))
                if kind != 8 and offset + size > file_size:
                    raise SystemExit(f'Out-of-bounds ELF object section: {path}')
                has_tls |= bool(flags & 0x400)
        elif elf_type in (2, 3):
            if header[7] != 0x53:
                raise SystemExit(f'Not a Scarlet native executable/shared library: {path}')
            phoff = struct.unpack_from('<Q', header, 32)[0]
            phentsize, phnum = struct.unpack_from('<HH', header, 54)
            if phentsize != 56 or not 0 < phnum <= 65535 or phoff + phnum * 56 > file_size:
                raise SystemExit(f'Invalid ELF program headers: {path}')
            for index in range(phnum):
                f.seek(phoff + index * phentsize)
                kind, _, offset, _, _, filesz, memsz, _ = struct.unpack('<IIQQQQQQ', f.read(56))
                if offset + filesz > file_size or (kind == 1 and filesz > memsz):
                    raise SystemExit(f'Out-of-bounds ELF segment: {path}')
                if kind == 3:
                    if not 0 < filesz <= 4096:
                        raise SystemExit(f'Invalid ELF interpreter: {path}')
                    f.seek(offset)
                    interp = f.read(filesz).rstrip(b'\0').decode('utf8')
                    if interp != '/system/bin/scarlet-ld':
                        raise SystemExit(f'Non-native interpreter {interp}: {path}')
                has_tls |= kind == 7
        else:
            raise SystemExit(f'Unexpected native ELF type: {path}')
        if path == package / 'bin/rustc' and interp != '/system/bin/scarlet-ld':
            raise SystemExit('Native rustc must use /system/bin/scarlet-ld')
    with path.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    elfs.append({'path': str(path.relative_to(package)), 'machine': machine, 'osabi': header[7],
                 'elf_type': elf_type,
                 'interpreter': interp, 'has_tls': has_tls, 'sha256': digest, 'size': path.stat().st_size})
manifest = json.loads((out / 'manifest.json').read_text())
manifest.update(status='built-not-guest-verified', build_host=build_host, elf_files=elfs,
                capabilities={'codegen': os.environ['NATIVE_HOST_BACKEND'] != 'dummy', 'guest_execution': False},
                note='ELF identity checks passed. Guest startup, dynamic relocations, compilation and linking remain to be tested.')
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
shutil.copy2(out / 'manifest.json', package / 'native-host-manifest.json')
archive = out / 'native-host.tar.xz'
with tarfile.open(archive, 'w:xz') as tar:
    tar.add(package, arcname='native-host')
with archive.open('rb') as f:
    checksum = hashlib.file_digest(f, 'sha256').hexdigest()
(out / 'native-host.tar.xz.sha256').write_text(checksum + '  native-host.tar.xz\n')
print(f'Native Scarlet compiler packaged: {archive} (not yet guest verified)')
PY
