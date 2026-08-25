"""Replay-side proof, against a corpus whose answers are known by construction."""
import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

RUN = Path("/opt/bujji/gate1-run")
PY = "/opt/bujji/.venv/bin/python"
ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(f"  [{'OK ' if cond else 'BAD'}] {name}" + (f" -- {detail}" if detail else ""))


def build_corpus(tmp, *, shuffle=False, drop_seq=None, omit_seq_field=False,
                 tamper_payload=False, seal=True, tamper_manifest=False,
                 wall_jump=False):
    """A KNOWN corpus: 3 symbols, 10 records each, exactly 1.0s apart."""
    sess = Path(tmp); sess.mkdir(parents=True, exist_ok=True)
    recs, seq = [], 0
    for i in range(10):
        for s_i, sym in enumerate(["NSE:A", "NSE:B", "NSE:C"]):
            seq += 1
            wall = 1_700_000_000.0 + i
            if wall_jump and i >= 5:
                wall -= 3600            # NTP step backwards mid-session
            payload = {"symbol": sym, "ltp": 100.0 + i, "type": "sf",
                       "bid_price": 99.0 + i, "ask_price": 101.0 + i,
                       "bid_size": 50, "ask_size": 75,
                       "open_interest": 1000 + i, "vol_traded_today": 500 * (i + 1),
                       "exch_feed_time": 1_700_000_000 + i}
            r = {"seq": seq, "recv_ts": wall, "recv_monotonic": 500.0 + i,
                 "tid": 140000, "payload": payload}
            if omit_seq_field and seq == 7:
                r.pop("seq")
            recs.append(r)
    if drop_seq:
        recs = [r for r in recs if r.get("seq") not in drop_seq]
    if shuffle:
        recs = recs[::-1]               # file order reversed; seq still valid
    corpus = sess / "raw_full.jsonl"
    corpus.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))

    written = len(recs)
    (sess / "gate1_results.json").write_text(json.dumps({
        "feed_state": {"full": {"corpus": {
            "offered": written, "written": written, "rejected": 0,
            "dropped": 0, "in_flight": 0, "accounted": True, "settled": True}}}}))
    # SEAL THE CLEAN BYTES FIRST. Tampering before sealing would produce a
    # seal that legitimately matches the altered file, and the test would then
    # be proving nothing -- which is exactly what it did on the first run.
    if seal:
        h = hashlib.sha256(corpus.read_bytes()).hexdigest()
        if tamper_manifest:
            h = "0" * 64
        (sess / "gate1_manifest.json").write_text(json.dumps({
            "manifest": {"host": "test", "checkout_sha": "abc",
                         "harness_sha256": "def", "universe_sha256": "ghi"},
            "artifact_seal": {"raw_full.jsonl": {"sha256": h}}}))

    # NOW tamper, after the seal exists -- the real attack shape.
    if tamper_payload:
        for r in recs:
            if r.get("seq") == 3:
                r["payload"]["ltp"] = 999999.0
        corpus.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n"
                                  for r in recs))
    return sess


def replay(sess):
    r = subprocess.run([PY, str(RUN / "gate1_replay.py"), "--session-dir", str(sess)],
                       capture_output=True, text=True)
    rep = json.loads((Path(sess) / "gate1_replay_report.json").read_text())
    return r.returncode, rep


print("1. KNOWN-ANSWER REPLAY (clean corpus)")
rc, rep = replay(build_corpus(tempfile.mkdtemp()))
a = rep["analysis"]
check("certifiable", rep["certifiable"], f"blockers={rep['blockers']}")
check("30 records replayed", a["records_replayed"] == 30)
check("per-symbol counts exact", a["per_symbol_counts"] == {"NSE:A": 10, "NSE:B": 10, "NSE:C": 10})
check("interarrival gap is exactly 1.0s",
      abs(a["interarrival_gaps_s"]["p50"] - 1.0) < 1e-9,
      str(a["interarrival_gaps_s"]["p50"]))
check("bid/ask analysed", a["field_availability"]["bid_price"]["status"] == "PRESENT")
check("OI analysed", a["field_availability"]["open_interest"]["status"] == "PRESENT")
check("volume analysed", a["field_availability"]["vol_traded_today"]["status"] == "PRESENT")
check("exchange timestamp analysed",
      a["field_availability"]["exch_feed_time"]["status"] == "PRESENT")

print("\n2. MISSING OPTIONAL FIELDS ARE 'UNAVAILABLE', NOT ZERO")
st = a["field_availability"]["prev_oi"]["status"]
check("absent field reported NOT_DELIVERED_BY_SDK", st == "NOT_DELIVERED_BY_SDK", st)
check("no depth claimed when none present", "note" in a["depth_fields_seen"])
check("provenance says SDK payload, not wire frame",
      "NOT the raw exchange wire frame" in a["payload_provenance"])

print("\n3. OUT-OF-ORDER FILE LINES, VALID SEQUENCE")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), shuffle=True))
a = rep["analysis"]
check("still certifiable with reversed file order", rep["certifiable"])
check("gaps still exactly 1.0s after sorting by seq",
      abs(a["interarrival_gaps_s"]["p50"] - 1.0) < 1e-9,
      str(a["interarrival_gaps_s"]["p50"]))
check("replay declares it sorts by sequence", "sequence" in a["replay_order"])

print("\n4. SEQUENCE HOLE (records missing)")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), drop_seq={5, 6, 7}))
check("refused for certification", not rep["certifiable"])
check("hole detected", rep["verification"]["sequence"]["holes"] == 3,
      str(rep["verification"]["sequence"]["holes"]))
check("still produced analysis for diagnosis", rep["analysis"]["records_replayed"] == 27)

print("\n5. MISSING SEQUENCE FIELD")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), omit_seq_field=True))
check("refused for certification", not rep["certifiable"])
check("counted records without sequence",
      rep["verification"]["sequence"]["records_without_sequence"] == 1)

print("\n6. TAMPERED PAYLOAD")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), tamper_payload=True))
check("hash mismatch detected", not rep["verification"]["artifact_hash"]["match"])
check("refused for certification", not rep["certifiable"])

print("\n7. TAMPERED MANIFEST")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), tamper_manifest=True))
check("refused for certification", not rep["certifiable"])

print("\n8. UNSEALED CORPUS")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), seal=False))
check("refused for certification", not rep["certifiable"])
check("named as unsealed", any("UNSEALED" in b for b in rep["blockers"]),
      str(rep["blockers"][:1]))

print("\n9. WALL-CLOCK JUMP IN THE CORPUS")
rc, rep = replay(build_corpus(tempfile.mkdtemp(), wall_jump=True))
a = rep["analysis"]
check("monotonic gaps unaffected by the step",
      abs(a["interarrival_gaps_s"]["p50"] - 1.0) < 1e-9,
      str(a["interarrival_gaps_s"]["p50"]))
check("skew between the clocks is reported",
      a["clock_skew_wall_minus_monotonic_s"] is not None,
      str(a["clock_skew_wall_minus_monotonic_s"]))

print("\n10. CONTROL -- a clean corpus must be able to certify")
rc, rep = replay(build_corpus(tempfile.mkdtemp()))
check("CONTROL: clean corpus certifies", rep["certifiable"] and rc == 0,
      "otherwise every refusal above proves nothing")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
