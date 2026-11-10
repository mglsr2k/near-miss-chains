"""Offline build: raw ASRS exports + the NTSB Access database -> compact Parquet files in data/.

Run once (or after adding more ASRS exports):
    uv run build_data.py

Inputs (not committed, see README "Data"):
    raw/asrs/*.csv     ASRS Database Online exports (all fields), any number of files
    raw/ntsb/avall.mdb NTSB avall.mdb from https://data.ntsb.gov/avdata (needs `mdb-export` from mdbtools)

Outputs (committed, a few MB):
    data/asrs_reports.parquet  one row per ASRS report
    data/ntsb_events.parquet   one row per NTSB event (accident or incident)
    data/tags.parquet          (source, id, kind, tag, method): shared factors/events for both
    data/build_info.json       counts and date ranges, shown in the UI and to the agents
"""

import csv
import glob
import io
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

from taxonomy import ASRS_PHASE, EVENTS, FACTORS, asrs_operation, ntsb_operation, ntsb_phase

HERE = Path(__file__).parent
RAW = HERE / "raw"
OUT = HERE / "data"
NTSB_FACTUAL_CHARS = 3500  # the factual narrative is long; the probable cause is kept whole

csv.field_size_limit(sys.maxsize)


# --- ASRS ----------------------------------------------------------------------------------------

# (group, column) in the export's two header rows -> our column name
ASRS_COLUMNS = {
    ("", "ACN"): "acn",
    ("Time", "Date"): "date",
    ("Time", "Local Time Of Day"): "time_of_day",
    ("Place", "Locale Reference"): "locale",
    ("Place", "State Reference"): "state",
    ("Place", "Altitude.MSL.Single Value"): "altitude_msl",
    ("Environment", "Flight Conditions"): "flight_conditions",
    ("Environment", "Weather Elements / Visibility"): "weather",
    ("Environment", "Light"): "light",
    ("Aircraft 1", "Aircraft Operator"): "operator",
    ("Aircraft 1", "Make Model Name"): "aircraft",
    ("Aircraft 1", "Operating Under FAR Part"): "far_part",
    ("Aircraft 1", "Flight Plan"): "flight_plan",
    ("Aircraft 1", "Mission"): "mission",
    ("Aircraft 1", "Flight Phase"): "flight_phase",
    ("Aircraft 1", "Airspace"): "airspace",
    ("Aircraft 2", "Make Model Name"): "aircraft_2",
    ("Component", "Aircraft Component"): "component",
    ("Component", "Problem"): "component_problem",
    ("Person 1", "Function"): "reporter_function",
    ("Person 1", "Reporter Organization"): "reporter_org",
    ("Person 1", "Experience"): "reporter_experience",
    ("Person 1", "Human Factors"): "hf_1",
    ("Person 2", "Human Factors"): "hf_2",
    ("Person 1", "Communication Breakdown"): "comm_breakdown",
    ("Events", "Anomaly"): "anomaly",
    ("Events", "Miss Distance"): "miss_distance",
    ("Events", "Detector"): "detector",
    ("Events", "When Detected"): "when_detected",
    ("Events", "Result"): "result",
    ("Assessments", "Contributing Factors / Situations"): "contributing",
    ("Assessments", "Primary Problem"): "primary_problem",
    ("Report 1", "Narrative"): "narrative_1",
    ("Report 1", "Callback"): "callback_1",
    ("Report 2", "Narrative"): "narrative_2",
    ("Report 2", "Callback"): "callback_2",
    ("Report 1", "Synopsis"): "synopsis",
}


def prose(text: str) -> str:
    """ASRS's CSV export swaps every comma in free text for a semicolon: put them back."""
    return re.sub(r";(?=\s|$)", ",", text)


