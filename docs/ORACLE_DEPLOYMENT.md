# Oracle Cloud deployment

Fresh installation verified on 15 October 2026 at `ubuntu@132.145.27.115`. No existing Tauric data or model/broker credentials were migrated.

## Verified installation

Release `1a3f0766b9e78934c1d0c7a43c69550c4bc08074` from [successful hosted release 38058314563](https://github.com/aoodie/Tauric/actions/runs/38058314563) is installed at `/srv/tauric/current`, linked to `/srv/tauric/releases/1a3f0766b9e78934c1d0c7a43c69550c4bc08074/tauric-release`. Private state is `/srv/tauric-private`.

Release archive SHA-256: `3d61e3137805b6f936374c5dcc64f4a98e8fd696de2409364a6e583735d8d263`. All 34 manifest files and five published amd64 images were verified. Registry credentials were restricted to image-repository pulls, kept in memory/temporary RAM files and removed after use.

The host has Ubuntu 22.04, 4 CPUs, 16 GB RAM, Docker 29.9.1 and Compose 5.6.0. Preflight, HTTP readiness, owner authentication, unauthenticated-access rejection and owner/workspace persistence after restart passed. All 12 intended application/AI services are healthy. The isolated engineering executor is installed and active.

Application and AI profiles were started. Financial execution workers remain disabled: automatic approval review rejected the original command containing `--with-execution`; the safer installation omitted that profile. No live account or trading policy was activated.

Ubuntu 22.04 supplies Python 3.10. `/srv/tauric/run-python.py` supplies Python 3.11's `datetime.UTC` alias using `datetime.timezone.utc`, without modifying verified release files. The engineering systemd service uses the same compatibility launcher through a drop-in.

## Access and initial login

The application binds only to server loopback. A private SSH tunnel was opened during deployment. Open [Tauric](http://localhost:18080).

To reopen the tunnel later, run on the Mac and keep it running:

```sh
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 -i ~/Downloads/ssh-key-2026-10-15.key ubuntu@132.145.27.115
```

Port 18080 avoids the Mac's occupied port 8080. The exact allowed browser origin is `http://localhost:18080`. Server ingress remains `127.0.0.1:8080`; database, API, worker and gateway ports are private.

Owner username: `aoodie`. Retrieve the randomly generated initial password privately in your Terminal:

```sh
ssh -i ~/Downloads/ssh-key-2026-10-15.key ubuntu@132.145.27.115 'cat ~/tauric-initial-login.json'
```

The login file is owned by `ubuntu` with mode 0600. Change the initial password in **Settings → Security**, then remove that file. The SSH private key remains on the Mac. Configure AI/data connections in Settings; none were imported.

## Recovery evidence

The initial private configuration was backed up before startup. After owner creation, the release backup captured all 83 database tables and four runtime volumes:

- Archive: `/srv/private-backups/tauric-oracle-installed-20261015.tar.gz`
- SHA-256: `63775a136386dbec3b3cdb542a867af06d829132444f1a13e2ffcc7c198c3ea5`
- Restored state: `/srv/tauric-restore-oracle-20261015`
- Isolated project: `tauric_restore_oracle_20261015`
- Schema: `0036_agent_tasks`

The isolated restore matched every table's count/checksum and all four runtime volumes' contents, permissions and ownership. Restored background automation never started. Its containers were stopped afterward; restored volumes and the policy-review marker remain for inspection. The original services and engineering executor resumed and passed final health/authentication checks.

Backups contain credentials and must remain private. Off-host encrypted retention, host reboot, representative 24-hour workloads and model-driven autonomous ownership were not verified by this installation. Deployment success does not establish complete production acceptance.

Non-secret evidence is in `artifacts/oracle-deployment-2026-10-15/status.json`, `installed.json` and `final-check.json`.
