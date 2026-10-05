# Server deployment package verification

Recorded 5 October 2026. **The server package passed an isolated Linux arm64 container installation and full database/runtime restore rehearsal. It is a deployment candidate, not a production-certified release.**

## Implemented package

- GitHub checks on pull requests/main and release-tag checks before image publication.
- Four versioned GHCR images, Linux amd64/arm64 build configuration, source labels, build provenance and SBOMs. The downloadable bundle binds its files and image digests to one source commit.
- Separate server Compose configuration with no on-host builds, bounded logs, persistent volumes, readiness checks and private ingress. Database/API/Gateway/background ports are not published. Application services use fixed non-root users, read-only root filesystems and zero Linux capabilities.
- Private setup that creates empty model/data connections and never overwrites credentials. PostgreSQL passwords are file-mounted and absent from expanded application environment metadata.
- Optional public HTTPS configuration with exact-origin checks, secure cookies and persistent Caddy certificate storage. Public certificate issuance was not exercised locally.
- Quiesced database/runtime backup, isolated restoration with table and file/permission/ownership comparisons, and a restored-policy review marker that prevents automatic service startup.
- Read-only resource observation and a source-only GitHub archive builder that excludes private workstation state and rejects known credentials embedded in source.

## Checks and evidence

The non-browser backend suite passed **874 tests**, with 18 browser checks deselected. Eight dedicated deployment/upload-control checks passed. Scoped/backend static checks, frontend production image build/formatting, pinned OpenClaw plugin build/validation and GitHub workflow syntax validation passed.

The rehearsal used uniquely named disposable Compose projects, new credentials and an isolated database. It exercised actual built services and HTTP requests, the real owner-bootstrap command, a saved interface preference and one simulated paper account. It verified authenticated access after API and whole-stack restart. Backup/restore matched every public table's count and canonical row checksum at schema `0029_market_intelligence`: **70 tables**. All four existing runtime volumes were restored and compared: OpenClaw state, native runtime, Caddy data and Caddy configuration. Restored background services were never started. The rehearsal removed its containers and volumes; the primary demo accounts and policies were not changed.

Local evidence is under ignored `artifacts/`: `server-release-backend-tests.log`, `release-smoke.json`, `release-smoke-with-observer.json`, the image build logs and `release-workflow-validation.log`. The observer record includes a short resource sample only; it is not a 24-hour soak. Rehearsal reports identify the exact local image IDs. Concurrent research/UI development may produce later source changes; publishing a GitHub tag reruns checks and rehearsal against that tag's fixed source revision rather than treating this local snapshot as proof for all later changes.

The host is macOS with Docker Desktop running Linux aarch64 containers and approximately 8 GB assigned to Docker. This does not certify an IONOS host, Linux amd64 operation, a clean native Linux installation, host reboot recovery or the minimum memory needed for representative research/trading workloads. No model or broker requests were made by the release rehearsal.

## Open gates

The private `aoodie/Tauric` repository is now accessible through the connected GitHub account. The local command-line token is invalid, SSH authentication is unavailable, and the in-app browser is signed out. Source was published through the GitHub connector. The deployment-candidate branch runs hosted checks, multi-architecture image publication and a clean Linux amd64 installation/restore rehearsal. Initial hosted checks exposed a missing test output directory and a stale watchlist persistence assertion; both were corrected. Generated API types were synchronized with the published backend, and paper-exit fixtures now timestamp persisted fills with their simulated market clock to remove morning/evening test differences. Image publication and the hosted rehearsal remain pending until that workflow succeeds.

The target Linux host, access mode and public hostname have not been supplied. Clean installation, DNS/HTTPS operation when selected, external-model connectivity, off-host backup storage/retention, host reboot and upgrade/rollback rehearsal require that environment. A representative 24-hour soak and connected qualifying-signal → native assessment → OpenClaw approval → broker fill → attributed exit remain uncertified. Real-money execution stays disabled.

See [deployment instructions](GITHUB_DEPLOYMENT.md) and [full acceptance requirements](SERVER_READINESS.md).
