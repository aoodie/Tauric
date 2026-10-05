# OpenClaw chat

Development 0.9.0 adds research requests to **OpenClaw chat** and **Settings → Chat connections**. The separately versioned project OpenClaw package is 0.6.0; its upstream remains pinned to 2026.9.7. Tauric's native analysis graph is unchanged.

## Web

Start the development services with the governance profile enabled:

```sh
docker compose --env-file .env -f deploy/compose.dev.yml --profile analysis --profile governance up -d --build
```

Sign in, refresh the workstation, then select **OpenClaw chat**. A saved cloud or external-local model connection, the private Gateway and the chat worker must be ready. The initial chat route is external-local, matching the connected owner model. Change it in **Settings → Chat connections**. Model tiers, timeout and output budgets use the existing OpenClaw settings in **AI connections**; saving settings makes no model call.

Send a message or create a new conversation. PostgreSQL stores web requests and replies; reopening a conversation uses its stable private OpenClaw session. The UI lists the latest 50 conversations and loads their latest 60 turns; older records remain stored. Web, Telegram and Discord histories are separate. Channel session data lives in the persistent private OpenClaw state volume.

Chat can explain Tauric, read status and original reports, and queue current daily research for supported registered crypto, stocks and ETFs when the owner explicitly asks. Every web turn and read-only context includes fresh registry-backed research capabilities. Forex daily analysis is available for supported registered pairs from the analysis screen; chat dispatch remains deferred. Commodities such as XAU/USD and weekly reports are not implemented. Try “Run daily BTC research”, then ask for the run's status. Web chat shows the real queued/running/terminal state and opens the original report when available. Native daily USD research remains distinct from intraday USDT execution; unsupported commodity, weekly, intraday, historical or futures requests must not silently become daily research.

**Settings → Chat connections → New research runs per UTC day** controls the shared web/Telegram/Discord allowance (initially 3, range 0–50). It is a run allowance, not a monetary spending cap. Set 0 to stop new chat research. Identical instrument/date/AI connection/settings requests reuse their existing chat research job across channels, including failed or interrupted jobs, without a hidden model retry or extra allowance charge. Changing configuration revisions does not reset today's count. New work also uses the existing native queue and worker readiness checks. Direct requests from the analysis screen are outside this chat allowance.

Chat cannot submit orders, issue governance permissions, edit settings, run commands or contact other people. Research dispatch never creates a paper proposal, review, order or fill.

## Telegram

1. Create a dedicated bot with Telegram's **@BotFather** and obtain its bot token.
2. Identify your **personal numeric Telegram user ID**, rather than your phone number, username or the bot ID. For a dedicated bot that is still disabled here, the official Bot API `getUpdates` response can show the `message.from.id` of a message you send to that bot. Inspect it privately; do not paste tokens or the response in chat. Confirm it belongs to your own message.
3. In **Chat connections**, choose **Enter new token**, enter the token and your user ID, and enable **Telegram owner DMs**. Save.
4. After the private configuration applies, send your bot an ordinary text DM, such as “Hello OpenClaw”.

OpenClaw uses outbound long polling, so this setup needs no public webhook. Another service must not poll with the same bot token. Group messages, introductions and native/text configuration commands are disabled. Only the configured numeric sender is admitted.

## Discord

1. Create an application and bot in the **Discord Developer Portal**, and obtain its bot token.
2. Enable **Message Content Intent** in its Bot settings. Invite the bot to your server using the **bot** OAuth2 scope. Ensure your Discord privacy settings allow DMs from that server's members.
3. Enable **Developer Mode** in your Discord account's advanced settings. Right-click your own profile and select **Copy User ID**.
4. Save the token, your numeric user ID and **Discord owner DMs** in Tauric's Chat connections.
5. Send the bot an ordinary text DM after the configuration applies.

OpenClaw connects outward to the Discord Gateway. Only the configured owner may DM it. Server channel messages and bot senders are disabled. No privileged slash-command or remote configuration interface is exposed.

