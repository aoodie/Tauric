# Host memory recovery

This is the historical 4 GB legacy-host recovery record. A direct Oracle inspection on 25 October 2026 found 16,765,104,128 bytes of physical RAM and no configured swap; the legacy 4 GB / 2 GiB-swap assumptions do not describe that host. See the [corrected audit](AUDIT_REVIEW_2026-10-25.md). Measure the current host before recommending a memory change.

The 4 GB Tauric VPS had no active swap on 15 October 2026. Kernel records show OpenClaw container OOM kills on 14 October at 17:15 and 17:26 UTC, with roughly 1.38 GiB of gateway anonymous memory. The running container permits 1.5 GiB of RAM and 3 GiB of combined RAM and swap. Recent memory and I/O pressure also accompanied stalled SSH and HTTPS handshakes.

`scripts/host_memory.py` provides a trusted operator recovery path that allocates a private 2 GiB swap file on the existing ext4 disk and enables its systemd swap unit at boot. This buffer provides room for short RAM bursts. It does not increase physical RAM or change container limits, model settings, financial authority, application images, `fstab` or kernel tuning.

Run `python3 scripts/host_memory.py check` to reconcile the current buffer. `apply` requires root and is reserved for the trusted operator recovery process. It refuses foreign swap configurations, symlinks, unsafe existing files, unexpected units and insufficient disk reserve. Existing files are never reformatted or truncated. The boot unit is validated before activation.

Record the exact checked source commit and script checksum before installation. Preserve the existing recovery backups. Compare container/image identities, private configuration and signing-key hashes before and after; independently verify active swap, enabled boot persistence and service health. A response timeout requires reconciliation of the original operation before any retry.

Validation includes eight host-recovery regressions and the existing deployment suite. These checks do not establish full autonomous ownership; that acceptance remains separate.
