# Tauric public hostname

**Live and verified 17 October 2026:** [https://tauric.aoodie.xyz](https://tauric.aoodie.xyz).

Cloudflare proxies the hostname to the Oracle deployment at `132.145.27.115`. The existing digest-pinned Caddy web image serves ports 80/443 using the shipped HTTPS override. Caddy obtained a trusted Let's Encrypt certificate; renewal state remains in the persistent Caddy data volume. The release remains `1a3f0766b9e78934c1d0c7a43c69550c4bc08074`.

Public readiness and homepage requests passed through Cloudflare. A direct request to the origin using the real hostname passed normal certificate validation and returned readiness `ok`. HTTP redirects to HTTPS (301), unauthenticated settings requests return 401, and internal paths are denied. The browser visibly displayed Tauric's owner sign-in page at the public hostname. Owner credentials were not changed or used for this domain test.

The exact browser origin is `https://tauric.aoodie.xyz`, with Secure cookies enabled. The previous localhost tunnel is no longer the application ingress; use the public HTTPS URL. This update supersedes the initial private-access instructions in `ORACLE_DEPLOYMENT.md`.

## Operation and recovery

Live state: `/srv/tauric-private/server.env`. Release: `/srv/tauric/current`. The normal server helper selects `deploy/compose.https.yml` when the domain is configured. API/web were recreated; financial execution workers were not started.

Pre-change backup: `/srv/private-backups/tauric-before-domain-20261017.tar.gz`, SHA-256 `c9b46e05ff732c5248de9e3912b1a09b2e843140f879a0b36593a25802b95dd1`. It contains 83 tables and four runtime volumes. Previous environment and verification records remain at `/srv/tauric-domain-staging-20261017`.

Oracle initially blocked web ingress; the owner corrected the network rules. Earlier attempts safely restored private access when checks failed. Once networking worked, Python urllib requests received Cloudflare 403 responses even while Caddy's health check passed. Verification using standard curl succeeded without weakening Cloudflare protections or disabling TLS validation. Earlier failed-attempt records are retained.

Public evidence is `artifacts/oracle-domain-2026-10-17/deployment.json`. Historical staging/attempt records do not describe the final active state. Complete production acceptance and model-driven autonomous ownership remain separate from this verified HTTPS deployment.
