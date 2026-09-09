import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pins", Path(__file__).with_name("pin-toolchains.py"))
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)


class PinTests(unittest.TestCase):
    def lock(self):
        return {"root": "root", "nodes": {
            "root": {"inputs": {"scarlet-rust-toolchain": "toolchain"}},
            "toolchain": {"locked": {
                "type": "github", "owner": "petitstrawberry", "repo": "scarlet-rust-nix",
                "rev": "a" * 40,
            }},
        }}

    def test_reads_the_lock_input_instead_of_assuming_a_node_name(self):
        self.assertEqual(pins.locked_revision(self.lock()), "a" * 40)

    def test_rejects_unexpected_repository_or_revision(self):
        for key, value in [("owner", "someone"), ("type", "path"), ("rev", "main")]:
            lock = self.lock()
            lock["nodes"]["toolchain"]["locked"][key] = value
            with self.assertRaises(ValueError):
                pins.locked_revision(lock)

    def test_shared_toolchains_are_evaluated_once_and_latest_is_pinned_last(self):
        calls = []

        def evaluate(revision, system):
            calls.append((revision, system))
            return f"/nix/store/{'a' * 32}-scarlet-rust-toolchain-{revision[:12]}"

        result = pins.plan_pins({"scarlet-distro": "a" * 40, "latest": "a" * 40}, evaluate)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result), 6)
        self.assertTrue(all(name.startswith("latest-") for name, _ in result[-3:]))

    def test_rejects_non_toolchain_outputs_before_pinning(self):
        with self.assertRaises(ValueError):
            pins.plan_pins({"scarlet-distro": "a" * 40}, lambda *_: "/tmp/not-a-toolchain")

    def test_missing_darwin_consumer_is_restored_after_available_paths_are_pinned(self):
        def fake_run(*args):
            if args[0] == "nix":
                ref, attr = args[-1].split("#")
                revision = ref.split("/")[-1]
                system = attr.split(".")[1]
                index = str(pins.SYSTEMS.index(system))
                return f"/nix/store/{revision[0] * 31}{index}-scarlet-rust-toolchain-{revision[:12]}"
            if args[0] == "curl":
                return "404" if "b" * 31 + "2.narinfo" in args[-1] else "200"
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as directory:
            plan, output = Path(directory) / "plan.json", Path(directory) / "output"
            with patch.dict(os.environ, {"CACHIX_CACHE_NAME": "test", "CACHIX_AUTH_TOKEN": "test", "GITHUB_OUTPUT": str(output), "LATEST_REV": ""}), \
                    patch("sys.argv", ["pin-toolchains.py", "--prepare", str(plan)]), \
                    patch.object(pins, "consumers", return_value={"scarlet-distro": "b" * 40}), \
                    patch.object(pins, "run", side_effect=fake_run), patch.object(pins, "pin") as pin:
                pins.main()
            self.assertEqual(len(json.loads(plan.read_text())), 3)
            self.assertEqual(pin.call_count, 2)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(outputs["has_missing"], "true")
            missing = json.loads(outputs["matrix"])["include"]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0]["system"], "aarch64-darwin")
            self.assertEqual(missing[0]["os"], "macos-14")
            self.assertEqual(missing[0]["revision"], "b" * 40)

    def test_pin_api_uses_a_bounded_revision_history(self):
        with patch.dict(os.environ, {"CACHIX_AUTH_TOKEN": "test-token"}), \
                patch.object(pins.urllib.request, "urlopen") as request:
            pins.pin("test", "scarlet-distro-aarch64-darwin", "/nix/store/example")
        data = json.loads(request.call_args.args[0].data)
        self.assertEqual(data["keep"], {"tag": "Revisions", "contents": 1})
        self.assertEqual(data["artifacts"], [])

    def test_changed_consumer_stops_a_stale_plan_before_replacing_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            plan.write_text(json.dumps([{
                "name": "scarlet-distro-aarch64-darwin", "system": "aarch64-darwin",
                "revision": "a" * 40,
                "path": f"/nix/store/{'a' * 32}-scarlet-rust-toolchain-{'a' * 12}",
            }]))
            with patch.dict(os.environ, {"CACHIX_CACHE_NAME": "test", "CACHIX_AUTH_TOKEN": "test"}), \
                    patch("sys.argv", ["pin-toolchains.py", "--apply", str(plan)]), \
                    patch.object(pins, "consumers", return_value={"scarlet-distro": "b" * 40}), \
                    patch.object(pins, "pin") as pin:
                with self.assertRaisesRegex(RuntimeError, "consumers changed"):
                    pins.main()
                pin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
