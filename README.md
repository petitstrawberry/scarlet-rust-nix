# scarlet-rust-nix

Nix packaging for the Scarlet Rust toolchain.

This repository owns the Nix build and cache contract for the Rust fork used by
Scarlet. Scarlet itself should consume the cached package from this flake and
must not build the Rust fork in normal CI.

The Scarlet LLVM fork is packaged separately from the Rust toolchain. Updating
the Rust fork revision does not force an LLVM rebuild unless the LLVM revision
also changes.

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

- `scarlet-rustc` builds the host compiler plus Scarlet targets with the
  Scarlet Rust and LLVM forks.
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