def read_asrs() -> list[dict]:
    files = sorted(glob.glob(str(RAW / "asrs" / "*.csv")))
    if not files:
        sys.exit("No ASRS exports in raw/asrs/")
    reports: dict[str, dict] = {}
    for path in files:
        rows = list(csv.reader(open(path, encoding="utf-8", errors="replace", newline="")))
        groups, names = rows[0], rows[1]
        # The first header row only names a group where it starts: carry it to the right
        current, index = "", {}
        for i, (g, n) in enumerate(zip(groups, names)):
            current = g.strip() or current if i else ""
            key = (current, n.strip())
            if key in ASRS_COLUMNS and ASRS_COLUMNS[key] not in index.values():
                index[i] = ASRS_COLUMNS[key]
        for row in rows[2:]:
            if not row or not row[0].strip().isdigit():
                continue
            r = {name: (row[i].strip() if i < len(row) else "") for i, name in index.items()}
            for k in ("narrative_1", "callback_1", "narrative_2", "callback_2", "synopsis"):
                r[k] = prose(r[k])
            reports[r["acn"]] = r  # the same ACN in two exports is one report
        print(f"  {Path(path).name}: {len(rows) - 2} rows")
    out = []
    for r in reports.values():
        hf = sorted({v.strip() for v in (r["hf_1"] + ";" + r["hf_2"]).split(";") if v.strip()})
        narrative = "\n\n".join(
            part for part in [
                r["narrative_1"],
                r["callback_1"] and "Callback: " + r["callback_1"],
                r["narrative_2"] and "Second reporter: " + r["narrative_2"],
                r["callback_2"] and "Callback: " + r["callback_2"],
            ] if part
        )
        d = r["date"]
        out.append({
            "acn": r["acn"],
            "date": f"{d[:4]}-{d[4:6]}" if len(d) == 6 else d,
            "year": int(d[:4]) if d[:4].isdigit() else None,
            "time_of_day": r["time_of_day"],
            "locale": r["locale"],
            "state": r["state"],
            "flight_conditions": r["flight_conditions"],
            "weather": r["weather"],
            "light": r["light"],
            "operator": r["operator"],
            "far_part": r["far_part"],
            "operation": asrs_operation(r["far_part"]),
            "aircraft": r["aircraft"],
            "aircraft_2": r["aircraft_2"],
            "flight_phase": r["flight_phase"],
            "phase": ASRS_PHASE.get(r["flight_phase"].split(";")[0].strip()),
            "mission": r["mission"],
            "flight_plan": r["flight_plan"],
            "airspace": r["airspace"],
            "component": r["component"],
            "component_problem": r["component_problem"],
            "reporter_function": r["reporter_function"],
            "reporter_org": r["reporter_org"],
            "reporter_experience": r["reporter_experience"],
            "human_factors": "; ".join(hf),
            "comm_breakdown": r["comm_breakdown"],
            "anomaly": r["anomaly"],
            "miss_distance": r["miss_distance"],
            "detector": r["detector"],
            "when_detected": r["when_detected"],
            "result": r["result"],
            "contributing": r["contributing"],
            "primary_problem": r["primary_problem"],
            "synopsis": r["synopsis"],
            "narrative": narrative,
        })
    return out


def tag_asrs(report: dict) -> list[tuple]:
    tags = []
    text = report["synopsis"] + "\n" + report["narrative"]
    for fid, f in FACTORS.items():
        if f.get("condition"):
            continue
        if any(sub in report[col] for col, sub in f["asrs_coded"]):
            tags.append(("asrs", report["acn"], "factor", fid, "coded"))
        elif f["asrs_text"] and re.search(f["asrs_text"], text, re.I):
            tags.append(("asrs", report["acn"], "factor", fid, "text"))
    if any(v in report["light"] for v in ("Night", "Dusk", "Dawn")):
        tags.append(("asrs", report["acn"], "factor", "night", "coded"))
    if "IMC" in report["flight_conditions"]:
        tags.append(("asrs", report["acn"], "factor", "imc", "coded"))
    for eid, e in EVENTS.items():
        if any(sub in report["anomaly"] for sub in e["asrs"]):
            tags.append(("asrs", report["acn"], "event", eid, "coded"))
    return tags


