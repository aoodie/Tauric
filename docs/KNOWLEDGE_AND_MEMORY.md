# Private knowledge and learning

The Knowledge destination contains a local research wiki, explicit owner preferences and memory controls. It records completed web chats, saved strategy versions, datasets/backtests and OpenClaw research notes. The original Tauric records stay authoritative. Native analysis reports remain in their original research reader; the wiki is not a substitute for those reports.

## Owner workflow

1. Open **Knowledge → Library** to search keywords or filter by category. Add a note with the hypothesis, evidence, counter-evidence and next test.
2. Open **Your preferences** to write an explicit preference and inspect Honcho's inferred patterns. Inferences can be wrong; they do not grant account permissions or establish a strategy's profitability.
3. Open **Memory controls** to choose which sources to record and whether to share new copies with local Honcho. Recording and learning default off for a new installation. The owner authorized them for this local preview.
4. Open the displayed folder as an Obsidian vault. Put new Markdown files into **Inbox** to import them into the wiki. Exported copies are organized into Strategies, Chats, Research, Preferences and Notes, with readable titles and stable record references in their filenames. Existing edits are preserved; copy an edited export into Inbox to make it a separate searchable note.
5. Hide a note from recall to exclude it from future retrieval. Source-selection changes invalidate the cached profile immediately and rebuild this owner's local Honcho workspace during the next successful sync. Obsolete Honcho copies are retired in the background, with retries; their workspace identity is never reused. Original records and Obsidian files are preserved. This is exclusion, not deletion of the original chat or research record.

Files are under the ignored, private `.env.knowledge/Obsidian/<owner-id>` directory. The outer directory is owner-only. Exported files belong to the host owner so Obsidian can edit them. Do not put credentials into notes. Redaction removes common credential patterns, but it is not a guarantee that arbitrary secrets can be recognized.

## Privacy and authority

The current OpenClaw profile labelled `local` actually points to a remote API. The owner chose **local-only memory retrieval**. Automatic memory injection and `knowledge`/`user_memory` tool reads are blocked for that endpoint. The local Knowledge page and Obsidian remain available. Discuss with OpenClaw stays disabled until the actual chat endpoint is local, or the owner explicitly enables external memory context in the controls. Existing chat routing and broker/risk settings are preserved.

The server checks the configured endpoint rather than its display label. General local conversations may recall bounded saved notes, explicit preferences and a cached Honcho profile. Portfolio conversations receive only explicit owner preferences; other portfolios' chats and the global inferred profile are excluded. Honcho inference never runs in the chat request itself. Optional memory failures do not fail an ordinary chat reply.

The Knowledge reader does not automatically load images embedded in Markdown notes or inferred profiles. Image references remain visible as text, so opening a saved note cannot silently request an external tracking image.

OpenClaw cannot change memory/privacy controls through project tools. Retrieved Markdown and model inferences are data, not instructions. Original native Tauric evidence, qualification, approval, capital limits and execution checks remain required. Memory does not train an RL trading policy.

## Local services

Honcho is pinned to v3.2.2, commit `06ed1929cf017c333a87c41d130bb6a0605d91c0`. Its dependencies use the upstream frozen lockfile, its container bases are pinned, and static tokenizers are bundled during the build. Runtime API, deriver, model, vector database and cache run on an internal Docker network with no internet route. Honcho has a separate database, authenticated API and no published host port. Only Tauric's API/chat/memory services join its internal network. The bounded adapter accepts fixed local endpoints and refuses redirects.

The small local Qwen3 4B model uses the `tauric-memory:4b` configuration to match the three-core CPU limit; Nomic Embed Text v1.5 provides 768-dimensional embeddings. Model weight digests are recorded in `artifacts/memory-models.json`. All Honcho generation modules point to the local model. Cloud telemetry, dreams, peer cards and automatic long summaries are disabled for this first version. Preference learning is asynchronous and can be slow on this Mac.

Only the owner peer is observed. Assistant and project text are context, not owner statements. General and portfolio sessions have separate scopes. Honcho delivery uses document checksums and chunk metadata to reconcile a lost reply before resending. Duplicate or conflicting remote receipts stop synchronization for inspection.

The first version uses local PostgreSQL keyword search for the wiki. Honcho uses embeddings for its internal memory. Cross-document semantic wiki ranking, multi-vault synchronization, complete native-report indexing, deletion/retention policies and measured personalization quality remain future work.

## Setup and operations

From the project directory, clone the pinned Honcho source into the ignored `.runtime-honcho` folder, then run:

```sh
git clone --depth 1 --branch v3.2.2 https://github.com/plastic-labs/honcho.git .runtime-honcho
.venv/bin/python scripts/memory_setup.py
docker compose --env-file .env.honcho -f deploy/compose.honcho.yml --profile download run --rm model-download
docker compose --env-file .env.honcho -f deploy/compose.honcho.yml up -d --build honcho memory-deriver
docker compose --env-file .env -f deploy/compose.dev.yml -f deploy/compose.memory-app.yml --profile analysis --profile governance --profile knowledge up -d --build
```

`memory_setup.py` verifies the source commit and creates private credentials without overwriting existing ones. Model downloads use a separate temporary container with only the weights volume mounted. Honcho configures its vector dimension before startup using the upstream script; that script refuses to change populated embeddings. Migration `0031_knowledge_memory` is additive. Make and verify a private database backup before upgrading an existing preview.

The optional `compose.memory-app.yml` adds the collector and vault bindings only to the local development stack. Set `KNOWLEDGE_HOST_UID` and `KNOWLEDGE_VAULT_DISPLAY` when using a different host path/user. Hosted/server deployment has not been performed.

Source references: [Honcho source and setup](https://github.com/plastic-labs/honcho/tree/v3.2.2), [Honcho documentation](https://docs.honcho.dev/), [Ollama local API](https://docs.ollama.com/api/openai-compatibility), [Obsidian vaults](https://help.obsidian.md/Files+and+folders/Manage+vaults).
