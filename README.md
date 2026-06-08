# scarlet-rust-nix

Nix packaging for the Scarlet Rust toolchain.

This repository owns the Nix build and cache contract for the Rust fork used by
Scarlet. Scarlet itself should consume the cached package from this flake and
must not build the Rust fork in normal CI.

This build uses upstream nixpkgs `llvmPackages_21` — no LLVM fork is required.
The Rust fork handles scarlet target support on its own (see below).

## Supported hosts

- `x86_64-linux` (`x86_64-unknown-linux-gnu`)
- `aarch64-linux` (`aarch64-unknown-linux-gnu`)
- `aarch64-darwin` (`aarch64-apple-darwin`)

Each host toolchain contains libraries for:

- `riscv64gc-unknown-scarlet`
- `aarch64-unknown-scarlet`
- `riscv64gc-unknown-none-elf`
- `aarch64-unknown-none`
- `wasm32-unknown-unknown`
- `wasm32-wasip1`

The compiler build follows nixpkgs' Rust packaging split:

- `scarlet-rustc` builds the host compiler plus Scarlet targets using the
  Scarlet Rust fork and upstream nixpkgs LLVM.
- Existing upstream targets are built as target std-only outputs through the
  nixpkgs cross/fastCross path where applicable, then assembled into the final
  `scarlet-rust-toolchain`.

## Consumer rule

Scarlet CI should use this flake with fallback builds disabled:

```sh
nix develop --option fallback false
```

If the package is not available from Cachix, CI should fail instead of building
the Rust fork in the Scarlet repository.

## Build

Build the fixed Scarlet Rust fork revision configured in `flake.nix`:

```sh
nix build .#scarlet-llvm -L --accept-flake-config
nix build .#scarlet-rust-toolchain -L --accept-flake-config
```

The Rust fork source is fetched internally with shallow submodule checkout.
Consumers should not override or supply the fork source themselves.

## Updating the Rust fork revision

This repository treats the Rust fork's `scarlet-target` branch as the packaged
integration branch. Development commits can happen elsewhere in the Rust fork,
but only commits promoted to `scarlet-target` should be picked up automatically
here.

The `Update Scarlet Rust Revision` workflow can be started three ways:

- scheduled check of `petitstrawberry/rust` `scarlet-target`
- manual `workflow_dispatch`
- `repository_dispatch` with event type `rust-updated`

The updater does not build the full toolchain directly. It updates `rustRev`
and `rustHash` in `flake.nix`, opens a PR, and lets the normal build workflow
validate the toolchain on all supported hosts.

From the Rust fork, trigger this repository after updating the `scarlet-target`
branch:

```yaml
name: Trigger Scarlet Rust Nix

on:
  push:
    branches: [ "scarlet-target" ]
  workflow_dispatch:

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger scarlet-rust-nix
        run: |
          gh api repos/petitstrawberry/scarlet-rust-nix/dispatches \
            -f event_type=rust-updated \
            -F client_payload[rev]="${GITHUB_SHA}" \
            -F client_payload[ref]="${GITHUB_REF_NAME}"
        env:
          GH_TOKEN: ${{ secrets.SCARLET_RUST_NIX_DISPATCH_TOKEN }}
```

`SCARLET_RUST_NIX_DISPATCH_TOKEN` needs permission to create repository
dispatch events in `petitstrawberry/scarlet-rust-nix`.

For local updates:

```sh
scripts/update-rust-rev.sh <full-rust-commit-sha>
```

The output is a relocatable Rust toolchain:

```text
$out/
  bin/
  lib/
  lib/rustlib/
    <host-triple>/
    riscv64gc-unknown-scarlet/
    aarch64-unknown-scarlet/
    riscv64gc-unknown-none-elf/
    aarch64-unknown-none/
    wasm32-unknown-unknown/
    wasm32-wasip1/
    src/rust/library/
  manifest.toml
```
