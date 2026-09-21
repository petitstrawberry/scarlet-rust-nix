{ flake }:
let
  pkgs = import flake.inputs.nixpkgs { system = builtins.currentSystem; };
  toolchain = flake.packages.${builtins.currentSystem}.scarlet-rust-toolchain;
  llvm = pkgs.llvmPackages_21;
in
pkgs.mkShell {
  packages = [ toolchain llvm.clang llvm.llvm llvm.lld pkgs.python3 pkgs.git pkgs.pkg-config pkgs.zlib ];
  SCARLET_TOOLCHAIN = toolchain;
  SCARLET_CLANG = "${llvm.clang-unwrapped}/bin/clang";
  SCARLET_LLVM_AR = "${llvm.llvm}/bin/llvm-ar";
  SCARLET_NATIVE_LINKER = "${llvm.lld}/bin/ld.lld";
}
