"""Checks remote placement and the production probe's refusal boundary."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "hermes/profiles/oppai-gen/scripts/oppai_prod_probe.py"
spec = importlib.util.spec_from_file_location("oppai_prod_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def task_rows(nodes=("gad", "aiueos-6600hs-1")):
    return [
        {"id": task_id, "node": nodes[index % 2], "exit": 0,
         "stdout": "{}__HTTP__" + ("401" if task_id == "video-gate" else "200") + "__TIME__0.123"}
        for index, task_id in enumerate(sorted(probe.FIXED_IDS))
    ]


def readings(gate=401):
    return {
        "health": (200, 10, json.dumps({"ok": True, "douga": {"configured": True}})),
        "index": (200, 10, '<title>R18</title><meta name="RTA-5042"><script src="/assets/main.abc.js">'),
        "modelmap": (200, 10, json.dumps({"media": []})),
        "video-gate": (gate, 10, ""),
    }


class RemoteProbeTest(unittest.TestCase):
    def test_four_tasks_on_two_nodes(self):
        result, nodes = probe.decode_batch(task_rows(), probe.FIXED_IDS, True)
        self.assertEqual(401, result["video-gate"][0])
        self.assertEqual(2, len(nodes))

    def test_one_node_refused(self):
        with self.assertRaisesRegex(ValueError, "two sandbox nodes"):
            probe.decode_batch(task_rows(("gad", "gad")), probe.FIXED_IDS, True)

    def test_missing_result_refused(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            probe.decode_batch(task_rows()[:3], probe.FIXED_IDS, True)

    def test_bundle_path_rejects_command_injection(self):
        with self.assertRaisesRegex(ValueError, "invalid bundle path"):
            probe.bundle_probe("/assets/main.a.js;touch /tmp/x")

    def test_anonymous_gate_401_is_healthy_and_200_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "oppai-ledger.jsonl"
            previous = Path(tmp) / ".prev-modelmap.json"
            with patch.object(probe, "LEDGER", ledger), patch.object(probe, "PREV", previous), \
                 patch.object(probe, "run_tasks", return_value=(readings(), ["aiueos-6600hs-1", "gad"])), \
                 patch.object(probe, "bundle_probe", return_value=(200, 10, "")):
                self.assertEqual(0, probe.main())
            first = json.loads(ledger.read_text().splitlines()[0])
            self.assertEqual(401, first["video_submit_status"])
            with patch.object(probe, "LEDGER", ledger), patch.object(probe, "PREV", previous), \
                 patch.object(probe, "run_tasks", return_value=(readings(gate=200), ["aiueos-6600hs-1", "gad"])), \
                 patch.object(probe, "bundle_probe", return_value=(200, 10, "")):
                self.assertEqual(1, probe.main())
            second = json.loads(ledger.read_text().splitlines()[1])
            self.assertIn("unexpected status 200", second["video_blocker"])


if __name__ == "__main__":
    unittest.main()
