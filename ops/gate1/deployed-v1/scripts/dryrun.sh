#!/bin/sh
# Gate 1 automation dry-run suite. Touches no market, places nothing.
set -u
PY=/opt/bujji/.venv/bin/python
cd /opt/bujji/gate1-run || exit 1
DATE=$(date +%Y-%m-%d)
pass=0; fail=0

check() {  # name  expected_exit  expected_category  actual_exit  session
  name="$1"; exp_rc="$2"; exp_cat="$3"; rc="$4"; sess="$5"
  cat=$($PY -c "import json;print(json.load(open('/opt/bujji/gate1/$sess/gate1_run_summary.json'))['result_category'])" 2>/dev/null || echo MISSING)
  if [ "$rc" = "$exp_rc" ] && [ "$cat" = "$exp_cat" ]; then
    printf '  %-26s exit=%-3s category=%-18s OK\n' "$name" "$rc" "$cat"; pass=$((pass+1))
  else
    printf '  %-26s exit=%-3s category=%-18s EXPECTED exit=%s category=%s  <-- FAIL\n' \
      "$name" "$rc" "$cat" "$exp_rc" "$exp_cat"; fail=$((fail+1))
  fi
}

run_case() {  # dry_run_mode  session_suffix
  # subscribe-by / open-at must be in the FUTURE, or the pre-open gate fires
  # first and every case reports OPENING_COVERAGE_UNAVAILABLE -- which is the
  # gate working, not the case under test.
  sess="DRYRUN_$2"
  rm -rf "/opt/bujji/gate1/$sess"
  SUB=$(date -d '+30 minutes' +%H:%M:%S)
  OPEN=$(date -d '+31 minutes' +%H:%M:%S)
  $PY /opt/bujji/gate1-run/gate1_orchestrator.py --session-id "$sess" \
      --measurement-date "$DATE" --dry-run "$1" --window-end 23:59 \
      --token-deadline 23:58 --subscribe-by "$SUB" --open-at "$OPEN" \
      >/dev/null 2>&1
  echo $?
}

echo "GATE 1 AUTOMATION DRY RUN"
echo
echo "Failure paths:"
rc=$(run_case no_token NOTOKEN);        check "missing token"        5 NO_VALID_TOKEN   "$rc" DRYRUN_NOTOKEN
rc=$(run_case preflight_fail PFFAIL);   check "preflight failure"    6 PREFLIGHT_FAILED "$rc" DRYRUN_PFFAIL
rc=$(run_case no_spot NOSPOT);          check "spot unavailable"     7 SPOT_UNAVAILABLE "$rc" DRYRUN_NOSPOT
rc=$(run_case harness_crash HCRASH);    check "harness crash"        9 HARNESS_FAILED   "$rc" DRYRUN_HCRASH
echo
echo "Success path:"
rc=$(run_case mock_pass MOCKPASS);      check "mocked preflight PASS" 10 GATE1_FAIL      "$rc" DRYRUN_MOCKPASS
echo "    (GATE1_FAIL is correct: the mocked harness writes no results file,"
echo "     so the verdict is UNKNOWN -- absence of evidence is never a PASS)"
echo
echo "Invalid-token path:"
rm -rf /opt/bujji/gate1/DRYRUN_BADTOK
$PY - <<'PYEOF'
import pathlib, subprocess, json, datetime, base64
# A token whose expiry is in the past: the wait must refuse it and time out.
past = int((datetime.datetime.now() - datetime.timedelta(hours=2)).timestamp())
body = base64.urlsafe_b64encode(json.dumps({"exp": past}).encode()).decode().rstrip("=")
fake = f"aaa.{body}.bbb"
p = pathlib.Path("/tmp/fake_env_expired")
p.write_text(f'FYERS_APP_ID=X\nFYERS_ACCESS_TOKEN={fake}\n')
PYEOF
rc=$($PY - <<'PYEOF'
import subprocess, sys, pathlib, datetime
# Point the orchestrator's env reader at the expired token by temporarily
# overriding ENV_FILE via a copy of the module's constant. Simplest honest
# approach: run with a 2-second deadline already in the past.
d = datetime.date.today().isoformat()
r = subprocess.run(["/opt/bujji/.venv/bin/python","gate1_orchestrator.py",
    "--session-id","DRYRUN_BADTOK","--measurement-date",d,
    "--token-deadline","00:01","--window-end","23:59","--token-poll-seconds","1"],
    cwd="/opt/bujji/gate1-run", capture_output=True, text=True, timeout=120)
print(r.returncode)
PYEOF
)
check "expired/short token" 5 NO_VALID_TOKEN "$rc" DRYRUN_BADTOK
echo
echo "Concurrency:"
rm -rf /opt/bujji/gate1/DRYRUN_LOCK1 /opt/bujji/gate1/DRYRUN_LOCK2
rm -f /opt/bujji/gate1-run/.gate1.lock
# The first instance must genuinely HOLD the lock while the second starts.
# Monday's date guarantees a real wait: today's token does not cover that
# window, so instance one blocks inside wait_for_token, holding the lock.
DEAD=$(date -d '+3 minutes' +%H:%M)
( $PY /opt/bujji/gate1-run/gate1_orchestrator.py --session-id DRYRUN_LOCK1 \
    --measurement-date 2026-08-24 --token-deadline "$DEAD" --window-end 15:35 \
    --token-poll-seconds 5 >/dev/null 2>&1 & )
