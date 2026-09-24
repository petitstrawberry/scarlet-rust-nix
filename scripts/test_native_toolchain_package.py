"""Checks for the release packager; no network, compiler or Nix build is used."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("package-native-toolchain.py")
SPEC = importlib.util.spec_from_file_location("package_native_toolchain", SCRIPT)
assert SPEC and SPEC.loader
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)

TARGET = "aarch64-unknown-scarlet"
RUST_COMMIT = "1" * 40
SCARLET_COMMIT = "2" * 40
PACKAGE_COMMIT = "3" * 40


def write_elf(
    path: Path,
    machine: int = 183,
    interpreter=None,
    osabi: int = 0x53,
    needed=(),
):
    path.parent.mkdir(parents=True, exist_ok=True)
    interpreter_bytes = (
        interpreter.encode("utf-8") + b"\0" if interpreter is not None else b""
    )
    strings = bytearray(b"\0")
    needed_offsets = []
    for name in needed:
        needed_offsets.append(len(strings))
        strings.extend(name.encode("utf-8") + b"\0")
    phnum = 1 + bool(interpreter_bytes) + bool(needed_offsets)
    phoff = 64
    interpreter_offset = phoff + phnum * 56
    dynamic_offset = interpreter_offset + len(interpreter_bytes)
    dynamic_size = (len(needed_offsets) + 3) * 16 if needed_offsets else 0
    string_offset = dynamic_offset + dynamic_size
    total_size = string_offset + (len(strings) if needed_offsets else 0)
    virtual_base = 0x1000
    header = bytearray(64)
    header[:16] = bytes(
        [0x7F, ord("E"), ord("L"), ord("F"), 2, 1, 1, osabi]
    ) + bytes(8)
    struct.pack_into("<HHIQQQIHHHHHH", header, 16, 3, machine, 1, 0, phoff, 0, 0, 64, 56, phnum, 0, 0, 0)
    load = struct.pack(
        "<IIQQQQQQ", 1, 5, 0, virtual_base, virtual_base, total_size, total_size, 4096
    )
    program_headers = bytearray(load)
    if interpreter_bytes:
        program_headers.extend(
            struct.pack(
                "<IIQQQQQQ",
                3,
                4,
                interpreter_offset,
                0,
                0,
                len(interpreter_bytes),
                len(interpreter_bytes),
                1,
            )
        )
    dynamic = bytearray()
    if needed_offsets:
        program_headers.extend(
            struct.pack(
                "<IIQQQQQQ",
                2,
                4,
                dynamic_offset,
                virtual_base + dynamic_offset,
                virtual_base + dynamic_offset,
                dynamic_size,
                dynamic_size,
                8,
            )
        )
        for offset in needed_offsets:
            dynamic.extend(struct.pack("<qQ", 1, offset))
        dynamic.extend(struct.pack("<qQ", 5, virtual_base + string_offset))
        dynamic.extend(struct.pack("<qQ", 10, len(strings)))
        dynamic.extend(struct.pack("<qQ", 0, 0))
    path.write_bytes(
        bytes(header)
        + bytes(program_headers)
        + interpreter_bytes
        + bytes(dynamic)
        + (bytes(strings) if needed_offsets else b"")
    )
    path.chmod(0o755)


class NativeToolchainPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.host = self.root / "host"
        self.linker = self.root / "linker"
        self.destination = self.root / "native-rust-aarch64-v0.1.0"

        write_elf(
            self.host / "bin/rustc",
            interpreter=PACKAGE.INTERPRETER,
            needed=("librustc_driver-test.so",),
        )
        write_elf(self.host / "bin/cargo", interpreter=PACKAGE.INTERPRETER,
                  needed=("libcompiler-support.so",))
        write_elf(
            self.host / "lib/librustc_driver-test.so",
            needed=("libcompiler-support.so",),
        )
        write_elf(self.host / "lib/libcompiler-support.so")
        write_elf(
            self.host
            / f"lib/rustlib/{TARGET}/codegen-backends/"
            "librustc_codegen_cranelift-test.so",
            needed=("librustc_driver-test.so",),
        )
        target_lib = self.host / f"lib/rustlib/{TARGET}/lib"
        target_lib.mkdir(parents=True)
        for name in (
            "libcore-test.rlib",
            "liballoc-test.rlib",
            "libstd-test.rlib",
            "libcompiler_builtins-test.rlib",
            "crt1.o",
            "libunwind.a",
            "libstd-test.rmeta",
            "libstd-test.so",
        ):
            (target_lib / name).write_text(name)
        for name in ("COPYRIGHT", "LICENSE-APACHE", "LICENSE-MIT"):
            path = self.host / "share/doc/rust" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"rust {name}\n")

        host_manifest = {
            "schema": 1,
            "status": "built-not-guest-verified",
            "native_host": TARGET,
            "backend": "cranelift",
            "exit_code": 0,
            "guest_verified": False,
            "rust_commit": RUST_COMMIT,
            "github_run_id": "1234",
            "packaging_commit": "4" * 40,
            "capabilities": {"codegen": True, "cargo": True},
        }
        (self.host / "native-host-manifest.json").write_text(
            json.dumps(host_manifest)
        )

        write_elf(self.linker / "bin/wild")
        for name in ("LICENSE-APACHE", "LICENSE-MIT"):
            (self.linker / name).parent.mkdir(parents=True, exist_ok=True)
            (self.linker / name).write_text(f"wild {name}\n")
        linker_manifest = {
            "schema": 1,
            "status": "built-not-guest-verified",
            "target": TARGET,
            "guest_verified": False,
            "rust_toolchain_revision": RUST_COMMIT,
            "github_run_id": "5678",
            "packaging_commit": "5" * 40,
            "version": "0.9.0",
            "revision": "6" * 40,
        }
        (self.linker / "manifest.json").write_text(json.dumps(linker_manifest))

    def tearDown(self):
        self.temporary.cleanup()

    def assemble(self):
        return PACKAGE.assemble(
            native_host=self.host,
            native_linker=self.linker,
            target=TARGET,
            version="v0.1.0",
            scarlet_commit=SCARLET_COMMIT,
            destination=self.destination,
            package_commit=PACKAGE_COMMIT,
        )

    def test_minimal_versioned_package_has_explicit_loader_boundary(self):
        self.assemble()
        manifest = json.loads((self.destination / "manifest.json").read_text())
        self.assertEqual(
            manifest["install_prefix"], "/opt/scarlet/toolchains/rust/v0.1.0"
        )
        self.assertEqual(manifest["requires"]["scarlet_commit"], SCARLET_COMMIT)
        self.assertEqual(
            manifest["requires"]["dynamic_loader"], "/bin/scarlet-ld"
        )
        self.assertFalse(manifest["components"]["scarlet_ld"]["included"])
        self.assertEqual(
            manifest["capabilities"],
            {
                "cargo": True,
                "codegen": True,
                "guest_verified": False,
                "linker": True,
                "proc_macro": False,
                "rustc": True,
                "static_target_std": True,
            },
        )

        self.assertTrue((self.destination / "bin/rustc").is_file())
        self.assertTrue((self.destination / "bin/cargo").is_file())
        self.assertTrue((self.destination / "bin/wild").is_file())
        self.assertEqual(os.readlink(self.destination / "bin/rust-lld"), "wild")
        self.assertEqual(
            os.readlink(self.destination / "bin/librustc_driver-test.so"),
            "../lib/librustc_driver-test.so",
        )
        self.assertEqual(os.readlink(self.destination / "bin/libcompiler-support.so"),
                         "../lib/libcompiler-support.so")
        self.assertTrue((self.destination / "lib/libcompiler-support.so").is_file())
        self.assertEqual(
            os.readlink(
                self.destination
                / f"lib/rustlib/{TARGET}/codegen-backends/librustc_driver-test.so"
            ),
            "../../../librustc_driver-test.so",
        )
        self.assertFalse(any(self.destination.rglob("*.rmeta")))
        self.assertFalse(
            (self.destination / f"lib/rustlib/{TARGET}/lib/libstd-test.so").exists()
        )
        self.assertFalse(any(path.name == "scarlet-ld" for path in self.destination.rglob("*")))
        inventory = {entry["path"]: entry for entry in manifest["files"]}
        self.assertEqual(inventory["bin/rust-lld"]["kind"], "symlink")
        self.assertNotIn("manifest.json", inventory)
        self.assertIn("share/provenance/native-host.json", inventory)
        self.assertEqual(
            manifest["dynamic_dependencies"]["lib/librustc_driver-test.so"],
            ["libcompiler-support.so"],
        )

    def test_rejects_mismatched_wild_rust_revision(self):
        path = self.linker / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["rust_toolchain_revision"] = "7" * 40
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "different Rust revisions"):
            self.assemble()

    def test_rejects_wrong_native_architecture(self):
        write_elf(
            self.host / "bin/rustc",
            machine=243,
            interpreter=PACKAGE.INTERPRETER,
            needed=("librustc_driver-test.so",),
        )
        with self.assertRaisesRegex(ValueError, "wrong ELF machine"):
            self.assemble()

    def test_rejects_non_scarlet_runtime_loader(self):
        write_elf(
            self.host / "bin/rustc",
            interpreter="/lib/ld-linux.so",
            needed=("librustc_driver-test.so",),
        )
        with self.assertRaisesRegex(ValueError, "does not request"):
            self.assemble()

    def test_rejects_cargo_with_non_scarlet_loader(self):
        write_elf(self.host / "bin/cargo", interpreter="/system/bin/scarlet-ld")
        with self.assertRaisesRegex(ValueError, "native Cargo does not request"):
            self.assemble()

    def test_rejects_missing_cargo(self):
        (self.host / "bin/cargo").unlink()
        with self.assertRaisesRegex(ValueError, "native Cargo must be a regular file"):
            self.assemble()

    def test_rejects_missing_transitive_dynamic_dependency(self):
        (self.host / "lib/libcompiler-support.so").unlink()
        with self.assertRaisesRegex(ValueError, "unpackaged DT_NEEDED dependency"):
            self.assemble()

    def test_refuses_existing_destination(self):
        self.destination.mkdir()
        with self.assertRaisesRegex(ValueError, "destination already exists"):
            self.assemble()


if __name__ == "__main__":
    unittest.main()
