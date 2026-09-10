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

- `riscv32gc-unknown-scarlet`
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

The updater computes source and vendor hashes on all three hosts and maintains
one PR from `automation/rust-update`. Both the branch push and the PR operation
use `SCARLET_RUST_NIX_UPDATE_TOKEN`, so automatic PR CI does not wait for a human
to approve a `GITHUB_TOKEN`-triggered workflow. A retry with identical contents
does not replace the commit. New updates replace the pending candidate and cancel
the previous PR build.

After all three host builds, checks and Cachix uploads succeed, the build workflow
merges the update with `SCARLET_RUST_NIX_UPDATE_TOKEN`. The merger accepts only
revision/hash changes in `flake.nix` and `nix/vendor-rust-src.nix`, checks the
current Rust integration branch, and uses the exact PR head SHA that passed CI.
If the base moved during the build, it updates the PR branch for fresh CI.
Other PRs remain manual. Configure `main` with the required status check
`Toolchain CI`, strict/up-to-date checks enabled, and no required review approval.

The update token needs Contents and Pull requests write access to this repository.
Using a PAT for the merge also triggers the normal `main` workflow, which reuses
the PR's cached derivations and updates the latest pins.

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
The Rust fork installs this sender as `.github/workflows/notify-scarlet-rust-nix.yml`.
Dispatch payloads are notifications; the receiver resolves the integration
branch again to prevent delayed events from selecting an older commit. The daily
scheduled check remains a fallback if a notification is missed.

## Cache retention

Same-repository PRs upload checked toolchain outputs and their runtime closures
to Cachix without pinning them. Fork PRs only read the public cache. Builds upload
explicit output paths after checks, rather than pushing every build dependency
through a post-build hook. In particular, new source/vendor derivations are not
uploaded unless they are actually part of a toolchain output's closure.

Before uploading any candidate, `Protect Scarlet toolchains` reads Scarlet's
committed `flake.lock` from the newest published `distro-*` release.
It pins the corresponding toolchain output for each supported host:

- `scarlet-distro-<system>` protects the latest published distro.
- `latest-<system>` identifies the newest successful toolchain build on `main`.

Each name keeps one revision. Multiple names that reference the same store path
share its storage. Consumer pins are refreshed every six hours and before the
latest pins move; an older CI run cannot move latest backwards. Available consumer
paths are protected first. A missing consumer is restored from Scarlet's cache
or rebuilt on its native host, then uploaded and pinned before candidate uploads are allowed. Failure to resolve,
restore or pin a consumer blocks new uploads instead of silently discarding its
protection.

Unpinned PR outputs still consume storage and may be garbage-collected under
capacity pressure. Pins protect consumers, but cannot make an undersized cache
large enough: it must hold the protected closures plus space for a candidate.
This policy protects the latest distro release, not every historical release.

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
