# Tauric on GitHub

The owner chose one repository with separate pull requests on 13 October 2026.

The private [aoodie/Tauric repository](https://github.com/aoodie/Tauric) is the common home for Tauric source, tests, deployment tooling and project documentation. Work from both active Codex chats is published there. Separate branches and pull requests preserve each workstream's review and validation history.

## Workstreams

| Pull request | Work |
| --- | --- |
| [#1](https://github.com/aoodie/Tauric/pull/1) | Market catalog and GitHub-to-IONOS deployment |
| [#3](https://github.com/aoodie/Tauric/pull/3) | OpenClaw gateway recovery and memory headroom |
| [#4](https://github.com/aoodie/Tauric/pull/4) | TradingView connection timeouts |
| [#5](https://github.com/aoodie/Tauric/pull/5) | Broker-verified Forex pairs and daily research |
| [#6](https://github.com/aoodie/Tauric/pull/6) | Trading worker progress and qualification evidence |
| [#7](https://github.com/aoodie/Tauric/pull/7) | Research Lab, LEAN and Qlib |
| [#8](https://github.com/aoodie/Tauric/pull/8) | Governed broker controls and owner risk-setting interface |
| [#9](https://github.com/aoodie/Tauric/pull/9) | Trader dashboard and authoritative OpenClaw activity |
| [#10](https://github.com/aoodie/Tauric/pull/10) | Durable OpenClaw ownership, isolated engineering and governed releases |

Some pull requests are stacked on other branches. Being in this repository does not mean a change is merged into `main`, included in another workstream, deployed, or accepted. Review the individual pull request's base and checks. The [complete pull-request history](https://github.com/aoodie/Tauric/pulls) retains completed and superseded work.

## Verified deployment and remaining acceptance

As of 14 October 2026, the independently verified production source is `b8ba3147b0cdb2ce2b846e601f74ce86607f30ce`, installed at 15:33 UTC. Its [full hosted release](https://github.com/aoodie/Tauric/actions/runs/38416824368) passed mandatory checks, five image publications and installation/restoration acceptance. Independent installation checks found all 16 services healthy, 161 loaded Python module hashes matching source, 42 protected settings and authority tables unchanged, and 9,488 historical readiness records preserved. The exact installation backup was restored in isolation before production changes. Private configuration, signing identities and original receipts were preserved; the separate release bridge is active.

Earlier real OpenClaw acceptance wrote source and executed two tests successfully, but the goal did not complete correctly. A subsequent exact repair application succeeded before its isolated compiler exhausted its heap; that was a failed build, not a release. Fresh acceptance on the installed flat task schema revealed that the model populated every shared optional field: a create request also included wait and completion fields, which the strict API correctly rejected. The adapter now selects the known fields for the requested action while preserving unknown fields and invalid required inputs for server rejection. Four local regression tests, the full plugin build, plugin validation and configuration checks pass. This additional repair still needs a checked release and fresh model-driven acceptance.

Full autonomous project ownership remains unverified. Research availability does not grant financial execution permission; existing account, evidence, policy, approval and reconciliation controls remain mandatory.

## Private state

Production credentials, GitHub App private keys, SSH keys, database contents, private backups, broker state and local execution artifacts stay outside GitHub source. Repository organization does not change financial permissions or activate trading.