# --- NTSB ----------------------------------------------------------------------------------------


def export_table(mdb: Path, table: str) -> list[dict]:
    """mdb-export a table to CSV and parse it."""
    out = subprocess.run(["mdb-export", str(mdb), table], check=True, capture_output=True).stdout
    return list(csv.DictReader(io.StringIO(out.decode("utf-8", errors="replace"))))


def occurrence_names(occurrences) -> dict[str, str]:
    """Occurrence descriptions are "<phase> <event>", e.g. "Landing-landing roll Loss of control on
    ground". The phase is the words every description with that phase code starts with."""
    by_phase = defaultdict(set)
    for s in occurrences:
        if len(s["Occurrence_Code"]) == 6:
            by_phase[s["Occurrence_Code"][:3]].add((s["Occurrence_Code"], s["Occurrence_Description"]))
    names = {}
    for items in by_phase.values():
        words = [d.split(" ") for _, d in items]
        n = 0
        while len(items) > 1 and all(len(w) > n + 1 and w[n] == words[0][n] for w in words):
            n += 1
        n = max(n, 1)
        for code, d in items:
            names[code] = " ".join(d.split(" ")[n:])
    return names


def read_ntsb() -> tuple[list[dict], list[tuple]]:
    mdb = RAW / "ntsb" / "avall.mdb"
    if not mdb.exists():
        sys.exit("No raw/ntsb/avall.mdb (download avall.zip from https://data.ntsb.gov/avdata)")
    events = export_table(mdb, "events")
    aircraft = {(a["ev_id"], a["Aircraft_Key"]): a for a in export_table(mdb, "aircraft")}
    narratives = {(n["ev_id"], n["Aircraft_Key"]): n for n in export_table(mdb, "narratives")}
    findings = defaultdict(list)
    for f in export_table(mdb, "Findings"):
        findings[f["ev_id"]].append(f)
    sequence = defaultdict(list)
    for s in export_table(mdb, "Events_Sequence"):
        s["Occurrence_Code"] = (s["Occurrence_Code"] or "").strip()
        s["Occurrence_Description"] = re.sub(r"\s+", " ", s["Occurrence_Description"] or "").strip()
        sequence[s["ev_id"]].append(s)
    event_name = occurrence_names(s for seq in sequence.values() for s in seq)
    print(f"  events {len(events)}, aircraft {len(aircraft)}, findings {sum(map(len, findings.values()))}")

    rows, tags = [], []
    for e in events:
        ev = e["ev_id"]
        a = aircraft.get((ev, "1"), {})
        n = narratives.get((ev, "1"), {})
        seq = sorted(sequence.get(ev, []), key=lambda s: int(s["Occurrence_No"] or 0))
        defining = next((s for s in seq if s["Defining_ev"] == "1"), seq[0] if seq else None)
        # Events come from the defining event only: later steps ("collision with terrain") follow
        # almost every accident and would swamp the tags
        occ_codes = {defining["Occurrence_Code"].strip()} if defining and defining["Occurrence_Code"] else set()
        fnd = findings.get(ev, [])
        # " - C" = cause, " - F" = contributing factor, as NTSB codes them
        finding_lines = []
        for f in fnd:
            desc = re.sub(r"\s+-\s+[CF]$", "", f["finding_description"].strip())
            role = {"C": "cause", "F": "factor"}.get(f["Cause_Factor"].strip(), "finding")
            finding_lines.append(f"{desc} [{role}]")
        date = e["ev_date"][:8]  # mm/dd/yy
        mm, dd, yy = date[:2], date[3:5], date[6:8]
        year = int(e["ev_year"]) if e["ev_year"].isdigit() else None
        row = {
            "ev_id": ev,
            "ntsb_no": e["ntsb_no"],
            "ev_type": {"ACC": "accident", "INC": "incident"}.get(e["ev_type"], e["ev_type"]),
            "date": f"{year}-{mm}-{dd}" if year else "",
            "year": year,
            "city": e["ev_city"],
            "state": e["ev_state"],
            "country": e["ev_country"],
            "light_cond": e["light_cond"],
            "wx_cond": e["wx_cond_basic"],
            "highest_injury": {"FATL": "fatal", "SERS": "serious", "MINR": "minor", "NONE": "none"}.get(
                e["ev_highest_injury"], e["ev_highest_injury"] or "unknown"),
            "fatalities": int(e["inj_tot_f"]) if e["inj_tot_f"].isdigit() else 0,
            "aircraft": " ".join(x for x in [a.get("acft_make", ""), a.get("acft_model", "")] if x).strip(),
            "aircraft_category": a.get("acft_category", ""),
            "far_part": a.get("far_part", ""),
            "operation": ntsb_operation(a.get("far_part", ""), a.get("acft_category", "")),
            "operator": a.get("oper_name", ""),
            "damage": a.get("damage", ""),
            "phase": ntsb_phase(defining["Occurrence_Code"]) if defining else None,
            "defining_event": event_name.get(defining["Occurrence_Code"], "") if defining else "",
            "sequence_of_events": " -> ".join(s["Occurrence_Description"] for s in seq),
            "findings": "\n".join(finding_lines),
            "probable_cause": (n.get("narr_cause") or "").strip(),
            "factual": (n.get("narr_accf") or n.get("narr_accp") or "").strip()[:NTSB_FACTUAL_CHARS],
        }
        rows.append(row)

        # Text rules read only the probable cause: NTSB's conclusion, not every fact it gathered
        text = row["probable_cause"]
        finding_text = "\n".join(f["finding_description"] for f in fnd)
        for fid, f in FACTORS.items():
            if f.get("condition"):
                continue
            if f["ntsb_coded"] and re.search(f["ntsb_coded"], finding_text):
                tags.append(("ntsb", ev, "factor", fid, "coded"))
            elif f["ntsb_text"] and re.search(f["ntsb_text"], text, re.I):
                tags.append(("ntsb", ev, "factor", fid, "text"))
        if e["light_cond"] in ("NITE", "NDRK", "NBRT", "DUSK", "DAWN"):
            tags.append(("ntsb", ev, "factor", "night", "coded"))
        if e["wx_cond_basic"] == "IMC":
            tags.append(("ntsb", ev, "factor", "imc", "coded"))
        for eid, ev_def in EVENTS.items():
            if any(code[3:] in ev_def["ntsb"] for code in occ_codes):
                tags.append(("ntsb", ev, "event", eid, "coded"))
    return rows, tags