## Storage and failures

Bot credentials use the host's existing Fernet encryption key and immutable encrypted configuration versions in PostgreSQL. Keep/replace/clear actions are explicit; API responses contain only a has-token flag. Saves use revision checks and record redacted audit metadata. Backups must preserve the database, matching private key and OpenClaw state volume.

Gateway/operator tokens stay behind the private service boundary. Caddy exposes only the authenticated Tauric API. Chat's tool allowlist contains `tauric_chat_context`, `tauric_request_research` and `tauric_research_status`. The request tool binds channel, sender, owner status, session and call ID from the pinned SDK's trusted factory context, not model arguments, and checks the invocation guard before its network effect. Tauric checks the active web-turn lease or the currently configured native owner's direct-message session. Group, cron, other-agent and other-owner requests fail closed. A changed saved AI connection revision rejects queued chat research before native execution.

`tauric_review_context` remains exclusive to governance agents. The chat workspace has its own instructions and no shell, file, order or settings tools. Reports and tool text are untrusted evidence, never permission to dispatch more work.

Web requests have a four-message queue, one leased worker and a single pending reply per conversation. Request identifiers prevent duplicate admissions after a browser/network retry. Lost leases and failures become visible terminal records; no automatic model retry is made. A configuration change before execution rejects queued work. Gateway configuration changes may interrupt active work; the owner can explicitly send a new message afterward.

**Configured is not connected or delivery verified.** External bot tokens and sender IDs have not been supplied on the actual owner account. Settings therefore report channel configuration without claiming a working external connection. End-to-end Telegram and Discord delivery must be checked after those credentials are saved. This milestone does not make the full platform ready for server installation.

## Verification

- The full regression suite passed 218 tests, including 21 chat research checks and 13 chat checks. Coverage includes owner/CSRF restrictions, encrypted credentials, identity/scope rejection, active leases, cross-channel reuse, shared UTC allowances, queue/worker/model failures without charges, immutable admissions, original report links, connection invalidation and failed-run reuse without retries.
- Seven browser flows passed with a disposable database and explicitly offline transport: send/reply, conversation reload, research job cards/original reports/failures, both bot forms and allowance settings, wildcard rejection, password settings and desktop/mobile layouts. These fixtures make no model or messaging calls.
- The actual pinned OpenClaw CLI accepts both bot configurations, explicit chat-agent bindings and separate tool policies. The compiled plugin passes real SDK registration and execution checks offline.
- A real owner web request called the trusted research factory, queued native job `fa621abf-02fa-45f5-8610-f7a03af4f889`, and returned its correct ID in the saved chat reply. Native research completed in 251 seconds with all nine original report fields; artifact `6f5e5de7-fa09-417d-a605-217bf8a10572` has checksum `28090c22bed7fdd314ebf7b455b6d124122e2ce5b4bf9b556aaed43b7069b8b4`. The chat used 11,435 reported tokens and charged one of the three daily runs. Existing native artifact checksums and zero owner financial records remained unchanged. Sanitized evidence is retained privately in `artifacts/real-chat-research-results.json`. This verifies real web dispatch and completion; native bot delivery is still unverified.
- A real web-chat reply used the configured `ag/gemini-3.8-flash-high` model through the private Gateway and leased worker. The assistant called the actual read-only context tool and correctly reported web ready, both external channels disabled, and zero owner paper accounts/strategy versions/candidates. A second real reply correctly recalled the first message, verifying conversation continuity. The two calls reported 10,832 and 6,188 total tokens. The sanitized local artifact `artifacts/real-chat-results.json` records turn IDs, usage and the unchanged native-report checksum.
- TypeScript/production builds, generated API contracts, frontend formatting and changed-file backend lint/format checks passed. Chat research admissions use migration `0011_chat_research`; concurrent research/strategy stages extend the local migration chain.
