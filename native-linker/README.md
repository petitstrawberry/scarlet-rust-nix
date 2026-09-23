# Native Scarlet build-time linker

The pinned [Scarlet Wild fork](https://github.com/petitstrawberry/wild) supplies
`wild`, the build-time linker for native Scarlet Rust programs. `recipe.json`
records its exact source revision. `scarlet-ld` is Scarlet's separate runtime
ELF loader. This build applies `rust-lld-identity.patch` when compiling the
linker, so the `rust-lld` alias uses its invoked name in help and diagnostics.
The Wild fork does not carry that display-name change.

`scripts/build-native-linker.sh TARGET` builds Wild for AArch64 or RV64 in
Actions and packages the binary, licenses and checksummed manifest. The native
linker is provided as `wild` and exposed as `rust-lld` in the combined toolchain.
`scripts/fetch-native-linker.sh` downloads one exact run artifact and validates
its manifest and file checksums. The versioned packaging workflow combines it
with a native-host artifact built from the same Rust fork revision.

Generated logs and build directories are Actions artifacts or ignored local
outputs. They are not checked in. Successful compilation is not a runtime
compatibility claim.
