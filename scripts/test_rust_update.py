"""Exercise the updater's workflow shell steps without Nix or network access."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parent.parent
REV = "a" * 40
HASH = "sha256-" + "A" * 43 + "="
NEW_HASH = "sha256-" + "B" * 43 + "="
SYSTEMS = ("x86_64-linux", "aarch64-linux", "aarch64-darwin")


def workflow_step(name):
    workflow = (ROOT / ".github/workflows/update-rust.yml").read_text()
    block = workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]
    return textwrap.dedent(block.split("        run: |\n", 1)[1])


class RustUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.flake = self.root / "flake.nix"
        self.flake.write_text(f'  rustRev = "{REV}";\n  rustHash = "{HASH}";\n')
        (self.root / "nix").mkdir()
        (self.root / "nix/vendor-rust-src.nix").write_text("\n".join(
            f'    {system} = "{HASH}";' for system in SYSTEMS) + "\n")
        self.output = self.root / "outputs"
        self.summary = self.root / "summary"
        self.env = dict(os.environ, GITHUB_OUTPUT=str(self.output),
                        GITHUB_STEP_SUMMARY=str(self.summary),
                        REQUESTED_RUST_REV=REV, REQUESTED_RUST_REF="",
                        DEFAULT_RUST_REF="scarlet-target")

    def run_step(self, name, **env):
        self.output.write_text("")
        script = workflow_step(name).replace("${{ needs.resolve.outputs.rev }}", REV)
        return subprocess.run(["bash", "-c", script], cwd=self.root,
                              env=dict(self.env, **env), text=True, capture_output=True)

    def test_manual_runs_recompute_unchanged_revisions_in_both_modes(self):
        for hash_only in ("true", "false"):
            with self.subTest(hash_only=hash_only):
                result = self.run_step("Resolve Rust revision", GITHUB_EVENT_NAME="workflow_dispatch",
                                       HASH_ONLY=hash_only)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("changed=true\n", self.output.read_text())
                self.assertIn(f"rev={REV}\n", self.output.read_text())

    def test_automatic_events_skip_unchanged_revisions(self):
        for event in ("schedule", "repository_dispatch"):
            with self.subTest(event=event):
                result = self.run_step("Resolve Rust revision", GITHUB_EVENT_NAME=event)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("changed=false\n", self.output.read_text())

    def test_all_events_compute_new_revisions(self):
        for event in ("workflow_dispatch", "schedule", "repository_dispatch"):
            with self.subTest(event=event):
                result = self.run_step("Resolve Rust revision", GITHUB_EVENT_NAME=event,
                                       REQUESTED_RUST_REV="b" * 40)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("changed=true\n", self.output.read_text())

    def test_invalid_revision_is_rejected(self):
        result = self.run_step("Resolve Rust revision", GITHUB_EVENT_NAME="workflow_dispatch",
                               REQUESTED_RUST_REV="not-a-revision")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid Rust revision", result.stderr)

    def prepare_hashes(self, rust_hash, vendor_hash):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "flake.nix", "nix/vendor-rust-src.nix"],
                       cwd=self.root, check=True)
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copy2(ROOT / "scripts/apply-rust-update-hashes.sh", scripts)
        artifacts = self.root / ".rust-update-hashes"
        artifacts.mkdir()
        for system in SYSTEMS:
            (artifacts / f"{system}.env").write_text(
                f"system={system}\nrust_hash={rust_hash}\nvendored_hash={vendor_hash}\n")

    def test_identical_hashes_finish_without_a_pr(self):
        self.prepare_hashes(HASH, HASH)
        result = self.run_step("Apply update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "changed=false\n")
        self.assertIn("already current", self.summary.read_text())

    def test_source_hash_refresh_requests_a_pr(self):
        self.prepare_hashes(NEW_HASH, NEW_HASH)
        result = self.run_step("Apply update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "changed=true\n")
        self.assertIn(NEW_HASH, self.flake.read_text())
        self.assertIn(REV, self.flake.read_text())

    def test_vendor_only_refresh_requests_a_pr(self):
        self.prepare_hashes(HASH, NEW_HASH)
        result = self.run_step("Apply update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), "changed=true\n")
        self.assertIn(HASH, self.flake.read_text())
        self.assertEqual((self.root / "nix/vendor-rust-src.nix").read_text().count(NEW_HASH), 3)


if __name__ == "__main__":
    unittest.main()
