"""Query layer over the Parquet files in data/: what the agents' tools call.

Every function returns plain dicts (JSON-safe) and the report IDs it showed, so the app can check
that the Writer only cites reports a tool actually returned.

IDs:   ASRS reports are cited as "ACN 1234567"; NTSB events as their NTSB number, e.g. "ERA22LA123".
"""

import json
import re
import threading
from pathlib import Path

import duckdb

from taxonomy import EVENTS, FACTORS, OPERATIONS, PHASES

DATA = Path(__file__).parent / "data"
INFO = json.loads((DATA / "build_info.json").read_text())

_local = threading.local()


def _con() -> duckdb.DuckDBPyConnection:
    """One connection per thread (tools run in worker threads), all over the same Parquet files."""
    if not hasattr(_local, "con"):
        con = duckdb.connect()
        con.execute(f"CREATE VIEW asrs AS SELECT * FROM read_parquet('{DATA / 'asrs_reports.parquet'}')")
        con.execute(f"CREATE VIEW ntsb AS SELECT * FROM read_parquet('{DATA / 'ntsb_events.parquet'}')")
        con.execute(f"CREATE VIEW tags AS SELECT * FROM read_parquet('{DATA / 'tags.parquet'}')")
        # Materialise per-report tag lists once: every query filters on them
        con.execute("""
            CREATE TEMP TABLE tagset AS
            SELECT source, id,
                   list(DISTINCT tag) FILTER (WHERE kind = 'factor') AS factors,
                   list(DISTINCT tag) FILTER (WHERE kind = 'event')  AS events
            FROM tags GROUP BY ALL""")
        _local.con = con
    return _local.con


STOP = set("""a an and are as at be by did do does for from had has have how in is it its of on or
that the their then there these this to was were what when where which who why with without about
after before during near miss misses accident accidents report reports pilot pilots aircraft""".split())

SOURCES = {"asrs", "ntsb"}


class ToolError(ValueError):
    """A bad argument. The message goes back to the model so it can correct itself."""


def _terms(query: str) -> list[str]:
    phrases = re.findall(r'"([^"]+)"', query or "")
    rest = re.sub(r'"[^"]+"', " ", query or "")
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'/-]+", rest.lower()) if w not in STOP and len(w) > 2]
    return [p.lower() for p in phrases] + words


def _check(factors=None, event=None, phase=None, operation=None):
    bad = [f for f in factors or [] if f not in FACTORS]
    if bad:
        raise ToolError(f"Unknown factor(s) {bad}. Valid: {sorted(FACTORS)}")
    if event and event not in EVENTS:
        raise ToolError(f"Unknown event {event!r}. Valid: {sorted(EVENTS)}")
    if phase and phase not in PHASES:
        raise ToolError(f"Unknown phase {phase!r}. Valid: {PHASES}")
    if operation and operation not in OPERATIONS:
        raise ToolError(f"Unknown operation {operation!r}. Valid: {sorted(OPERATIONS)}")


def _where(source: str, factors=None, event=None, phase=None, operation=None, year_from=None, year_to=None,
           fatal_only=False, alias="r") -> tuple[str, list]:
    _check(factors, event, phase, operation)
    clauses, params = ["TRUE"], []
    for f in factors or []:
        clauses.append("list_contains(coalesce(ts.factors, []), ?)")
        params.append(f)
    if event:
        clauses.append("list_contains(coalesce(ts.events, []), ?)")
        params.append(event)
    if phase:
        clauses.append(f"{alias}.phase = ?")
        params.append(phase)
    if operation:
        clauses.append(f"{alias}.operation = ?")
        params.append(operation)
    if year_from:
        clauses.append(f"{alias}.year >= ?")
        params.append(int(year_from))
    if year_to:
        clauses.append(f"{alias}.year <= ?")
        params.append(int(year_to))
    if fatal_only and source == "ntsb":
        clauses.append(f"{alias}.highest_injury = 'fatal'")
    return " AND ".join(clauses), params


def _from(source: str) -> str:
    key = "acn" if source == "asrs" else "ev_id"
    return f"{source} r LEFT JOIN tagset ts ON ts.source = '{source}' AND ts.id = r.{key}"


