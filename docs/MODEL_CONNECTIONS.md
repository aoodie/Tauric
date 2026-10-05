# Cloud and external-local model connections

Both routes are available in Settings. On 3 October 2026 the owner authorized using `ag/gemini-3.8-flash-high` for both quick and deep roles on the saved external-local endpoint. Its protocol was corrected to OpenAI-compatible, retaining the credential privately. A real native daily BTC run, real OpenClaw no-tool diagnostic and one real bound-context rejection review succeeded; see [connected AI verification](CONNECTED_AI_VERIFICATION.md). Cloud remains unconfigured. Fresh installations enable neither route by default. Earlier fixture verification made no paid calls; this connected verification made real provider requests. Public Binance charts need no model key.

## Configure in Settings

Open **Settings → AI connections**. Cloud AI and External local AI each have their own provider, API address, quick/deep model names and API-key controls. Save each connection independently. An empty model configuration can be saved for later.

To add a key, choose **Replace key** and enter it in the masked field. **Keep saved key** preserves it without returning it to the browser; **Clear key** removes it from the active configuration. Changing provider requires replacing or clearing an existing key. Saved keys never appear in API responses or activity records. Do not paste credentials into chat.

Connections save immediately to encrypted, immutable database revisions. The API and native worker read the same saved configuration. The private OpenClaw service applies changes automatically; no manual container restart is needed. A stale edit from another browser tab is rejected with a reload option. Saves do not start model calls. Model names and endpoint are captured on admission; changing them makes a queued run fail instead of silently using another model.

Use `openai_compatible` for an external OpenAI-compatible server. Give its complete API base URL, including `/v1` when required by that service. The native `ollama` route also uses Ollama's OpenAI-compatible API. A URL must contain no embedded credentials, query string or fragment. Model names and endpoint are captured on admission; changing them makes a queued run fail instead of silently using another model. Keys are excluded from saved requests and artifacts.

The local endpoint runs on another machine/service. `localhost` inside the worker means that container, so use a reachable private endpoint/VPN hostname instead. Tool calling and structured-output compatibility must be verified with the chosen model. A configured badge means required fields are supplied; a completed real run is the connection check.

Start the optional worker after setup:

```sh
docker compose --env-file .env -f deploy/compose.dev.yml --profile analysis up -d --build
```

## Private storage and existing host configuration

Initial setup generates a private `.env.model-key` at permissions `0600`, preserves it on subsequent setup runs, and mounts it into the API and worker. Keys and connection configuration are encrypted using this key before database insertion. Back up this key privately with the database: losing it prevents decrypting saved connections. Replacing it without a migration is not key rotation. Host administrators who possess both the database and key can decrypt the credentials.

Existing `.env.models.json` configuration remains a fallback until a profile is first saved in Settings. A saved profile takes precedence, including a profile whose active key has been cleared. The old host file is not rewritten by the UI. Encrypted prior revisions retain their previous credentials; clearing an active key does not erase history or revoke the credential at its provider.

The container bootstrap copies the read-only key mount to a private file owned by UID 10001, then drops privileges before starting the service. Missing or mismatched encryption keys block saved connections and saves; they do not cause a silent fallback to old credentials.

## Change the owner password

Open **Settings → Security**. Enter the current password, a new password of at least 12 characters and its confirmation, then select **Change password**. The new password is hashed, every existing session is revoked, and you return to sign-in with a confirmation. Sign in with the new password. Incorrect current-password attempts are rate-limited. Passwords are excluded from responses and activity records. This is a signed-in password change; forgotten-password recovery is still pending.

## Run and inspect

In the chart, choose BTC, ETH or SOL and refresh the market. Open Tauric analysis, choose Cloud AI or External local AI, then request daily analysis after its connection is configured and the worker heartbeat is ready. Debates/tool budgets can be changed through Settings, with reasons and version history.

Each request gets a durable identity, instrument/date/model/settings snapshot and optional chart-snapshot link. The worker executes the original pinned native graph. Completed runs retain original analyst reports, research/risk debate histories, trader proposal and final portfolio decision. Public reports remain unchanged and separate from linked governance records. No sizing or order submission follows a rating.

One run executes at a time, with at most four queued/running jobs and a 20-minute resource timeout. Interrupted or failed runs are not automatically retried. Native SDKs may perform their own request-level retries; this is not an exactly-once billing guarantee. Review the saved status before requesting another run. A lost heartbeat is marked interrupted when the worker reconnects and claims work. Native checkpoint files are retained, but operator-directed checkpoint resume is not implemented.

## Scope limits

Current daily crypto research only. Native providers use `BTC-USD`, `ETH-USD` and `SOL-USD`; chart observations use Binance USDT spot markets. The chart snapshot is a reference, not a substitute for native evidence. Historical replay, intraday FX, futures, model fallback and native per-call usage accounting remain later work. Requested OpenClaw paper governance and reported aggregate review usage are implemented; [governance verification](GOVERNANCE_VERIFICATION.md) records fixture evidence and real-model limits. Selecting a chart timeframe does not change the native daily research horizon.

## Request an OpenClaw paper review

Start the `governance` profile alongside `analysis`. Set the review model tiers and output/time budgets in **Settings → AI connections**, then use **Paper trading → Reviewer** to choose a route. A review needs an immutable successful native report for the same instrument and current daily research date. The configured Gateway and review worker must be ready. All resulting permission is paper-only, and the deterministic planner validates current prices, policy, ledger and expiry separately. A failed review does not automatically repeat model work. Upstream provider transports may retry individual requests, so there is no exactly-once billing guarantee. Saving/rotating a connection invalidates pending routing context and starts no model call.
