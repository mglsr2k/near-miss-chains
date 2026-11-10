# Near-miss chains

**Deep research over aviation near misses and accidents.** Accidents rarely have one cause: a tired crew, a misheard clearance and a busy descent line up. Usually someone catches it, and the crew files a NASA ASRS report. Sometimes nobody does, and the NTSB investigates an accident. This agent asks:

> **Which chains of small human errors and operational conditions appear in both near misses and accidents, and what broke the chain in the near misses that was missing in the accidents?**

- **From ASRS (near misses):** which combinations of contributing factors recur, and who or what caught them (crew cross-check, ATC, TCAS, terrain warning) and what was done.
- **From NTSB (accidents):** which of the same combinations appear in accidents, with their defining events and outcomes.

**Who it's for:** aviation safety analysts, flight-safety officers, student and working pilots, and researchers who want evidence from the reports themselves rather than generic safety advice.

**Path:** Deep Research (`"path": "research"`).

## How it works

```
question ─► Triage ──clarify / can't answer──► reply
              │ research brief
              ▼
            Lead ──(optional quick look: factor_chains / compare_chain / count_by)
              │ research(task) × 2–4 in ONE turn  → Researchers run in parallel, fresh contexts
              │ reads findings; gap? research(...) again (≤ 2 follow-up rounds, ≤ 8 researchers)
              ▼
            Writer (sees only brief + findings) ─► cited report
              ▼
            Citation check (code): every [ACN …] / [NTSB …] must have been returned by a tool;
            if not, one repair pass by the Writer
```

| Agent | Job | Tools |
|---|---|---|
| **Triage** | Clarifies vague questions ("Is flying safe?"), explains when the data can't answer (rankings, risk trends), otherwise writes a specific research brief | none (structured output) |
| **Lead** | Plans, fans out 2–4 Researchers in one turn, goes back for gaps up to a cap (`MAX_ROUNDS = 3`, `MAX_RESEARCHERS = 8`, 200 s budget) | `research`, `factor_chains`, `compare_chain`, `count_by` |
| **Researcher** | One task, in a fresh context, ≤ 8 tool calls; returns bullets citing report IDs | `search_near_misses`, `search_accidents`, `read_report`, `factor_chains`, `compare_chain`, `count_by` |
| **Writer** | Cited report: answer, recurring chains, what broke the chain, when it wasn't broken, what the data can't tell you | none |

The data tools (`data.py`) run DuckDB over Parquet files in `data/`:

- `search_near_misses` / `search_accidents`: keyword-ranked narrative search with filters on shared factors, event, phase and operation type.
- `read_report`: one full ASRS report or NTSB event.
- `factor_chains`: the most common 2- or 3-factor combinations in a subset, with counts and example IDs. These are the chains.
- `compare_chain`: one chain in both datasets: near-miss count, detectors and actions (ASRS), accident count, injuries and defining events (NTSB), plus caveats.
- `count_by`: a distribution over one field.

**Admitting what the data can't answer** is built in at three levels. Triage declines questions that are entirely out of reach. Every tool result carries caveats ("counts are reports, not rates"). The Writer always ends with *What this data can't tell you*.

## API

`POST /chat` keeps Project 1's shape: `{message, session_id}` in, `{response, session_id, tool_calls}` out. The server keeps the conversation per `session_id`, so a reply to a clarifying question is read in context. Each tool call is `{name, args, result}` as in Project 1, plus an `agent` field. Sub-agents' calls are included.

```bash
curl -s -X POST $URL/chat -H 'content-type: application/json' \
  -d '{"message": "How do runway incursions at airlines usually get stopped?"}'
```

```json
{
  "response": "**Answer** …[ACN 2326519]…",
  "session_id": "6f1c…",
  "tool_calls": [
    {"agent": "Lead", "name": "research", "args": {"task": "…"}, "result": "…"},
    {"agent": "Researcher 2", "name": "count_by", "args": {"source": "asrs", "field": "detector", "event": "runway_incursion"}, "result": "{…}"}
  ],
  "status": "answered",
  "citations": {"cited": [], "verified": [], "unverified": []},
  "researchers": 3, "rounds": 1, "seconds": 74.2, "brief": "…", "run_id": "…"
}
```

`status` is `answered`, `clarify` (send the answer with the same `session_id`), `cannot_answer` or `error`.

