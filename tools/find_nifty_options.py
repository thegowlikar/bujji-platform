import csv

with open("/opt/bujji/app/data/bhavcopy/BhavCopy_NSE_FO_0_0_0_20260729_F_0000.csv") as f:
    reader = csv.DictReader(f)
    types = set()
    rows = []
    for row in reader:
        if row["TckrSymb"] == "NIFTY":
            types.add(row["FinInstrmTp"])
            if row["FinInstrmTp"] == "IDO" and row["StrkPric"]:
                rows.append(row)

print("FinInstrmTp values for NIFTY:", types)
print("Sample option rows:", len(rows))
for r in rows[:5]:
    print(r["StrkPric"], r["OptnTp"], r["XpryDt"], r["UndrlygPric"], r["ClsPric"], r["FinInstrmNm"])

# Find near-ATM options for the nearest expiry
if rows:
    expiries = sorted(set(r["XpryDt"] for r in rows))
    nearest = expiries[0]
    spot = float(rows[0]["UndrlygPric"])
    print(f"\nNearest expiry: {nearest}, spot: {spot}")
    near = [r for r in rows if r["XpryDt"] == nearest and abs(float(r["StrkPric"]) - spot) <= 300]
    for r in sorted(near, key=lambda r: (float(r["StrkPric"]), r["OptnTp"]))[:20]:
        print(r["StrkPric"], r["OptnTp"], r["FinInstrmNm"], r["ClsPric"])
