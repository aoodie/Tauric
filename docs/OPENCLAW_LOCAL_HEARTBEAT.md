# Local OpenClaw heartbeat

The local Docker workstation can run OpenClaw's service heartbeat using the installed Qwen3 4B weights through a dedicated `tauric-heartbeat:4b` alias. The optional `deploy/compose.memory-app.yml` joins OpenClaw to the private memory network and sets `OPENCLAW_LOCAL_HEARTBEAT=1`. Without this extension the heartbeat and scheduler remain disabled. The hosted server has not been changed.

## Behavior and authority

A dedicated `tauric-heartbeat` agent runs every 30 minutes. It uses the bundled native Ollama provider at `http://memory-model:11434`, an 8,192-token context, thinking requested off, at most 1,024 output tokens, a fresh session and a 600-second timeout. There is no remote fallback. Its only project tool is `tauric_heartbeat_status`, a fixed read-only request to Tauric's `/health/ready` endpoint. The result contains only check time, readiness, HTTP status and scope; no credentials or returned error bodies enter model context.

Healthy checks reply `NO_REPLY`. Delivery is internal (`target: none`); results and errors are retained in OpenClaw's heartbeat and automation state, without Telegram/Discord notifications. A failed check does not authorize a repair, broker action or policy change. API and migrated-storage readiness does not establish worker health, successful research, trading readiness or completed autonomous project ownership.

Chat, paper governance and broker governance keep their current models, tools and financial controls. The dedicated agent has no action, shell, broker or owner-chat tools. Honcho continues to share the same local model service, which serializes inference. Its `tauric-memory:4b` alias is unchanged. `deploy/Heartbeat.Modelfile` reuses those weights with a heartbeat-specific template that closes the initial thinking prefix; Qwen can still emit some reasoning before a tool call. The download setup creates both aliases. The local OpenClaw memory ceiling is 2 GiB because the original 1 GiB ceiling caused an observed OOM during verification.

OpenClaw 2026.9.7 requires its scheduler for recurring heartbeats. This configuration disables built-in autonomous Workshop maintenance, built-in memory dreaming, missed-job catch-up and event triggers so heartbeat enablement does not opt into unrelated background work. Existing dormant Workshop review jobs are reconciled as disabled. A pre-existing orphan `Memory Dreaming Promotion` row remains visible but cannot execute: it has no resolvable agent under explicit ownership and its memory plugin is disabled. The runtime rejects attempts even to edit that orphan without an owner; it is not assigned an owner or granted authority here.

## Validation and operations

The configuration and plugin suites cover the disabled default, configured chat/governor routes, isolated heartbeat model policy, registered Ollama provider, restricted readiness tool, failures, cancellation and current management contracts. The built image runs pinned-schema validation in all three configurations plus plugin compilation and validation. Real scheduler verification must additionally show a completed local `/api/chat` request and a real readiness-tool result; a saved configuration or submitted run is insufficient.

For the standard local build, use the existing `deploy/Dockerfile.openclaw` with the memory Compose extension. During the 10 October change, some older host files were unavailable from local storage, so the tested source snapshot was built on the exact already-installed image and deployed through Compose. The tested image is also tagged with the normal local Compose image name, so ordinary service recreation uses it. The private build snapshot, source manifest, tested image override and pre-change recovery override are under `artifacts/private/local-heartbeat-*`. Never publish that private directory or the OpenClaw state backup.

Inspect the schedule with `openclaw cron list --all --json` inside the OpenClaw container, and the latest heartbeat with `openclaw system heartbeat last --json`. To test the actual scheduler, manually run the `heartbeat-tauric-heartbeat` job by its returned ID and wait for completion. To disable this feature, remove the memory extension's `OPENCLAW_LOCAL_HEARTBEAT` value or set it to `0` and recreate only OpenClaw. Keep the private network if another use requires it. The saved model connection and memory privacy settings are not changed by this feature.

## Verified local deployment — 10 October 2026

The final real scheduler run completed successfully in 176.9 seconds. The dedicated agent used `tauric-heartbeat:4b`, issued two read-only readiness calls (despite the prompt asking for one), received HTTP 200 / `ready: true`, and finished normally with `NO_REPLY`. No owner notification was requested. This proves a complete local model/tool heartbeat, rather than just configuration or submission. A future automatic cadence tick has not yet been observed; the enabled 1,800,000 ms schedule and its next run are persisted.

- Image identity: `sha256:3ef781ff9176b44250789b6ccd0b49a868501ba2a2eb7994eb4a3e29b3ce14c3`.
- Tested image source manifest: `3c2301574134ba1df2e72dddb279aad4b6466dae8de6ab5717a8990c4b46d72c`.
- Deployed configuration, bootstrap and plugin source checksums match the tested host source.
- Gateway healthy, no OOM after recovery, local memory ceiling 2 GiB.
- Existing memory-model weights and alias preserved; the heartbeat alias shares the same Qwen weights.
- Database backup verified by restore-directory listing; runtime state archive and old image retained for recovery. No schema changes.
- Sanitized runtime/schedule/tool evidence and deployment-file checksums: `artifacts/private/local-heartbeat-runtime-verification.json`.

The temporary broad schedule-disable proposal was rejected by automatic review and was not executed. Configuration reconciliation disabled only built-in Workshop jobs. The pre-existing unowned memory job remains fail-closed as described above. Routine hosted deployment and full autonomous ownership were not performed by this local heartbeat change.


## Dashboard integration — 10 October 2026

The local dashboard now includes a clickable heartbeat indicator in its status bar and a dedicated panel under System status. It displays the local Qwen3 4B model, cadence, last run and readiness result, manual/automatic origin, check time and next scheduled check. It refreshes every 15 seconds while visible. In-progress checks show “Checking”; failed, interrupted, unverified, overdue, paused, disabled and unavailable telemetry never receive the healthy indicator. Readiness covers the API and migrated storage only.

The owner-authenticated `GET /api/v1/chat/heartbeat` endpoint reads a private service-authenticated telemetry endpoint on OpenClaw port 18790. This port is not published on the host. The adapter reads only the dedicated heartbeat agent's scheduler receipts and readiness-tool evidence, through read-only SQLite connections using the pinned OpenClaw schema. It exposes neither conversation text nor service credentials and does not run a model or alter scheduling. Unsupported or corrupt state fails closed as unavailable.

The earlier manual-only observation above is superseded: an automatic 13:51 UTC run was observed with real readiness evidence and normal completion. During this dashboard release, a later overdue request was recovered through the existing local service restart process. Its interrupted record and a skipped verification attempt were preserved. The subsequent bounded verification completed successfully at 19:50:01 UTC, with local Qwen3 4B, a real HTTP 200 readiness result and normal scheduler completion. The next persisted check was 20:01:42 UTC.

The installed dashboard uses the previously deployed UI version as its release base, with only heartbeat visibility added. The newer checked-out ActivityFeed also embeds the panel for its next full release. This change is installed in the local Docker stack; the hosted server has not been updated. The database restore directory and stopped OpenClaw state archive were verified before installation; the previous service images remain tagged for rollback. Exact source, image and backup identities and sanitized runtime evidence are recorded under `artifacts/private/heartbeat-dashboard-*`.
