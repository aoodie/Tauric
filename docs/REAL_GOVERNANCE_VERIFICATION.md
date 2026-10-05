# Real bound OpenClaw review verification

Recorded 3 October 2026 for development 0.7.0. This verifies one real model's bound-context tool use, strict decision transport and the rejected-proposal path on an isolated paper ledger. It does not certify the real approval-to-fill path or a server release.

The previously completed real native BTC report `d00bc9cc-d635-4bcd-9042-c30a8d6a8ed1` was copied unchanged into a disposable verification database. Its original payload SHA-256 remained `9b77ea3e1f5877fdf0364f99426d4c7871b8086ce0466c9c81f24c09c56329f7`. The import was labelled on the verification job; the original report and rating were not rewritten.

The approved saved local model, `ag/gemini-3.8-flash-high`, was used through the actual pinned OpenClaw Gateway, project plugin and governance worker. Its credential was decrypted only in process memory and re-encrypted under the disposable database's private key. It was never printed or sent through the browser. The test used a clearly labelled disposable strategy/account/allocation with explicit fictional policies and actual public Binance spot quotes. None of those financial settings were created in the real owner's database.

| Check | Result |
|---|---|
| Review job | `b5c91f00-3098-4886-8f04-dc1a0ec52dbb`, succeeded |
| Bound context read | Persisted receipt confirmed before final decision |
| Context checksum | `bb84b68c0ab472ec165b4ee1a49a223667344bfe5c15aebe300681fbdcb4010b` |
| Model outcome | Reject; the result was accepted through the strict identifier/checksum/outcome contract |
| Decision | `e323e863-bf1d-4ed5-8956-266cd80799b5`, separate from original research |
| Repeat enqueue | Same request returned the same job; no additional model review |
| Planning after rejection | HTTP 409; no paper entry plan/fill created |
| Ledger | Reconciled, no open positions |
| Original research | Payload/checksum unchanged |
| Reported usage | Prompt 26,756; completion 470; total 27,226 tokens |
| Broker order calls | Zero |
| Real-owner paper changes | Zero |

The model was allowed to reject or defer; it was not prompted to force approval. No automatic retry was made. Reported usage reflects the Gateway response and is not an exactly-once billing guarantee or a verified monetary cost.

The disposable services, runtime volume, database and encryption key were removed after verification. A sanitized result record remains in ignored `artifacts/real-governance-results.json`; the temporary job/decision IDs are evidence references, not resources in the real owner's account. A redacted verification audit is retained in the primary application.

At this stage the full positive path remained unverified with a real model. A later development 0.9.0 isolated daily BTC run now verifies fresh analysis, bound approval, deterministic plan, paper entry, explicit close, independent journal reconstruction and API-restart persistence; see [connected paper verification](REAL_PAPER_WORKFLOW_VERIFICATION.md). Owner-reviewed and model-fixture positive paper paths have separate evidence. OpenClaw requesting native analysis itself, scanner orchestration, broader data contexts, live brokers and Linux release/recovery gates remain pending.

Later on 3 October, a fresh isolated native analysis and bound review also succeeded. The fresh report was Underweight, and OpenClaw correctly rejected the long-entry proposal; planning denial, original preservation and API-restart persistence passed. The verifier now checks the full positive journal branch independently, and a subsequent development 0.9.0 run now passes that branch with a real approval. See [fresh connected workflow evidence](REAL_PAPER_WORKFLOW_VERIFICATION.md).
