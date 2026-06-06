{
  lib,
  stdenv,
  bash,
  bootstrapRust,
  cacert,
  cmake,
  curl,
  git,
  gnumake,
  libffi,
  ninja,
  openssl,
  pkg-config,
  python3,
  rustPlatform,
  xz,
  zlib,
  rust-src,
  rustRev,
  hostTriple,
  targetTriples,
}:

let
  allTargets = [ hostTriple ] ++ targetTriples;
  targetList = lib.concatStringsSep "," allTargets;
  targetManifest = lib.concatMapStringsSep "\n" (target: ''  "${target}",'') targetTriples;
  bootstrapCargoDeps = rustPlatform.fetchCargoVendor {
    name = "scarlet-rust-bootstrap-cargo-deps-${builtins.substring 0 12 rustRev}";
    src = rust-src;
    sourceRoot = "source/src/bootstrap";
    hash = "sha256-xxaYOc4Xn3F3ghDEpFR2041gJ98t4lONJ9Ka1OkzGzI=";
  };
in
stdenv.mkDerivation {
  pname = "scarlet-rust-toolchain";
  version = builtins.substring 0 12 rustRev;

  src = rust-src;

  nativeBuildInputs = [
    bash
    bootstrapRust
    cmake
    curl
    git
    gnumake
    ninja
    pkg-config
    python3
    rustPlatform.cargoSetupHook
    xz
  ];

  buildInputs = [
    libffi
    openssl
    zlib
    xz
  ];

  CARGO_NET_GIT_FETCH_WITH_CLI = "true";
  SSL_CERT_FILE = "${cacert}/etc/ssl/certs/ca-bundle.crt";

  cargoDeps = bootstrapCargoDeps;
  cargoRoot = "src/bootstrap";

  unpackPhase = ''
    runHook preUnpack

    cp -R "$src" source
    chmod -R u+w source
    sourceRoot=source
    cd "$sourceRoot"
    sourceRoot=.

    runHook postUnpack
  '';

  configurePhase = ''
    runHook preConfigure

    test -x ./x
    test -f library/Cargo.lock
    test -f src/bootstrap/Cargo.lock

    cat > bootstrap.toml <<'EOF'
    change-id = "ignore"

    [build]
    cargo = "${bootstrapRust}/bin/cargo"
    patch-binaries-for-nix = true
    rustc = "${bootstrapRust}/bin/rustc"
    rustfmt = "${bootstrapRust}/bin/rustfmt"
    extended = false
    tools = []

    [llvm]
    download-ci-llvm = false

    [rust]
    download-rustc = false
    EOF

    runHook postConfigure
  '';

  buildPhase = ''
    runHook preBuild

    ./x build compiler/rustc
    ./x build library/std library/proc_macro library/test --target "${targetList}"

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    stage_dir="build/${hostTriple}/stage1"
    test -x "$stage_dir/bin/rustc"
    test -x "$stage_dir/bin/cargo"

    mkdir -p "$out"
    cp -R "$stage_dir/." "$out/"

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

    runHook postInstall
  '';

  doInstallCheck = true;

  passthru = {
    inherit bootstrapCargoDeps;
  };

  installCheckPhase = ''
    runHook preInstallCheck

    test -x "$out/bin/rustc"
    test -x "$out/bin/cargo"
    test -f "$out/manifest.toml"
    test -f "$out/lib/rustlib/src/rust/library/Cargo.lock"

    for target in ${lib.escapeShellArgs allTargets}; do
      test -d "$out/lib/rustlib/$target/lib"
      find "$out/lib/rustlib/$target/lib" -maxdepth 1 -name 'libstd-*.rlib' -type f | grep -q .
    done

    "$out/bin/rustc" -vV

    runHook postInstallCheck
  '';

  meta = {
    description = "Scarlet Rust fork toolchain";
    homepage = "https://github.com/petitstrawberry/rust";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
    ];
  };
}
