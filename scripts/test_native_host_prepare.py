"""Exercise isolated dependency preparation inside an enclosing checkout."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "prepare_native_host", Path(__file__).with_name("prepare-native-host.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class DependencyPreparationTests(unittest.TestCase):
    def test_prepares_dependencies_without_patching_rust_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            inputs = root / "inputs"
            (source / ".cargo").mkdir(parents=True)
            (source / ".cargo/config.toml").write_text("\n")
            target = source / "compiler/rustc_target/src/spec/targets/aarch64_unknown_scarlet.rs"
            target.parent.mkdir(parents=True)
            target.write_text("fork-owned source stays unchanged\n")
            backend = source / "compiler/rustc_codegen_cranelift/Cargo.toml"
            backend.parent.mkdir(parents=True)
            backend.write_text("[patch.crates-io]\n# existing section\n")
            (source / "Cargo.toml").write_text("[workspace]\n")
            (source / "vendor").mkdir()
            lexicon_fork = root / "lexicon-fork"
            lexicon_fork.mkdir()
            (lexicon_fork / "Cargo.toml").write_text(
                '[package]\nname = "target-lexicon"\nversion = "0.13.3"\n')
            (lexicon_fork / "triple.txt").write_text("scarlet\n")
            subprocess.run(["git", "init", "-q", str(lexicon_fork)], check=True)
            subprocess.run(["git", "-C", str(lexicon_fork), "add", "."], check=True)
            subprocess.run(["git", "-C", str(lexicon_fork), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm",
                            "Scarlet triple"], check=True)
            revision = subprocess.check_output(["git", "-C", str(lexicon_fork), "rev-parse",
                                                "HEAD"], text=True).strip()
            libloading_fork = root / "libloading-fork"
            libloading_fork.mkdir()
            libloading_manifest = libloading_fork / "Cargo.toml"
            libloading_manifest.write_text('[package]\nname = "libloading"\nversion = "0.8.9"\n')
            (libloading_fork / "scarlet.txt").write_text("Scarlet adapter\n")
            subprocess.run(["git", "init", "-q", str(libloading_fork)], check=True)
            subprocess.run(["git", "-C", str(libloading_fork), "add", "."], check=True)
            subprocess.run(["git", "-C", str(libloading_fork), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm",
                            "Scarlet libloading 0.8.9"], check=True)
            revision_08 = subprocess.check_output(["git", "-C", str(libloading_fork), "rev-parse",
                                                   "HEAD"], text=True).strip()
            libloading_manifest.write_text('[package]\nname = "libloading"\nversion = "0.9.0"\n')
            subprocess.run(["git", "-C", str(libloading_fork), "add", "Cargo.toml"], check=True)
            subprocess.run(["git", "-C", str(libloading_fork), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm",
                            "Scarlet libloading 0.9.0"], check=True)
            revision_09 = subprocess.check_output(["git", "-C", str(libloading_fork), "rev-parse",
                                                   "HEAD"], text=True).strip()
            inputs.mkdir()
            packages = [{
                "name": "libloading", "version": version, "root_patch": alias,
                "git": {"url": str(libloading_fork), "revision": commit},
                "operations": [],
            } for version, alias, commit in (("0.8.9", "libloading_08", revision_08),
                                             ("0.9.0", "libloading_09", revision_09))]
            packages.append({
                "name": "target-lexicon", "version": "0.13.3", "root_patch": None,
                "git": {"url": str(lexicon_fork), "revision": revision},
                "operations": [],
            })
            (inputs / "recipe.json").write_text(json.dumps({"packages": packages}))
            with mock.patch.dict(os.environ, {"SCARLET_RUST_REV": "a" * 40}):
                prepare.prepare(source, inputs)
            self.assertEqual(target.read_text(), "fork-owned source stays unchanged\n")
            self.assertEqual((source / "native-host-deps/target-lexicon-0.13.3/triple.txt").read_text(), "scarlet\n")
            self.assertFalse((source / "native-host-deps/target-lexicon-0.13.3/.git").exists())
            for version in ("0.8.9", "0.9.0"):
                dependency = source / "native-host-deps" / f"libloading-{version}"
                self.assertEqual((dependency / "scarlet.txt").read_text(), "Scarlet adapter\n")
                self.assertIn(f'version = "{version}"', (dependency / "Cargo.toml").read_text())
                self.assertFalse((dependency / ".git").exists())
            self.assertIn('libloading_08 = { package = "libloading", path = "native-host-deps/libloading-0.8.9" }',
                          (source / "Cargo.toml").read_text())
            self.assertIn('libloading_09 = { package = "libloading", path = "native-host-deps/libloading-0.9.0" }',
                          (source / "Cargo.toml").read_text())
            self.assertIn('target-lexicon = { path = "../../native-host-deps/target-lexicon-0.13.3" }',
                          backend.read_text())
            marker = json.loads((source / ".scarlet-native-host-prepared.json").read_text())
            self.assertEqual(marker["rust_revision"], "a" * 40)
            self.assertEqual(marker["inputs"], [
                {"repository": str(libloading_fork), "revision": revision_08},
                {"repository": str(libloading_fork), "revision": revision_09},
                {"repository": str(lexicon_fork), "revision": revision},
            ])



if __name__ == "__main__":
    unittest.main()
