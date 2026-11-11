import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest

spec = importlib.util.spec_from_file_location("merge_vendor", Path(__file__).with_name("merge-vendor-sources.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
GIT = "https://github.com/example/home"
REV = "a" * 40
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
GIT_SOURCE = f"git+{GIT}?rev={REV}#{REV}"
HASH = "b" * 64


def crate(directory, name, version, checksum, contents="pub fn value() {}\n"):
    directory.mkdir(parents=True)
    (directory / "Cargo.toml").write_text(f'[package]\nname = "{name}"\nversion = "{version}"\nedition = "2021"\n')
    (directory / "src").mkdir()
    (directory / "src/lib.rs").write_text(contents)
    files = {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in directory.rglob("*") if path.is_file()}
    (directory / ".cargo-checksum.json").write_text(json.dumps({"package": checksum, "files": files}))


class VendorMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = contextlib.chdir(self.root)
        self.cwd.__enter__()
        self.addCleanup(self.cwd.__exit__, None, None, None)
        Path("vendor").mkdir()
        Path("vendor-rust-analyzer").mkdir()
        self.main = Path("main.toml")
        self.extra = Path("extra.toml")
        self.lock = Path("extra.lock")
        self.main.write_text('[source.crates-io]\nreplace-with = "vendored-sources"\n[source.vendored-sources]\ndirectory = "vendor"\n')
        self.extra.write_text(self.main.read_text().replace('"vendor"', '"vendor-rust-analyzer"') +
                             f'[source."{GIT}?rev={REV}"]\ngit = "{GIT}"\nrev = "{REV}"\nreplace-with = "vendored-sources"\n')
        self.packages = []

    def add(self, name, version, source):
        self.packages.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\nsource = "{source}"\n')
        self.lock.write_text("\n".join(self.packages))

    def merge(self):
        return module.merge(self.main, self.extra, self.lock)

    def collision_fixture(self):
        crate(Path("vendor/home"), "home", "0.5.12", HASH, "pub fn registry() {}\n")
        crate(Path("vendor-rust-analyzer/home"), "home", "0.5.12", None, "pub fn fork() {}\n")
        self.add("home", "0.5.12", GIT_SOURCE)

    def test_same_name_version_distinct_sources_survive(self):
        self.collision_fixture()
        sources = tomllib.loads(self.merge())["source"]
        self.assertEqual(sources["crates-io"]["replace-with"], "vendored-sources")
        self.assertEqual(sources[f"{GIT}?rev={REV}"]["replace-with"], "vendored-rust-analyzer")
        self.assertIn("registry", Path("vendor/home/src/lib.rs").read_text())
        self.assertIn("fork", Path("vendor-rust-analyzer/home/src/lib.rs").read_text())

    def test_shared_registry_deduplicates_and_extra_version_merges(self):
        crate(Path("vendor/thing"), "thing", "1.0.0", HASH)
        crate(Path("vendor-rust-analyzer/thing-1.0.0"), "thing", "1.0.0", HASH)
        crate(Path("vendor-rust-analyzer/thing"), "thing", "2.0.0", "c" * 64)
        self.add("thing", "1.0.0", REGISTRY)
        self.add("thing", "2.0.0", REGISTRY)
        self.merge()
        self.assertTrue(Path("vendor/thing-2.0.0/Cargo.toml").is_file())
        self.assertFalse(Path("vendor-rust-analyzer").exists())

    def test_shared_package_content_mismatch_fails(self):
        crate(Path("vendor/thing"), "thing", "1.0.0", HASH)
        crate(Path("vendor-rust-analyzer/thing"), "thing", "1.0.0", "c" * 64)
        self.add("thing", "1.0.0", REGISTRY)
        with self.assertRaisesRegex(ValueError, "different contents"):
            self.merge()

    def test_shared_git_source_keeps_all_packages(self):
        self.main.write_text(self.main.read_text() + f'[source."{GIT}?rev={REV}"]\ngit = "{GIT}"\nrev = "{REV}"\nreplace-with = "vendored-sources"\n')
        crate(Path("vendor/first"), "first", "1.0.0", None)
        crate(Path("vendor-rust-analyzer/second"), "second", "1.0.0", None)
        self.add("second", "1.0.0", GIT_SOURCE)
        sources = tomllib.loads(self.merge())["source"]
        self.assertEqual(sources[f"{GIT}?rev={REV}"]["replace-with"], "vendored-sources")
        self.assertTrue(Path("vendor/first/Cargo.toml").is_file())
        self.assertTrue(Path("vendor/second/Cargo.toml").is_file())

    @unittest.skipUnless(shutil.which("cargo"), "Cargo not available")
    def test_offline_locked_cargo_resolves_both_source_identities(self):
        self.collision_fixture()
        Path(".cargo").mkdir()
        Path(".cargo/config.toml").write_text(self.merge())
        Path("Cargo.toml").write_text(f'''[package]
name = "vendor-regression"
version = "0.1.0"
edition = "2021"
[dependencies]
registry-home = {{ package = "home", version = "=0.5.12" }}
git-home = {{ package = "home", git = "{GIT}", rev = "{REV}" }}
''')
        Path("src").mkdir()
        Path("src/lib.rs").write_text("")
        Path("Cargo.lock").write_text(f'''version = 4
[[package]]
name = "home"
version = "0.5.12"
source = "{REGISTRY}"
checksum = "{HASH}"
[[package]]
name = "home"
version = "0.5.12"
source = "{GIT_SOURCE}"
[[package]]
name = "vendor-regression"
version = "0.1.0"
dependencies = [
 "home 0.5.12 ({REGISTRY})",
 "home 0.5.12 ({GIT_SOURCE.rsplit('#', 1)[0]})",
]
''')
        before = Path("Cargo.lock").read_bytes()
        environment = dict(os.environ, CARGO_HOME=str(self.root / "empty-cargo-home"))
        result = subprocess.run(["cargo", "metadata", "--offline", "--locked", "--format-version", "1"],
                                env=environment, check=True, text=True, capture_output=True)
        packages = [p for p in json.loads(result.stdout)["packages"] if p["name"] == "home"]
        self.assertEqual({p["source"] for p in packages}, {REGISTRY, GIT_SOURCE})
        self.assertEqual(len({p["manifest_path"] for p in packages}), 2)
        self.assertEqual(Path("Cargo.lock").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
