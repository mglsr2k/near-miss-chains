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

Expected answers come straight from the tools over the committed data (e.g. `uv run python -c "import data; print(data.compare_chain(['fatigue','night']))"`). The agent's wording varies between runs, and its example reports vary because examples are sampled; the counts do not. Below each query, the same model (`gemini-3.5-flash`) answers with **no tools and no system prompt** next to **our agent**. The full answers, with every tool call, are in [docs/sample_answers.md](docs/sample_answers.md), generated by `uv run compare.py`.

### 1. "In near misses involving fatigue at night, what caught the error, and do fatigue accidents at night lack those defenses?"

- *Expected:* 58 ASRS near misses are tagged fatigue + night. They were caught mostly by the **flight crew (39)**, then **ATC (13)**, aircraft automation (7) and terrain warnings (6); 29 of the 58 are airline (Part 121) and 21 general aviation. In NTSB, 54 events share the chain and **26 were fatal accidents**; 33 of them are general aviation (18 fatal). Their defining events are mostly **loss of control in flight (14)** and **CFIT (10)**. The agent should not turn 58 vs 54 into a ratio.
- *Source:* `compare_chain(factors=["fatigue","night"])` and `count_by(..., field="detector" | "operation")` over `data/asrs_reports.parquet` (`detector`, `result`), `data/ntsb_events.parquet` (`highest_injury`, `defining_event`) and `data/tags.parquet`.

| Same model, no tools | Our agent |
|---|---|
| "The difference between a night-time fatigue near miss and a fatal accident usually comes down to the presence, redundancy, and functioning of safety barriers, often explained by James Reason's 'Swiss Cheese Model' … In near-miss reports (common in aviation, maritime, rail, and trucking) …" Generic, cross-industry, no counts, no reports. | "Flight crews … detect[ed] 17 of 29 scheduled airline (Part 121) near misses … general aviation (Part 91) operations experienced 33 night-fatigue accidents, 18 of which were fatal … scheduled airlines experienced 6 accidents, 2 of which were fatal … Out of 54 total night-fatigue NTSB accidents, loss of control in flight was the most common defining event with 14 occurrences, followed by 10 CFIT events." 11 cited reports, all verified; 5 researchers, 2 rounds, 57 s. |

### 2. "What chains of factors lead to fuel-related near misses, and do the same chains appear in general aviation fuel accidents?"

- *Expected:* Of the 143 general aviation (Part 91) fuel near misses in ASRS, **138 were caught by the crew**; the usual outcomes were an emergency landing (70) or a diversion (45). Equipment failure appears in 137 of the 143 (ASRS analysts usually also code fuel problems as equipment problems). NTSB has 1,593 Part 91 fuel-event accidents (1,392 of them also tagged with the fuel factor), mostly **fuel exhaustion (618)** and **starvation (514)** in the tagged set, with 150 fatal accidents. The chains diverge: **fuel + decision making** is in 185 GA accidents but only 3 GA near misses; **fuel + night** is in 210 accidents.
- *Source:* `compare_chain(factors=["fuel", …], event="fuel_event", operation="general_91")`, `count_by(source="ntsb", field="defining_event" | "highest_injury", event="fuel_event", factors=["fuel"], operation="general_91")`, `factor_chains(source="ntsb", event="fuel_event", operation="general_91")`.

| Same model, no tools | Our agent |
|---|---|
| "In General Aviation (GA), fuel-related occurrences are among the most preventable yet persistent safety issues … The short answer to your second question is **yes**: the causal chains leading to fuel-related near misses and fuel-related accidents are almost entirely identical up until the final moments of the flight." Asserts the chains are identical, with no evidence. | "In general aviation under Part 91, 143 ASRS fuel near-misses were reported, with flight crews successfully detecting the hazard in 138 of those cases … out of 1,392 GA fuel-related accidents, 618 involved fuel exhaustion and 514 involved fuel starvation, resulting in 150 fatal accidents and 268 serious injury accidents." It finds the chains are **not** identical: decision-making appears in 185 GA accidents but 3 GA near misses. 7 cited reports, all verified; 4 researchers, 2 rounds, 70 s. |

### 3. "How do runway incursions at airline (Part 121) operations usually get stopped, and what chain of factors precedes them?"

- *Expected:* There are 346 ASRS Part 121 runway-incursion reports, 258 of them during taxi. Across all 346, the **flight crew (232)**, **ATC (81)**, ground personnel (40) and ATC automation (15) detected them; in taxi, the crew caught 174 of 258. The commonest actions were evasive action (112) and asking ATC for clarification (81). The leading taxi chains are **airport surface + situational awareness (110 of 258)** and **communication + situational awareness (109)**. NTSB has only 20 Part 121 runway-incursion events (13 at takeoff, 6 in taxi), none fatal, so it says little here.
- *Source:* `count_by(source="asrs", field="detector" | "result" | "phase", event="runway_incursion", operation="airline_121")`, `factor_chains(source="asrs", event="runway_incursion", operation="airline_121", phase="taxi")`, `count_by(source="ntsb", field="phase", …)`.

| Same model, no tools | Our agent |
|---|---|
| "In US commercial aviation (governed by 14 CFR Part 121), runway incursions are among the most heavily scrutinized safety hazards … requires looking at aviation through James Reason's 'Swiss Cheese' Model …" A textbook list of defenses (RWSL, ASDE-X, CRM), none measured. | "Flight crews are the most frequent detectors of runway incursions, identifying the conflict in 174 of 258 taxi near misses, 15 of 27 takeoff near misses, and 19 of 23 landing near misses … flight crews took evasive action in 99 of 258 taxi near misses, while ATC issued new clearances in 48 of 258." 10 cited reports, all verified; 7 researchers, 2 rounds, 59 s. |

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
