#!/usr/bin/env python3
"""Compile the production Scarlet allocator placement helpers with host tests.

Usage: python3 scripts/check-native-host-allocator.py path/to/scarlet.rs [--rustc PATH]
No production files are modified. The current buggy source fails the odd-size
regression; the corrected source must pass all layout, overflow and fit tests.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


def item(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--rustc', default=os.environ.get('RUSTC', 'rustc'))
    args = parser.parse_args()
    source = args.source.read_text()
    print(f"Allocator input: {args.source.resolve()}", flush=True)
    print(f"Allocator SHA-256: {hashlib.sha256(args.source.read_bytes()).hexdigest()}", flush=True)
    constants = '\n'.join(re.findall(r'^const (?:HEADER_SIZE|BACK_PTR_SIZE|MIN_FREE_BLOCK_SIZE):[^\n]+', source, re.M))
    assert len(constants.splitlines()) == 3, 'allocator constants not found'
    production = '\n'.join([
        'use std::mem::{align_of, size_of};', constants,
        '#[repr(C)]\n' + item(source, 'struct Block {'),
        item(source, 'fn placement('), item(source, 'fn align_up('),
    ])
    regression = r'''
#[test]
fn odd_size_libc_request_keeps_the_next_block_aligned() {
    // libc malloc(1) adds its 16-byte prefix to the allocation request.
    let (data, used) = placement(0x1000, 65536, 17, 16).unwrap();
    assert_eq!(data, 0x1020);
    assert_eq!((0x1000 + used) % align_of::<Block>(), 0);
    assert_eq!(used, 56);
}

#[test]
fn all_small_allocations_preserve_split_and_payload_invariants() {
    let mut accepted = 0;
    for start in [0x1000usize, 0x1008, 0x1ff8] {
        for size in 1..=513 {
            for align in [8usize, 16, 32, 64, 128, 4096] {
                for block_size in [40usize, 47, 48, 49, 55, 56, 63, 64, 255, 4096, 8192] {
                    if let Some((data, used)) = placement(start, block_size, size, align) {
                        accepted += 1;
                        assert_eq!(data % align, 0, "payload alignment");
                        assert!(data >= start + HEADER_SIZE + BACK_PTR_SIZE);
                        assert!(data.checked_add(size).unwrap() <= start + used);
                        assert!(used <= block_size);
                        let remainder = block_size - used;
                        if remainder >= MIN_FREE_BLOCK_SIZE {
                            assert_eq!((start + used) % align_of::<Block>(), 0,
                                "free Block alignment: start={start} size={size} align={align}");
                        }
                    }
                }
            }
        }
    }
    assert!(accepted > 10000);
}

#[test]
fn rounding_does_not_reject_near_end_or_exact_fit_requests() {
    // The payload fits, but there is no space to round the next block address.
    assert_eq!(placement(0x1000, 49, 17, 16), Some((0x1020, 49)));
    assert_eq!(placement(0x1000, 53, 17, 16), Some((0x1020, 53)));
    assert_eq!(placement(0x1000, 56, 17, 16), Some((0x1020, 56)));
    assert_eq!(placement(0x1000, 48, 17, 16), None);
    // Enumerate payloads that end exactly at the block boundary.
    for size in 1..=513 {
        assert!(placement(0x1000, 32 + size, size, 16).is_some());
    }
}

#[test]
fn checked_arithmetic_handles_address_and_size_boundaries() {
    assert_eq!(placement(usize::MAX - 15, 16, 1, 8), None);
    assert_eq!(placement(usize::MAX - 15, 8, 1, 8), None);
    assert_eq!(placement(0x1000, 8192, usize::MAX, 8), None);
    assert_eq!(placement(0x1000, 8192, 8192, 8), None);
    let start = usize::MAX - 63;
    // A valid last-address payload cannot round up without overflow, so the
    // caller must consume the complete block instead of failing or wrapping.
    let (data, used) = placement(start, 63, 31, 16).unwrap();
    assert_eq!(data, start + 32);
    assert_eq!(used, 63);
}
'''
    with tempfile.TemporaryDirectory(prefix='scarlet-allocator-regression-') as temp:
        test = Path(temp) / 'placement.rs'
        binary = Path(temp) / 'placement-test'
        test.write_text(production + '\n' + regression)
        subprocess.run([args.rustc, '--edition=2024', '--test', str(test), '-o', str(binary)], check=True)
        result = subprocess.run([str(binary), '--nocapture'])
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
