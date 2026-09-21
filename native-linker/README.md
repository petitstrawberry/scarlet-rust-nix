# Native Scarlet build-time linker

This ports Wild 0.9.0 at the exact revision in `recipe.json`. It produces `wild`,
a build-time linker that combines objects and archives into ELF executables.
Scarlet's separate `scarlet-ld` loads shared objects at runtime.

The port uses owned input buffers, buffered output, and one current-thread Rayon
worker by default. Native outputs carry Scarlet ELF OSABI 83. It omits libc,
C zstd, fork and linker plugins. Perfetto is an optional dependency instead of
pulling an entropy backend into ordinary builds. UUID build IDs and zstd input
or output compression return explicit errors on Scarlet; hashed build IDs and
ordinary objects/archives remain available.

`Build Scarlet Native Linker` runs on Actions for AArch64 and RV64. The existing
Scarlet cross compiler and LLVM tools must be available from binary caches; the
workflow refuses to rebuild them. `shell.nix` does not fetch or rebuild Rust
sources. `scripts/build-native-linker.sh TARGET` builds the host test variant,
checks direct and archive linkage plus missing-symbol errors, then builds the
native binary and guest probe. It also captures and replays a Rust std program's
actual objects, rlibs and linker arguments, using the cached cross compiler.
Each artifact has a pinned source revision,
patch digest, per-file checksums and an archive checksum.

Host checks are not guest evidence. Artifacts are marked
`built-not-guest-verified` until the probe runs in Scarlet. `probe.rs` executes the
native linker, checks the two newly created executable headers, then executes
both programs and requires exit status 37. It also requires an unresolved
strong symbol to fail. New artifacts additionally link the captured Rust std
inputs, then require the generated program to print the exact expected text and
exit with status 37. Only then does it print `NATIVE_LINKER FULL PASS`.

The initial object/archive acceptance passed in Scarlet QEMU guests on both
AArch64 and RV64 using binaries from
[run 35554514866](https://github.com/petitstrawberry/scarlet-rust-nix/actions/runs/35554514866).
That run predates the Rust std test. Building or linking on the CI host is not
evidence for compiling Rust source inside Scarlet.

Download a successful artifact without rebuilding it:

```sh
scripts/fetch-native-linker.sh 35554514866 aarch64-unknown-scarlet /tmp/native-linker-aarch64
```

The helper verifies the requested run and target, archive checksum, manifest and
every packaged file checksum. It refuses an existing destination. Stage
`bin/wild` as `/system/bin/wild` alongside the native compiler overlay and pass
`--linker /system/bin/wild --linker-flavor ld.lld` to the full native rustc probe.

Guest layout for the probe:

```
/system/bin/wild
/system/bin/native-linker-probe
/opt/native-linker/fixtures/{main.o,answer.o,bias.o,libanswer.a}
/opt/native-linker/fixtures/rust/{link.args,*.o,*.rlib}
```

The initial acceptance covers static executables. Native rustc integration,
larger workloads, TLS, and dynamic output compatibility with `scarlet-ld` need
their own guest tests. Inputs are buffered in RAM, so peak memory consumption is
higher than upstream's mapped-file implementation. Upstream's Apache-2.0/MIT
licenses continue to apply to Wild.
