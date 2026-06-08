{
  lib,
  runCommand,
  baseToolchain,
  targetStdPackages,
  rustRev,
  hostTriple,
  targetTriples,
  noStdTargetTriples,
}:

let
  version = builtins.substring 0 12 rustRev;
  targetManifest = lib.concatMapStringsSep "\n" (target: ''"${target}",'') targetTriples;
  allTargets = [ hostTriple ] ++ targetTriples;
  stdTargets = [ hostTriple ] ++ (lib.subtractLists noStdTargetTriples targetTriples);
  expectedBinTools = [
    "rustc"
    "cargo"
    "rustdoc"
    "rustfmt"
    "cargo-fmt"
    "clippy-driver"
    "cargo-clippy"
    "rust-analyzer"
  ];
  copyTargetStd = lib.concatMapStringsSep "\n" (
    { target, package }:
    ''
      test -d "${package}/lib/rustlib/${target}/lib"
      rm -rf "$out/lib/rustlib/${target}"
      cp -R "${package}/lib/rustlib/${target}" "$out/lib/rustlib/${target}"
      chmod -R u+w "$out/lib/rustlib/${target}"

      if [ -f "${package}/lib/rustlib/manifest-rust-std-${target}" ]; then
        cp "${package}/lib/rustlib/manifest-rust-std-${target}" "$out/lib/rustlib/manifest-rust-std-${target}"
      fi
      echo "rust-std-${target}" >> "$out/lib/rustlib/components"
    ''
  ) targetStdPackages;
in
runCommand "scarlet-rust-toolchain-${version}"
  {
    outputs = [
      "out"
      "man"
      "doc"
    ];
    passthru = (baseToolchain.passthru or { }) // {
      inherit baseToolchain targetStdPackages;
    };
    meta = baseToolchain.meta or { };
  }
  ''
    cp -R "${baseToolchain}" "$out"
    chmod -R u+w "$out"
    mkdir -p "$man" "$doc"
    cp -R "${baseToolchain.man}/." "$man/"
    cp -R "${baseToolchain.doc}/." "$doc/"
    chmod -R u+w "$man" "$doc"

    ${copyTargetStd}

    sort -u "$out/lib/rustlib/components" -o "$out/lib/rustlib/components"

    cat > "$out/manifest.toml" <<EOF
    schema = 1
    rust_commit = "${rustRev}"
    host = "${hostTriple}"
    targets = [
    ${targetManifest}
    ]
    EOF

    test -x "$out/bin/rustc"
    test -x "$out/bin/cargo"
    for tool in ${lib.escapeShellArgs expectedBinTools}; do
      test -x "$out/bin/$tool"
    done
    test -x "$out/bin/rust-analyzer-proc-macro-srv" \
      || test -x "$out/libexec/rust-analyzer-proc-macro-srv"
    test -f "$out/manifest.toml"
    test -f "$out/lib/rustlib/src/rust/library/Cargo.lock"
    test -x "$out/lib/rustlib/${hostTriple}/bin/rust-lld"

    for target in ${lib.escapeShellArgs allTargets}; do
      test -d "$out/lib/rustlib/$target/lib"
      find "$out/lib/rustlib/$target/lib" -maxdepth 1 -name 'libcore-*.rlib' -type f | grep -q .
    done

    for target in ${lib.escapeShellArgs stdTargets}; do
      find "$out/lib/rustlib/$target/lib" -maxdepth 1 -name 'libstd-*.rlib' -type f | grep -q .
    done

    "$out/bin/rustc" -vV
  ''
