# Server deployment package verification

**Monitoring deployment — 8 October 2026:** source `18b3d34473b666cfa83df6679357481bd4c504bb` is now deployed. Its hosted release passed 1,004 backend tests, all image jobs and clean installation/full restore of schema 0033. Fresh target backup/restoration and isolated recovery after an injected post-migration startup failure passed. All 15 services and seven progress lanes are healthy. [Detailed validation](VALIDATION_RESULT_2026-10-08.md) preserves the failed strategy result and remaining gates.

**Current target — 19 October 2026:** Oracle is the sole active workstation. The IONOS runtime is retired, with its final backup independently restored on Oracle and its original volumes/private recovery state retained. Current installed source, service scope and backup evidence belong in the [Oracle-only deployment record](ORACLE_ONLY_2026-10-19.md). The dated IONOS checks below are historical evidence and do not certify Oracle or complete the [remaining acceptance work](ACCEPTANCE_PLAN.md).

The sections below preserve the dated 5 October rehearsal. Their pending hosted-publication and target-installation statements are historical and superseded by the current status above.

Recorded 5 October 2026. **The server package passed an isolated Linux arm64 container installation and full database/runtime restore rehearsal. It is a deployment candidate, not a production-certified release.**

## Implemented package

- GitHub checks on pull requests/main and release-tag checks before image publication.
- Four versioned GHCR images, Linux amd64/arm64 build configuration, source labels, build provenance and SBOMs. The downloadable bundle binds its files and image digests to one source commit.
- Separate server Compose configuration with no on-host builds, bounded logs, persistent volumes, readiness checks and private ingress. Database/API/Gateway/background ports are not published. Application services use fixed non-root users, read-only root filesystems and zero Linux capabilities.
- Private setup that creates empty model/data connections and never overwrites credentials. PostgreSQL passwords are file-mounted and absent from expanded application environment metadata. All secret leaf files are readable inside owner-only directories so explicitly mounted files work across native Linux host/container UIDs, including the unprivileged migration service.
- Optional public HTTPS configuration with exact-origin checks, secure cookies and persistent Caddy certificate storage. Public certificate issuance was not exercised locally.
- Quiesced database/runtime backup, isolated restoration with table and file/permission/ownership comparisons, and a restored-policy review marker that prevents automatic service startup.
- Read-only resource observation and a source-only GitHub archive builder that excludes private workstation state and rejects known credentials embedded in source.

## Checks and evidence

The final combined catalog/release source passed **879 non-browser backend tests**, with 18 browser checks deselected. Its corrected autonomous/stock clock fixtures also passed all 57 scoped checks; immutable journal timestamps preserve insertion order when the simulated clock is fixed. Eight dedicated deployment/upload-control checks and eight market-intelligence browser workflows passed. Backend static checks, frontend production build/formatting, pinned OpenClaw plugin build/validation and GitHub workflow syntax validation passed.

The catalog now browses 68 OANDA pairs, 13,191 Alpaca listings and 501 Binance USDT spot pairs separately from the nine registered research instruments. Real daily prices for NVDA, AUD/CAD and ADA/USDT were verified without creating research jobs or changing the registered universe. See [market-intelligence evidence](MARKET_INTELLIGENCE.md).

The final source checks are recorded in ignored `artifacts/catalog-release-verified-backend-tests.log`, `catalog-release-clock-order-tests.log`, `catalog-release-deployment-tests.log`, `catalog-release-browser-tests.log`, `catalog-release-frontend-format.log`, `catalog-release-frontend-build.log`, `catalog-release-openclaw-tests.log`, `catalog-release-final-lint.log` and `catalog-release-workflow-validation-v2.log`.

The rehearsal used uniquely named disposable Compose projects, new credentials and an isolated database. It exercised actual built services and HTTP requests, the real owner-bootstrap command, a saved interface preference and one simulated paper account. It verified authenticated access after API and whole-stack restart. Backup/restore matched every public table's count and canonical row checksum at schema `0029_market_intelligence`: **70 tables**. All four existing runtime volumes were restored and compared: OpenClaw state, native runtime, Caddy data and Caddy configuration. Restored background services were never started. The rehearsal removed its containers and volumes; the primary demo accounts and policies were not changed.

Local evidence is under ignored `artifacts/`: `server-release-backend-tests.log`, `release-smoke.json`, `release-smoke-with-observer.json`, the image build logs and `release-workflow-validation.log`. The observer record includes a short resource sample only; it is not a 24-hour soak. Rehearsal reports identify the exact local image IDs. Concurrent research/UI development may produce later source changes; publishing a GitHub tag reruns checks and rehearsal against that tag's fixed source revision rather than treating this local snapshot as proof for all later changes.

The host is macOS with Docker Desktop running Linux aarch64 containers and approximately 8 GB assigned to Docker. This does not certify an IONOS host, Linux amd64 operation, a clean native Linux installation, host reboot recovery or the minimum memory needed for representative research/trading workloads. No model or broker requests were made by the release rehearsal.

## Open gates

Hosted checks passed on prior snapshot `26f5d1c2089f50f48f05ec0faf234fbc3a4d6025`, including 874 backend tests, eight deployment controls and frontend/OpenClaw checks. Its four multi-architecture images began building. This is prior-snapshot evidence: the final combined catalog source must pass its own hosted workflow and Linux amd64 installation/restore rehearsal. New pushes cancel stale candidate-branch runs while tagged releases remain uncancelled.

The private `aoodie/Tauric` repository is now accessible through the connected GitHub account. The local command-line token is invalid, SSH authentication is unavailable, and the in-app browser is signed out. Source was published through the GitHub connector. The deployment-candidate branch runs hosted checks, multi-architecture image publication and a clean Linux amd64 installation/restore rehearsal. Initial hosted checks exposed a missing test output directory and a stale watchlist persistence assertion; both were corrected. Generated API types were synchronized with the published backend, and paper-exit fixtures now timestamp persisted fills with their simulated market clock to remove morning/evening test differences. Image publication and the hosted rehearsal remain pending until that workflow succeeds.

The owner selected `root@217.160.9.196` for deployment. Its changed ED25519 identity was verified against the owner's IONOS console before attempting access. Initial ingress is private through SSH; no public hostname has been selected. Key authentication is established. Read-only inspection found a clean Ubuntu 26.04.1 x86_64 host with Docker 29.8.2/Compose 5.6.0, approximately 3.76 GiB RAM, 120 GB free disk, no swap and synchronized time. No Tauric installation or representative workload has run there yet. Clean installation, DNS/HTTPS operation when selected, external-model connectivity, off-host backup storage/retention, host reboot and upgrade/rollback rehearsal require that environment. A representative 24-hour soak and connected qualifying-signal → native assessment → OpenClaw approval → broker fill → attributed exit remain uncertified. Real-money execution stays disabled.

See [deployment instructions](GITHUB_DEPLOYMENT.md) and [full acceptance requirements](SERVER_READINESS.md).
