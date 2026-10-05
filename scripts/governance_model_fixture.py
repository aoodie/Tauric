"""An internal test-only OpenAI SSE fixture, never a real research/model service."""

import json
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
metrics = []


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_content(v) for v in value)
    if isinstance(value, dict):
        return value.get("text", "")
    return ""


def bound(value):
    if isinstance(value, dict):
        if "context_checksum" in value and "context" in value:
            return value
        for item in value.values():
            found = bound(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = bound(item)
            if found:
                return found
    if isinstance(value, str):
        try:
            return bound(json.loads(value))
        except ValueError:
            return None
    return None


@app.post("/v1/chat/completions")
async def completion(request: Request):
    body = await request.json()
    expected = (
        "fixture-rotated-key"
        if body["model"].endswith("malformed")
        else "fixture-key-not-a-real-credential"
    )
    if request.headers.get("authorization") != "Bearer " + expected:
        raise HTTPException(401, "Fixture credential handoff failed")
    messages = body["messages"]
    tool_names = [t["function"]["name"] for t in body.get("tools", [])]
    metrics.append(
        {
            "model": body["model"],
            "tools": tool_names,
            "tool_response": any(m["role"] == "tool" for m in messages),
        }
    )
    assert tool_names == ["tauric_review_context"], (
        "Fixture requires the single scoped project tool"
    )
    content = None
    for message in reversed(messages):
        if message["role"] == "tool":
            content = bound(message.get("content"))
            break
    if content:
        value = {
            "job_id": content["job_id"],
            "context_checksum": content["context_checksum"],
            "proposal_id": content["context"]["proposal"]["id"],
            "policy_id": content["context"]["policy_id"],
            "outcome": "allow",
            "reason": "ISOLATED MODEL FIXTURE: paper governance transport test, not investment research.",
        }
        delta = {
            "content": "malformed fixture result"
            if body["model"].endswith("malformed")
            else json.dumps(value)
        }
        finish = "stop"
    else:
        user = "\n".join(
            text_content(m.get("content")) for m in messages if m["role"] == "user"
        )
        job_id = re.search(r'"job_id"\s*:\s*"([0-9a-f-]{36})"', user).group(1)
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_fixture_" + job_id,
                    "type": "function",
                    "function": {
                        "name": "tauric_review_context",
                        "arguments": json.dumps({"job_id": job_id}),
                    },
                }
            ]
        }
        finish = "tool_calls"

    def events():
        base = {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 1790985600,
            "model": body["model"],
        }
        yield (
            "data: "
            + json.dumps(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", **delta},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            + "\n\n"
        )
        yield (
            "data: "
            + json.dumps(
                {
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 20,
                        "total_tokens": 40,
                    },
                }
            )
            + "\n\n"
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/metrics")
def stats():
    return {"calls": metrics}
