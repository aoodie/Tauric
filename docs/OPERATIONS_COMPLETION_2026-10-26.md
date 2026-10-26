# Research release and operational completion — 26 October 2026

The owner requested completion, commit, push and deployment of the remaining audit work. The AI setting is already enabled on Oracle with a US$99 daily ceiling and owner-supplied accounting rates of US$2 input and US$10 output per million tokens. The two-hypothesis and six-engine-job daily allowances remain unchanged. These settings do not establish a passing strategy.

## Completed workstation recovery cleanup

The four historical plaintext `.env.*backup.dump` files were encrypted with authenticated AES-256-GCM using the existing private Oracle backup key. Each ciphertext was authenticated and decrypted for a complete PostgreSQL restoration in a separate network-disabled container before its plaintext original was removed. The four archives restored 70, 70, 71 and 68 public tables. Ciphertexts and private restoration receipts remain in `.env.server-backups/`; neither keys nor database contents are committed. The isolated container was removed afterward.

`scripts/backup_vault.py` uses bounded reads and refuses overwrites. A corrupted archive cannot publish a named plaintext restoration. Interrupted output retains its original identity and requires reconciliation.

## Implemented release controls

- Shared Caddy routing and its network overlay can live under the private host's `secrets/shared-ingress/`, independently inventoried and hash-checked. Application release manifests remain immutable. Missing or modified release ingress still fails the original guard; the host routing manifest also fails closed on drift. Restorations remap the private directory to the new isolated state rather than using production paths.
- The autonomous release bridge still rejects changed execution identity. The separate root-only `trusted_research_install.py` verifies the exact complete GitHub release, immutable operator intent, source tree, before/after execution fingerprints, original service set and empty financial authority/evidence tables. It rejects financial workers and installs research code without creating qualification, policies, routes or approvals. Existing qualified or financially active installations cannot use this research-only transition.
- Protected snapshots now explicitly include saved research settings, records and price history. Backup restoration, migration, private identity, loaded source and healthy service verification remain mandatory.
- `maintenance_backup.py` freezes existing research only when its original jobs have finished, captures the full application plus a separate research-artifact/source archive, restores both independently, encrypts and authenticates them, and only then removes plaintext. Its journal prevents blind repetition of an uncertain backup or restoration. Retention keeps fourteen verified scheduled checkpoints and removes an older checkpoint only after an independently recorded encrypted off-host copy; manual and unverified archives are preserved.
- `operations_watch.py` writes private text metrics and health evidence, with actionable change-only journal alerts for required services, unexpected financial services, memory/disk pressure, research heartbeats and an overdue native queue. It submits no research, model request or broker action.

The autonomous bridge, trusted release installer, scheduled backup and key maintenance share one host maintenance lock. Backups recheck engineering work after application writers stop and research work before research units stop. Work that appeared after an early readiness probe remains preserved; a checkpoint is refused rather than terminating it.

These are implemented capabilities until the exact checked release and host units are installed and independently verified. Deployment, key activation, first scheduled backup, off-host transfer and a new 24-hour observation have separate evidence; none is implied by this document.

The first complete release attempt, [40281411346](https://github.com/aoodie/Tauric/actions/runs/40281411346), passed all check lanes, five image publications and clean installation/full backup restoration, then failed its isolated engineering backend/API test. The new host database guard required Docker, which is intentionally absent from that executor. The correction verifies the executor's read-only, non-root, capability-free and externally disconnected environment and binds the disposable PostgreSQL identity to its local control file. No environment bypass or weaker host/tunnel check was added. A real isolated container with the repair passed all 26 selected guard and backend/API tests. The failed release remains failed; a fresh source-bound complete release is required.

## Acceptance that requires real outcomes

The next AI-enabled cycle is due after 18:05:46 UTC on 26 October. Verify its original usage record, frozen ideas, actual engine results and final outcome. Keep failures and reservations; do not rebill an uncertain request or relax research criteria.

Start the representative 24-hour observation after release and key maintenance, so the planned restart does not masquerade as an unexplained failure. Verify actual duration, work performed, headroom, queues and recovery. An idle or shortened sample is not a complete workload result.

Full autonomous OpenClaw acceptance requires a new bounded owner-authorized goal and independently verified executed engineering, release, restart/resume and recovery outcomes. The paused legacy goal must remain paused. Operator deployment and passing fixtures are not autonomous ownership acceptance.

All twelve research trials observed before this work were rejected. Strategy qualification and prospective connected demo validation still require a passing new preregistered strategy, suitable dedicated account and elapsed observations. No financial service or live account is activated by this operational work. IBKR/Binance execution and broader platform features require their own complete implementation and connected acceptance; they are not marked complete by this release.
