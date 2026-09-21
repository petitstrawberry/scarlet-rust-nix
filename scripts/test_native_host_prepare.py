"""Exercise source patching inside an enclosing checkout, as on Actions."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "prepare_native_host", Path(__file__).with_name("prepare-native-host.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PatchTests(unittest.TestCase):
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
