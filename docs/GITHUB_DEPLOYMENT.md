# GitHub deployment candidates

**Monitoring candidate — 8 October 2026:** the separate `codex/trading-acceptance-monitoring` branch is deployed at `18b3d34`; [PR 6](https://github.com/aoodie/Tauric/pull/6) retains it for review. [Hosted release](https://github.com/aoodie/Tauric/actions/runs/37734549950) passed checks, image publication and full installation/restoration. The shared deployment branch and concurrent research work were preserved.

**8 October status:** hosted publication and native amd64 installation/restore passed for deployed source `0e30a3b`. The IONOS candidate serves trusted public-IP HTTPS and has reboot/backup evidence. Current acceptance work is described in [the acceptance plan](ACCEPTANCE_PLAN.md); earlier pending-publication statements below are historical.

The repository now contains a GitHub Actions delivery path and a server-only Compose package. It remains a **deployment candidate** until the target Linux host, connected automatic trading cycle and soak/recovery gates pass. GitHub stores source, runs checks and publishes images; the application runs on a Linux server. GitHub Pages cannot host its database, API or background workers.

## Publish from a dedicated repository

Use a dedicated Tauric repository. This workstation folder is currently inside a larger parent Git repository; do not publish the parent workspace or its other projects. The private `.env*`, `artifacts/`, local environments and runtime directories remain excluded. GitHub credentials must be connected before publishing; do not add tokens to the repository.

`checks.yml` runs backend/database checks, deployment controls, frontend build/formatting and the pinned OpenClaw contract checks on Ubuntu 24.04. Tagging a tested commit with `v*`, pushing the dedicated `codex/deployment-candidate` branch or manually running the workflow starts `release.yml`: it reruns checks, builds API/native-worker/web/OpenClaw images for Linux amd64 and arm64, publishes them to GHCR, records immutable digests and assembles a server-only downloadable artifact after an actual amd64 installation/restore rehearsal. Official actions are pinned by commit. Build provenance and SBOMs are attached to image publication. A GitHub publication is separate from permission to deploy or enable trading on a host.

Repository owners should require the checks job before merging, restrict release-tag creation and enable GitHub secret scanning where available. Private GHCR packages require a server-side login with package-read access. Use an interactive login or password input through standard input; keep registry credentials outside this bundle.

## Install a candidate

Download the `tauric-deployment-COMMIT` artifact from the successful release workflow, unpack its archive, and keep `release-manifest.json` with the selected images. The bundle includes source/file checksums and exact image digests, rather than mutable tags. Python 3 and Docker with Compose 2.24.4 or later are required on the host. Builds happen in GitHub; the host only pulls images.

For initial private access, run from the unpacked bundle:

```sh
python3 scripts/server.py --state-dir /srv/tauric-private setup --images images.env
python3 scripts/server.py --state-dir /srv/tauric-private preflight
python3 scripts/server.py --state-dir /srv/tauric-private start --with-ai --with-execution
python3 scripts/server.py --state-dir /srv/tauric-private create-owner --username YOUR_USERNAME
```

Choose a directory owned by the operating-system deployment account. Setup creates new credentials inside owner-only directories and refuses existing state. Compose preserves host permissions for file-backed secrets. All secret files, including the migration administrator password, use readable leaf permissions inside owner-only directories and are mounted only into their authorized services. Server configuration remains owner-only. Application containers run as fixed unprivileged users with no Linux capabilities and read-only root filesystems. It starts with empty AI/data connections and no enabled trading policies. The owner command prompts for a password and never overwrites an existing owner. Configure models and demo broker connections in Settings. Keep inference on an external endpoint.

The private web listener is `127.0.0.1:8080`; no database/API/Gateway/worker port is published. Connect with an SSH tunnel and open `http://localhost:8080`. The exact allowed origin is deliberately `localhost`; if a different local port is needed, choose it during setup. Transport is protected by SSH/VPN.

For a public hostname, add `--domain trade.example.com` to setup. The HTTPS Compose override publishes ports 80/443, Caddy obtains a certificate, cookies require HTTPS, and the API accepts the exact HTTPS origin. Point DNS to the host and permit ports 80/443. HTTPS certificate issuance, renewal, DNS and firewall behavior need verification on that host. No hostname has been selected for the current workstation.

For a public IPv4 address, use `--domain 217.160.9.196`. This selects the public-IP override, requires HTTPS cookies and the exact HTTPS origin, and explicitly requests Let's Encrypt's `shortlived` certificate profile. Caddy 2.11 or newer is required; verify the version inside the deployed web image before enabling this option. Ports 80/443 must be reachable externally. HTTP serves only certificate validation or redirects to HTTPS. Caddy stores its ACME account and certificates in the existing persistent `caddy_data` volume and manages renewal automatically; IP certificates last approximately 160 hours. Include that volume in backups. Verify public certificate trust, IP subject, authenticated sign-in, renewal configuration, and recovery after restarting the web service. Do not disable certificate validation or deliver an internal/self-signed certificate as public access. Existing private state must be backed up before changing its origin and Secure-cookie settings; setup never overwrites credentials.

Preflight validates private file permissions, immutable image references and ingress configuration, checks Docker/Compose and reports container-host architecture, memory and free disk. These numbers do not certify workload capacity, clock synchronization or outbound provider access. Complete those checks on the target host and measure the selected universe/cadence in the soak test.

## Back up and rehearse restoration

Pause new trading entries and wait for active model jobs before planned maintenance. Broker protection and the handling of existing positions need owner review: the backup operation stops the application's monitoring until capture finishes. Schedule maintenance accordingly.

```sh
python3 scripts/server.py --state-dir /srv/tauric-private backup --output /srv/private-backups/tauric-before-upgrade.tar.gz
python3 scripts/server.py --state-dir /srv/tauric-restore-check --project tauric_restore_check restore-check --archive /srv/private-backups/tauric-before-upgrade.tar.gz
```

Backup freezes application writes, captures PostgreSQL plus model encryption keys, service tokens, connection files and existing OpenClaw/native/Caddy volumes, then resumes exactly the previously running services. The archive is private and contains credentials. Move it to encrypted off-host storage with a documented retention policy; the script does not upload or delete backups.

Restore-check accepts only a new directory and new Compose project with no existing volumes. It restores the database, compares every public table's count and canonical row checksum, restores runtime volumes and compares contents, permissions and ownership. It starts only the isolated database and read-only diagnostics; restored automation settings cannot run. The `RESTORED_REVIEW_REQUIRED` marker blocks the start command until an operator has completed policy/recovery review. The script never deletes the existing deployment or overwrites its database. Remove the isolated rehearsal's containers/volumes only after recording its result.

## Upgrade and rollback

Retain the previous release bundle and digest references. Back up before changing `server.env` image references. Review migrations and release compatibility, pull the selected images, and start the new version. Check readiness, owner login, original reports and journals before resuming entry policies.

Do not point an older binary at a newer database unless that release explicitly supports the schema. Use an isolated restoration of the prior database/runtime backup and prior image digests to rehearse recovery first. Restoring a pre-trade backup can lose later broker facts; reconcile actual broker orders/positions before allowing execution. There is no general automatic database downgrade or production rollback certification yet.

## Remaining acceptance

Record resource and service health while your approved representative workloads run:

```sh
python3 scripts/server_soak.py --state-dir /srv/tauric-private --hours 24 --interval 60 --workload "Describe the approved universe, cadence and external model route" --output /srv/private-backups/tauric-soak.json
```

The observer records health, unexpected restarts/recreations, OOM flags, CPU/memory/network/block-I/O/process counts and available disk. Reports are updated atomically. It never queues research or trading and does not certify that the workload is representative; that requires checking the actual job/order/ledger evidence. A short test cannot count as the 24-hour acceptance gate.

- Connect the intended dedicated GitHub repository and run the actual hosted workflows; multi-architecture builds remain unverified until those runs succeed.
- Test a clean Linux server, outbound model/provider access, HTTPS when selected and host reboot recovery.
- Verify the automatic qualifying-signal → native → OpenClaw → broker fill → attributed exit cycle on connected demo accounts.
- Record a representative 24-hour soak, resource/latency/disk growth, provider interruptions and restore/upgrade/rollback behavior on the host.
- Keep real-money execution disabled. Wider platform features retain their separate acceptance checks in `SERVER_READINESS.md`.

Primary workflow references: [GitHub container publication](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images), [Docker provenance and SBOMs](https://docs.docker.com/build/ci/github-actions/attestations/), and [Compose services](https://docs.docker.com/reference/compose-file/services/).
