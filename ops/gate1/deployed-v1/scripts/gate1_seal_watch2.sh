#!/bin/sh
# Re-armed seal watcher. NOTIFY ONLY.
#
# TWO CORRECTIONS OVER THE FIRST ONE.
#
# 1. IT WRITES NOTHING INTO THE ARTIFACT DIRECTORY. The first watcher logged
#    into GATE1_20260824_POSTOPEN/ and ran the table exporter there on exit.
#    Artifact preservation and checksumming is now step 1 of the post-seal
#    sequence, so anything writing beside the corpus before that step
#    corrupts the very thing being preserved. This logs outside the session
#    directory and derives nothing.
#
# 2. ITS BACKSTOP OUTLIVES THE EVENT. The first watcher was given a 17:30
#    backstop while the capture was expected to end at 15:35. The measure_v5
#    deadline defect pushed the real end to 20:26:21, and the watcher expired
#    at 17:30 with the capture still running -- a watcher that stopped
#    watching before the thing it was watching for. 22:30 clears the 20:26
#    phase end plus offline re-parse and sealing.
set -u

LOG=/opt/bujji/gate1_seal_watch2.log          # outside the artifact directory
NOTIFY=/opt/bujji/notify.sh
DEADLINE=$(date -d 'today 22:30' +%s 2>/dev/null || echo 0)

say() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
say "re-armed seal watcher started (notify only; backstop 22:30)"

while pgrep -f gate1_measure_v5.py > /dev/null 2>&1; do
    if [ "$DEADLINE" -ne 0 ] && [ "$(date +%s)" -gt "$DEADLINE" ]; then
        say "BACKSTOP 22:30 reached with the harness still running"
        [ -x "$NOTIFY" ] && "$NOTIFY" "Bujji capture: still running at 22:30" \
            "Post-open capture had not exited by 22:30 IST. Nothing stopped or changed." \
            "high" "warning"
        exit 0
    fi
    sleep 60
done

say "harness exited; artifacts NOT touched (preservation is step 1, done by hand)"
[ -x "$NOTIFY" ] && "$NOTIFY" "Bujji capture: sealed" \
    "Post-open capture has exited and sealing is complete. Artifacts untouched, awaiting preservation and checksum. Full sustained phase remains classified INVALID (post-close overrun)." \
    "default" "abacus"
say "notified"
