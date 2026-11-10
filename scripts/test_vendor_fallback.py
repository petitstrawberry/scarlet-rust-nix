"""Exercise the actual Nix build-phase shell without Nix or compilation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_merge_vendor_sources import crate, GIT, GIT_SOURCE, HASH, REV

REPO = Path(__file__).resolve().parents[1]
SYNC_MANIFESTS = [
    "src/tools/cargo/Cargo.toml", "src/tools/clippy/Cargo.toml",
    "src/tools/clippy/clippy_test_deps/Cargo.toml", "src/tools/rust-analyzer/Cargo.toml",
    "src/tools/rustfmt/Cargo.toml", "compiler/rustc_codegen_cranelift/Cargo.toml",
    "compiler/rustc_codegen_gcc/Cargo.toml", "library/Cargo.toml",
    "src/bootstrap/Cargo.toml", "src/tools/rustbook/Cargo.toml",
    "src/tools/rustc-perf/Cargo.toml", "src/tools/opt-dist/Cargo.toml",
    "src/doc/book/packages/trpl/Cargo.toml",
]

FAKE_CARGO = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
with (root / "calls").open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1] == "metadata":
    print("{}")
    sys.exit(0)
first = len((root / "calls").read_text().splitlines()) == 1
mode = os.environ["VENDOR_MODE"]
if first:
    if mode == "success":
        pathlib.Path("vendor").mkdir()
        pathlib.Path("vendor/preserved").write_bytes(b"old vendor bytes\n")
        sys.stdout.write('# original formatting\n[source.crates-io]\nreplace-with = "vendored-sources"\n[source.vendored-sources]\ndirectory = "vendor"\n')
        sys.stderr.write("normal cargo log\n")
        sys.exit(0)
    pathlib.Path("vendor/partial").mkdir(parents=True)
    sys.stdout.write("partial config")
    if mode == "duplicate":
        sys.stderr.write("error: found duplicate version of package `home v0.5.12` vendored from two sources:\n")
    else:
        sys.stderr.write("error: could not download dependency: network unavailable\n")
    sys.exit(101)
assert not pathlib.Path("vendor/partial").exists(), "partial vendor output retained"
assert not pathlib.Path(".cargo/config.toml").exists(), "partial config retained"
directory = sys.argv[-1]
shutil.copytree(root / "fixtures" / directory, directory)
sys.stdout.write((root / "fixtures" / (directory + ".toml")).read_text())
'''


class VendorFallbackTests(unittest.TestCase):
    def run_phase(self, mode):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        source = root / "source"
        source.mkdir()
        (root / "tmp").mkdir()
        (root / "bin").mkdir()
        cargo = root / "bin/cargo"
        cargo.write_text(FAKE_CARGO)
        cargo.chmod(0o755)
        fixtures = root / "fixtures"
        fixtures.mkdir()
        crate(fixtures / "vendor/home", "home", "0.5.12", HASH, "pub fn registry() {}\n")
        crate(fixtures / "vendor-rust-analyzer/home", "home", "0.5.12", None, "pub fn fork() {}\n")
        base = '[source.crates-io]\nreplace-with = "vendored-sources"\n[source.vendored-sources]\ndirectory = "vendor"\n'
        (fixtures / "vendor.toml").write_text(base)
        (fixtures / "vendor-rust-analyzer.toml").write_text(
            base.replace('"vendor"', '"vendor-rust-analyzer"') +
            f'[source."{GIT}?rev={REV}"]\ngit = "{GIT}"\nrev = "{REV}"\nreplace-with = "vendored-sources"\n')
        lock = source / "src/tools/rust-analyzer/Cargo.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(f'[[package]]\nname = "home"\nversion = "0.5.12"\nsource = "{GIT_SOURCE}"\n')
        nix = (REPO / "nix/vendor-rust-src.nix").read_text()
        shell = nix.split("  buildPhase = ''\n", 1)[1].split("\n  '';", 1)[0]
        shell = shell.replace("${bootstrapRust}", str(root))
        shell = shell.replace("${../scripts/merge-vendor-sources.py}", str(REPO / "scripts/merge-vendor-sources.py"))
        environment = dict(os.environ, PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
                           TMPDIR=str(root / "tmp"), FIXTURE_ROOT=str(root), VENDOR_MODE=mode)
        result = subprocess.run(["bash", "-eu", "-c", 'runHook() { :; };\n' + shell],
                                cwd=source, env=environment, text=True, capture_output=True)
        calls = [json.loads(line) for line in (root / "calls").read_text().splitlines()]
        return result, source, calls

    def test_success_retains_original_arguments_and_output_bytes(self):
        result, source, calls = self.run_phase("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = ["vendor"]
        for manifest in SYNC_MANIFESTS:
            expected.extend(["--sync", manifest])
        expected.append("vendor")
        self.assertEqual(calls, [expected])
        self.assertEqual((source / "vendor/preserved").read_bytes(), b"old vendor bytes\n")
        self.assertEqual((source / ".cargo/config.toml").read_text(), '# original formatting\n[source.crates-io]\nreplace-with = "vendored-sources"\n[source.vendored-sources]\ndirectory = "vendor"\n')
        self.assertFalse((source / "vendor-rust-analyzer").exists())
        self.assertIn("normal cargo log", result.stderr)
        self.assertEqual(sorted(p.name for p in source.iterdir()), [".cargo", "src", "vendor"])

    def test_duplicate_conflict_cleans_partial_output_and_splits(self):
        result, source, calls = self.run_phase("duplicate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 6)  # full attempt, two split attempts, three metadata checks
        self.assertNotIn("src/tools/rust-analyzer/Cargo.toml", calls[1])
        self.assertEqual(calls[2], ["vendor", "--manifest-path", "src/tools/rust-analyzer/Cargo.toml", "vendor-rust-analyzer"])
        self.assertTrue(all("--offline" in call and "--locked" in call for call in calls[3:]))
        self.assertIn("duplicate version", result.stderr)
        self.assertIn("vendored-rust-analyzer", (source / ".cargo/config.toml").read_text())
        self.assertTrue((source / "vendor/home/src/lib.rs").exists())
        self.assertTrue((source / "vendor-rust-analyzer/home/src/lib.rs").exists())
        self.assertFalse((source / "vendor/partial").exists())

    def test_unrelated_error_propagates_without_retry(self):
        result, source, calls = self.run_phase("network")
        self.assertEqual(result.returncode, 101)
        self.assertEqual(len(calls), 1)
        self.assertIn("network unavailable", result.stderr)
        self.assertTrue((source / "vendor/partial").exists())


if __name__ == "__main__":
    unittest.main()
