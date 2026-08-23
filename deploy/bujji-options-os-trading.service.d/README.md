# Bujji Options OS — trading unit drop-ins

**These are the canonical, version-controlled copies of the systemd drop-ins
that configure `bujji-options-os-trading.service`.**

They were added on 2026-08-22. Before that they existed only under
`/etc/systemd/system/bujji-options-os-trading.service.d/`, which meant two
production safety behaviours — the operator alert on a failed session, and the
token pre-flight gate — were not version-controlled, not reviewable in a diff,
and not recoverable if the host were rebuilt.

| Drop-in | What it configures | Why it matters |
| --- | --- | --- |
| `onfailure.conf` | `OnFailure=bujji-alert@%n.service` | every non-zero exit, including `3` (UNSAFE) and `4` (PENDING_EVIDENCE), reaches the operator |
| `token-gate.conf` | `Requires=`/`After=bujji-token-preflight.service` | the session cannot start against a token that will die mid-day |

## This directory is a declaration, not a deployment

Nothing here is applied automatically. `deploy/` states the *desired*
configuration so it can be reviewed, tested and restored; installing it is a
deliberate operator action, and the install commands are in each file's header.

## Verified by

`tests/test_deploy_dropins_are_owned.py`, which asserts the token precondition
is declared, the alert target exists and leaves a durable record, and that
nothing narrows what systemd counts as failure for the trading unit.

> Note: `deploy/README.md` in the parent directory covers the **legacy**
> ORB-VWAP bot (`bujji-orb-vwap-legacy.service`), which is retired and
> unreachable — see ARCHITECTURE.md. It does not describe these files.
