"""Repository-only GitHub App access for the fixed release service.

This module is never copied into a submitted engineering workspace with keys.
Tokens stay in memory and are scoped again on every refresh.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

PERMISSIONS = {
    "metadata": "read",
    "contents": "write",
    "pull_requests": "write",
    "actions": "write",
}
REPOSITORY = "aoodie/Tauric"
REPOSITORY_ID = 1405263842


class ReleaseAccessError(RuntimeError):
    """A public category, never a response body containing private credentials."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def base64url(value):
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=")


class GitHubApp:
    def __init__(self, credential_directory: Path):
        self.directory = credential_directory
        self.config = json.loads((credential_directory / "github-app.json").read_text())
        if (
            self.config.get("repository") != REPOSITORY
            or self.config.get("repository_id") != REPOSITORY_ID
            or self.config.get("permissions") != PERMISSIONS
            or self.config.get("repository_selection") != "selected"
            or not self.config.get("installed")
        ):
            raise ReleaseAccessError("The approved repository installation is required.")
        for path in (credential_directory / "github-app.json", credential_directory / "github-app.pem"):
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ReleaseAccessError("Release credentials must be private regular files.")
        self.token = None
        self.token_deadline = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def jwt(self):
        stamp = int(time.time())
        key = serialization.load_pem_private_key(
            (self.directory / "github-app.pem").read_bytes(), password=None
        )
        header = base64url(b'{"alg":"RS256","typ":"JWT"}')
        body = base64url(json.dumps({
            "iat": stamp - 60, "exp": stamp + 540, "iss": str(self.config["app_id"])
        }, separators=(",", ":")).encode())
        message = header + b"." + body
        return (message + b"." + base64url(key.sign(message, padding.PKCS1v15(), hashes.SHA256()))).decode()

    def request(self, path, *, method="GET", body=None, auth=None):
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise ReleaseAccessError("Only a fixed GitHub API path is allowed.")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": "Bearer " + (auth or self.access_token()),
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Tauric-fixed-release-service",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(20 * 1024**2 + 1)
                if len(data) > 20 * 1024**2:
                    raise ReleaseAccessError("GitHub response exceeded the release allowance.")
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            # A redirect must never forward an installation token to another host.
            raise ReleaseAccessError("GitHub request returned HTTP " + str(error.code), status=error.code) from None
        except (urllib.error.URLError, TimeoutError):
            raise ReleaseAccessError("GitHub transport outcome requires reconciliation.") from None

    def access_token(self):
        if self.token and time.monotonic() < self.token_deadline:
            return self.token
        jwt = self.jwt()
        installation = self.request(
            "/app/installations/" + str(self.config["installation_id"]), auth=jwt
        )
        if (
            installation.get("app_id") != self.config["app_id"]
            or installation.get("account", {}).get("login", "").lower() != "aoodie"
            or installation.get("repository_selection") != "selected"
            or installation.get("permissions") != PERMISSIONS
            or installation.get("suspended_at") is not None
        ):
            raise ReleaseAccessError("Repository installation identity or permissions changed.")
        issued = self.request(
            "/app/installations/" + str(self.config["installation_id"]) + "/access_tokens",
            method="POST", auth=jwt,
            body={"repositories": ["Tauric"], "permissions": PERMISSIONS},
        )
        if issued.get("permissions") != PERMISSIONS or issued.get("repository_selection") != "selected":
            raise ReleaseAccessError("Issued access was not restricted as requested.")
        repositories = self.request("/installation/repositories", auth=issued["token"])
        if (
            repositories.get("total_count") != 1
            or len(repositories.get("repositories", [])) != 1
            or repositories["repositories"][0].get("id") != REPOSITORY_ID
            or repositories["repositories"][0].get("full_name", "").lower() != REPOSITORY.lower()
        ):
            raise ReleaseAccessError("The App must have access to Tauric only.")
        self.token = issued["token"]
        # Refresh conservatively; no token or authenticated response is persisted.
        self.token_deadline = time.monotonic() + 45 * 60
        return self.token

    def repo(self, path, **kwargs):
        if not path.startswith("/") or ".." in path.split("/") or path.startswith("//"):
            raise ReleaseAccessError("Invalid repository API path.")
        return self.request("/repos/" + REPOSITORY + path, **kwargs)
