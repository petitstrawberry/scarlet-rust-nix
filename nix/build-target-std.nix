{
  lib,
  stdenv,
  callPackage,
  llvmPackages,
  ninja,
  bootstrapRust,
  baseRustc,
  nixpkgsPath,
  vendoredRustSrc,
  rustRev,
}:

let
  version = builtins.substring 0 12 rustRev;
  targetTriple = stdenv.targetPlatform.rust.rustcTargetSpec;
  rustcForBuild = baseRustc // {
    unwrapped = baseRustc;
  };

  baseRustcStd = callPackage "${nixpkgsPath}/pkgs/development/compilers/rust/rustc.nix" {
    inherit version;
    sha256 = lib.fakeHash;
    cargo = rustcForBuild;
    rustc = rustcForBuild;
    rustfmt = bootstrapRust;
    withBundledLLVM = false;
    enableRustcDev = false;
    fastCross = true;
    llvmShared = llvmPackages.llvm;
    llvmSharedForBuild = llvmPackages.llvm;
    llvmSharedForHost = llvmPackages.llvm;
    llvmSharedForTarget = llvmPackages.llvm;
    inherit llvmPackages;
    patches = [ ];
  };

  keepNixpkgsConfigureFlag =
    flag: !(lib.hasPrefix "--release-channel=" flag || lib.hasPrefix "--set=build.rustfmt=" flag);
in
baseRustcStd.overrideAttrs (old: {
  pname = "scarlet-rust-std-${targetTriple}";
  inherit version;

  src = vendoredRustSrc;

  nativeBuildInputs = old.nativeBuildInputs ++ [
    ninja
  ];

  dontUseNinjaBuild = true;
  dontUseNinjaInstall = true;
  dontUseNinjaCheck = true;

  hardeningDisable = (old.hardeningDisable or [ ]) ++ [
    "zerocallusedregs"
  ];

  buildPhase = ''
    runHook preBuild

    host="${stdenv.hostPlatform.rust.rustcTargetSpec}"
    rustlib="${rustcForBuild.unwrapped}/lib/rustlib/$host"
    stage="build/$host"

    mkdir -p "$stage/stage0-std/$host/release" "$stage/stage0-rustc/$host/release" "$stage/stage1-rustc/$host/release"

    libstd_src=
    for candidate in "$rustlib"/lib/libstd-*.so "$rustlib"/lib/libstd-*.dylib; do
      if [ -e "$candidate" ]; then
        libstd_src="$candidate"
        break
      fi
    done
    test -n "$libstd_src"

    librustc_src=
    for candidate in \
      "$rustlib"/lib/librustc_driver-*.so \
      "$rustlib"/lib/librustc_driver-*.dylib \
      ${rustcForBuild.unwrapped}/lib/librustc_driver-*.so \
      ${rustcForBuild.unwrapped}/lib/librustc_driver-*.dylib
    do
      if [ -e "$candidate" ]; then
        librustc_src="$candidate"
        break
      fi
    done
    test -n "$librustc_src"

    ln -s "$libstd_src" "$stage/stage0-std/$host/release/libstd.so"
    ln -s "$librustc_src" "$stage/stage0-rustc/$host/release/librustc.so"
    ln -s ${rustcForBuild.unwrapped}/bin/rustc "$stage/stage0-rustc/$host/release/rustc-main"
    ln -s ${rustcForBuild.unwrapped}/bin/rustc "$stage/stage1-rustc/$host/release/rustc-main"
    touch "$stage/stage0-std/$host/release/.libstd-stamp"
    touch "$stage/stage0-rustc/$host/release/.librustc-stamp" "$stage/stage1-rustc/$host/release/.librustc-stamp"

    python ./x.py --keep-stage=0 --stage=1 build library/std

    runHook postBuild
  '';

  configureFlags = lib.filter keepNixpkgsConfigureFlag old.configureFlags ++ [
    "--release-channel=nightly"
    "--set=build.locked-deps=true"
    "--set=build.patch-binaries-for-nix=true"
    "--set=build.rustfmt=${bootstrapRust}/bin/rustfmt"
    "--set=llvm.download-ci-llvm=false"
    "--set=rust.deny-warnings=false"
    "--set=rust.download-rustc=false"
  ];

  postPatch = ''
    patchShebangs src/etc

    if [ ! -f .cargo/config.toml ]; then
      mkdir -p .cargo
      cat > .cargo/config.toml <<\EOF
    [source.crates-io]
    replace-with = "vendored-sources"
    [source.vendored-sources]
    directory = "vendor"
    EOF
    fi
  ''
  + lib.optionalString stdenv.hostPlatform.isDarwin ''
    substituteInPlace compiler/rustc_codegen_ssa/src/back/link.rs \
      --replace-fail "/usr/bin/strip" "${lib.getExe' llvmPackages.llvm "llvm-strip"}"
  '';

  requiredSystemFeatures = [ ];

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    test -d "$out/lib/rustlib/${targetTriple}/lib"
    find "$out/lib/rustlib/${targetTriple}/lib" -maxdepth 1 -name 'libcore-*.rlib' -type f | grep -q .

    "$out/bin/rustc" -vV

    runHook postInstallCheck
  '';

  passthru = (old.passthru or { }) // {
    inherit targetTriple vendoredRustSrc;
  };

  meta = {
    description = "Scarlet Rust target standard libraries for ${targetTriple}";
    homepage = "https://github.com/petitstrawberry/rust";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
    ];
  };
})
