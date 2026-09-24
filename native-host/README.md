# Native Scarlet Rust compiler build

This directory defines the native `rustc` and Cranelift build inputs used by
`scarlet-rust-nix`. The Rust fork revision is pinned by `flake.nix`; compiler,
standard-library, CRT, and backend changes belong in that fork. `recipe.json`
pins dependency versions and exact fork commits. `scripts/prepare-native-host.py`
prepares only a fresh writable copy of the vendored source; it does not edit
registry caches or an installed toolchain.

The Scarlet adaptations of
[libloading](https://github.com/petitstrawberry/rust_libloading),
[stacker](https://github.com/petitstrawberry/stacker),
[target-lexicon](https://github.com/petitstrawberry/target-lexicon),
[tempfile](https://github.com/petitstrawberry/tempfile), and
[getrandom](https://github.com/petitstrawberry/getrandom) live in pinned forks.
Their exact commits are listed in `recipe.json`.

`prepare_host_build.py` emits an isolated stage2 bootstrap configuration.
`scripts/build-native-host.sh` builds and packages the result in Actions using
build-host LLVM and a Scarlet-native Cranelift backend. The package contains the
compiler, matching private driver/backend libraries, and matching target std.
Rust std remains statically linked into `rustc_driver`; no OS-wide shared std
is installed. The build's manifest records the Rust revision, target, backend,
and artifact identity. A successful build does not imply guest execution.

Use `scripts/fetch-native-host.sh` with an exact Actions run ID to download a
native-host artifact. Main's versioned packaging workflow automatically selects
matching native-host and Wild artifacts for both architectures and publishes an
installable bundle in this repository. Scarlet selects its version separately. Generated logs,
source copies, and build directories are Actions artifacts or ignored local
outputs, never checked-in source.
