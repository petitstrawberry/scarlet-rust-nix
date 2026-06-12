#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: scripts/update-rust-rev.sh <rust-rev> [system]

Updates flake.nix rustRev/rustHash and the vendored Rust source hash for the
given system. The script fetches only the Rust source and vendored source
derivations to discover their fixed-output hashes. It does not build the Rust
toolchain.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

rust_rev="${1:-}"
system="${2:-x86_64-linux}"

if [ -z "${rust_rev}" ]; then
    usage
    exit 2
fi

if ! printf '%s' "${rust_rev}" | grep -Eq '^[0-9a-f]{40}$'; then
    echo "Rust revision must be a full 40-character Git SHA: ${rust_rev}" >&2
    exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "${repo_root}"

set_rust_rev() {
    perl -0pi -e 's/rustRev = "[0-9a-f]{40}";/rustRev = "'"${rust_rev}"'";/' flake.nix
}

set_rust_hash_expr() {
    local expr="$1"
    RUST_HASH_EXPR="${expr}" perl -0pi -e \
        's/rustHash = (?:lib\.fakeHash|"sha256-[^"]+");/"rustHash = $ENV{RUST_HASH_EXPR};"/e' \
        flake.nix
}

set_vendored_hash_expr() {
    local system="$1"
    local expr="$2"
    VENDORED_SYSTEM="${system}" VENDORED_HASH_EXPR="${expr}" perl -0pi -e \
        's/^([[:space:]]*\Q$ENV{VENDORED_SYSTEM}\E = )(?:lib\.fakeHash|"sha256-[^"]+");/$1 . $ENV{VENDORED_HASH_EXPR} . ";"/gem' \
        nix/vendor-rust-src.nix
}

get_vendored_hash_expr() {
    local system="$1"
    sed -n 's/^[[:space:]]*'"${system}"' = "\(sha256-[^"]*\)";/\1/p' nix/vendor-rust-src.nix
}

set_rust_rev
set_rust_hash_expr "lib.fakeHash"

log_file="$(mktemp)"
if nix build ".#packages.${system}.scarlet-rust-source" --no-link -L --accept-flake-config >"${log_file}" 2>&1; then
    cat "${log_file}"
    echo "Expected a fixed-output hash mismatch, but the source build succeeded with lib.fakeHash." >&2
    rm -f "${log_file}"
    exit 1
fi

cat "${log_file}"
rust_hash="$(sed -n 's/.*got:[[:space:]]*\(sha256-[^[:space:]]*\).*/\1/p' "${log_file}" | tail -n 1)"
rm -f "${log_file}"

if [ -z "${rust_hash}" ]; then
    echo "Could not extract the new Rust source hash from nix output." >&2
    exit 1
fi

set_rust_hash_expr "\"${rust_hash}\""

nix build ".#packages.${system}.scarlet-rust-source" --no-link -L --accept-flake-config

if [ -z "$(get_vendored_hash_expr "${system}")" ]; then
    echo "Unsupported system in nix/vendor-rust-src.nix: ${system}" >&2
    exit 1
fi

set_vendored_hash_expr "${system}" "lib.fakeHash"

log_file="$(mktemp)"
if nix build ".#packages.${system}.scarlet-rust-vendored-src" --no-link -L --accept-flake-config >"${log_file}" 2>&1; then
    cat "${log_file}"
    echo "Expected a fixed-output hash mismatch, but the vendored source build succeeded with lib.fakeHash." >&2
    rm -f "${log_file}"
    exit 1
fi

cat "${log_file}"
vendored_hash="$(sed -n 's/.*got:[[:space:]]*\(sha256-[^[:space:]]*\).*/\1/p' "${log_file}" | tail -n 1)"
rm -f "${log_file}"

if [ -z "${vendored_hash}" ]; then
    echo "Could not extract the new vendored Rust source hash from nix output." >&2
    exit 1
fi

set_vendored_hash_expr "${system}" "\"${vendored_hash}\""

nix build ".#packages.${system}.scarlet-rust-vendored-src" --no-link -L --accept-flake-config
nix flake metadata --accept-flake-config >/dev/null

echo "Updated Rust fork revision:"
echo "  rustRev      = ${rust_rev}"
echo "  rustHash     = ${rust_hash}"
echo "  system       = ${system}"
echo "  vendoredHash = ${vendored_hash}"
