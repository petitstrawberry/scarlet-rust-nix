# scarlet-rust-nix

Nix packaging for the Scarlet Rust toolchain.

This repository owns the Nix build and cache contract for the Rust fork used by
Scarlet. Scarlet itself should consume the cached package from this flake and
must not build the Rust fork in normal CI.

## Supported hosts

- `x86_64-linux` (`x86_64-unknown-linux-gnu`)
- `aarch64-linux` (`aarch64-unknown-linux-gnu`)
- `aarch64-darwin` (`aarch64-apple-darwin`)

Each host toolchain contains libraries for:

- `riscv64gc-unknown-scarlet`
- `aarch64-unknown-scarlet`

## Consumer rule

Scarlet CI should use this flake with fallback builds disabled:

```sh
nix develop --option fallback false
```

If the package is not available from Cachix, CI should fail instead of building
the Rust fork in the Scarlet repository.

## Build

For local source builds, provide the Rust fork source as the `rust-src` input:

```sh
git clone --recursive https://github.com/petitstrawberry/rust.git rust-src
git -C rust-src checkout b9573d6cd0731d24486f77ddf24d502e2e6bef02
nix build .#scarlet-rust-toolchain --override-input rust-src path:./rust-src -L
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
    src/rust/library/
  manifest.toml
```