def main():
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect()

    print("ASRS")
    asrs = read_asrs()
    asrs_tags = [t for r in asrs for t in tag_asrs(r)]
    print("NTSB")
    ntsb, ntsb_tags = read_ntsb()

    import pyarrow as pa

    def write(rows, name):
        table = pa.Table.from_pylist(rows)
        con.register("t", table)
        con.execute(f"COPY t TO '{OUT / name}' (FORMAT parquet, COMPRESSION zstd)")
        con.unregister("t")

    write(asrs, "asrs_reports.parquet")
    write(ntsb, "ntsb_events.parquet")
    tag_rows = [dict(zip(("source", "id", "kind", "tag", "method"), t)) for t in asrs_tags + ntsb_tags]
    write(tag_rows, "tags.parquet")

    info = {
        "asrs": {
            "reports": len(asrs),
            "first": min(r["date"] for r in asrs),
            "last": max(r["date"] for r in asrs),
        },
        "ntsb": {
            "events": len(ntsb),
            "accidents": sum(r["ev_type"] == "accident" for r in ntsb),
            "first": min(r["date"] for r in ntsb if r["date"]),
            "last": max(r["date"] for r in ntsb if r["date"]),
        },
        "tags": len(tag_rows),
    }
    (OUT / "build_info.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))
    for f in sorted(OUT.glob("*.parquet")):
        print(f"  {f.name}: {f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
