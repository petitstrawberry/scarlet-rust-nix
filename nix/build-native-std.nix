{ lib, stdenvNoCC, rustPlatform, baseRustc, runtimeSource, backtraceSource, runtimeRev, targetTriple }:

# Refresh the Native runtime independently of the unchanged compiler. The
# compiler and library source must remain compatible; this pin changes only
# Scarlet TLS ownership, not compiler internals or the Rust language ABI.
stdenvNoCC.mkDerivation {
  pname = "scarlet-rust-native-std-${targetTriple}";
  version = builtins.substring 0 12 runtimeRev;
  src = runtimeSource;
  nativeBuildInputs = [ baseRustc rustPlatform.cargoSetupHook ];
  cargoDeps = rustPlatform.importCargoLock {
    lockFile = "${runtimeSource}/library/Cargo.lock";
  };
  cargoRoot = "library";
  dontConfigure = true;
  dontFixup = true;
  buildPhase = ''
    runHook preBuild
    cp -R ${backtraceSource}/. library/backtrace/
    mkdir -p sysroot/lib/rustlib/src/rust project/src
    for item in ${baseRustc}/lib/rustlib/*; do
      if [ "$(basename "$item")" != src ]; then
        ln -s "$item" sysroot/lib/rustlib/
      fi
    done
    ln -s "$PWD/library" sysroot/lib/rustlib/src/rust/library
    cat > rustc-for-build <<EOF
    #!${stdenvNoCC.shell}
    exec ${baseRustc}/bin/rustc --sysroot "$PWD/sysroot" "\$@"
    EOF
    chmod +x rustc-for-build
    cat > project/Cargo.toml <<EOF
    [package]
    name = "scarlet-std-build"
    version = "0.0.0"
    edition = "2024"
    [workspace]
    [profile.release]
    panic = "abort"
    EOF
    printf '#![no_std]\npub fn marker() {}\n' > project/src/lib.rs
    export RUSTC="$PWD/rustc-for-build"
    export RUSTC_BOOTSTRAP=1
    export CARGO_TARGET_DIR="$PWD/target"
    cargo build --offline --release --manifest-path project/Cargo.toml \
      --target ${targetTriple} -Zbuild-std=std,panic_abort \
      -Zbuild-std-features=compiler-builtins-mem
    runHook postBuild
  '';
  installPhase = ''
    mkdir -p "$out/lib/rustlib/${targetTriple}/lib"
    for archive in target/${targetTriple}/release/deps/*.rlib; do
      case "$archive" in *libscarlet_std_build-*) continue ;; esac
      cp "$archive" "$out/lib/rustlib/${targetTriple}/lib/"
    done
    # The compiler's unchanged Native entry ABI still requires its startup
    # objects. build-std produces Rust libraries, not these CRT inputs.
    for object in ${baseRustc}/lib/rustlib/${targetTriple}/lib/*.o; do
      if [ -f "$object" ]; then
        cp "$object" "$out/lib/rustlib/${targetTriple}/lib/"
      fi
    done
    test -n "$(find "$out/lib/rustlib/${targetTriple}/lib" -name 'libstd-*.rlib' -print -quit)"
    mkdir -p "$out/lib/rustlib/src/rust"
    cp -R library "$out/lib/rustlib/src/rust/"
  '';
  doInstallCheck = targetTriple != "riscv32gc-unknown-scarlet";
  installCheckPhase = ''
    runHook preInstallCheck
    test -f "$out/lib/rustlib/${targetTriple}/lib/scarlet-crt0.o"
    rm "sysroot/lib/rustlib/${targetTriple}"
    ln -s "$out/lib/rustlib/${targetTriple}" "sysroot/lib/rustlib/${targetTriple}"
    cat > native-startup-check.rs <<EOF
    fn main() {
        let worker = std::thread::spawn(|| std::env::args().count());
        println!("{}", worker.join().unwrap());
    }
    EOF
    ${baseRustc}/bin/rustc --sysroot "$PWD/sysroot" --edition=2024 \
      --target ${targetTriple} -C panic=abort -C opt-level=3 \
      native-startup-check.rs -o native-startup-check
    runHook postInstallCheck
  '';
  passthru = { inherit targetTriple runtimeRev; };
  meta.platforms = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ];
}
