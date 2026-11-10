"""Exercise the Nix install-check gate without building a Rust compiler."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parent.parent


class LoaderImportCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="loader gate ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.out = self.root / "built toolchain"
        # Nix interpolates a store path here, which cannot contain spaces.
        self.llvm = Path("/nix/store/test-llvm")
        recipe = (ROOT / "nix/build-toolchain.nix").read_text()
        self.assertIn("doInstallCheck = true;", recipe)
        phase = recipe.split("installCheckPhase = ''", 1)[1].split("\n  '';", 1)[0]
        match = re.search(
            r"    if \[ -f library/rtstartup/test-scarlet-loader-imports\.py \]; then\n"
            r".*?\n    fi", phase, re.DOTALL
        )
        self.assertIsNotNone(match, "loader check must be part of installCheckPhase")
        self.gate = match.group().replace("${llvmPackages.llvm}", str(self.llvm))
        self.script = self.root / "library/rtstartup/test-scarlet-loader-imports.py"

    def run_gate(self):
        # Nix's build shell uses errexit. The sentinel models later checks and
        # publication: a failed regression test must not reach it.
        return subprocess.run(
            ["bash", "-e", "-c", self.gate + '\nprintf "checked\\n"'],
            cwd=self.root,
            env={**os.environ, "out": str(self.out)},
            text=True,
            capture_output=True,
        )

    def write_script(self, body):
        self.script.parent.mkdir(parents=True)
        self.script.write_text(textwrap.dedent(body))

    def test_old_source_without_regression_script_keeps_existing_checks(self):
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "checked\n")

    def test_regression_uses_installed_compiler_and_selected_llvm(self):
        self.write_script('''
            import json
            from pathlib import Path
            import sys
            Path("arguments.json").write_text(json.dumps(sys.argv[1:]))
        ''')
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads((self.root / "arguments.json").read_text()),
            ["--rustc", str(self.out / "bin/rustc"),
             "--readelf", str(self.llvm / "bin/llvm-readelf")],
        )
        self.assertEqual(result.stdout, "checked\n")

    def test_regression_failure_stops_install_check(self):
        self.write_script('''
            import sys
            sys.exit(42)
        ''')
        result = self.run_gate()
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn("checked", result.stdout)

    def test_invalid_regression_script_is_not_silently_skipped(self):
        self.write_script("this is not valid Python\n")
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("checked", result.stdout)


if __name__ == "__main__":
    unittest.main()
