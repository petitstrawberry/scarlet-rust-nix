{
  description = "Nix-packaged Scarlet Rust toolchain";

  nixConfig = {
    extra-substituters = [ "https://scarlet-rust-toolchain.cachix.org" ];
    extra-trusted-public-keys = [
      "scarlet-rust-toolchain.cachix.org-1:p+coBExi0nNTIvWF/oM9H9/1/GhwFtqGZ2Vs+4pYl6o="
    ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    rust-src = {
      url = "path:./rust-src-stub";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      rust-overlay,
      rust-src,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      hostTriples = {
        x86_64-linux = "x86_64-unknown-linux-gnu";
        aarch64-linux = "aarch64-unknown-linux-gnu";
        aarch64-darwin = "aarch64-apple-darwin";
      };

      targetTriples = [
        "riscv64gc-unknown-scarlet"
        "aarch64-unknown-scarlet"
      ];

      rustRev = "b9573d6cd0731d24486f77ddf24d502e2e6bef02";

      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs {
            inherit system;
            overlays = [ rust-overlay.overlays.default ];
          };
          bootstrapRust = pkgs.rust-bin.nightly."2025-12-31".default.override {
            extensions = [
              "rust-src"
              "llvm-tools-preview"
            ];
            targets = [
              "riscv64gc-unknown-none-elf"
              "aarch64-unknown-none"
            ];
          };
          vendoredRustSrc = pkgs.callPackage ./nix/vendor-rust-src.nix {
            inherit bootstrapRust rust-src rustRev;
          };
          scarlet-rust-toolchain = pkgs.callPackage ./nix/build-toolchain.nix {
            inherit vendoredRustSrc rustRev targetTriples;
            inherit bootstrapRust;
            nixpkgsPath = pkgs.path;
            hostTriple = hostTriples.${system};
          };
        in
        {
          scarlet-rust-vendored-src = vendoredRustSrc;
          scarlet-rust-bootstrap-cargo-deps = vendoredRustSrc;
          inherit scarlet-rust-toolchain;
          default = scarlet-rust-toolchain;
        }
      );

      checks = forAllSystems (
        system:
        let
          toolchain = self.packages.${system}.scarlet-rust-toolchain;
          pkgs = import nixpkgs { inherit system; };
        in
        {
          manifest = pkgs.runCommand "scarlet-rust-toolchain-manifest-check" { } ''
            test -x ${toolchain}/bin/rustc
            test -x ${toolchain}/bin/cargo
            test -f ${toolchain}/manifest.toml
            test -f ${toolchain}/lib/rustlib/src/rust/library/Cargo.lock
            test -d ${toolchain}/lib/rustlib/${hostTriples.${system}}/lib
            test -d ${toolchain}/lib/rustlib/riscv64gc-unknown-scarlet/lib
            test -d ${toolchain}/lib/rustlib/aarch64-unknown-scarlet/lib
            ${toolchain}/bin/rustc -vV
            touch $out
          '';
        }
      );
    };
}
