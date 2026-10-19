# Oracle-only Tauric deployment

The owner selected Oracle as the sole deployment target on 19 October 2026. The workstation is [tauric.aoodie.xyz](https://tauric.aoodie.xyz), on `ubuntu@132.145.27.115`. IONOS is retired as a Tauric runtime. Server evidence below uses UTC; the owner's timezone is Europe/London.

## Oracle source and service scope

The verified starting source is `d40048849e6cb4c226447fa5311aeaea8092c524`. All 45 release-manifest files, running image identities, 163 loaded Python/instruction files and the OpenClaw plugin/saved instructions matched that source. Public HTTPS and owner authentication passed. The execution signature is `broker_protected_long_v2` / `7f0c0ef18c6f315af137f04f4fc98499b4d676774cc10fbbd2b05d4f461a9b01`.

The 12 intended services are `db`, `api`, `web`, `openclaw`, `worker`, `chat_worker`, `governance_worker`, `agent_tasks`, `portfolio_research`, `candidate_research`, `scanner` and `strategy_scheduler`. Financial execution services `broker_execution`, `valuation`, `forward_validation` and `autonomous_paper` remain disabled. The legacy server's larger service profile is not permission to start them on Oracle.

[PR #15](https://github.com/aoodie/Tauric/pull/15) passed [all three hosted check lanes](https://github.com/aoodie/Tauric/actions/runs/39307469319). It preserves the previously running service set during installation and preparation recovery, supports native host Python 3.10 and distinguishes an ended task continuation from an expired or paused goal without relaxing either guard. The installed release source is **`e6b1b656c0f5d552c89ca1fe95e6bdf0445d072e`**, frozen on `codex/release-oracle-20261019`. Its [complete release workflow](https://github.com/aoodie/Tauric/actions/runs/39309248600) passed 1,178 backend tests, 59 deployment/tooling tests on both the application and native Python 3.10 runtimes, four mandatory browser workflows and two real Qlib/LEAN tests. All five multi-architecture image publications, clean installation/full restoration and an isolated executed engineering test passed.

Independent Oracle verification completed at **19:39:52 UTC**. All 12 intended services were healthy; 45 manifest files, actual image identities, 163 loaded Python/instruction files and the plugin/saved instructions matched the installed source. All 42 protected authority/settings tables were preserved before workers started and remained unchanged in the independent final check. Private configuration, signing identities and the execution signature were preserved. Owner login and authenticated project access returned HTTP 200; unsigned project access returned 401; public readiness returned 200 with trusted TLS. The financial route, order-intent, close-request and qualification-protocol tables remain empty.

The actual installation backup was restored before production migration, verifying all 83 tables and four runtime volumes without starting restored background services:

- Backup: `/srv/private-backups/openclaw-release-40ce36ae-89c3-544c-a15d-944fe9156465.tar.gz`
- SHA-256: `f46790e50a45aff2f569ecb7f5bc1d56559e1e0da18347c9cb7ddebca3272019`
- Installation result: `/srv/tauric-private/oracle-upgrade-result.json`
- Independent verification: `/srv/tauric-private/oracle-independent-final-verification.json`

## Connections and Oracle recovery

At the owner's request, the existing local AI connection and OANDA practice/Alpaca paper data connections were transferred over pinned SSH pipes. No plaintext credentials or archive were saved on the Mac. The model/data connections were saved through the standard versioned services and encrypted with Oracle's existing key. Its chat profile, allowance, owner login, financial policies and empty route/order ledgers were preserved. Both provider checks returned 300 verified 15-minute candles with no missing bars; the AI route, gateway and chat worker are ready. One normal queued chat request returned the expected connection-test reply at 19:20:35 UTC (`fa7f6e28-94a7-4ce0-8e0e-3f64a277707d`). This proves model transport on Oracle, not a completed autonomous goal or a broker fill.

Before connection migration, Oracle's actual backup was restored in an isolated project. All 83 table counts/checksums and four runtime volumes matched, including their recorded metadata. Restored background services did not start; the isolated database was stopped afterward.

- Backup: `/srv/private-backups/oracle-only-preconnection-20261019.tar.gz`
- SHA-256: `9e2ec0541ce6e30fdb520f4496487f7d50e65b89a449d1cdfb4e74e9129042bc`
- Recovery record: `/srv/tauric-private/oracle-only-preconnection-backup.json`

The existing repository-only GitHub App identity was transferred privately to Oracle. Its private keys remain outside application and engineering mounts. The fixed release bridge and isolated engineering executor now run the installed source natively on Python 3.10; their running process arguments and runtime files were verified. The obsolete engineering compatibility drop-in, which pinned the old image, was archived before removal. Legacy release work was reconciled before its service was disabled. No broader repository permissions were granted.

For future operations, `/srv/tauric-private/secrets/operations/active-release.json` is the release bridge's authoritative source pointer. The `/srv/tauric/current` shortcut was also updated to this installed release at this checkpoint. Image-scoped pull access used encrypted ephemeral handoffs; their artifacts and recipient keys were removed after verification.

## Retired IONOS runtime and preserved history

IONOS source `0a11da8d26bec3c5f5377544a29cb4199ded6478` is historical. Its automation was stopped before the final backup. The final database still matched that backup immediately before its Compose project was removed at **19:15:42 UTC**. The engineering/release services are disabled. All five persistent volumes, private state, original execution receipts and backups remain on IONOS; the VM was not deleted and its billing was not cancelled.

The final private archive was copied to Oracle over pinned SSH with a matching SHA-256. An isolated restoration verified all 83 tables and four runtime volumes. No restored background service ran, and all restored containers are stopped. The archived database did not overwrite Oracle's active settings, credentials or ledgers.

- Archive on Oracle: `/srv/private-backups/legacy-ionos/oracle-only-legacy-final-20261019.tar.gz` (root-only, read-only; 50,888,342 bytes)
- SHA-256: `becfd8141a583ce55b8d59bdb232ddb71ac76c48b228b4f45898448d34f30d64`
- Restored private state: `/srv/tauric-legacy-recovery/20261019`
- Stopped project: `tauric_legacy_archive_20261019`
- Verification record: `/srv/tauric-private/oracle-legacy-archive-transfer.json`
- Retirement record on IONOS: `/srv/tauric-private/oracle-only-retirement.json`

The Mac's existing Tauric access shortcut now opens the canonical Oracle URL; its original launcher and legacy SSH keys remain for recovery. Do not restart IONOS as a second workstation.

## Remaining acceptance

The failed 8 October EUR/USD trial remains unqualified in the archived immutable history. No qualification, approval, broker route or live authority was fabricated or imported. New source and an idle deployment do not establish alpha, broker execution parity or a representative memory/load result.

The legacy engineering goal `67e8cb0b-1897-5343-9bdc-435ea5fbc7bf` is paused under the Oracle-only direction. Its original 24-wake budget and errors are retained. It required operator review and did not complete its timing feature, new tests, release or research criteria. This operator-led migration does not establish complete autonomous project ownership.

Connected governed demo execution, actual prospective observations, independent model-driven engineering/release acceptance, representative 24-hour workloads and remaining broker/platform features retain their existing gates. Heavyweight Qlib/LEAN engine tests run in disposable CI; their production research runtimes have not been installed by this migration.
