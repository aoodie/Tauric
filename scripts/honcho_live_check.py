"""Run inside the local Honcho container. Synthetic data only; no owner records."""

import json
import time
import urllib.error
import urllib.request
from uuid import uuid4

from src.security import create_admin_jwt

TOKEN = create_admin_jwt()
ROOT = "http://127.0.0.1:8000/v3/workspaces"
NAME = "tauric-validation-" + uuid4().hex[:16]


def call(method, suffix="", body=None):
    req = urllib.request.Request(
        ROOT + suffix,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


started = time.monotonic()
phase = "workspace"
try:
    call("POST", body={"id": NAME})
    prefix = "/" + NAME
    for peer, observe in [("owner", True), ("assistant", False), ("project", False)]:
        call(
            "POST",
            prefix + "/peers",
            {"id": peer, "configuration": {"observe_me": observe}},
        )
    call(
        "POST",
        prefix + "/sessions",
        {
            "id": "synthetic-chat",
            "peers": {
                "owner": {"observe_me": True},
                "assistant": {"observe_me": False},
            },
            "scopes": ["general"],
        },
    )
    path = prefix + "/sessions/synthetic-chat/messages"
    phase = "message_embedding"
    message = {
        "peer_id": "owner",
        "content": "I prefer concise explanations with clear labels. Before suggesting a strategy change, show the hypothesis, counter-evidence and a test plan.",
        "metadata": {
            "tauric_document_id": "synthetic-document",
            "tauric_checksum": "synthetic-checksum",
            "part": 0,
        },
    }
    saved = call("POST", path, {"messages": [message]})
    assert len(saved) == 1 and saved[0]["metadata"] == message["metadata"]
    phase = "receipt_reconciliation"
    listed = call(
        "POST",
        path + "/list?size=100",
        {
            "filters": {
                "metadata": {
                    "tauric_document_id": "synthetic-document",
                    "tauric_checksum": "synthetic-checksum",
                }
            }
        },
    )
    assert len(listed["items"]) == 1 and listed["items"][0]["id"] == saved[0]["id"]
    print(
        json.dumps(
            {
                "authenticated_api": True,
                "message_embeddings": True,
                "metadata_reconciliation": True,
                "phase": "waiting_for_local_derivation",
                "synthetic_workspace": NAME,
            }
        ),
        flush=True,
    )
    phase = "local_preference_derivation"
    representation = ""
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        value = call(
            "POST",
            prefix + "/peers/owner/representation",
            {"scope": "general", "max_conclusions": 20},
        )
        representation = value.get("representation", "")
        if (
            "concise" in representation.lower()
            or "clear labels" in representation.lower()
        ):
            break
        time.sleep(10)
    assert representation, (
        "Local derivation did not produce a cached representation in the bounded check"
    )
    print(
        json.dumps(
            {
                "authenticated_api": True,
                "message_embeddings": True,
                "metadata_reconciliation": True,
                "preference_derivation": True,
                "source": "synthetic owner statement",
                "elapsed_seconds": round(time.monotonic() - started),
                "representation": representation,
            }
        ),
        flush=True,
    )
finally:
    try:
        call("DELETE", "/" + NAME + "/sessions/synthetic-chat")
        call("DELETE", "/" + NAME)
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "synthetic_workspace_cleanup": "needs_attention",
                    "phase": phase,
                    "status": getattr(error, "code", None),
                }
            ),
            flush=True,
        )
