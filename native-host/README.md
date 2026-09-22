# Experimental native Scarlet compiler inputs

These inputs apply to the pinned Rust fork revision
`39c689a4859b9d8ee1828720135defd125c03d31`. The ordinary published cross toolchain
is unchanged. `scripts/prepare-native-host.py` applies them to a fresh writable
copy of the existing vendored source, never to registry or installed compiler
caches. `recipe.json` records the exact dependency versions; preparation records
all patch and adapter hashes.

The Rust patch enables native dynamic linking and separates executable startup
into `scarlet-crt0.o`, allowing std to remain statically linked into rustc_driver.
It adds native compiler cfg paths, owned-memory metadata reads, executable-path
lookup, and Cranelift AOT support. The compiler marks its successfully linked
Scarlet ELF outputs with OSABI83 after checking the ELF architecture/type; the
packager does not relabel foreign executables. Executable startup CRT objects
may retain generic ELF OSABI0.

Dependency patches cover libloading0.8.9/0.9.0, stacker0.1.21, target-lexicon0.13.3,
tempfile3.23.0, and getrandom0.3.3. The getrandom backend requires actual registered
entropy (for QEMU, VirtIO RNG); it never accepts Scarlet's pseudo-random fallback.
The initial stacker backend uses aligned allocations without guard pages.
Tempfile supports native named creation and keeping/overwrite rename; unsupported
identity/permission/atomic no-clobber operations return errors explicitly.
Scarlet compiler builds also select blake3's pure Rust implementation; its
optional AArch64 C implementation requires libc headers that Scarlet does not
provide. Build-host targets retain their normal blake3 features.

`prepare_host_build.py` emits the actual bootstrap config and command. The
build-host compiler uses cached LLVM; the Scarlet compiler defaults to Cranelift.
The optional dummy backend is only a frontend diagnostic and cannot generate
code. The download helper rejects dummy artifacts. Native compiler artifacts
omit the bootstrap sysroot's `rustlib/src` and `rustlib/rustc-src` source
components: they are not needed to run rustc and would otherwise copy the full
checkout, including foreign-architecture ELF test fixtures, into the runtime
artifact.

Scarlet's emulated TLS now separates each statically linked std's key namespace.
The thread pointer owns a list of namespace tables, identified by the address of
each std's destructor table. This prevents rustc and a loaded proc macro from
interpreting each other's key 1 (or later keys) as different Rust types. Native
thread exit runs destructors from all namespaces and frees their tables. Initial
threads publish their table through the architecture's thread pointer too.
Libraries remain pinned until process exit, as required by the existing loader.
Rebuild the complete native toolchain when adopting this runtime change.

The std allocator overlay aligns split free blocks to their header alignment.
Previously, an odd-sized allocation could place a `Block` at an unaligned
address even though the returned user pointer was aligned. The fix preserves
valid near-end allocations by consuming the whole block when rounding its end
would exceed the block boundary or overflow.

After source preparation and before bootstrap, the build executes
`scripts/check-native-host-allocator.py` with the pinned build-host rustc. It extracts
the real patched allocator's `Block`, constants and placement helpers, then
checks odd sizes, alignment, address/size overflow and exact/near-end fits.
`allocator-regression.log` records the source hash and test result. This host
test does not execute native `sbrk`, concurrency or the complete allocator;
native guest stress checks remain required. To run it against a prepared tree:

```sh
python3 scripts/check-native-host-allocator.py \
  /path/to/prepared-source/library/std/src/sys/alloc/scarlet.rs \
  --rustc "$SCARLET_BOOTSTRAP/bin/rustc"
```

The `--proc-macro` mode of Scarlet's `tools/native-rustc/run-qemu.py` builds two
separate macro DSOs inside the guest, loads both in one rustc process, exercises
function-like, attribute and derive macros plus thread TLS destruction, then
runs the generated executable. A build alone does not establish this capability.

The filesystem overlay requires Scarlet's Native operations 303/304 and 413–416:
descriptor/path timestamp updates, file sync, canonicalization, and metadata/mkdir
with detailed errors. The updated std resolves canonical paths through VFS,
preserves error kinds for `create_dir_all` and `try_exists`, and performs real
`FileTimes` and `sync_all` operations. Legacy kernel operations keep their old
error convention; an older kernel is not sufficient for this sysroot.

Scarlet's `tools/native-rustc` probe can be built with `--features native-fs` and
run with `--native-fs --proc-macro`. The AArch64 release-kernel HVF acceptance
checks both ext2 and tmpfs, actual C realpath/timestamp/sync adapters, symlink and
mount traversal, persistence, and the existing std/proc-macro compile-and-run
path. See Scarlet's `docs/development/native-rustc.md` and its
`2026-09-22-native-fs-aarch64` evidence. The local check rebuilt the target std
and reused the compatible previous local compiler; a full Actions rebuild and
guest acceptance are still required before updating the published bundle.
The initial C library does not yet supply Cargo's complete native dependency
closure. The target remains outside `cfg(unix)`.

Bootstrap caches first use the current branch, then the default branch with the
same Nix inputs. A cached build for the other native architecture may seed the
common Linux build-host compiler; Cargo still rebuilds changed target artifacts.

Before bootstrap, a tiny build-host LLVM client is linked and executed using
`llvm-config --link-static --system-libs`. This checks that the Nix shell exposes
LLVM's system dependencies (including libxml2) before compiling rustc. Its result
is saved in `host-llvm.log` alongside the other build evidence.

A successful Actions artifact proves cross-build and ELF identity only. Native
startup, Rust code generation, assembly/linker integration, and executing the
compiled program still require Scarlet guest evidence. In particular, Cranelift
needs a native assembler for inline/global assembly and a native linker for
executables. The separate [native Wild port](https://github.com/petitstrawberry/scarlet-rust-nix/pull/21)
now supplies the linker and passes native object/archive/Rust std link-and-run
tests on AArch64 and RV64. Integrating it with this compiler still requires the
full guest compile-and-run probe. No LLVM/C++ native runtime is silently bundled
or assumed.
