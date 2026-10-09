"""Reject broader or mutable registry authority before calling the downloader."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from registry_pull import validate


class RegistryPullControls(unittest.TestCase):
    def setUp(self):
        self.revision = "a" * 40
        self.payload = {
            "revision": self.revision,
            "pulls": [
                {
                    "image": "ghcr.io/aoodie/tauric-" + kind + "@sha256:" + "b" * 64,
                    "scope": "repository:aoodie/tauric-" + kind + ":pull",
                    "token": "fixture-token-with-no-production-authority",
                }
                for kind in ("api", "worker", "web", "openclaw")
            ],
        }

    def test_exact_verified_release_is_accepted_without_external_access(self):
        validate(self.payload, self.revision)
        with self.assertRaises(ValueError):
            validate(self.payload, "b" * 40)
        with self.assertRaises(ValueError):
            validate(self.payload, "main")

    def test_mutable_foreign_duplicate_or_incomplete_images_are_rejected(self):
        for image in (
            "ghcr.io/aoodie/tauric-api:latest",
            "ghcr.io/other/project@sha256:" + "b" * 64,
        ):
            payload = copy.deepcopy(self.payload)
            payload["pulls"][0]["image"] = image
            with self.assertRaises(ValueError):
                validate(payload, self.revision)
        for pulls in (self.payload["pulls"][:3], [self.payload["pulls"][0]] * 4):
            with self.assertRaises(ValueError):
                validate({**self.payload, "pulls": pulls}, self.revision)

    def test_push_or_other_repository_scope_is_rejected(self):
        for scope in (
            "repository:aoodie/tauric-api:pull,push",
            "repository:other/project:pull",
        ):
            payload = copy.deepcopy(self.payload)
            payload["pulls"][0]["scope"] = scope
            with self.assertRaises(ValueError):
                validate(payload, self.revision)
