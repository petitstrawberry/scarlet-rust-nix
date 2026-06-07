{
  lib,
  stdenvNoCC,
  bootstrapRust,
  cacert,
  git,
  rust-src,
  rustRev,
}:

let
  outputHashes = {
    x86_64-linux = "sha256-fGGo38wFyRs1zaRYeSN+/fhubvkY+/dWUiimi3/0Foc=";
    aarch64-linux = "sha256-fGGo38wFyRs1zaRYeSN+/fhubvkY+/dWUiimi3/0Foc=";
    aarch64-darwin = "sha256-be59t0qkaG455gBhCi5DWFiTP8I64Rj67xDjsTO/YU4=";
  };
in
stdenvNoCC.mkDerivation {
  pname = "scarlet-rust-src-vendored";
  version = builtins.substring 0 12 rustRev;

  src = rust-src;

  nativeBuildInputs = [
    bootstrapRust
    git
  ];

  outputHashAlgo = "sha256";
  outputHashMode = "recursive";
  outputHash =
    outputHashes.${stdenvNoCC.hostPlatform.system}
      or (throw "unsupported Scarlet Rust vendored source host: ${stdenvNoCC.hostPlatform.system}");

  CARGO_NET_GIT_FETCH_WITH_CLI = "true";
  CARGO_HTTP_CAINFO = "${cacert}/etc/ssl/certs/ca-bundle.crt";
  GIT_SSL_CAINFO = "${cacert}/etc/ssl/certs/ca-bundle.crt";
  NIX_SSL_CERT_FILE = "${cacert}/etc/ssl/certs/ca-bundle.crt";
  SSL_CERT_FILE = "${cacert}/etc/ssl/certs/ca-bundle.crt";
  dontConfigure = true;
  dontFixup = true;
  dontUpdateAutotoolsGnuConfigScripts = true;

  unpackPhase = ''
    runHook preUnpack

    cp -R "$src" source
    chmod -R u+w source
    cd source

    runHook postUnpack
  '';

  buildPhase = ''
    runHook preBuild

    mkdir -p .cargo
    export CARGO_HOME="$TMPDIR/cargo-home"
    mkdir -p "$CARGO_HOME"

    RUSTC_BOOTSTRAP=1 \
    RUSTC="${bootstrapRust}/bin/rustc" \
    cargo vendor \
      --sync src/tools/cargo/Cargo.toml \
      --sync src/tools/clippy/clippy_test_deps/Cargo.toml \
      --sync src/tools/rust-analyzer/Cargo.toml \
      --sync compiler/rustc_codegen_cranelift/Cargo.toml \
      --sync compiler/rustc_codegen_gcc/Cargo.toml \
      --sync library/Cargo.toml \
      --sync src/bootstrap/Cargo.toml \
      --sync src/tools/rustbook/Cargo.toml \
      --sync src/tools/rustc-perf/Cargo.toml \
      --sync src/tools/opt-dist/Cargo.toml \
      --sync src/doc/book/packages/trpl/Cargo.toml \
      vendor > .cargo/config.toml

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    cp -R . "$out/"

    runHook postInstall
  '';
}
