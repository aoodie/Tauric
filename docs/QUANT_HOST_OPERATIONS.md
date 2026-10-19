# Research engine host supervision

Qlib and LEAN are optional research runtimes, separate from the API and financial execution services. [The Research Lab guide](research-lab.md) describes their data contract, bounded queues and research limitations. This runbook covers the fixed host supervisor in [`scripts/quant_host.py`](../scripts/quant_host.py); rendering a service unit does not install dependencies or activate a worker.

## Installation identity

Use the source revision of the independently verified installed API. Export that revision's `backend/tauric`, `backend/runners`, `backend/requirements.lock` and `backend/requirements.qlib.lock` into `/srv/tauric-quant/source/<revision>/`. Keep the source root-owned and read-only to both service users. Record each exported file's SHA-256, the archive checksum, dependency inventories, Python version, host-control commit/checksum and LEAN image identity. A passing fixture is not a production installation result.

Create separate Python 3.12 environments under `/srv/tauric-quant/runtimes/app` and `/srv/tauric-quant/runtimes/qlib`, using their respective locks. Linux LightGBM needs the distribution's `libgomp1` library. Record the exact installed Python patch/build and installed dependency inventories. Pull the LEAN digest from `quant_runtime.LEAN_IMAGE`; mutable image tags are not accepted by the host helper.

The root-only `/srv/tauric-quant/control/config.json` contains:

```json
{
  "source_revision": "<full installed application commit>",
  "source_files": {
    "backend/tauric/quant_worker.py": "<sha256>",
    "backend/runners/qlib_runner.py": "<sha256>"
  },
  "lean_image": "<the exact reviewed LEAN digest>"
}
```

Include the full exported source inventory, not just the two illustrative entries above. This file contains no database password. Install the reviewed helper as `control/quant_host.py`, root-owned mode 0755. The control directory may be mode 0711 so service users can read the known helper path; configuration, evidence and validation directories remain private. Run the seven supervisor tests on the native host Python before enabling services.

Create dedicated non-login users `tauric-quant-qlib` and `tauric-quant-lean`. Give each ownership of its own mode-0750 `/srv/tauric-quant/artifacts/<engine>/` directory. Keep source, environments and service definitions root-owned. Create `/run/tauric-quant` root-owned mode 0700 and preserve its creation at boot with a matching tmpfiles rule.

Render both systemd units with `python scripts/quant_host.py unit --engine qlib` and `--engine lean`, validate them with `systemd-analyze verify`, then install and enable the reviewed definitions. Preserve their hashes and source revision in the installation record. The supervisor discovers the healthy Compose database's private address at each start; it does not publish a database port or assume a permanent container IP.

## Credential and resource boundaries

Only the fixed root preparation step reads the application-role database password and writes a root-only transient environment file. The non-root controller caches its database configuration, then removes other environment entries before engine imports and startup probes. Job subprocesses also receive the existing explicit environment allowlist. Broker/model keys and owner login material are not supplied to engines.

Qlib's controller has no Docker group. LEAN's fixed host controller receives Docker access through its service supplementary group: it is a trusted operations component, not an arbitrary-code sandbox. General engineering workspaces, the API and financial workers receive no new socket or credentials. Each LEAN child retains the reviewed digest, a single job mount, no network, dropped capabilities, a non-root UID/GID, no privilege escalation, two CPUs, 3 GB memory and a process limit.

The Qlib controller and training child share a 3 GB/two-CPU systemd limit. LEAN's host controller has a 512 MB/two-CPU limit; its Docker child has the separate limits above. Each engine processes one job at a time. The existing 15-minute job deadline, cancellation, integrity checks and leases remain unchanged. Resource limits are not a measured throughput or memory-reduction claim.

## Backups, upgrades and pauses

The research units are ordered after, and are `PartOf`, `tauric-engineering.service`. Enabling a research unit adds it to that service's wants as well as normal host boot. The existing backup process stops the engineering service before taking its database snapshot, which synchronously stops these ordered research dependants. Resuming engineering starts only research units whose enabling links remain present. A persistent research pause uses `systemctl disable --now tauric-quant-<engine>.service`; this removes the dependency links so a backup or engineering restart does not reactivate it.

The helper waits for an active host supervisor, a healthy database and a healthy API with the exact recorded application revision. It also verifies the exported source inventory. An application upgrade requires revalidating and recording matching research source and dependencies before restarting the workers. The helper does not silently use another release. API readiness and a recent database heartbeat must both be checked after maintenance.

On stop, cleanup can remove only this supervisor's LEAN job containers: their image, UUID name and exact job mount must match. Other containers are left alone. Interrupted jobs retain their identities; the existing lease logic fails abandoned work visibly instead of silently rerunning it.

The standard application backup does **not** include `/srv/tauric-quant/artifacts/`. Back up that directory, the private control records, source inventory and installed unit definitions separately while both research workers are stopped. Retain model files, results and logs with a checksummed inventory, and rehearse restoration into a new private directory without starting a worker. Dependency environments and the engine image can be reconstructed from their recorded identities. Protect any off-host application backup with encryption; it contains private settings and recovery material.

## Acceptance

Verify both engines through owner-authenticated Research Lab APIs, not just service status. Retrieve and validate historical bid/ask data, queue Qlib training with fixed chronological splits, wait for its durable completion, and queue the matching LEAN model handoff. Verify output and model files against their database provenance. A no-trade model result is valid; use a separately labelled causal baseline when actual simulated fills are needed to check the execution engine.

Then verify supervised restart, backup freeze/resume, preservation of a disabled research unit, unchanged financial authority and an actual restoration of the resulting backups. Keep installation checks labelled as research acceptance. They do not qualify a strategy, establish broker fill parity, enable financial workers, or authorize demo/live orders.

Installation evidence belongs in the private control records and deployment report. The [Oracle-only migration record](ORACLE_ONLY_2026-10-19.md) is the earlier application/connection checkpoint; it does not by itself prove subsequent research engine activation.
