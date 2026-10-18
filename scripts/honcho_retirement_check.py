"""Run in the Tauric API container; synthetic workspaces only, no model calls."""

import json
import time
from types import SimpleNamespace
from uuid import uuid4

from tauric import honcho_memory


class CommitOnly:
    def commit(self):
        pass


provider = honcho_memory.private_config()
assert provider, "Local Honcho is not configured"
old = "tauric-validation-retire-" + uuid4().hex[:16]
current = "tauric-validation-current-" + uuid4().hex[:16]
names = [old, current]
state = SimpleNamespace(honcho_retired_workspaces=[{"id": old, "requested": False}])
for name in names:
    honcho_memory.request(provider, "POST", "/v3/workspaces", {"id": name})
prefix = "/v3/workspaces/" + old
honcho_memory.request(
    provider,
    "POST",
    prefix + "/peers",
    {"id": "owner", "configuration": {"observe_me": False}},
)
honcho_memory.request(
    provider,
    "POST",
    prefix + "/sessions",
    {"id": "synthetic-chat", "peers": {"owner": {"observe_me": False}}},
)
honcho_memory.retire_workspaces(CommitOnly(), state, provider)
assert state.honcho_retired_workspaces == [{"id": old, "requested": True}]
live = honcho_memory.request(
    provider, "POST", "/v3/workspaces/list?size=1", {"filters": {"id": current}}
)["items"]
assert len(live) == 1 and live[0]["id"] == current
honcho_memory.request(provider, "DELETE", "/v3/workspaces/" + current)
removed = set()
for _ in range(8):
    for name in names:
        listed = honcho_memory.request(
            provider, "POST", "/v3/workspaces/list?size=1", {"filters": {"id": name}}
        )["items"]
        if not listed:
            removed.add(name)
    if len(removed) == len(names):
        break
    time.sleep(5)
assert len(removed) == len(names), "Synthetic background deletion did not complete"
honcho_memory.retire_workspaces(CommitOnly(), state, provider)
assert not state.honcho_retired_workspaces
print(
    json.dumps(
        {
            "active_session_retired": True,
            "workspace_retirement_accepted": True,
            "distinct_current_workspace_preserved": True,
            "asynchronous_cleanup_completed": True,
            "synthetic_workspaces_removed": sorted(removed),
            "model_requests": 0,
            "owner_records_modified": 0,
        }
    )
)
