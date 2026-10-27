"""Monitor failures are observable outside the production host and retries remain quiet."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offhost_watch as watch


class OffhostWatch(unittest.TestCase):
    def test_monitor_loss_delivery_retry_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            with (patch.object(watch, "ssh", side_effect=RuntimeError("offline")),
                  patch.object(watch, "pull", side_effect=RuntimeError("offline")),
                  patch.object(watch, "notify", side_effect=subprocess.TimeoutExpired("notify", 10)) as notify):
                first = watch.observe({}, out)
                self.assertEqual(first["delivery"], "failed")
                self.assertIn("host_unreachable_or_monitor_unavailable", first["keys"])
                watch.observe({}, out)
                self.assertEqual(notify.call_count, 2)
            with (patch.object(watch, "ssh", return_value=b'{"age_seconds":5,"keys":[]}'),
                  patch.object(watch, "pull", return_value={"verified_checkpoints": ["one"]}),
                  patch.object(watch, "notify") as notify):
                recovered = watch.observe({}, out)
                self.assertEqual(recovered["keys"], [])
                self.assertEqual(recovered["delivery"], "submitted_to_macos_notifications")
                watch.observe({}, out)
                self.assertEqual(notify.call_count, 1)

    def test_stale_host_sample_cannot_report_healthy(self):
        with (tempfile.TemporaryDirectory() as folder,
              patch.object(watch, "ssh", return_value=json.dumps({"age_seconds":900,"keys":[]}).encode()),
              patch.object(watch, "pull", return_value={"verified_checkpoints": ["one"]}),
              patch.object(watch, "notify")):
            self.assertIn("host_monitor_stale", watch.observe({}, Path(folder))["keys"])
