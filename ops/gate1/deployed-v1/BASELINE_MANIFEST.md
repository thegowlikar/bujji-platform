# Gate 1 deployed-surface baseline — v1

IMMUTABLE RECORD OF WHAT ACTUALLY RAN on 2026-08-24. This is a snapshot of
the Gate 1 deployment surface as deployed, captured BEFORE any repair, so
every subsequent fix reads as a visible change rather than as something
that was always there.

captured_at_ist: 2026-08-24 17:51:09 IST
captured_from:   /opt/bujji/gate1-run and /etc/systemd/system (production host)
branch:          m4/one-state-machine @ eca9682

## Deployed surface (path, mode, sha256)

| source path | mode | sha256 |
|---|---|---|
| /opt/bujji/gate1-run/RUNBOOK.html | 644 | 63b514af9299958c0a7c13076abca897 |
| /opt/bujji/gate1-run/capacity.py | 644 | 37c3a5befe2a86e5d132161db9dff2a1 |
| /opt/bujji/gate1-run/dryrun.sh | 755 | 199c5a2671d4a938cb01b04d65cc425c |
| /opt/bujji/gate1-run/entry_capable_analysis.py | 644 | 1d90a01c1fccc9078877346dec39ed82 |
| /opt/bujji/gate1-run/gate1_build_universe.py | 644 | 8ae1deefbbb79b35697eeafe64b302bb |
| /opt/bujji/gate1-run/gate1_capacity_probe.py | 644 | 1f08bcf5067eb339b20aa53974af8fdc |
| /opt/bujji/gate1-run/gate1_measure_v5.py | 644 | a6ef1a314723e7d8eaeea5ed4597cd64 |
| /opt/bujji/gate1-run/gate1_opening_capture.py | 644 | 25640b7090166e46783518193c6f6f14 |
| /opt/bujji/gate1-run/gate1_orchestrator.py | 644 | 26aa9390a57c7bee5b2649b63f13714b |
| /opt/bujji/gate1-run/gate1_preflight.py | 644 | 86df2a0606433e52ae26d56a1aef9c5e |
| /opt/bujji/gate1-run/gate1_reconcile.py | 644 | 3f919f28991d331292716c6495aefde1 |
| /opt/bujji/gate1-run/gate1_replay.py | 644 | e8d9dc5aad415238c69481a8d792a976 |
| /opt/bujji/gate1-run/gate1_sandbox_selftest.py | 644 | d8aca26ed9f1554cd4766e3c3490e979 |
| /opt/bujji/gate1_seal_watch2.sh | 755 | f0e7ea7007c4e6efebfba73b5f76b554 |
| /opt/bujji/gate1-run/gate1_spot.py | 644 | af2f48f76d86867f2ee14664a97651a8 |
| /opt/bujji/gate1-run/prove_no_trading.py | 644 | 16ab6fe4d0f30ab52bc47e1b7956b820 |
| /etc/systemd/system/bujji-gate1-selftest.service | 644 | e753dacf6f2293ac1d32316148c6fe03 |
| /etc/systemd/system/bujji-gate1.service | 644 | e28e53c5f409717d315e5a0ad03d6e0d |
| /etc/systemd/system/bujji-gate1.timer | 644 | 554bc62b3421f8ceaa34e9c90a2a6b8d |
| /opt/bujji/gate1-run/test_capture_correctness.py | 644 | 84a84ef4f851ed4c9e38d54bf0defbb1 |
| /opt/bujji/gate1-run/test_close_time_authority.py | 644 | 2cfb7becfe4723252fb7ac219b1da549 |
| /opt/bujji/gate1-run/test_containment.py | 644 | 8a68f0c8fce53ae172df6db910b57d2c |
| /opt/bujji/gate1-run/test_opening_coverage.py | 644 | f376bd042a6712a15cc251f86784a48a |
| /opt/bujji/gate1-run/test_preflight_ordering.py | 644 | 5203548feaa907459e90115b63499150 |
| /opt/bujji/gate1-run/test_probe_isolation.py | 644 | 4a5d5acab057eb442868691081019a46 |
| /opt/bujji/gate1-run/test_replay.py | 644 | ff9d4ad8a2f7e668288f12fa3fec9460 |

## Pre-change backups — METADATA ONLY, deliberately not staged

These are historical forensic artifacts, not deployed execution surface.
Recording their hashes preserves the forensic link without importing
unreviewed content into the repository. They remain in place on the host.

| path | mode | mtime | sha256 |
|---|---|---|---|
| /opt/bujji/gate1-run/.RUNBOOK.html.bak-20260824 | 644 | 2026-08-23 16:47:16 | 9a9bc8a388e09f5cef3443caa9b86e4b |
| /opt/bujji/gate1-run/.bujji-gate1.service.bak-20260824 | 644 | 2026-08-24 09:28:44 | dccf30b10df7c1e894c294b15756a688 |
| /opt/bujji/gate1-run/.bujji-gate1.service.bak-20260824-window | 644 | 2026-08-24 14:54:03 | a07914c459e5386bd26c9d2f0a456489 |
| /opt/bujji/gate1-run/.bujji-gate1.timer.bak-20260824 | 644 | 2026-08-24 09:27:45 | 1f65113b09e3af76b42a601a454b73e4 |
| /opt/bujji/gate1-run/.gate1_orchestrator.py.bak-20260824-window | 644 | 2026-08-24 14:51:19 | f7944d2f04afba8f657d76a3dcec48c8 |
| /opt/bujji/gate1-run/.gate1_preflight.py.bak-20260824 | 644 | 2026-08-24 09:23:06 | 7cd455906b8d6e6e43d12abe6e23aa66 |
| /opt/bujji/gate1-run/.gate1_preflight.py.bak-20260824-window | 644 | 2026-08-24 14:53:14 | 0a3e269eef9311c955fce1713fb34c5a |

## Enablement — recorded, NOT versioned

The timers.target.wants symlink is deliberately excluded from version
control. Versioning it would let a checkout IMPLY enablement — a repository
side effect that arms a job. Unit definitions are versioned; enablement is
an explicit operator action, verified separately.

symlink:  /etc/systemd/system/timers.target.wants/bujji-gate1.timer
target:   /etc/systemd/system/bujji-gate1.timer
observed at capture: bujji-gate1.timer is enabled, next elapse Tue 2026-08-25 08:45:00 IST

RESTORING THIS BASELINE DOES NOT ARM ANYTHING.

## Deliberately excluded

| excluded | reason |
|---|---|
| /opt/bujji/.env, .env.bak-* | credentials |
| /opt/bujji/.ntfy | the ntfy topic IS the secret |
| /opt/bujji/notify.sh | mode 700, reads the topic |
| preflight_last.json, .gate1.lock | live run artifacts, not surface |
| /opt/bujji/gate1/** | corpora, results, sealed session dirs |
| timers.target.wants symlink | see Enablement above |
