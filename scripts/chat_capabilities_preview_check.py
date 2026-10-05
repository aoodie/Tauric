"""Verify deployed capability facts without model calls or research dispatch."""

import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
compose = [
    "docker",
    "compose",
    "--env-file",
    str(root / ".env"),
    "-f",
    str(root / "deploy/compose.dev.yml"),
    "exec",
    "-T",
]
files = [
    "chat_contracts.py",
    "chat_research.py",
    "chat_worker.py",
    "openclaw_api.py",
    "chat_instructions.txt",
]
expected = {
    name: hashlib.sha256((root / "backend/tauric" / name).read_bytes()).hexdigest()
    for name in files
}
for service in ("api", "chat_worker"):
    code = (
        'import hashlib,json;from pathlib import Path;print(json.dumps({name:hashlib.sha256((Path("/app/tauric")/name).read_bytes()).hexdigest() for name in '
        + repr(files)
        + "}))"
    )
    result = subprocess.run(
        compose + [service, "python", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        "Deployed source check failed; private output withheld"
    )
    assert json.loads(result.stdout) == expected, f"Chat source mismatch: {service}"

gateway_code = """
const fs=require('node:fs'),crypto=require('node:crypto');
const digest=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const tokens=JSON.parse(fs.readFileSync('/state/service-tokens.json','utf8'));
fetch('http://api:8000/internal/openclaw/chat-context',{headers:{Authorization:'Bearer '+tokens.platform_token}})
.then(async response=>{
 if(!response.ok)throw Error('Context unavailable');
 const c=await response.json();
 console.log(JSON.stringify({instructions:digest('/app/chat_instructions.txt'),workspace_instructions:digest('/state/chat-workspace/AGENTS.md'),plugin:digest('/app/plugin/dist/index.js'),authority:c.authority,live_execution_enabled:c.live_execution_enabled,capabilities:c.research_capabilities,web_ready:c.chat_connections.web_ready}));
}).catch(()=>{console.error('Private deployed context check failed');process.exitCode=1});
"""
result = subprocess.run(
    compose + ["openclaw", "node", "-e", gateway_code],
    check=False,
    capture_output=True,
    text=True,
    timeout=30,
)
assert result.returncode == 0, "Gateway capability read failed; private output withheld"
data = json.loads(result.stdout)
assert (
    data["instructions"]
    == data["workspace_instructions"]
    == expected["chat_instructions.txt"]
)
assert (
    data["plugin"]
    == hashlib.sha256((root / "openclaw/plugin/dist/index.js").read_bytes()).hexdigest()
)
assert data["authority"] == "read_only" and data["live_execution_enabled"] is False
capabilities = data["capabilities"]
assert {"weekly", "commodities"}.issubset(capabilities["unsupported_scopes"])
rows = capabilities["registered_instruments"]
assert any(
    r["asset_type"] == "fx"
    and r["daily_native_research"]
    and not r["chat_request_enabled"]
    for r in rows
)
assert any(
    r["asset_type"] in {"equity", "etf"} and r["chat_request_enabled"] for r in rows
)
assert data["web_ready"], "Chat has not become ready yet"
with urllib.request.urlopen(
    "http://127.0.0.1:8080/health/ready", timeout=10
) as response:
    assert json.load(response)["status"] == "ok"
evidence = {
    "read_only": True,
    "model_calls": 0,
    "research_requests": 0,
    "readiness": True,
    "backend_services": 2,
    "source_files": len(files),
    "gateway_plugin_verified": True,
    "workspace_instructions_verified": True,
    "registered_count": capabilities["registered_count"],
    "forex_daily_screen_supported": True,
    "stock_etf_chat_supported": True,
    "weekly_and_commodities_supported": False,
}
(root / "artifacts/chat-capabilities-preview-verification.json").write_text(
    json.dumps(evidence, indent=2) + "\n"
)
print(json.dumps(evidence))
