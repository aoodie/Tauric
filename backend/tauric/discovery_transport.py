"""One bounded provider request. No database, retries, logs or credential arguments."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

DEADLINE_SECONDS = 30


class ProviderFailure(Exception):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic["category"])


def fetch(request):
    diagnostic = {}
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", request["url"], headers=request["headers"], json=request["body"]) as response:
                diagnostic["http_status"] = response.status_code
                identity = response.headers.get("x-request-id", response.headers.get("request-id", ""))
                secret = request["headers"].get("Authorization", "").removeprefix("Bearer ")
                if (re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", identity)
                        and not (secret and (identity in secret or secret in identity))):
                    diagnostic["provider_request_id"] = identity
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 64000:
                        return {"error": {**diagnostic, "category": "response_too_large"}}
        return {"payload": json.loads(raw), "diagnostic": diagnostic}
    except httpx.HTTPStatusError:
        category = "http_status"
    except httpx.TimeoutException:
        category = "transport_timeout"
    except httpx.HTTPError:
        category = "transport_error"
    except (ValueError, TypeError):
        category = "invalid_json"
    return {"error": {**diagnostic, "category": category}}


def request_json(url, headers, body):
    # A process deadline also bounds a provider that continually drips bytes.
    # Only this trusted transport source is executed; secrets travel through stdin.
    child = subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).resolve())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        output, _ = child.communicate(json.dumps({"url": url, "headers": headers, "body": body}).encode(), timeout=DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise ProviderFailure({"category": "overall_deadline"}) from None
    if child.returncode or len(output) > 100000:
        raise ProviderFailure({"category": "transport_process"})
    try:
        result = json.loads(output)
    except ValueError:
        raise ProviderFailure({"category": "transport_protocol"}) from None
    if "error" in result:
        raise ProviderFailure(result["error"])
    return result["payload"], result["diagnostic"]


if __name__ == "__main__":
    try:
        result = fetch(json.load(sys.stdin))
    except Exception:
        result = {"error": {"category": "transport_process"}}
    print(json.dumps(result, separators=(",", ":")))
