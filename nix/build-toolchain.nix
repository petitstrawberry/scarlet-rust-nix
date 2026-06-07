{
  lib,
  stdenv,
  callPackage,
  llvmPackages,
  ninja,
  bootstrapRust,
  nixpkgsPath,
  vendoredRustSrc,
  rustRev,
  hostTriple,
  targetTriples,
}:

let
  version = builtins.substring 0 12 rustRev;
  allTargets = [ hostTriple ] ++ targetTriples;
  targetList = lib.concatStringsSep "," allTargets;
  targetManifest = lib.concatMapStringsSep "\n" (target: ''  "${target}",'') targetTriples;

  baseRustc = callPackage "${nixpkgsPath}/pkgs/development/compilers/rust/rustc.nix" {
    inherit version;
    sha256 = lib.fakeHash;
    cargo = bootstrapRust;
    rustc = bootstrapRust;
    rustfmt = bootstrapRust;
    withBundledLLVM = true;
    enableRustcDev = false;
    fastCross = false;
    llvmShared = llvmPackages.llvm;
    llvmSharedForBuild = llvmPackages.llvm;
    llvmSharedForHost = llvmPackages.llvm;
    llvmSharedForTarget = llvmPackages.llvm;
    inherit llvmPackages;
    patches = [ ];
  };

  keepNixpkgsConfigureFlag =
    flag:
    !(lib.hasPrefix "--release-channel=" flag
      || lib.hasPrefix "--target=" flag
      || lib.hasPrefix "--tools=" flag
      || lib.hasPrefix "--set=build.rustfmt=" flag
      || lib.hasInfix "target.wasm32-" flag
      || lib.hasInfix "target.wasm32v1-" flag
      || lib.hasInfix "target.bpfel-" flag
      || lib.hasInfix "target.bpfeb-" flag
      || flag == "--disable-lld"
      || flag == "--enable-profiler");
in
baseRustc.overrideAttrs (old: {
  pname = "scarlet-rust-toolchain";
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

  configureFlags = lib.filter keepNixpkgsConfigureFlag old.configureFlags ++ [
    "--release-channel=nightly"
    "--set=build.locked-deps=true"
    "--set=build.patch-binaries-for-nix=true"
    "--set=build.rustfmt=${bootstrapRust}/bin/rustfmt"
    "--set=llvm.download-ci-llvm=false"
    "--set=rust.download-rustc=false"
    "--target=${targetList}"
    "--tools=rustc,cargo,rustdoc,rust-analyzer-proc-macro-srv"
  ];

  postPatch =
    ''
      patchShebangs src/etc

      mkdir -p .cargo
      cat > .cargo/config.toml <<\EOF
      [source.crates-io]
      replace-with = "vendored-sources"
      [source.vendored-sources]
      directory = "vendor"
      EOF
    ''
    + lib.optionalString stdenv.hostPlatform.isDarwin ''
      substituteInPlace compiler/rustc_codegen_ssa/src/back/link.rs \
        --replace-fail "/usr/bin/strip" "${lib.getExe' llvmPackages.llvm "llvm-strip"}"
    '';

  postInstall =
    old.postInstall
    + ''
      rm -rf "$out/lib/rustlib/src/rust"
      mkdir -p "$out/lib/rustlib/src/rust"
      cp -R library "$out/lib/rustlib/src/rust/library"

      cat > "$out/manifest.toml" <<EOF
      schema = 1
      rust_commit = "${rustRev}"
      host = "${hostTriple}"
      targets = [
      ${targetManifest}
      ]
      EOF
    '';

  requiredSystemFeatures = [ ];

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    test -x "$out/bin/rustc"
    test -x "$out/bin/cargo"
    test -f "$out/manifest.toml"
    test -f "$out/lib/rustlib/src/rust/library/Cargo.lock"
    test -x "$out/lib/rustlib/${hostTriple}/bin/rust-lld"

    for target in ${lib.escapeShellArgs allTargets}; do
      test -d "$out/lib/rustlib/$target/lib"
      find "$out/lib/rustlib/$target/lib" -maxdepth 1 -name 'libstd-*.rlib' -type f | grep -q .
    done

    "$out/bin/rustc" -vV

    runHook postInstallCheck
  '';

  passthru = (old.passthru or { }) // {
    inherit vendoredRustSrc;
  };

  meta = {
    description = "Scarlet Rust fork toolchain";
    homepage = "https://github.com/petitstrawberry/rust";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
    ];
  };
})