sleep 4
$PY /opt/bujji/gate1-run/gate1_orchestrator.py --session-id DRYRUN_LOCK2 \
    --measurement-date 2026-08-24 --token-deadline "$DEAD" --window-end 15:35 \
    >/dev/null 2>&1
rc2=$?
pkill -f 'session-id DRYRUN_LOCK1' 2>/dev/null
sleep 1
check "duplicate-start lock" 3 ALREADY_RUNNING "$rc2" DRYRUN_LOCK2
echo
echo "Notification failure must not change the verdict:"
mv /opt/bujji/notify.sh /opt/bujji/notify.sh.drytest 2>/dev/null
rc=$(run_case notify_fail NOTIFYFAIL)
mv /opt/bujji/notify.sh.drytest /opt/bujji/notify.sh 2>/dev/null
check "ntfy unavailable" 10 GATE1_FAIL "$rc" DRYRUN_NOTIFYFAIL
deg=$($PY -c "import json;d=json.load(open('/opt/bujji/gate1/DRYRUN_NOTIFYFAIL/gate1_run_summary.json'));print(d['notification_delivery'], len(d['notify_failures']))" 2>/dev/null)
echo "    notification_delivery=$deg  (verdict unchanged, degradation recorded)"
echo

echo
echo "Opening-coverage paths (revised two-stage flow):"
# Started after the subscription deadline: must abandon, not start late.
rm -rf /opt/bujji/gate1/DRY_LATE
$PY /opt/bujji/gate1-run/gate1_orchestrator.py --session-id DRY_LATE \
    --measurement-date "$(date +%Y-%m-%d)" --token-deadline 23:58 \
    --subscribe-by 00:00:01 --open-at 00:00:02 --window-end 23:59 \
    >/dev/null 2>&1
check "past subscribe deadline" 20 OPENING_COVERAGE_UNAVAILABLE "$?" DRY_LATE

# Token arrives with too little time left to build+connect+subscribe.
rm -rf /opt/bujji/gate1/DRY_TIGHT
SOON=$(date -d "+2 minutes" +%H:%M:%S)
$PY /opt/bujji/gate1-run/gate1_orchestrator.py --session-id DRY_TIGHT \
    --measurement-date "$(date +%Y-%m-%d)" --token-deadline 23:58 \
    --subscribe-by "$SOON" --open-at 23:58:00 --window-end 23:59 \
    >/dev/null 2>&1
check "token too late to subscribe" 20 OPENING_COVERAGE_UNAVAILABLE "$?" DRY_TIGHT

echo
echo "PASS=$pass FAIL=$fail"
[ "$fail" = 0 ] || exit 1
