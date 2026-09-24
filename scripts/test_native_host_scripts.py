"""Lightweight artifact handling checks; never invoke Rust/Nix or a network request."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
TARGET = "aarch64-unknown-scarlet"


class NativeHostArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.artifact = self.root / "artifact"
        self.artifact.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text("""#!/usr/bin/env python3
import os, pathlib, shutil, sys
assert sys.argv[1:3] == ['run', 'download']
destination = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])
shutil.copytree(os.environ['TEST_NATIVE_ARTIFACT'], destination, dirs_exist_ok=True)
""")
        gh.chmod(0o755)
        self.env = dict(os.environ, TEST_NATIVE_ARTIFACT=str(self.artifact),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.destination = self.root / "downloaded"

    def create_artifact(self, *, member="native-host/bin/rustc", symlink=False,
                        target=TARGET, run_id="1234", backend="cranelift", codegen=True,
                        cargo=True):
        archive = self.artifact / "native-host.tar.xz"
        with tarfile.open(archive, "w:xz") as tar:
            info = tarfile.TarInfo(member)
            info.mode = 0o755
            if symlink:
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/unrelated"
                tar.addfile(info)
            else:
                content = b"test artifact; never executed\n"
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            if member != "native-host/bin/cargo":
                cargo_bin = tarfile.TarInfo("native-host/bin/cargo")
                cargo_bin.mode = 0o755
                cargo_bin.size = len(b"test cargo artifact\n")
                tar.addfile(cargo_bin, io.BytesIO(b"test cargo artifact\n"))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.artifact / "native-host.tar.xz.sha256").write_text(digest + "  native-host.tar.xz\n")
        (self.artifact / "manifest.json").write_text(json.dumps({
            "schema": 1, "status": "built-not-guest-verified", "native_host": target,
            "github_run_id": run_id, "exit_code": 0, "guest_verified": False,
            "backend": backend, "capabilities": {"codegen": codegen, "cargo": cargo},
        }))

    def fetch(self):
        return subprocess.run([str(SCRIPTS / "fetch-native-host.sh"), "1234", TARGET,
                               str(self.destination)], env=self.env, text=True, capture_output=True)

    def test_fetch_checks_identity_and_preserves_executable_mode(self):
        self.create_artifact()
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.access(self.destination / "bin/rustc", os.X_OK))
        self.assertTrue(os.access(self.destination / "bin/cargo", os.X_OK))
        self.assertFalse(json.loads((self.destination / "native-host-manifest.json").read_text())["guest_verified"])

    def test_checksum_failure_does_not_extract(self):
        self.create_artifact()
        with (self.artifact / "native-host.tar.xz").open("ab") as file:
            file.write(b"modified")
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertFalse(self.destination.exists())

    def test_frontend_only_or_missing_codegen_artifact_is_rejected(self):
        for backend, codegen in [("dummy", False), ("dummy", True), ("cranelift", False)]:
            with self.subTest(backend=backend, codegen=codegen):
                self.create_artifact(backend=backend, codegen=codegen)
                result = self.fetch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("no Cranelift code generator", result.stderr)
                self.assertFalse(self.destination.exists())

    def test_artifact_without_cargo_is_rejected(self):
        self.create_artifact(cargo=False)
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no native Cargo", result.stderr)

    def test_wrong_target_or_run_is_rejected(self):
        for target, run_id in [("riscv64gc-unknown-scarlet", "1234"), (TARGET, "1235")]:
            with self.subTest(target=target, run_id=run_id):
                self.create_artifact(target=target, run_id=run_id)
                result = self.fetch()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.destination.exists())

    def test_archive_traversal_and_symlinks_are_rejected(self):
        for member, symlink in [("native-host/../escaped", False), ("native-host/bin/rustc", True)]:
            with self.subTest(member=member, symlink=symlink):
                self.create_artifact(member=member, symlink=symlink)
                result = self.fetch()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.destination.exists())
                self.assertFalse((self.root / "escaped").exists())

    def test_existing_destination_is_not_overwritten(self):
        self.destination.mkdir()
        keep = self.destination / "user-file"
        keep.write_text("preserve")
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(keep.read_text(), "preserve")

    def test_missing_environment_records_failure_and_removes_stale_archive(self):
        output = self.root / "output"
        output.mkdir()
        (output / "native-host.tar.xz").write_text("stale")
        env = {key: value for key, value in os.environ.items() if not key.startswith("SCARLET_")}
        result = subprocess.run([str(SCRIPTS / "build-native-host.sh"), "--output", str(output),
                                 "--work-dir", str(self.root / "work")], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["exit_code"], 2)
        self.assertFalse((output / "native-host.tar.xz").exists())


if __name__ == "__main__":
    unittest.main()
