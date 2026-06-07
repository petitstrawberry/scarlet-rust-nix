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
  };

  outputs =
    {
      self,
      nixpkgs,
      rust-overlay,
    }:
    let
      lib = nixpkgs.lib;
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

      scarletTargetTriples = [
        "riscv64gc-unknown-scarlet"
        "aarch64-unknown-scarlet"
      ];

      upstreamTargetTriples = [
        "riscv64gc-unknown-none-elf"
        "aarch64-unknown-none"
        "wasm32-unknown-unknown"
        "wasm32-wasip1"
      ];

      nixpkgsCompilerTargetTriples = [
        "wasm32-unknown-unknown"
      ];
      targetTriples = scarletTargetTriples ++ upstreamTargetTriples;
      noStdTargetTriples = [
        "riscv64gc-unknown-none-elf"
        "aarch64-unknown-none"
      ];
      noOptimizedCompilerBuiltinsTargetTriples = scarletTargetTriples ++ nixpkgsCompilerTargetTriples;

      rustRev = "804637c89bf86d2cdce35db31a08b0aabd98cb08";
      rustHash = "sha256-OhDUvvpPXJhOA00CjtV8XGv1g90o2R3WTH+ZmMz9Epc=";
      llvmRev = "6865ecb3f8dc308df539210970b7f4008ea70309";

      forAllSystems = f: lib.genAttrs systems (system: f system);
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
          };
          rustSrc = pkgs.fetchgit {
            url = "https://github.com/petitstrawberry/rust.git";
            rev = rustRev;
            fetchSubmodules = true;
            deepClone = false;
            leaveDotGit = false;
            hash = rustHash;
          };
          llvmSrc = pkgs.fetchgit {
            url = "https://github.com/petitstrawberry/llvm-project.git";
            rev = llvmRev;
            fetchSubmodules = false;
            deepClone = false;
            leaveDotGit = false;
            hash = "sha256-pjOaNaKjmt4ls0MmPnouqOjM+ERaqNj7mj3RkyWxykA=";
          };
          scarletLlvmPackages = pkgs.llvmPackages_21.override {
            monorepoSrc = llvmSrc;
          };
          vendoredRustSrc = pkgs.callPackage ./nix/vendor-rust-src.nix {
            inherit bootstrapRust rustRev;
            rust-src = rustSrc;
          };
          scarlet-rustc = pkgs.callPackage ./nix/build-toolchain.nix {
            inherit vendoredRustSrc rustRev noOptimizedCompilerBuiltinsTargetTriples;
            inherit bootstrapRust;
            nixpkgsPath = pkgs.path;
            llvmPackages = scarletLlvmPackages;
            hostTriple = hostTriples.${system};
            targetTriples = scarletTargetTriples ++ nixpkgsCompilerTargetTriples;
          };
          targetStdScopes = {
            riscv64gc-unknown-none-elf = pkgs.pkgsCross.riscv64-embedded.buildPackages;
            aarch64-unknown-none = pkgs.pkgsCross.aarch64-embedded.buildPackages;
            wasm32-wasip1 = pkgs.pkgsCross.wasi32.buildPackages;
          };
          targetStdPackages = lib.mapAttrsToList (target: scope: {
            inherit target;
            package = scope.callPackage ./nix/build-target-std.nix {
              inherit
                bootstrapRust
                vendoredRustSrc
                rustRev
                ;
              nixpkgsPath = pkgs.path;
              baseRustc = scarlet-rustc;
              llvmPackages = scarletLlvmPackages;
            };
          }) targetStdScopes;
          scarlet-rust-toolchain = pkgs.callPackage ./nix/combine-toolchain.nix {
            inherit
              rustRev
              targetTriples
              noStdTargetTriples
              targetStdPackages
              ;
            baseToolchain = scarlet-rustc;
            hostTriple = hostTriples.${system};
          };
        in
        {
          scarlet-rust-source = rustSrc;
          scarlet-llvm-source = llvmSrc;
          scarlet-llvm = scarletLlvmPackages.llvm;
          scarlet-lld = scarletLlvmPackages.lld;
          scarlet-rust-vendored-src = vendoredRustSrc;
          scarlet-rust-bootstrap-cargo-deps = vendoredRustSrc;
          inherit scarlet-rustc;
          inherit scarlet-rust-toolchain;
          default = scarlet-rust-toolchain;
        }
      );

      checks = forAllSystems (
        system:
        let
          toolchain = self.packages.${system}.scarlet-rust-toolchain;
          pkgs = import nixpkgs { inherit system; };
          allCheckedTargets = [ hostTriples.${system} ] ++ targetTriples;
          stdCheckedTargets = [
            hostTriples.${system}
          ]
          ++ (lib.subtractLists noStdTargetTriples targetTriples);
        in
        {
          manifest = pkgs.runCommand "scarlet-rust-toolchain-manifest-check" { } ''
            test -x ${toolchain}/bin/rustc
            test -x ${toolchain}/bin/cargo
            test -f ${toolchain}/manifest.toml
            test -f ${toolchain}/lib/rustlib/src/rust/library/Cargo.lock
            for target in ${lib.escapeShellArgs allCheckedTargets}; do
              test -d ${toolchain}/lib/rustlib/$target/lib
              find ${toolchain}/lib/rustlib/$target/lib -maxdepth 1 -name 'libcore-*.rlib' -type f | grep -q .
            done
            for target in ${lib.escapeShellArgs stdCheckedTargets}; do
              find ${toolchain}/lib/rustlib/$target/lib -maxdepth 1 -name 'libstd-*.rlib' -type f | grep -q .
            done
            ${toolchain}/bin/rustc -vV
            touch $out
          '';
        }
      );
    };
}
