# Near-miss chains

Accidents rarely have one cause. A tired crew, a misheard clearance and a busy descent line up, and usually someone catches it: the crew files a report with NASA's Aviation Safety Reporting System (ASRS) and the flight lands normally. Sometimes nobody catches it, and the National Transportation Safety Board (NTSB) investigates an accident.

Near-miss chains is a research agent that reads both. It asks:

> **Which chains of small errors and operating conditions show up in both near misses and accidents, and what broke the chain in the near misses that was missing in the accidents?**

- **In ASRS near misses** it finds which combinations of contributing factors recur, who or what caught the problem (a crew cross-check, ATC, TCAS, a terrain warning) and what they did about it.
- **In NTSB accidents** it looks for the same combinations and reports how those events ended.

**Who it's for:** aviation safety analysts, flight-safety officers, student and working pilots, and researchers who want evidence from the reports themselves rather than general safety advice.

**Live app:** https://near-miss-chains-git-349752962523.europe-west1.run.app

## How it works

```
question ─► Triage ──clarify / can't answer──► reply
              │ research brief
              ▼
            Lead ──(optional quick look: factor_chains / compare_chain / count_by)
              │ research(task) × 2–4 in ONE turn  → Researchers run in parallel, each in a fresh context
              │ reads the findings; gap? research(...) again (at most 2 follow-up rounds, 8 researchers)
              ▼
            Writer (sees only the brief and the findings) ─► cited report
              ▼
            Citation check (code): every [ACN …] / [NTSB …] cited must have come back from a tool;
            if one didn't, the Writer gets one pass to fix it
```

| Agent | Job | Tools |
|---|---|---|
| **Triage** | Asks a clarifying question when a question is too vague ("Is flying safe?"), explains when the data can't answer it (rankings, risk trends), and otherwise turns it into a specific research brief | none (structured output) |
| **Lead** | Plans the research, sends 2–4 Researchers out in one turn, and sends more if the findings leave a gap (`MAX_ROUNDS = 3`, `MAX_RESEARCHERS = 8`, 150 s time budget) | `research`, `factor_chains`, `compare_chain`, `count_by` |
| **Researcher** | Works one task in a fresh context, with at most 8 tool calls, and returns findings that cite report IDs | `search_near_misses`, `search_accidents`, `read_report`, `factor_chains`, `compare_chain`, `count_by` |
| **Writer** | Writes the cited report: the answer, the recurring chains, what broke them, how they ended when nothing did, and what the data can't tell you | none |

The tools (`data.py`) run DuckDB over the Parquet files in `data/`:

- `search_near_misses` / `search_accidents`: keyword-ranked search of the narratives, filtered by shared factors, event, flight phase and type of flying.
- `read_report`: one full ASRS report or NTSB event.
- `factor_chains`: the most common 2- or 3-factor combinations in a group of reports, with counts and example IDs. These are the chains.
- `compare_chain`: one chain in both datasets: how many near misses have it, who caught them and what was done (ASRS); how many accidents have it, how severe they were and how they ended (NTSB).
- `count_by`: how a group of reports breaks down by one field.

The agent is built to be honest about the data's limits. Triage turns down questions the data can't answer at all, every tool result carries its own caveats ("counts are reports, not rates"), and every report ends with a section on what the data can't tell you.

## Using the API

`POST /chat` takes `{message, session_id}` and returns `{response, session_id, tool_calls}`. The server keeps each conversation by `session_id`, so a reply to a clarifying question is read in context. Each tool call is `{agent, name, args, result}`, and the Researchers' calls are included alongside the Lead's.

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

`status` is `answered`, `clarify` (reply with the same `session_id`), `cannot_answer` or `error`.

Other endpoints:
- `POST /clear?session_id=…` forgets a conversation.
- `POST /run` takes the same body and streams progress events to the page.
- `GET /record/{id}` returns one report.
- `GET /matrix?operation=…` returns the factor-pair counts behind the Chain map.
- `GET /compare?factors=fatigue,night&operation=…` runs `compare_chain` directly.

