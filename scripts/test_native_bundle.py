"""Exercise the distributed bundle with tiny synthetic release candidates."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest


SPEC = importlib.util.spec_from_file_location(
    "package_native_bundle", Path(__file__).with_name("package-native-bundle.py")
)
BUNDLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUNDLE)
COMMIT, RUST, SCARLET = "a" * 40, "b" * 40, "c" * 40
VERSION = f"v0.1.0-dev.{COMMIT[:12]}"


def fixture(directory):
    for arch, target in BUNDLE.TARGETS.items():
        stem = f"native-rust-{arch}-{VERSION}"
        archive = directory / f"{stem}.tar.zst"
        archive.write_bytes(f"synthetic {arch} payload".encode())
        (directory / f"{archive.name}.sha256").write_text(
            f"{BUNDLE.sha256(archive)}  {archive.name}\n"
        )
        (directory / f"{stem}.manifest.json").write_text(json.dumps({
            "schema": 1, "kind": "scarlet-native-rust-toolchain",
            "version": VERSION, "packaging_commit": COMMIT,
            "target": target, "architecture": arch,
            "install_prefix": BUNDLE.PREFIX + VERSION,
            "rust": {"revision": RUST, "backend": "cranelift"},
            "requires": {"scarlet_commit": SCARLET, "dynamic_loader": "/bin/scarlet-ld"},
            "components": {"scarlet_ld": {"included": False}},
            "capabilities": {key: True for key in (
                "rustc", "codegen", "linker", "static_target_std", "cargo"
            )},
        }))


class NativeBundleTests(unittest.TestCase):
    def test_bundle_is_reproducible_and_installs_the_selected_version_for_both_architectures(self):
        archives = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture(directory)
                archive = BUNDLE.package_bundle(directory, COMMIT, RUST)
                archives.append(archive.read_bytes())
                self.assertEqual(archive.with_name(archive.name + ".sha256").read_text(),
                                 f"{hashlib.sha256(archives[-1]).hexdigest()}  {archive.name}\n")
                with tarfile.open(archive) as bundle:
                    self.assertEqual(len(bundle.getmembers()), 2)
                    content = bundle.extractfile("rust-toolchain/bundle.toml").read().decode()
                    self.assertIn(SCARLET, content)
                    layers = tomllib.loads(content)["layers"]
                    self.assertEqual(len(layers), 2)
                    for arch in BUNDLE.TARGETS:
                        payload = directory / f"native-rust-{arch}-{VERSION}.tar.zst"
                        self.assertEqual(layers[0]["sha256"][arch], f"sha256:{BUNDLE.sha256(payload)}")
                        self.assertEqual(layers[0]["url"].replace("{arch}", arch),
                                         f"https://github.com/petitstrawberry/scarlet-rust-nix/releases/download/{VERSION}/{payload.name}")
                    self.assertEqual(layers[0]["to"], BUNDLE.PREFIX + VERSION)
                    self.assertEqual(layers[0]["strip_components"], 1)
                    self.assertEqual(layers[1], {"kind": "copy", "source": "fs", "to": "/"})
                    current = bundle.getmember(f"rust-toolchain/fs{BUNDLE.PREFIX}current")
                    self.assertTrue(current.issym())
                    self.assertEqual(current.linkname, VERSION)
                with self.assertRaisesRegex(ValueError, "existing bundle"):
                    BUNDLE.package_bundle(directory, COMMIT, RUST)
        self.assertEqual(*archives)

    def test_bad_candidates_are_rejected_before_creating_the_bundle(self):
        changes = [
            lambda m: m.update(target="aarch64-unknown-scarlet"),
            lambda m: m.update(packaging_commit=RUST),
            lambda m: m["rust"].update(revision=COMMIT),
            lambda m: m["requires"].update(dynamic_loader="/system/bin/scarlet-ld"),
            lambda m: m["requires"].update(scarlet_commit=COMMIT),
            lambda m: m["capabilities"].update(static_target_std=False),
            lambda m: m["capabilities"].update(cargo=False),
        ]
        for case in range(len(changes) + 2):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                fixture(directory)
                stem = f"native-rust-riscv64-{VERSION}"
                if case < len(changes):
                    sidecar = directory / f"{stem}.manifest.json"
                    manifest = json.loads(sidecar.read_text())
                    changes[case](manifest)
                    sidecar.write_text(json.dumps(manifest))
                elif case == len(changes):
                    (directory / f"{stem}.tar.zst.sha256").write_text(f'{"0" * 64}  wrong.tar.zst\n')
                else:
                    (directory / f"{stem}.tar.zst").write_bytes(b"modified archive")
                with self.assertRaises(ValueError):
                    BUNDLE.package_bundle(directory, COMMIT, RUST)
                self.assertFalse(list(directory.glob("rust-toolchain-bundle-*")))


if __name__ == "__main__":
    unittest.main()
