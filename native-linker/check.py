#!/usr/bin/env python3
"""Build object/archive fixtures and check a host-executed Scarlet linker port."""
import argparse
import os
from pathlib import Path
import struct
import subprocess

TARGETS = {"aarch64-unknown-scarlet": ("aarch64-none-elf", "aarch64elf", 183),
           "riscv64gc-unknown-scarlet": ("riscv64-unknown-elf", "elf64lriscv", 243)}


def audit(path, target):
    data = path.read_bytes()
    if len(data) < 64 or data[:8] != b"\x7fELF\x02\x01\x01\x53":
        raise ValueError(f"not a native Scarlet ELF64 executable: {path}")
    kind, machine, version, entry, phoff = struct.unpack_from("<HHIQQ", data, 16)
    phsize, phnum = struct.unpack_from("<HH", data, 54)
    if kind != 2 or machine != TARGETS[target][2] or version != 1 or phsize != 56 or not 0 < phnum < 65535:
        raise ValueError(f"wrong ELF type/architecture/headers: {path}")
    if phoff + phnum * phsize > len(data):
        raise ValueError("truncated program headers")
    entry_mapped = False
    for index in range(phnum):
        kind, flags, offset, address, _, size, memory, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phsize)
        if kind in (2, 3):
            raise ValueError("expected a static executable without PT_DYNAMIC/PT_INTERP")
        if offset + size > len(data) or (kind == 1 and size > memory):
            raise ValueError("invalid ELF segment bounds")
        if kind == 1 and flags & 1 and address <= entry < address + size:
            entry_mapped = True
    if not entry_mapped:
        raise ValueError("entry does not lie in executable file-backed memory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--linker", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    triple, emulation, _ = TARGETS[args.target]
    source = Path(__file__).with_name("fixtures")
    flags = ["--target=" + triple, "-fPIC", "-ffreestanding", "-fno-stack-protector", "-fno-builtin",
             "-fno-asynchronous-unwind-tables", "-O1"]
    if args.target.startswith("riscv64"):
        flags += ["-march=rv64gc", "-mabi=lp64d"]
    for name in ("main", "answer", "bias"):
        subprocess.run([os.environ["SCARLET_CLANG"], *flags, "-c", str(source / (name + ".c")),
                        "-o", str(args.output / (name + ".o"))], check=True)
    archive = args.output / "libanswer.a"
    subprocess.run([os.environ["SCARLET_LLVM_AR"], "crs", str(archive),
                    str(args.output / "answer.o"), str(args.output / "bias.o")], check=True)
    command = [str(args.linker), "-m", emulation, "-static", "--threads=1", "--gc-sections",
               "-z", "max-page-size=4096", "-e", "_start", str(args.output / "main.o")]
    for case, inputs in (("direct", [args.output / "answer.o", args.output / "bias.o"]),
                         ("archive", [archive])):
        output = args.output / ("host-linked-" + case)
        subprocess.run([*command, *map(str, inputs), "-o", str(output)], check=True)
        audit(output, args.target)
    missing = subprocess.run([*command, "-o", str(args.output / "unresolved")], capture_output=True, text=True)
    if missing.returncode == 0 or "answer" not in missing.stderr:
        raise ValueError("linker did not diagnose the missing strong symbol")
    rust_output = args.output / "host-linked-rust"
    environment = dict(os.environ, SCARLET_RUST_LINK_CAPTURE=str((args.output / "rust").resolve()),
                       SCARLET_HOST_WILD=str(args.linker.resolve()))
    subprocess.run([str(Path(os.environ["SCARLET_TOOLCHAIN"]) / "bin/rustc"),
                    "--edition=2024", "--target", args.target, "--crate-name=linker_hello",
                    "-Cpanic=abort", "-Copt-level=1", "-Clinker-flavor=gnu-lld",
                    "-Clinker=" + str(Path(__file__).with_name("capture-rust-link.py").resolve()),
                    "-Clink-arg=-z", "-Clink-arg=max-page-size=4096",
                    str(source / "hello.rs"), "-o", str(rust_output)], env=environment, check=True)
    audit(rust_output, args.target)
    # Replay only portable inputs; the guest must not need any host temp paths.
    portable = (args.output / "rust/link.args").read_text().splitlines()
    replay = args.output.resolve() / "host-linked-rust-replay"
    subprocess.run([str(args.linker.resolve()), *portable, "--threads=1", "-o", str(replay)],
                   cwd=args.output / "rust", check=True)
    audit(replay, args.target)
    print("Host-executed linker: object/archive/Rust std links and undefined-symbol rejection passed; guest execution pending.")


if __name__ == "__main__":
    main()
