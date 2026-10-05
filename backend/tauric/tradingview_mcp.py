"""Bounded official MCP transport; only three economic read tools are callable."""

import json
import re
import secrets
import time

import httpx

from .tradingview_connection import SERVER

READ_TOOLS = {"get_economic_calendar", "get_economic_symbols", "get_economic_data"}


def result_payload(result):
    if not isinstance(result, dict) or result.get("isError"):
        raise ValueError("Provider tool failed")
    if "structuredContent" in result:
        data = result["structuredContent"]
    else:
        content = result.get("content", [])
        texts = [v["text"] for v in content if v.get("type") == "text"]
        if len(texts) != 1 or len(texts[0]) > 1_000_000:
            raise ValueError("Expected bounded structured economic result")
        data = json.loads(texts[0])
    if isinstance(data, dict) and (
        data.get("success") is False or data.get("partial") is True or data.get("error")
    ):
        raise ValueError("Partial or failed economic result")
    return data


class EconomicMCP:
    def __init__(self, token):
        self.token, self.session, self.version, self.tools = token, None, "2025-06-18", {}
        self.client = httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False)

    def __enter__(self):
        try:
            initialized = self.rpc(
                "initialize",
                {
                    "protocolVersion": self.version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "Tauric read-only economic research",
                        "version": "0.11.0",
                    },
                },
            )
            version = initialized.get("protocolVersion")
            if not isinstance(version, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", version):
                raise ValueError("Unsupported MCP handshake")
            self.version = version
            self.rpc("notifications/initialized", {}, notification=True)
            catalog = self.rpc("tools/list", {})
            tools = catalog.get("tools")
            if not isinstance(tools, list) or len(tools) > 200:
                raise ValueError("Invalid tool catalog")
            for requested in READ_TOOLS:
                matches = [
                    t
                    for t in tools
                    if isinstance(t.get("name"), str)
                    and re.search(
                        r"(?:^|[_.:/-])"
                        + r"[-_]".join(map(re.escape, requested.split("_")))
                        + r"$",
                        t["name"],
                    )
                    and t.get("annotations", {}).get("readOnlyHint") is not False
                ]
                if len(matches) != 1:
                    raise ValueError("Required read-only tool unavailable")
                self.tools[requested] = matches[0]["name"]
            return self
        except Exception:
            self.client.close()
            raise

    def __exit__(self, *args):
        self.client.close()

    def rpc(self, method, params, notification=False):
        identity = secrets.token_hex(8)
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = identity
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.version,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        started, buffer = time.monotonic(), b""
        with self.client.stream("POST", SERVER, json=body, headers=headers) as response:
            response.raise_for_status()
            session = response.headers.get("Mcp-Session-Id")
            if session:
                if len(session) > 1000 or any(ord(c) < 33 or ord(c) > 126 for c in session):
                    raise ValueError("Invalid private MCP session")
                self.session = session
            if notification:
                if response.status_code not in {200, 202, 204}:
                    raise ValueError("MCP notification rejected")
                return None
            streaming = "text/event-stream" in response.headers.get("content-type", "")
            for chunk in response.iter_bytes():
                buffer += chunk
                if len(buffer) > 1_000_000 or time.monotonic() - started > 20:
                    raise ValueError("MCP response exceeds bounds")
                if streaming:
                    normalized = buffer.replace(b"\r\n", b"\n")
                    for event in normalized.split(b"\n\n")[:-1]:
                        text = b"\n".join(
                            line[5:].lstrip()
                            for line in event.splitlines()
                            if line.startswith(b"data:")
                        )
                        if text:
                            value = json.loads(text)
                            if value.get("id") == identity:
                                return self.checked(value, identity)
            if streaming:
                raise ValueError("MCP stream contains no matching result")
            return self.checked(json.loads(buffer), identity)

    @staticmethod
    def checked(value, identity):
        if (
            not isinstance(value, dict)
            or value.get("jsonrpc") != "2.0"
            or value.get("id") != identity
            or value.get("error")
        ):
            raise ValueError("MCP response binding failed")
        result = value.get("result")
        if not isinstance(result, dict):
            raise ValueError("MCP result missing")
        return result

    def call(self, name, arguments):
        if name not in READ_TOOLS or name not in self.tools:
            raise ValueError("Only allowlisted economic reads are permitted")
        return result_payload(
            self.rpc("tools/call", {"name": self.tools[name], "arguments": arguments})
        )
