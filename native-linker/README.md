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
native binary and guest probe. Each artifact has a pinned source revision,
patch digest, per-file checksums and an archive checksum.

Host checks are not guest evidence. Artifacts are marked
`built-not-guest-verified` until the probe runs in Scarlet. `probe.rs` executes the
native linker, checks the two newly created executable headers, then executes
both programs and requires exit status 37. It also requires an unresolved
strong symbol to fail. Only then does it print `NATIVE_LINKER FULL PASS`.

Guest layout for the probe:

```
/system/bin/wild
/system/bin/native-linker-probe
/opt/native-linker/fixtures/{main.o,answer.o,bias.o,libanswer.a}
```

The initial acceptance covers static executables. Native rustc integration,
larger workloads, TLS, and dynamic output compatibility with `scarlet-ld` need
their own guest tests. Inputs are buffered in RAM, so peak memory consumption is
higher than upstream's mapped-file implementation. Upstream's Apache-2.0/MIT
licenses continue to apply to Wild.
