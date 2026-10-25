"""Host observations distinguish measured RAM from container memory limits."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server_soak


class SoakMemory(unittest.TestCase):
    def test_linux_host_ram_and_swap_are_observed_in_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meminfo"
            path.write_text("MemTotal: 16000 kB\nMemAvailable: 6000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
            self.assertEqual(server_soak.host_memory(path), {
                "physical_total_bytes": 16000 * 1024, "physical_available_bytes": 6000 * 1024,
                "swap_total_bytes": 0, "swap_free_bytes": 0,
            })
            path.write_text("MemTotal: invalid kB\n")
            self.assertIsNone(server_soak.host_memory(path))
            self.assertIsNone(server_soak.host_memory(Path(directory) / "missing"))

    def test_memory_caps_are_counted_only_for_running_services(self):
        def container(name, running, limit):
            return {
                "Id": name, "Config": {"Labels": {"com.docker.compose.service": name}},
                "State": {"Running": running, "Health": {"Status": "healthy"}, "OOMKilled": False},
                "RestartCount": 0, "HostConfig": {"Memory": limit},
            }

        rows = [container("api", True, 384), container("paused", False, 192), container("web", True, 0)]
        outputs = [SimpleNamespace(stdout=b"api paused web"),
                   SimpleNamespace(stdout=json.dumps(rows).encode()), SimpleNamespace(stdout=b"")]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(server_soak.server, "command", return_value=["compose"]),
            patch.object(server_soak.server, "run", side_effect=outputs),
            patch.object(server_soak, "host_memory", return_value={"physical_total_bytes": 16000}),
        ):
            observed = server_soak.observe(Path(directory), "test")
        self.assertEqual(observed["active_memory_limit_bytes"], 384)
        self.assertEqual(observed["unbounded_memory_services"], ["web"])
        self.assertEqual(observed["host_memory"]["physical_total_bytes"], 16000)
