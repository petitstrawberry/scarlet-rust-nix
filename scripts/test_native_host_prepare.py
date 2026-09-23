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


class PatchTests(unittest.TestCase):
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
            errno_test = source / "library/std/src/sys/scarlet_errno_abi_test.py"
            errno_test.parent.mkdir(parents=True)
            errno_test.write_text("# fork-owned native runtime check\n")
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
            inputs.mkdir()
            (inputs / "recipe.json").write_text(json.dumps({"packages": [{
                "name": "target-lexicon", "version": "0.13.3", "root_patch": None,
                "git": {"url": str(lexicon_fork), "revision": revision},
                "operations": [],
            }]}))
            with mock.patch.dict(os.environ, {"SCARLET_RUST_REV": "a" * 40}):
                prepare.prepare(source, inputs)
            self.assertEqual(target.read_text(), "fork-owned source stays unchanged\n")
            self.assertEqual((source / "native-host-deps/target-lexicon-0.13.3/triple.txt").read_text(), "scarlet\n")
            self.assertFalse((source / "native-host-deps/target-lexicon-0.13.3/.git").exists())
            self.assertIn('target-lexicon = { path = "../../native-host-deps/target-lexicon-0.13.3" }',
                          backend.read_text())
            marker = json.loads((source / ".scarlet-native-host-prepared.json").read_text())
            self.assertEqual(marker["rust_revision"], "a" * 40)
            self.assertEqual(marker["inputs"], [{"repository": str(lexicon_fork),
                                                  "revision": revision}])

    def test_context_free_interior_hunk_checks_and_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            target = source / "tls.rs"
            target.write_text("first\nconst KEYS: usize = 128;\nlast\n")
            patch = Path(tmp) / "tls.patch"
            patch.write_text("diff --git a/tls.rs b/tls.rs\n"
                             "--- a/tls.rs\n+++ b/tls.rs\n"
                             "@@ -2 +2 @@\n"
                             "-const KEYS: usize = 128;\n"
                             "+const KEYS: usize = 1024;\n")
            prepare.apply_patch(source, patch, check_only=True)
            self.assertIn("= 128;", target.read_text())
            prepare.apply_patch(source, patch)
            self.assertEqual(target.read_text(), "first\nconst KEYS: usize = 1024;\nlast\n")

    def test_patch_applies_to_nested_source_and_dependency_without_touching_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "source.txt").write_text("keep parent\n")
            patch = root / "change.patch"
            patch.write_text("diff --git a/source.txt b/source.txt\n"
                             "--- a/source.txt\n+++ b/source.txt\n"
                             "@@ -1 +1 @@\n-before\n+after\n")
            for relative in ("work/source", "work/source/native-host-deps/example"):
                source = root / relative
                source.mkdir(parents=True)
                target = source / "source.txt"
                target.write_text("before\n")
                prepare.apply_patch(source, patch, check_only=True)
                self.assertEqual(target.read_text(), "before\n")
                prepare.apply_patch(source, patch)
                self.assertEqual(target.read_text(), "after\n")
                self.assertEqual((root / "source.txt").read_text(), "keep parent\n")


if __name__ == "__main__":
    unittest.main()
