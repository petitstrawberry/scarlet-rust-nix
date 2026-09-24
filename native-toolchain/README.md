# Native Rust toolchain distribution

This directory documents the release boundary for the compiler that runs inside
Scarlet. `scarlet-rust-nix` packages the compiler and build-time linker:

- `rustc` and its toolchain-private `librustc_driver` shared object
- the Cranelift codegen backend
- the matching target's static standard-library inputs
- Wild as `bin/wild`, with a `bin/rust-lld` toolchain alias whose display
  name is set by the native-linker build

Scarlet owns `/bin/scarlet-ld`. The runtime loader is coupled to Scarlet's
`exec`, VM and native ABI, so it is not copied into this repository's releases.
Every native Rust archive instead records the exact required Scarlet commit and
loader path in `manifest.json`.

The installed layout is versioned so incompatible Rust ABIs can coexist:

```text
/opt/scarlet/toolchains/rust/vX.Y.Z/
  bin/rustc
  bin/rust-lld -> wild
  bin/wild
  bin/librustc_driver-<hash>.so -> ../lib/librustc_driver-<hash>.so
  lib/librustc_driver-<hash>.so
  lib/rustlib/<target>/codegen-backends/librustc_driver-<hash>.so
    -> ../../../librustc_driver-<hash>.so
  lib/rustlib/<target>/codegen-backends/librustc_codegen_cranelift-<hash>.so
  lib/rustlib/<target>/lib/*.rlib
  manifest.json
```

The target standard library remains statically linked. Standalone `.rmeta`,
target `libstd.so`, compiler-build proc-macro DSOs, test fixtures and `cargo` are
excluded. Cargo and procedural-macro execution need separate guest bring-up and
are reported as unsupported capabilities until that work passes.

The Rust license files in `native-toolchain/licenses/rust` are copied from the
Rust fork revision currently pinned by this repository. They are packaged with
Wild's licenses because the bootstrap sysroot does not install Rust's notices.

## Automatic releases

Relevant PRs build AArch64 and RV64 after the cross toolchain has been checked
and uploaded to Cachix. Main then reuses artifacts whose build-input hashes
match the source Git trees, builds any missing components, and packages both
architectures. Only the current main revision can publish.

Each successful main revision publishes an immutable prerelease named
`v0.1.0-dev.<12-character-packaging-commit>`. Both architecture archives,
checksums and manifests are uploaded to a draft before it becomes public.
The workflow then commits both archive hashes, the versioned URL/prefix and
the `current` symlink atomically to Scarlet's `dev` branch. A concurrent Scarlet
commit is preserved by retrying from its new tip; toolchain downgrades are
rejected. `SCARLET_RUST_NIX_UPDATE_TOKEN` needs contents write access to Scarlet.

Retry the main **Build Scarlet Rust Toolchain** workflow to recover a failed
publication or consumer update. It reuses completed components and never
overwrites a published release. Release creation does not verify guest execution.

## Package exact artifacts manually

Download exact successful Actions artifacts first:

```sh
scripts/fetch-native-host.sh HOST_RUN aarch64-unknown-scarlet /tmp/native-host
scripts/fetch-native-linker.sh LINKER_RUN aarch64-unknown-scarlet /tmp/native-linker
scripts/build-native-toolchain-release.sh \
  --native-host /tmp/native-host \
  --native-linker /tmp/native-linker \
  --target aarch64-unknown-scarlet \
  --version v0.1.0 \
  --scarlet-commit FULL_SCARLET_COMMIT
```

The output contains a deterministic `tar.zst`, SHA-256 sidecar, complete package
manifest and a Scarlet bundle manifest fragment. The automatic release workflow
uses these same packaging commands without rebuilding matching Rust artifacts.