Other endpoints:
- `POST /clear?session_id=…` forgets a conversation.
- `POST /run` takes the same body and streams events for the page.
- `GET /record/{id}` returns one report.
- `GET /matrix?operation=…` returns factor-pair counts for the Chain map.
- `GET /compare?factors=fatigue,night&operation=…` runs `compare_chain` directly.

## Data

Two sources, one per team member. Both were copied offline and turned into Parquet by `build_data.py`, about 20 MB in total and committed in `data/`.

| Source | What | Where from | In this repo |
|---|---|---|---|
| **NASA ASRS** (Aviation Safety Reporting System) | 14,131 voluntary, de-identified near-miss reports by pilots, controllers, mechanics and cabin crew. Each has a narrative, a synopsis and expert-coded fields: human factors, contributing factors, anomaly, **Detector**, **Result** | [ASRS Database Online](https://asrs.arc.nasa.gov/search/database.html). Exported with all fields as 3 CSVs (the site caps each export). Almost all reports are from Aug 2024 to Aug 2026 | `data/asrs_reports.parquet` |
| **NTSB** aviation accident database | 31,453 investigated events from 2008 to Oct 2026 (29,075 accidents), with probable cause, coded findings (cause/factor), sequence of events, injuries and a factual narrative (first 3,500 characters) | `avall.zip` (MS Access) from [data.ntsb.gov/avdata](https://data.ntsb.gov/avdata), read with `mdb-export` (mdbtools) | `data/ntsb_events.parquet` |

**Connecting them.** There is no shared ID: a near miss never becomes an NTSB case. `taxonomy.py` defines one shared vocabulary of **19 factors** (fatigue, communication, automation, distraction, workload/time pressure, decision making, weather, night, IMC…), **15 events** (loss of control, runway incursion, CFIT/terrain, fuel…), phases and operation types (Part 121 / 135 / 91 / drones). Every rule maps ASRS coded fields and NTSB finding codes onto these tags, falling back to narrative regexes. Each tag records whether it came from the agency's own coding (`coded`) or from text (`text`). The result is `data/tags.parquet`, about 120k tags. NTSB events are tagged from the *defining* event only, and NTSB text rules read only the probable cause.

**Rebuild:**

```bash
# raw/asrs/*.csv     ← ASRS exports (all fields)
# raw/ntsb/avall.mdb ← unzipped from data.ntsb.gov/avdata
sudo apt-get install mdbtools        # or: brew install mdbtools
uv run --group build build_data.py
```

**What the data can't answer, and the agent says so:**
- **Rates, risk or trends.** ASRS is voluntary, so counts reflect who chose to report. Neither dataset has flight hours.
- **Rankings** of airlines, airports or aircraft. ASRS is de-identified (airports often "ZZZ"), and nothing is exposure-adjusted.
- **A near-miss-to-accident ratio.** The datasets cover different populations (ASRS is mostly Part 121 airlines, NTSB mostly Part 91 general aviation) and different years.
- **Anything before 2008 (NTSB) or mostly before mid-2024 (ASRS),** as well as military and non-US events.

## Sample queries for the grader

Expected answers come straight from the tools over the committed data (`uv run python -c "import data; print(data.compare_chain(['fatigue','night']))"`). The agent's exact wording varies, and its example reports vary because examples are sampled. Each agent answer appears next to the same model's no-tools answer in **[docs/sample_answers.md](docs/sample_answers.md)**, generated by `uv run compare.py`.

**1. "In near misses involving fatigue at night, what caught the error, and do fatigue accidents at night lack those defenses?"**
- *Expected:* 58 ASRS near misses are tagged fatigue + night. They were mostly caught by the **flight crew (39)**, then **ATC (13)**, aircraft automation (7) and **terrain warnings (6)**. The commonest actions were crew re-orienting (14), evasive action (13), ATC issuing a new clearance (10) and go-arounds (8). In NTSB, 54 events share the chain, and **26 (48%) were fatal**. Their defining events are mostly **loss of control in flight (14)** and **CFIT (10)**. The agent should *not* turn 58 vs 54 into a ratio.
- *Source:* `compare_chain(factors=["fatigue","night"])` → `data/asrs_reports.parquet` (`detector`, `result`), `data/ntsb_events.parquet` (`highest_injury`, `defining_event`; reported as fatal *accidents*, not people), `data/tags.parquet`.

**2. "What chains of factors lead to fuel-related near misses, and do the same chains appear in general aviation fuel accidents?"**
- *Expected:* Of the 143 Part 91 fuel near misses in ASRS, 138 were caught by the crew. The usual outcomes were an emergency landing (70) or a diversion (45). Across all operations, fuel near misses co-occur with equipment problems, situational awareness (137 of 363) and workload/time pressure (108). In NTSB there are 1,593 Part 91 fuel accidents (176 fatal), mostly fuel exhaustion (633) and starvation (544). The top accident chains are **fuel + night (210)** and **fuel + decision making (185)**. The agent should flag that ASRS has few GA fuel reports compared with NTSB, and that this is not a rate.
- *Source:* `factor_chains(source="asrs", event="fuel_event")`, `compare_chain(event="fuel_event", operation="general_91")`, `factor_chains(source="ntsb", event="fuel_event", operation="general_91")`.

**3. "How do runway incursions at airline (Part 121) operations usually get stopped, and what chain of factors precedes them?"**
- *Expected:* There are 346 ASRS Part 121 runway-incursion reports. They were detected by the **flight crew (232)**, **ATC (81)**, ground personnel (40) and ATC automation (15). The commonest actions were evasive action (112), asking ATC for clarification (81) and ATC issuing a new clearance (68). The leading chain is **communication + situational awareness (142, 41%)**, followed by airport surface + situational awareness (124). NTSB has only 20 Part 121 runway-incursion events, with no fatalities. The agent should say that NTSB therefore says little here.
- *Source:* `count_by(source="asrs", field="detector"|"result", event="runway_incursion", operation="airline_121")`, `factor_chains(...)`, `compare_chain(...)`.

**Vague input:** "Is flying safe?" → Triage asks which event, factor, phase or type of flying to look at. **Out of reach:** "Which airline is the safest?" → it explains that ASRS is de-identified and not exposure-adjusted, and suggests a question it can answer.

## Frontend

`index.html` is a new design built around the subject. It uses **B612**, the typeface Airbus designed for cockpit displays, and has three views:

- **Investigate** is a conversation. The home state explains what the agent explores, who it is for and how to use it, with a diagram of a broken chain (near miss) next to an unbroken one (accident) and starter questions. While a run is going, the **research scope** on the right is an ATC-style radar. The Lead sits at the centre, and each Researcher appears as a target with a data tag (`RSCH2 / compare chain / 58 near misses vs 54 accidents`). The tag pings blue for ASRS queries and magenta for NTSB, so the parallel fan-out is visible. Under the scope, a log lists every step and tool call. Each answer opens with a **chain card** built from the agents' own `compare_chain` results: the chain's links, who caught it and what they did in the near misses, and the fatal share and defining events of the accidents. Then comes the report. Every citation opens the full report in a side drawer, and a seal shows the citation check.
- **Chain map** is a 19 × 19 grid of factor pairs with no model involved. ASRS near misses are above the diagonal and NTSB accidents below. It can be filtered to airlines, charter or general aviation. Clicking a cell shows that pair in both datasets, with real report IDs and a button that hands the chain to the agent.
- **The data** describes the sources, the agent flow and what the data can't answer.

The starter's context-panel timeline is behind "Show each agent's prompts".

## Run locally

```bash
gcloud auth application-default login
uv run app.py                       # http://localhost:8000
uv run python tests/test_flow.py    # orchestration test with a scripted model (no credentials needed)
```

## Deploy (Cloud Run, continuous deploy from GitHub)

Follow the course's *Deploying to Cloud Run from GitHub* guide. The data ships in the repo (no file over 100 MB). Then:
- **Request timeout:** set it to **300 s** or more. The Lead stops at 200 s and the Writer finishes within the limit.
- The service account needs **Vertex AI User**.
- The app listens on `$PORT`. A `Procfile` (`web: python app.py`) is included for buildpacks.

## Files

`app.py` agents and server · `data.py` query tools · `taxonomy.py` the shared factor/event vocabulary · `build_data.py` offline build · `compare.py` no-tools vs. agent answers · `index.html` page · `context_panel.py`, `context-panel.js` the starter's context timeline · `static/` vendored marked + DOMPurify · `tests/` orchestration test with a scripted model.
