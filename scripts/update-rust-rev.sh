#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: scripts/update-rust-rev.sh <rust-rev> [system]

Updates flake.nix rustRev and rustHash for the Scarlet Rust fork.
The script fetches only the Rust source derivation to discover the fixed-output
hash. It does not build the Rust toolchain.
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
    perl -0pi -e 's/rustHash = (?:lib\.fakeHash|"sha256-[^"]+");/rustHash = '"${expr}"';/' flake.nix
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
nix flake metadata --accept-flake-config >/dev/null

echo "Updated Rust fork revision:"
echo "  rustRev  = ${rust_rev}"
echo "  rustHash = ${rust_hash}"
