{
  lib,
  stdenvNoCC,
  bootstrapRust,
  cacert,
  git,
  python3,
  rust-src,
  rustRev,
}:

let
  outputHashes = {
    x86_64-linux = "sha256-1soEWgKbJL3B9L9MXk7SufINiRQBs/W+RGPi4Rp46eg=";
    aarch64-linux = "sha256-1soEWgKbJL3B9L9MXk7SufINiRQBs/W+RGPi4Rp46eg=";
    aarch64-darwin = "sha256-m3J/r1vllZUOl7z48gh59Sx3oCKLkwG/1TwvLnd0H20=";
  };
in
stdenvNoCC.mkDerivation {
  pname = "scarlet-rust-src-vendored";
  version = builtins.substring 0 12 rustRev;

  src = rust-src;

  nativeBuildInputs = [
    bootstrapRust
    git
    python3
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

    if RUSTC_BOOTSTRAP=1 \
    RUSTC="${bootstrapRust}/bin/rustc" \
    cargo vendor \
      --sync src/tools/cargo/Cargo.toml \
      --sync src/tools/clippy/Cargo.toml \
      --sync src/tools/clippy/clippy_test_deps/Cargo.toml \
      --sync src/tools/rust-analyzer/Cargo.toml \
      --sync src/tools/rustfmt/Cargo.toml \
      --sync compiler/rustc_codegen_cranelift/Cargo.toml \
      --sync compiler/rustc_codegen_gcc/Cargo.toml \
      --sync library/Cargo.toml \
      --sync src/bootstrap/Cargo.toml \
      --sync src/tools/rustbook/Cargo.toml \
      --sync src/tools/rustc-perf/Cargo.toml \
      --sync src/tools/opt-dist/Cargo.toml \
      --sync src/doc/book/packages/trpl/Cargo.toml \
      vendor > .cargo/config.toml 2> "$TMPDIR/vendor.log"; then
      cat "$TMPDIR/vendor.log" >&2
    else
      vendor_status=$?
      cat "$TMPDIR/vendor.log" >&2
      # Keep successful old-pin output byte-for-byte unchanged. Retry only
      # Cargo's specific duplicate-name/version-across-sources failure.
      if ! grep -Eq 'found duplicate version of package `[^`]+` vendored from two sources:' "$TMPDIR/vendor.log"; then
        exit "$vendor_status"
      fi
      # These are generated outputs from the failed invocation, not sources.
      rm -rf vendor
      rm -f .cargo/config.toml
      export RUSTC_BOOTSTRAP=1
      export RUSTC="${bootstrapRust}/bin/rustc"
      cargo vendor \
        --sync src/tools/cargo/Cargo.toml \
        --sync src/tools/clippy/Cargo.toml \
        --sync src/tools/clippy/clippy_test_deps/Cargo.toml \
        --sync src/tools/rustfmt/Cargo.toml \
        --sync compiler/rustc_codegen_cranelift/Cargo.toml \
        --sync compiler/rustc_codegen_gcc/Cargo.toml \
        --sync library/Cargo.toml \
        --sync src/bootstrap/Cargo.toml \
        --sync src/tools/rustbook/Cargo.toml \
        --sync src/tools/rustc-perf/Cargo.toml \
        --sync src/tools/opt-dist/Cargo.toml \
        --sync src/doc/book/packages/trpl/Cargo.toml \
        vendor > "$TMPDIR/vendor-main.toml"

      # rust-analyzer uses a Git home with the same name/version as crates.io.
      # Cargo cannot vendor both in one invocation. Preserve their source IDs.
      cargo vendor --manifest-path src/tools/rust-analyzer/Cargo.toml \
        vendor-rust-analyzer > "$TMPDIR/vendor-rust-analyzer.toml"
      python3 ${../scripts/merge-vendor-sources.py} \
        "$TMPDIR/vendor-main.toml" "$TMPDIR/vendor-rust-analyzer.toml" \
        src/tools/rust-analyzer/Cargo.lock > .cargo/config.toml

      # Resolve both sides of the home collision using only the final vendor
      # configuration. This checks source identities without compiling anything.
      for manifest in Cargo.toml src/tools/cargo/Cargo.toml src/tools/rust-analyzer/Cargo.toml; do
        CARGO_TARGET_DIR="$TMPDIR/vendor-metadata-target" \
        cargo metadata --offline --locked --format-version 1 \
          --manifest-path "$manifest" > /dev/null
      done
    fi

    runHook postBuild
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    cp -R . "$out/"

    runHook postInstall
  '';
}

