"""Access boundaries for the separately credentialed release service."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_identity import (
    PERMISSIONS,
    REPOSITORY_ID,
    GitHubApp,
    NoRedirect,
    ReleaseAccessError,
)


class ReleaseIdentity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {
            "repository": "aoodie/Tauric",
            "repository_id": REPOSITORY_ID,
            "permissions": PERMISSIONS,
            "repository_selection": "selected",
            "installed": True,
            "installation_id": 169971080,
            "app_id": 5263786,
        }
        self.save()
        secret = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        (self.root / "github-app.pem").write_bytes(
            secret.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (self.root / "github-app.pem").chmod(0o600)

    def save(self):
        path = self.root / "github-app.json"
        path.write_text(json.dumps(self.config))
        path.chmod(0o600)

    def answers(self):
        return [
            {
                "app_id": 5263786,
                "account": {"login": "aoodie"},
                "repository_selection": "selected",
                "permissions": PERMISSIONS,
                "suspended_at": None,
            },
            {
                "token": "synthetic-short-lived-token",
                "permissions": PERMISSIONS,
                "repository_selection": "selected",
            },
            {
                "total_count": 1,
                "repositories": [{"id": REPOSITORY_ID, "full_name": "aoodie/Tauric"}],
            },
        ]

    def test_token_is_narrowed_and_cached_only_after_repository_verification(self):
        app = GitHubApp(self.root)
        with patch.object(app, "request", side_effect=self.answers()) as request:
            self.assertEqual(app.access_token(), "synthetic-short-lived-token")
            self.assertEqual(app.access_token(), "synthetic-short-lived-token")
            self.assertEqual(request.call_count, 3)
            self.assertEqual(
                request.call_args_list[1].kwargs["body"],
                {
                    "repositories": ["Tauric"],
                    "permissions": PERMISSIONS,
                },
            )
        self.assertNotIn(
            "synthetic-short-lived-token", (self.root / "github-app.json").read_text()
        )

    def test_extra_repository_or_changed_installation_never_yields_a_token(self):
        for changed in (
            "extra_repository",
            "suspended",
            "permissions",
            "account",
            "app",
        ):
            with self.subTest(changed=changed):
                answers = self.answers()
                if changed == "extra_repository":
                    answers[2]["total_count"] = 2
                elif changed == "suspended":
                    answers[0]["suspended_at"] = "2026-10-10T00:00:00Z"
                elif changed == "permissions":
                    answers[0]["permissions"] = {
                        **PERMISSIONS,
                        "administration": "write",
                    }
                elif changed == "account":
                    answers[0]["account"]["login"] = "another-owner"
                else:
                    answers[0]["app_id"] = 1
                app = GitHubApp(self.root)
                with (
                    patch.object(app, "request", side_effect=answers),
                    self.assertRaises(ReleaseAccessError),
                ):
                    app.access_token()
                self.assertIsNone(app.token)

    def test_foreign_repository_and_readable_keys_are_rejected(self):
        self.config["repository"] = "aoodie/Other"
        self.save()
        with self.assertRaises(ReleaseAccessError):
            GitHubApp(self.root)
        self.config["repository"] = "aoodie/Tauric"
        self.save()
        (self.root / "github-app.pem").chmod(0o644)
        with self.assertRaises(ReleaseAccessError):
            GitHubApp(self.root)

    def test_repository_path_cannot_escape_and_redirect_never_forwards_auth(self):
        app = GitHubApp(self.root)
        for path in (
            "https://another-host.test",
            "//another-host.test",
            "/../Other/git/refs",
        ):
            with self.subTest(path=path), self.assertRaises(ReleaseAccessError):
                app.repo(path)
        self.assertIsNone(
            NoRedirect().redirect_request(
                None, None, 302, "Found", {}, "https://another-host.test"
            )
        )

    def test_artifact_redirect_uses_a_fresh_request_without_repository_access(self):
        app = GitHubApp(self.root)
        redirect = HTTPError(
            "https://api.github.com",
            302,
            "Found",
            {
                "Location": "https://production.blob.core.windows.net/actions?sig=synthetic"
            },
            None,
        )
        with (
            patch.object(app, "access_token", return_value="synthetic-only-token"),
            patch.object(
                app.opener, "open", side_effect=[redirect, io.BytesIO(b"artifact")]
            ) as opened,
        ):
            self.assertEqual(app.artifact(71), b"artifact")
            first, second = [call.args[0] for call in opened.call_args_list]
            self.assertEqual(
                first.get_header("Authorization"), "Bearer synthetic-only-token"
            )
            self.assertIsNone(second.get_header("Authorization"))
        redirect = HTTPError(
            "https://api.github.com",
            302,
            "Found",
            {"Location": "https://another-host.test/private"},
            None,
        )
        with (
            patch.object(app, "access_token", return_value="synthetic-only-token"),
            patch.object(app.opener, "open", side_effect=redirect) as opened,
            self.assertRaises(ReleaseAccessError),
        ):
            app.artifact(71)
        self.assertEqual(opened.call_count, 1)


if __name__ == "__main__":
    unittest.main()
