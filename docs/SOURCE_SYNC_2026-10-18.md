# Tauric source reconciliation — 18 October 2026

This release combines published application source `27e8e152ba55695b25fe6576a06b301399a292c3`, the three additive host recovery files from `f4edbd551487dadd54b0aac6fcf36d0b919e307c`, and the workstation changes preserved in the pre-sync source backup. The shared Git baseline for overlapping changes was `1f97c750dcf13d52391142aa36db7ba76e8bd423`.

The integrated source retains durable task supervision, verified engineering-output handoff, the local Qwen heartbeat and dashboard, Research Lab tooling, and owner-configured risk ceilings. General project tools still reject financial policy changes and activation of broker routes. Saved financial settings, credentials, explicit pauses and live-account state are outside this source reconciliation.

API contracts were regenerated from the combined application. The mandatory checks include deployment controls, registry handoff controls, OpenClaw scope/configuration contracts and the local heartbeat evidence reader. The hosted release must pass complete checks, publish source-bound image digests and rehearse installation and backup restoration before production installation.

Production target: the existing Oracle deployment serving `https://tauric.aoodie.xyz`. Its pre-upgrade source was `1a3f0766b9e78934c1d0c7a43c69550c4bc08074`. Financial execution services were disabled there. The separate IONOS deployment and workstation runtime have their own state and are not implicitly upgraded by this Oracle release.

This document records the release scope. It does not claim installation success; exact commit, hosted workflow, backup/restore and post-install evidence will be recorded separately after verification. Complete autonomous project ownership remains subject to its existing acceptance criteria.
