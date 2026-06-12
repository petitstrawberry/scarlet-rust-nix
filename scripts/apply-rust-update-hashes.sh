#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: scripts/apply-rust-update-hashes.sh <rust-rev> <hash-dir>

Applies per-system hash files produced by scripts/update-rust-rev.sh.
Each file must contain:

  system=<nix-system>
  rust_hash=<sha256-...>
  vendored_hash=<sha256-...>
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

rust_rev="${1:-}"
hash_dir="${2:-}"

if [ -z "${rust_rev}" ] || [ -z "${hash_dir}" ]; then
    usage
    exit 2
fi

if ! printf '%s' "${rust_rev}" | grep -Eq '^[0-9a-f]{40}$'; then
    echo "Rust revision must be a full 40-character Git SHA: ${rust_rev}" >&2
    exit 2
fi

if [ ! -d "${hash_dir}" ]; then
    echo "Hash directory does not exist: ${hash_dir}" >&2
    exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "${repo_root}"

get_field() {
    local file="$1"
    local key="$2"
    sed -n 's/^'"${key}"'=\(.*\)$/\1/p' "${file}" | tail -n 1
}

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

rust_hash=""
found=0

for file in "${hash_dir}"/*.env; do
    if [ ! -e "${file}" ]; then
        continue
    fi

    system="$(get_field "${file}" system)"
    file_rust_hash="$(get_field "${file}" rust_hash)"
    vendored_hash="$(get_field "${file}" vendored_hash)"

    if ! printf '%s' "${system}" | grep -Eq '^[A-Za-z0-9_-]+$'; then
        echo "Invalid system in ${file}: ${system}" >&2
        exit 1
    fi
    if ! printf '%s' "${file_rust_hash}" | grep -Eq '^sha256-[A-Za-z0-9+/=]+$'; then
        echo "Invalid rust_hash in ${file}: ${file_rust_hash}" >&2
        exit 1
    fi
    if ! printf '%s' "${vendored_hash}" | grep -Eq '^sha256-[A-Za-z0-9+/=]+$'; then
        echo "Invalid vendored_hash in ${file}: ${vendored_hash}" >&2
        exit 1
    fi

    if [ -z "${rust_hash}" ]; then
        rust_hash="${file_rust_hash}"
    elif [ "${rust_hash}" != "${file_rust_hash}" ]; then
        echo "Mismatched Rust source hashes: ${rust_hash} vs ${file_rust_hash}" >&2
        exit 1
    fi

    set_vendored_hash_expr "${system}" "\"${vendored_hash}\""
    found=$((found + 1))
done

if [ "${found}" -eq 0 ]; then
    echo "No hash files found in ${hash_dir}" >&2
    exit 1
fi

set_rust_rev
set_rust_hash_expr "\"${rust_hash}\""

echo "Applied Rust fork revision:"
echo "  rustRev  = ${rust_rev}"
echo "  rustHash = ${rust_hash}"
echo "  systems  = ${found}"