def _score_sql(terms: list[str], text_sql: str) -> tuple[str, list]:
    if not terms:
        return "0", []
    parts = [f"len(regexp_extract_all(lower({text_sql}), ?))" for _ in terms]
    return " + ".join(parts), [re.escape(t) for t in terms]


def _snippet(text: str, terms: list[str], width: int = 320) -> str:
    text = re.sub(r"\s+", " ", text or "")
    low = text.lower()
    hits = [low.find(t) for t in terms if low.find(t) >= 0]
    start = max(0, min(hits) - width // 3) if hits else 0
    out = text[start:start + width]
    return ("…" if start else "") + out + ("…" if start + width < len(text) else "")


# --- Search ---------------------------------------------------------------------------------------


def search_near_misses(query: str = "", factors=None, event=None, phase=None, operation=None,
                       year_from=None, year_to=None, limit: int = 8) -> dict:
    terms = _terms(query)
    where, params = _where("asrs", factors, event, phase, operation, year_from, year_to)
    score, sparams = _score_sql(terms, "r.synopsis || ' ' || r.narrative")
    limit = max(1, min(int(limit or 8), 15))
    sql = f"""
        SELECT r.acn, r.date, r.aircraft, r.operation, r.phase, r.reporter_function, r.synopsis,
               r.narrative, r.detector, r.result, ts.factors, ts.events, {score} AS score,
               count(*) OVER () AS matches
        FROM {_from('asrs')} WHERE {where} {"AND " + score + " > 0" if terms else ""}
        ORDER BY score DESC, r.date DESC LIMIT {limit}"""
    rows = _con().execute(sql, sparams + params + (sparams if terms else [])).fetchall()
    results = [{
        "id": f"ACN {r[0]}", "date": r[1], "aircraft": r[2], "operation": r[3], "phase": r[4],
        "reporter": r[5], "synopsis": r[6], "excerpt": _snippet(r[7], terms),
        "detected_by": r[8], "result": r[9], "factors": sorted(r[10] or []), "events": sorted(r[11] or []),
    } for r in rows]
    return {"source": "ASRS near-miss reports", "scope": _scope("asrs", operation, phase),
            "keyword_hits": rows[0][-1] if rows else 0, "note": "These are EXAMPLES. keyword_hits counts only reports that match your keywords AND filters; it is not the size of the group. For how many reports a group has, use count_by, factor_chains or compare_chain.",
            "showing": len(results), "results": results}


def search_accidents(query: str = "", factors=None, event=None, phase=None, operation=None,
                     year_from=None, year_to=None, fatal_only: bool = False, limit: int = 8) -> dict:
    terms = _terms(query)
    where, params = _where("ntsb", factors, event, phase, operation, year_from, year_to, fatal_only)
    score, sparams = _score_sql(terms, "r.probable_cause || ' ' || r.findings || ' ' || r.factual")
    limit = max(1, min(int(limit or 8), 15))
    sql = f"""
        SELECT r.ntsb_no, r.date, r.ev_type, r.aircraft, r.operation, r.phase, r.defining_event,
               r.highest_injury, r.fatalities, r.probable_cause, r.factual, ts.factors, ts.events,
               {score} AS score, count(*) OVER () AS matches
        FROM {_from('ntsb')} WHERE {where} AND r.ntsb_no IS NOT NULL {"AND " + score + " > 0" if terms else ""}
        ORDER BY score DESC, (r.probable_cause <> '') DESC, r.date DESC LIMIT {limit}"""
    rows = _con().execute(sql, sparams + params + (sparams if terms else [])).fetchall()
    results = [{
        "id": r[0], "date": r[1], "type": r[2], "aircraft": r[3], "operation": r[4], "phase": r[5],
        "defining_event": r[6], "highest_injury": r[7], "fatalities": r[8],
        "probable_cause": (r[9] or "")[:500] or "(no probable cause published yet)",
        "excerpt": _snippet(r[10], terms, 240) if terms else "",
        "factors": sorted(r[11] or []), "events": sorted(r[12] or []),
    } for r in rows]
    return {"source": "NTSB accident/incident database", "scope": _scope("ntsb", operation, phase),
            "keyword_hits": rows[0][-1] if rows else 0, "note": "These are EXAMPLES. keyword_hits counts only reports that match your keywords AND filters; it is not the size of the group. For how many reports a group has, use count_by, factor_chains or compare_chain.",
            "showing": len(results), "results": results}


def read_report(report_id: str) -> dict:
    """Full record for one ASRS ACN or NTSB number."""
    rid = (report_id or "").strip().upper().replace("ASRS", "").replace("ACN", "").strip(" #:")
    con = _con()
    if rid.isdigit():
        row = con.execute(f"SELECT r.*, ts.factors, ts.events FROM {_from('asrs')} WHERE r.acn = ?", [rid]).fetchone()
        if row:
            cols = [d[0] for d in con.description]
            rec = dict(zip(cols, row))
            rec["id"] = f"ACN {rid}"
            rec["narrative"] = rec["narrative"][:6000]
            return {"source": "ASRS", **{k: v for k, v in rec.items() if v not in ("", None, [])}}
    row = con.execute(f"SELECT r.*, ts.factors, ts.events FROM {_from('ntsb')} WHERE upper(r.ntsb_no) = ? OR r.ev_id = ?",
                      [rid, rid]).fetchone()
    if row:
        cols = [d[0] for d in con.description]
        rec = dict(zip(cols, row))
        rec["id"] = rec["ntsb_no"]
        return {"source": "NTSB", **{k: v for k, v in rec.items() if v not in ("", None, [])}}
    raise ToolError(f"No report {report_id!r}. ASRS ids are 'ACN 1234567'; NTSB ids look like 'ERA22LA123'.")


# --- Aggregates -----------------------------------------------------------------------------------


def _scope(source: str, operation=None, phase=None) -> str:
    """A plain label for what a number covers, e.g. 'ASRS near misses, general aviation (Part 91), all phases'."""
    what = "ASRS near misses" if source == "asrs" else "NTSB accidents/incidents"
    return f"{what}, {OPERATIONS[operation] if operation else 'all types of flying'}, {phase or 'all phases'}"


def _subset_count(con, source, where, params) -> int:
    return con.execute(f"SELECT count(*) FROM {_from(source)} WHERE {where}", params).fetchone()[0]


def factor_chains(source: str, factors=None, event=None, phase=None, operation=None, size: int = 2,
                  top: int = 10) -> dict:
    """Which factor combinations recur in a subset of reports: the 'chains'."""
    if source not in SOURCES:
        raise ToolError("source must be 'asrs' or 'ntsb'")
    size = 3 if int(size or 2) >= 3 else 2
    top = max(1, min(int(top or 10), 20))
    con = _con()
    where, params = _where(source, factors, event, phase, operation)
    total = _subset_count(con, source, where, params)
    if not total:
        return {"source": source, "subset_size": 0, "note": "No reports match these filters."}
    key = "acn" if source == "asrs" else "ntsb_no"
    given = set(factors or [])
    # Every combination of `size` factors (excluding the ones already filtered on) in each report
    rows = con.execute(f"""
        WITH sub AS (
            SELECT r.{key} AS id, list_filter(coalesce(ts.factors, []), lambda x: NOT list_contains(?::VARCHAR[], x)) AS f
            FROM {_from(source)} WHERE {where}
        ),
        combos AS (
            SELECT id, list_sort([a, b{", c" if size == 3 else ""}]) AS combo
            FROM sub, unnest(f) t1(a), unnest(f) t2(b){", unnest(f) t3(c)" if size == 3 else ""}
            WHERE a < b{" AND b < c" if size == 3 else ""}
        )
        SELECT combo, count(*) n, list(id ORDER BY random())[1:3] examples
        FROM combos GROUP BY combo ORDER BY n DESC LIMIT {top}""", [list(given)] + params).fetchall()
    prefix = "ACN " if source == "asrs" else ""
    return {
        "source": "ASRS near misses" if source == "asrs" else "NTSB accidents/incidents",
        "scope": _scope(source, operation, phase),
        "filters": {"factors": sorted(given), "event": event, "phase": phase, "operation": operation},
        "subset_size": total,
        "chains": [{"chain": " + ".join(sorted(given) + list(c)),
                    "factors": sorted(given) + list(c),
                    "reports_with_every_factor_in_chain": n,
                    "share_of_subset": round(n / total, 3),
                    "examples": [prefix + str(e) for e in ex]} for c, n, ex in rows],
        "note": ("Each count is the number of reports in the subset that have EVERY factor in that chain, "
                 "not how often any single factor appears (use count_by field=factor for that). "
                 "Shares are of reports in the subset, not rates of occurrence in flying."),
    }


def compare_chain(factors=None, event=None, phase=None, operation=None) -> dict:
    """The same chain in both datasets: how often, how it was caught (ASRS), how it ended (NTSB)."""
    if not factors and not event:
        raise ToolError("Give at least one factor or an event.")
    con = _con()
    out = {"chain": {"factors": factors or [], "event": event, "phase": phase, "operation": operation}}

    w, p = _where("asrs", factors, event, phase, operation)
    base_w, base_p = _where("asrs", None, None, phase, operation)
    n = _subset_count(con, "asrs", w, p)
    base = _subset_count(con, "asrs", base_w, base_p)

    def top_values(column, n_top=6):
        return con.execute(f"""
            SELECT trim(v) val, count(*) c FROM (
                SELECT unnest(string_split(r.{column}, ';')) v FROM {_from('asrs')} WHERE {w})
            WHERE trim(v) <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT {n_top}""", p).fetchall()

    ex = con.execute(f"SELECT r.acn FROM {_from('asrs')} WHERE {w} ORDER BY random() LIMIT 4", p).fetchall()
    out["asrs_near_misses"] = {
        "scope": _scope("asrs", operation, phase),
        "matching": n, "of_reports_in_scope": base, "share": round(n / base, 3) if base else None,
        "detected_by": [{"detector": v, "reports": c} for v, c in top_values("detector")] if n else [],
        "outcome_actions": [{"result": v, "reports": c} for v, c in top_values("result", 8)] if n else [],
        "examples": [f"ACN {r[0]}" for r in ex],
    }

    w, p = _where("ntsb", factors, event, phase, operation)
    base_w, base_p = _where("ntsb", None, None, phase, operation)
    n = _subset_count(con, "ntsb", w, p)
    base = _subset_count(con, "ntsb", base_w, base_p)
    sev = con.execute(f"SELECT r.highest_injury, count(*) FROM {_from('ntsb')} WHERE {w} GROUP BY 1 ORDER BY 2 DESC", p).fetchall()
    defin = con.execute(f"""SELECT r.defining_event, count(*) FROM {_from('ntsb')}
                            WHERE {w} AND r.defining_event <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 5""", p).fetchall()
    ex = con.execute(f"""SELECT r.ntsb_no FROM {_from('ntsb')} WHERE {w} AND r.probable_cause <> ''
                         ORDER BY random() LIMIT 4""", p).fetchall()
    out["ntsb_accidents"] = {
        "scope": _scope("ntsb", operation, phase),
        "matching": n, "of_events_in_scope": base, "share": round(n / base, 3) if base else None,
        # Events counted by their most severe injury: NOT numbers of people injured or killed
        "events_by_highest_injury": {k or "unknown": c for k, c in sev},
        "fatal_event_share": round(sum(c for k, c in sev if k == "fatal") / n, 3) if n else None,
        "typical_defining_events": [{"event": e, "accidents": c} for e, c in defin],
        "examples": [r[0] for r in ex],
    }
    out["caveats"] = [
        "Counts are reports, not rates: ASRS is voluntary and NTSB counts investigated accidents.",
        "Shares compare how prominent a chain is inside each dataset; they cannot be divided into a "
        "near-miss-to-accident ratio.",
        f"ASRS covers {INFO['asrs']['first']} to {INFO['asrs']['last']} (mostly the last two years); NTSB "
        f"covers {INFO['ntsb']['first']} to {INFO['ntsb']['last']}.",
        "ASRS is mostly airline (Part 121) flying and NTSB mostly general aviation (Part 91). Unless "
        "`operation` is set, these two sides cover different kinds of flying: say so, or compare with "
        "the same `operation` on both sides.",
        "events_by_highest_injury counts events by their worst injury, not people: write '26 fatal "
        "accidents', never '26 fatalities'.",
    ]
    return out


COUNT_FIELDS = {
    "asrs": {"phase", "operation", "factor", "event", "detector", "result", "year", "aircraft", "reporter_function",
             "primary_problem", "light", "flight_conditions"},
    "ntsb": {"phase", "operation", "factor", "event", "year", "aircraft", "highest_injury", "defining_event",
             "ev_type", "state", "light_cond", "wx_cond"},
}


def count_by(source: str, field: str, factors=None, event=None, phase=None, operation=None, top: int = 12) -> dict:
    if source not in SOURCES:
        raise ToolError("source must be 'asrs' or 'ntsb'")
    if field not in COUNT_FIELDS[source]:
        raise ToolError(f"field must be one of {sorted(COUNT_FIELDS[source])} for {source}")
    con = _con()
    where, params = _where(source, factors, event, phase, operation)
    top = max(1, min(int(top or 12), 30))
    if field in ("factor", "event"):
        col = "ts.factors" if field == "factor" else "ts.events"
        expr = f"unnest(coalesce({col}, []))"
    elif source == "asrs" and field in ("detector", "result"):
        expr = f"trim(unnest(string_split(r.{field}, ';')))"
    elif field == "defining_event":
        expr = "r.defining_event"
    else:
        expr = f"r.{field}"
    total = _subset_count(con, source, where, params)
    rows = con.execute(f"""SELECT v, count(*) c FROM (SELECT {expr} v FROM {_from(source)} WHERE {where})
                           WHERE v IS NOT NULL AND CAST(v AS VARCHAR) <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT {top}""",
                       params).fetchall()
    return {"source": source, "scope": _scope(source, operation, phase), "field": field,
            "filters": {"factors": factors or [], "event": event},
            "reports_in_subset": total,
            "how_to_read": f"Each count is how many of the {total} reports in this subset have that value.",
            "counts": [{"value": v, "reports": c} for v, c in rows],
            "note": ("A report can count under several values of a multi-valued field." if field in (
                "factor", "event", "detector", "result") else
                "Counts events by their most severe injury, not numbers of people." if field == "highest_injury" else "")}


def factor_matrix(operation=None) -> dict:
    """Every factor pair's report count in each dataset (for the Chain map), within one operation type."""
    _check(operation=operation)
    con = _con()
    out = {"operation": operation, "factors": {k: v["label"] for k, v in FACTORS.items()}}
    for source in ("asrs", "ntsb"):
        where, params = _where(source, operation=operation)
        total = _subset_count(con, source, where, params)
        singles = dict(con.execute(f"""SELECT f, count(*) FROM (SELECT unnest(coalesce(ts.factors, [])) f
                                       FROM {_from(source)} WHERE {where}) GROUP BY 1""", params).fetchall())
        pairs = con.execute(f"""
            WITH sub AS (SELECT coalesce(ts.factors, []) f FROM {_from(source)} WHERE {where})
            SELECT a, b, count(*) FROM sub, unnest(f) t1(a), unnest(f) t2(b) WHERE a < b GROUP BY 1, 2""",
                            params).fetchall()
        out[source] = {"total": total, "singles": singles, "pairs": {f"{a}|{b}": n for a, b, n in pairs}}
    return out


def taxonomy() -> dict:
    return {
        "factors": {k: v["label"] for k, v in FACTORS.items()},
        "events": {k: v["label"] for k, v in EVENTS.items()},
        "phases": PHASES,
        "operations": OPERATIONS,
        "coverage": INFO,
    }


def ids_in(value) -> set[str]:
    """Every report ID mentioned in a tool result, for checking the Writer's citations."""
    text = json.dumps(value, default=str)
    return set(re.findall(r"ACN \d{5,8}", text)) | set(re.findall(r"\b[A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]?\b", text))


if __name__ == "__main__":
    import time

    t = time.time()
    print(json.dumps(search_near_misses("altitude readback", factors=["fatigue"], limit=2), indent=1)[:1500])
    print(json.dumps(factor_chains("asrs", event="altitude_deviation", top=5), indent=1))
    print(json.dumps(compare_chain(["fatigue", "night"]), indent=1))
    print(json.dumps(search_accidents("fuel exhaustion", operation="general_91", limit=2), indent=1)[:1200])
    print(json.dumps(count_by("ntsb", "factor", event="fuel_event", top=6), indent=1))
    print(read_report("ACN " + search_near_misses(limit=1)["results"][0]["id"].split()[1])["id"])
    print(f"{time.time() - t:.2f}s")
