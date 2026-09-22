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

The `--proc-macro` mode of Scarlet's `tools/native-rustc/run-qemu.py` builds two
separate macro DSOs inside the guest, loads both in one rustc process, exercises
function-like, attribute and derive macros plus thread TLS destruction, then
runs the generated executable. A build alone does not establish this capability.

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