## Data

We use two public datasets. Both are copied offline and converted to Parquet by `build_data.py` (about 20 MB in total, committed in `data/`).

| Source | What it contains | Where it comes from | File |
|---|---|---|---|
| **NASA ASRS** (Aviation Safety Reporting System) | 14,131 voluntary, de-identified near-miss reports written by pilots, controllers, mechanics and cabin crew. Each has a narrative, a synopsis and fields coded by NASA's analysts: human factors, contributing factors, anomaly, who **detected** the problem and the **result** | [ASRS Database Online](https://asrs.arc.nasa.gov/search/database.html), exported with all fields as 3 CSV files (the site limits the size of each export). Almost all reports are from Aug 2024 to Aug 2026 | `data/asrs_reports.parquet` |
| **NTSB** aviation accident database | 31,453 investigated events from 2008 to Oct 2026 (29,075 of them accidents), with the probable cause, coded findings, the sequence of events, injuries and the first 3,500 characters of the factual narrative | `avall.zip` (a Microsoft Access database) from [data.ntsb.gov/avdata](https://data.ntsb.gov/avdata), read with `mdb-export` from mdbtools | `data/ntsb_events.parquet` |

**How the two connect.** No ID links a near miss to an accident. Instead, `taxonomy.py` defines one shared vocabulary: **19 factors** (fatigue, communication, automation, distraction, workload and time pressure, decision making, weather, night, IMC and others), **15 event types** (loss of control, runway incursion, terrain/CFIT, fuel and others), flight phases and types of flying (airlines under Part 121, charter under Part 135, general aviation under Part 91, drones). Rules map each agency's coded fields onto these tags, with keyword matching on the narratives as a fallback, and every tag records whether it came from the agency's own coding or from text. The result is `data/tags.parquet` (about 120,000 tags). NTSB events are tagged only from their defining event, and the NTSB text rules read only the probable cause.

**Rebuilding the data:**

```bash
# raw/asrs/*.csv     ← ASRS exports (all fields)
# raw/ntsb/avall.mdb ← unzipped from data.ntsb.gov/avdata
brew install mdbtools                # or: sudo apt-get install mdbtools
uv run --group build build_data.py
```

**What the data can't answer** (the agent says so when a question needs it):
- **Rates, risk or trends.** ASRS is voluntary, so its counts reflect who chose to report, and neither dataset has flight hours.
- **Safety rankings** of airlines, airports or aircraft. ASRS hides names (airports often appear as "ZZZ"), and no count is adjusted for how much each one flies.
- **A ratio of near misses to accidents.** The two datasets cover different kinds of flying (ASRS is mostly airlines, NTSB mostly general aviation) over different years.
- **Events before 2008 (NTSB) or mostly before mid-2024 (ASRS),** military flights and events outside the US.

## Example questions

Each expected answer below comes straight from the tools over the committed data, e.g. `uv run python -c "import data; print(data.compare_chain(['fatigue','night']))"`. The agent's wording changes from run to run, and so do its example reports, because examples are sampled; the counts don't change.

For each question we also asked the same model (`gemini-3.5-flash`) with **no tools and no system prompt**, and show its answer next to the agent's. The full answers, with every tool call, are in [docs/sample_answers.md](docs/sample_answers.md), generated with `uv run compare.py`.

### 1. "In near misses involving fatigue at night, what caught the error, and do fatigue accidents at night lack those defenses?"

- **Expected:** 58 ASRS near misses are tagged fatigue + night. Most were caught by the **flight crew (39)**, then **ATC (13)**, aircraft automation (7) and terrain warnings (6); 29 of the 58 are airline flights and 21 general aviation. In NTSB, 54 events share the chain and **26 were fatal accidents**; 33 of them are general aviation (18 fatal). Most ended in **loss of control in flight (14)** or **controlled flight into terrain (10)**. The agent should not turn 58 and 54 into a ratio.
- **Where it comes from:** `compare_chain(factors=["fatigue","night"])` and `count_by(..., field="detector" | "operation")`, over `data/asrs_reports.parquet` (`detector`, `result`), `data/ntsb_events.parquet` (`highest_injury`, `defining_event`) and `data/tags.parquet`.

| Same model, no tools | Near-miss chains |
|---|---|
| "The difference between a night-time fatigue near miss and a fatal accident usually comes down to the presence, redundancy, and functioning of safety barriers, often explained by James Reason's 'Swiss Cheese Model' … In near-miss reports (common in aviation, maritime, rail, and trucking) …" Generic, spanning several industries, with no counts and no reports. | "Flight crews … detect[ed] 17 of 29 scheduled airline (Part 121) near misses … general aviation (Part 91) operations experienced 33 night-fatigue accidents, 18 of which were fatal … scheduled airlines experienced 6 accidents, 2 of which were fatal … Out of 54 total night-fatigue NTSB accidents, loss of control in flight was the most common defining event with 14 occurrences, followed by 10 CFIT events." 11 cited reports, all verified; 5 researchers, 2 rounds, 57 s. |

### 2. "What chains of factors lead to fuel-related near misses, and do the same chains appear in general aviation fuel accidents?"

- **Expected:** Of the 143 general aviation fuel near misses in ASRS, **138 were caught by the crew**, usually followed by an emergency landing (70) or a diversion (45). Equipment failure appears in 137 of the 143, partly because NASA's analysts usually also code fuel problems as equipment problems. NTSB has 1,593 general aviation fuel accidents (1,392 of them also tagged with the fuel factor); in the tagged set most were **fuel exhaustion (618)** or **fuel starvation (514)**, and 150 were fatal. The chains differ between the datasets: **fuel + decision making** appears in 185 accidents but only 3 near misses, and **fuel + night** in 210 accidents.
- **Where it comes from:** `compare_chain(factors=["fuel", …], event="fuel_event", operation="general_91")`, `count_by(source="ntsb", field="defining_event" | "highest_injury", event="fuel_event", factors=["fuel"], operation="general_91")` and `factor_chains(source="ntsb", event="fuel_event", operation="general_91")`.

| Same model, no tools | Near-miss chains |
|---|---|
| "In General Aviation (GA), fuel-related occurrences are among the most preventable yet persistent safety issues … The short answer to your second question is **yes**: the causal chains leading to fuel-related near misses and fuel-related accidents are almost entirely identical up until the final moments of the flight." It says the chains are identical, with no evidence. | "In general aviation under Part 91, 143 ASRS fuel near-misses were reported, with flight crews successfully detecting the hazard in 138 of those cases … out of 1,392 GA fuel-related accidents, 618 involved fuel exhaustion and 514 involved fuel starvation, resulting in 150 fatal accidents and 268 serious injury accidents." It finds the chains are **not** identical: decision making appears in 185 accidents but 3 near misses. 7 cited reports, all verified; 4 researchers, 2 rounds, 70 s. |

### 3. "How do runway incursions at airline (Part 121) operations usually get stopped, and what chain of factors precedes them?"

- **Expected:** ASRS has 346 airline runway-incursion reports, 258 of them during taxi. Across all 346, they were detected by the **flight crew (232)**, **ATC (81)**, ground personnel (40) and ATC automation (15); during taxi, the crew caught 174 of 258. The most common responses were evasive action (112) and asking ATC to clarify (81). The leading taxi chains are **airport surface + situational awareness (110 of 258)** and **communication + situational awareness (109)**. NTSB has only 20 airline runway-incursion events (13 at takeoff, 6 during taxi), none fatal, so it adds little here.
- **Where it comes from:** `count_by(source="asrs", field="detector" | "result" | "phase", event="runway_incursion", operation="airline_121")`, `factor_chains(source="asrs", event="runway_incursion", operation="airline_121", phase="taxi")` and `count_by(source="ntsb", field="phase", …)`.

| Same model, no tools | Near-miss chains |
|---|---|
| "In US commercial aviation (governed by 14 CFR Part 121), runway incursions are among the most heavily scrutinized safety hazards … requires looking at aviation through James Reason's 'Swiss Cheese' Model …" A textbook list of defenses (runway status lights, ASDE-X, CRM), none of them measured. | "Flight crews are the most frequent detectors of runway incursions, identifying the conflict in 174 of 258 taxi near misses, 15 of 27 takeoff near misses, and 19 of 23 landing near misses … flight crews took evasive action in 99 of 258 taxi near misses, while ATC issued new clearances in 48 of 258." 10 cited reports, all verified; 7 researchers, 2 rounds, 59 s. |

**A vague question:** "Is flying safe?" gets a clarifying question back, asking which event, factor, phase or type of flying to look at. **A question the data can't answer:** "Which airline is the safest?" gets an explanation (ASRS hides names, and nothing is adjusted for how much each airline flies) and a suggestion for a related question it can answer.

## The interface

The page uses **B612**, the typeface Airbus designed for cockpit displays, and has three views:

- **Investigate** is the conversation. The home screen explains what the agent explores, who it is for and how to use it, with a diagram of a broken chain (a near miss) next to an unbroken one (an accident), and a few starter questions. While a question is being researched, the **research scope** on the right works like an air traffic control radar: the Lead sits at the centre, and each Researcher appears as a target with a data tag (`RSCH2 / compare chain / 58 near misses vs 54 accidents`) that flashes blue for ASRS queries and magenta for NTSB queries, so you can watch the Researchers work in parallel. Below the radar, a log lists every step and tool call. Each answer opens with a **chain card** built from the agents' own `compare_chain` results, showing the chain, who caught it and what they did in the near misses, and how the accidents ended; the cited report follows. Every citation opens the full report in a side drawer, and a seal shows the result of the citation check.
- **Chain map** is a 19 × 19 grid of every pair of factors, with no model involved: ASRS near misses above the diagonal, NTSB accidents below. It can be filtered to airlines, charter or general aviation. Clicking a cell compares that pair in both datasets, with real report IDs, and offers to send the chain to the agent.
- **The data** describes the two sources, how a question is researched and what the data can't answer.

**Show each agent's prompts** opens a panel with the exact messages each agent's model received and sent, one column per agent.

## Running it locally

```bash
gcloud auth application-default login
uv run app.py                       # http://localhost:8000
uv run python tests/test_flow.py    # tests the orchestration with a scripted model, no credentials needed
```

## Deployment

The app runs on Google Cloud Run, deployed continuously from this repository: every push to `main` builds and deploys a new version. The data ships with the code (no file is over 100 MB).

- **Build:** Google Cloud buildpacks, started with `uvicorn app:app --host 0.0.0.0 --port $PORT`.
- **Access:** public, no sign-in.
- **Request timeout:** 240 s. The Lead stops at 150 s, and the Writer finishes well within the limit; typical runs take 40–80 s.
- **Environment variables:** `VERTEXAI_PROJECT` (the Google Cloud project ID) and `VERTEXAI_LOCATION=global`.
- **Permissions:** the Cloud Run service account has the **Vertex AI User** role, for Gemini.

## Files

| File | What it does |
|---|---|
| `app.py` | the agents and the web server |
| `data.py` | the query tools |
| `taxonomy.py` | the shared vocabulary of factors and events |
| `build_data.py` | the offline data build |
| `compare.py` | generates the no-tools vs. agent answers |
| `index.html` | the interface |
| `context_panel.py`, `context-panel.js` | the per-agent prompts panel |
| `static/` | bundled copies of marked and DOMPurify |
| `tests/` | the orchestration test, with a scripted model |
