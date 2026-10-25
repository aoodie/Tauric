# Credential encryption keys

Saved AI, data-provider, chat and related connection revisions use the shared credential encryption helper. A legacy file containing one Fernet key remains supported. A retained-key file contains one key per line: the first encrypts new revisions and all listed keys can decrypt existing revisions. Invalid or duplicate key material fails closed.

Historical credential revisions are immutable. Rotation therefore retains their keys and does not rewrite database history, account identity, financial policy or readiness records. Keep every key required by retained revisions and backups; a new key is not a replacement for disaster-recovery storage.

## Stage and verify

From the complete source checkout with its locked Python environment:

```sh
.venv/bin/python scripts/stage_key_rotation.py --key-file .env.model-key --output .env.model-key.next
```

The command validates the existing material and stages a new first key followed by the retained keys. It creates the output with mode 0600, refuses an existing output, never replaces the active key file, and prints only non-secret status. It does not query a broker, call a model or change saved settings. Do not publish either key file.

## Controlled activation

1. Verify a recovery backup containing the old key material, and retain the staged keyring in protected recovery storage.
2. Install a tested application version that understands retained keys while the legacy single-key file is still active. Every credential reader must be upgraded before activation; older images cannot read a multi-key file.
3. Through the trusted deployment/recovery process, activate the staged material and refresh the existing file-mounted-secret consumers without enabling paused services. Follow the existing host secret permissions: server leaf files are readable inside a private 0700 directory, whereas workstation/staged files remain 0600. Replacing a host file alone does not refresh an existing Docker file bind mount.
4. Verify old saved connection revisions still decrypt and that new revisions encrypt under the first key. Preserve all retained keys for immutable history and older backup restoration. Returning to an older application also requires a compatible key file; new ciphertext cannot be read with only the original key.

The staging tool is an implementation capability, not evidence that production keys have been rotated. No production or workstation key was activated by the 25 October audit remediation. Regression coverage includes immutable historical broker credentials, new-key encryption, legacy-file compatibility, overwrite refusal and source-export scanning of each retained key.
