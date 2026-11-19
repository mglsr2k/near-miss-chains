"""Near-miss chains: deep research over NASA ASRS near misses and NTSB accidents.

Four agent shapes, each with one job:
    Triage      reads the conversation: asks a clarifying question, says the data can't answer, or
                writes a research brief
    Lead        plans, fans out 2-4 Researchers in one turn (they run in parallel, each in a fresh
                context), reads their findings and sends more if there's a gap, up to a cap
    Researcher  one task, using the data tools (search, read, chains, compare, count)
    Writer      sees only the brief and the findings; writes a cited report
After the Writer, code checks every citation against the report IDs the tools actually returned.
"""

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import data
from context_panel import AgentContexts, PanelHooks, add_context_panel

load_dotenv(Path(__file__).parent / ".env")
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini on Vertex AI, using your gcloud credentials (api_key unused).
MODEL = LitellmModel(model=os.environ.get("MODEL", "vertex_ai/gemini-3.5-flash"), api_key="unused")
RESEARCHER_MODEL = LitellmModel(model=os.environ.get("RESEARCHER_MODEL", "vertex_ai/gemini-3.5-flash-lite"),
                                api_key="unused")
SETTINGS = ModelSettings(extra_args={"vertex_location": os.environ.get("VERTEX_LOCATION", "global"),
                                     "timeout": 90, "num_retries": 2})

MAX_RESEARCHERS = 8  # in all, across every round. Prompts ask for restraint; code enforces it.
MAX_ROUNDS = 3  # the first fan-out plus at most two follow-ups for gaps
LEAD_SECONDS = 150  # Cloud Run request timeout is 240 s: past this, the Writer works with what's been found
TOOL_OUTPUT_CHARS = 6000  # how much of each tool result /chat returns in tool_calls


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: "ResearchHooks"
    researchers: int = 0
    rounds: int = 0
    round_turn: int = -1  # which Lead turn opened the current round
    findings: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)  # for /chat: every call, with its agent
    seen_ids: set[str] = field(default_factory=set)  # every report ID a tool returned


class ResearchHooks(PanelHooks):
    """The context panel's hooks, plus a log of every tool call for /chat and the activity feed."""

    async def on_tool_start(self, context, agent, tool):
        args = getattr(context, "tool_arguments", None) or ""
        self.activity(agent.name, f"called {tool.name}({_short(args)})")

    async def on_tool_end(self, context, agent, tool, result):
        try:
            args = json.loads(getattr(context, "tool_arguments", None) or "{}")
        except ValueError:
            args = getattr(context, "tool_arguments", None)
        research: Research = context.context
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        if tool.name != "research":  # a researcher's findings are its own text, not data
            research.seen_ids |= data.ids_in(text)
        research.tool_calls.append({"agent": agent.name, "name": tool.name, "args": args,
                                    "result": text[:TOOL_OUTPUT_CHARS]})
        self.emit({"type": "tool", "agent": agent.name, "name": tool.name, "arguments": args,
                   "summary": _summarize(tool.name, text)})


def _short(args: str, limit: int = 140) -> str:
    try:
        parsed = json.loads(args)
        args = ", ".join(f"{k}={json.dumps(v)}" for k, v in parsed.items() if v not in (None, "", []))
    except (ValueError, AttributeError):
        pass
    return args if len(args) <= limit else args[:limit] + "…"


def _summarize(name: str, text: str) -> str:
    try:
        d = json.loads(text)
    except ValueError:
        return ""
    if "error" in d:
        return "error: " + d["error"][:120]
    if "keyword_hits" in d:
        kind = "near misses" if d["source"].startswith("ASRS") else "NTSB events"
        return f"{d['showing']} example {kind} ({d['keyword_hits']} keyword hits)"
    if "chains" in d:
        return f"{len(d['chains'])} chains in {d['subset_size']} reports"
    if "asrs_near_misses" in d:
        return f"{d['asrs_near_misses']['matching']} near misses vs {d['ntsb_accidents']['matching']} accidents"
    if "counts" in d:
        return f"{len(d['counts'])} values over {d['reports_in_subset']} reports"
    if "source" in d and "id" in d:
        return f"read {d['id']}"
    return ""


# --- Data tools -----------------------------------------------------------------------------------
# Each runs in a worker thread, so parallel Researchers really do query in parallel.

FACTOR_HELP = ", ".join(data.FACTORS)
EVENT_HELP = ", ".join(data.EVENTS)
FILTER_DOC = f"""
        factors: Shared factor tags a report must ALL have. One or more of: {FACTOR_HELP}.
        event: One shared event tag: {EVENT_HELP}.
        phase: ground, taxi, takeoff, climb, cruise, descent, maneuvering, approach, landing.
        operation: airline_121, charter_135, general_91, drone_uas, other."""


async def _call(fn, **kwargs) -> str:
    kwargs = {k: v for k, v in kwargs.items() if v not in (None, "", [])}
    try:
        return json.dumps(await asyncio.to_thread(fn, **kwargs), default=str)
    except data.ToolError as e:
        return json.dumps({"error": str(e)})  # the model can't see exceptions: give it something to fix
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


@function_tool(docstring_style="google")
async def search_near_misses(query: str = "", factors: list[str] | None = None, event: str | None = None,
                             phase: str | None = None, operation: str | None = None, limit: int = 8) -> str:
    """Search NASA ASRS near-miss reports (pilots', controllers' and crews' own accounts of what almost
    went wrong). Ranks by keyword hits in the narrative; filters narrow by shared tags. Each result has
    its ACN (cite as [ACN 1234567]), a synopsis, a narrative excerpt, who detected it and the result.

    Args:
        query: Keywords or a "quoted phrase" to find in narratives, e.g. 'readback "wrong altitude"'. Empty to filter only.
        factors: Shared factor tags a report must ALL have, e.g. ["fatigue", "night"].
        event: One shared event tag, e.g. "altitude_deviation".
        phase: Flight phase, e.g. "approach".
        operation: airline_121, charter_135, general_91, drone_uas or other.
        limit: How many reports to return, at most 15.
    """
    return await _call(data.search_near_misses, query=query, factors=factors, event=event, phase=phase,
                       operation=operation, limit=limit)


@function_tool(docstring_style="google")
async def search_accidents(query: str = "", factors: list[str] | None = None, event: str | None = None,
                           phase: str | None = None, operation: str | None = None, fatal_only: bool = False,
                           limit: int = 8) -> str:
    """Search the NTSB aviation accident database (investigated accidents and incidents, 2008 on). Ranks
    by keyword hits in the probable cause, findings and factual narrative. Each result has its NTSB number
    (cite as [NTSB ERA22LA123]), the probable cause, the defining event and the highest injury.

    Args:
        query: Keywords or a "quoted phrase", e.g. 'fatigue "visual approach"'. Empty to filter only.
        factors: Shared factor tags an event must ALL have, e.g. ["fatigue", "night"].
        event: One shared event tag, e.g. "loss_of_control".
        phase: Flight phase, e.g. "landing".
        operation: airline_121, charter_135, general_91, drone_uas or other.
        fatal_only: Only accidents with a fatality.
        limit: How many events to return, at most 15.
    """
    return await _call(data.search_accidents, query=query, factors=factors, event=event, phase=phase,
                       operation=operation, fatal_only=fatal_only, limit=limit)


@function_tool(docstring_style="google")
async def read_report(report_id: str) -> str:
    """Read one report in full: an ASRS near miss ('ACN 1234567') or an NTSB event ('ERA22LA123').
    Use it to confirm a chain in the reporter's or investigator's own words before citing it.

    Args:
        report_id: 'ACN 1234567' for ASRS, or an NTSB number like 'ERA22LA123'.
    """
    return await _call(data.read_report, report_id=report_id)


@function_tool(docstring_style="google")
async def factor_chains(source: str, factors: list[str] | None = None, event: str | None = None,
                        phase: str | None = None, operation: str | None = None, size: int = 2,
                        top: int = 10) -> str:
    """Find which combinations of shared factors recur together in a subset of reports: the chains.
    Returns each combination with how many reports have ALL of its factors, its share of the subset,
    and example IDs. A chain's count is never the count of one factor on its own: for single-factor
    counts use count_by with field='factor'.

    Args:
        source: 'asrs' (near misses) or 'ntsb' (accidents).
        factors: Factors every report in the subset must have; chains add to these.
        event: Limit to one event type, e.g. "runway_incursion".
        phase: Limit to one flight phase.
        operation: Limit to one operation type, e.g. "airline_121".
        size: Factors per combination: 2 or 3.
        top: How many combinations to return, at most 20.
    """
    return await _call(data.factor_chains, source=source, factors=factors, event=event, phase=phase,
                       operation=operation, size=size, top=top)


@function_tool(docstring_style="google")
async def compare_chain(factors: list[str] | None = None, event: str | None = None, phase: str | None = None,
                        operation: str | None = None) -> str:
    """Look up one chain in BOTH datasets: how many near misses and accidents have it, what detected it
    and what the crew or ATC did in the near misses (ASRS), and how bad the accidents were and their
    typical defining events (NTSB). Includes example IDs from both and caveats you must respect.

    Args:
        factors: The chain's factors, e.g. ["fatigue", "night"].
        event: Optional event type, e.g. "cfit_terrain".
        phase: Optional flight phase.
        operation: Optional operation type; set it to compare like with like (ASRS is mostly airlines, NTSB mostly general aviation).
    """
    return await _call(data.compare_chain, factors=factors, event=event, phase=phase, operation=operation)


@function_tool(docstring_style="google")
async def count_by(source: str, field: str, factors: list[str] | None = None, event: str | None = None,
                   phase: str | None = None, operation: str | None = None, top: int = 12) -> str:
    """Count reports in a subset by one field, e.g. which phases fatigue near misses happen in, or who
    detects runway incursions.

    Args:
        source: 'asrs' or 'ntsb'.
        field: asrs: phase, operation, factor, event, detector, result, year, aircraft, reporter_function, primary_problem, light, flight_conditions. ntsb: phase, operation, factor, event, year, aircraft, highest_injury, defining_event, ev_type, state, light_cond, wx_cond.
        factors: Factors every counted report must have.
        event: Limit to one event type.
        phase: Limit to one flight phase.
        operation: Limit to one operation type.
        top: How many values to return, at most 30.
    """
    return await _call(data.count_by, source=source, field=field, factors=factors, event=event, phase=phase,
                       operation=operation, top=top)


DATA_TOOLS = [search_near_misses, search_accidents, read_report, factor_chains, compare_chain, count_by]

INFO = data.INFO
DATA_BRIEF = f"""\
<data>
- ASRS: {INFO['asrs']['reports']:,} NASA Aviation Safety Reporting System near-miss reports,
  {INFO['asrs']['first']} to {INFO['asrs']['last']} (almost all from mid-2024 on). Voluntary,
  de-identified accounts written by the pilots, controllers and crews involved. Mostly airline
  (Part 121) flying, but about 4,900 are general aviation (Part 91). Coded fields include human
  factors, contributing factors, anomaly, who DETECTED the problem, and the RESULT (what was done).
  Cite as [ACN 1234567].
- NTSB: {INFO['ntsb']['events']:,} investigated events ({INFO['ntsb']['accidents']:,} accidents),
  {INFO['ntsb']['first']} to {INFO['ntsb']['last']}. Mostly general aviation (Part 91); about 900
  airline (Part 121). Probable cause, coded findings, the defining event, the worst injury.
  Cite as [NTSB ERA22LA123].
- Both are tagged with the same shared vocabulary, so one chain can be looked up in each:
  factors: {FACTOR_HELP}
  events: {EVENT_HELP}
  phases: ground, taxi, takeoff, climb, cruise, descent, maneuvering, approach, landing
  operations: airline_121, charter_135, general_91, drone_uas, other
- There is no ID linking a near miss to an accident. They connect only through shared tags.
</data>"""

LIMITS = """\
<limits>
What this data cannot answer. Say so plainly whenever a question needs it:
- Rates, risk or trends ("is X getting worse", "how likely"): ASRS is voluntary, so report counts
  reflect who chose to report, and neither dataset has flight hours.
- Rankings of airlines, airports or aircraft by safety: ASRS is de-identified (airports are often
  "ZZZ", no airline names), and no count is adjusted for how much each one flies.
- A near-miss-to-accident ratio: the datasets cover different kinds of flying over different years.
- Anything before 2008 (NTSB) or mostly before mid-2024 (ASRS), military, or non-US events.
</limits>"""

EVIDENCE_RULES = """\
<evidence_rules>
- Numbers: copy them exactly from tool results, and always say what they cover, using the result's
  "scope" (e.g. "39 of 58 ASRS near misses, all types of flying"). Never compute rates, and never
  divide near-miss counts by accident counts.
- Totals come only from count_by, factor_chains or compare_chain. Search results are examples:
  their keyword_hits depends on your keywords and is never the size of a group. A count from a
  narrower filter (e.g. phase=taxi) describes only that slice: never write "all" or "in the
  database" about it.
- Copy each number next to its own label. "13 by ATC and 7 by automation", never "each 13".
- Chains vs single factors: a factor_chains count is the number of reports with EVERY factor in
  that chain. Never present it as how often one factor appears; use count_by field=factor for that.
  Before writing "X appears in N of M", check N came from a single-factor count.
- Injuries: NTSB counts EVENTS by their worst injury. Write "26 fatal accidents", never
  "26 fatalities" or "26 people died". The "fatalities" field of a single accident is people.
- Like with like: ASRS is mostly airlines and NTSB mostly general aviation. When you compare the
  two, use the same `operation` on both sides, or state clearly that they cover different flying.
- Citations: one report per bracket: [ACN 1234567] [ACN 7654321], never [ACN 1234567, ACN 7654321].
  A report ID goes only after a statement that report itself supports. A count is not supported by
  an example report: tag counts with their source instead (see the output format).
- Only state details that appear in tool results. Don't add facts from memory: flight numbers,
  airline names, dates or outcomes that aren't in the report you cite.
- Language: the data shows factors co-occurring, not causing. Prefer "appears with", "preceded",
  "was tagged" over "caused" unless an NTSB probable cause says so.
</evidence_rules>"""


# --- Researcher -----------------------------------------------------------------------------------

researcher = Agent(
    name="Researcher",
    instructions=f"""\
You research one task over two aviation safety datasets, using the tools. You never answer from
memory: everything you report comes from a tool result in this conversation.

{DATA_BRIEF}

<method>
1. Orient (1-2 calls): factor_chains, compare_chain or count_by, with the filters your task names,
   to see how many reports there are and which chains or values dominate.
2. Find examples (1-3 calls): search_near_misses / search_accidents with those filters plus
   keywords. Prefer reports whose synopsis or probable cause shows the chain clearly.
3. Confirm (1-3 calls): read_report on the 1-3 best examples, to check the chain and what broke
   it (ASRS) or how it ended (NTSB) in the reporter's or investigator's own words.
At most 8 tool calls. If a tool returns an error, read it, fix the argument, and try once more.
If a subset has fewer than 10 reports, say so: it is too small to call a pattern.
</method>

<output_format>
Reply with these three parts and nothing else:
FINDINGS: at most 8 bullets. Each is one fact that answers the task, ending with its evidence:
  - a count: (source: <tool>, <filters>, <scope>), e.g.
    "Flight crews detected 39 of 58 fatigue + night near misses (source: count_by detector,
    factors=fatigue+night, ASRS near misses, all types of flying)."
  - a report: [ACN 1234567] or [NTSB ERA22LA123], one per bracket.
QUOTES: up to 3 short quotes (under 25 words) from narratives you read, each with its report ID.
GAPS: what the task asked that the data didn't answer, and why. Write "none" if nothing.
</output_format>

{EVIDENCE_RULES}

{LIMITS}""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=DATA_TOOLS,
)


@function_tool
async def research(ctx: RunContextWrapper[Research], task: str) -> str:
    """Send a researcher, in a fresh context, to answer one specific task with the data tools.
    Returns its findings, each with the reports it came from. Call it several times in one turn for
    independent tasks: they run in parallel.

    Args:
        task: One specific thing to find out, naming which dataset(s), factors, events, phase or operation to look at.
    """
    run = ctx.context
    if run.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    # Several calls in the same Lead turn are one round; a later turn starts a new one.
    turn = run.hooks.lead_turns
    if turn != run.round_turn:
        if run.rounds >= MAX_ROUNDS:
            return "No research rounds left. Reply DONE."
        run.rounds += 1
        run.round_turn = turn
    run.researchers += 1
    agent = researcher.clone(name=f"Researcher {run.researchers}")
    try:
        result = await Runner.run(agent, f"Task: {task}", context=run, hooks=run.hooks, max_turns=12)
        output = str(result.final_output)
    except Exception as e:  # one stuck researcher shouldn't sink the run
        output = f"GAPS: this researcher failed ({type(e).__name__}); its task is not covered."
    run.findings.append(f"Task: {task}\n\n{output}")
    return output


# --- Lead -----------------------------------------------------------------------------------------

lead = Agent(
    name="Lead",
    instructions=f"""\
You lead a research team investigating aviation near misses and accidents. You get a research
brief. You plan and check the research; you never write the answer yourself.

{DATA_BRIEF}

<method>
1. Scope (at most 2 calls): factor_chains, compare_chain or count_by, to see which chains are
   prominent and how big the subsets are, so your tasks are well aimed.
2. Fan out: split the brief into 2 to 4 independent tasks and call research once per task, ALL IN
   THE SAME TURN, so the researchers work in parallel. A good split usually covers:
   a. the chains in ASRS near misses (factor_chains, examples)
   b. what detected and broke those chains (ASRS detector and result)
   c. the same chain in NTSB accidents (compare_chain, defining events, outcomes, examples)
   d. if the brief needs it, one variant: a phase, night vs day, or another type of flying
   Write each task so it stands alone: name the dataset(s), the exact factor/event/phase/operation
   tags, and what to return. A researcher sees only its task, not the brief.
3. Choose operations deliberately. If the brief names a type of flying, use it in every task, on
   BOTH datasets. If it doesn't, and the task compares ASRS with NTSB, either use the same operation
   on both sides (general_91 has the most accidents, airline_121 the most near misses) or ask the
   researcher to report each side's operation explicitly.
4. Check the findings against this list. For each item that fails, send one targeted research task:
   - Is every part of the brief answered, on both the near-miss and the accident side?
   - Are the defenses covered: who detected it and what was done?
   - Is any comparison like with like, or clearly labelled as not?
   - Is a key claim resting on fewer than 10 reports, or on no cited report?
   - Do two researchers contradict each other?
   At most two follow-up rounds, {MAX_RESEARCHERS} researchers in all.
5. When the list passes, or nothing more can be found, reply DONE and nothing else.
</method>

{EVIDENCE_RULES}""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[factor_chains, compare_chain, count_by, research],
)


# --- Triage: clarify, decline, or brief -----------------------------------------------------------


class Triage(BaseModel):
    action: Literal["clarify", "cannot_answer", "research"]
    message: str  # clarify: the question to ask; cannot_answer: why, and what it CAN answer instead
    brief: str  # research: the question restated as a specific, self-contained research brief


triage = Agent(
    name="Triage",
    instructions=f"""\
You are the front door of a research tool over aviation near-miss reports (NASA ASRS) and accident
investigations (NTSB). Read the conversation and decide what to do with the user's latest message.
If the assistant's last turn asked a clarifying question, the latest message answers it: combine
the two into one question.

{DATA_BRIEF}

{LIMITS}

<actions>
Choose exactly one:
- "clarify": the question names no event, error, factor, phase or type of flying to look at
  ("is flying safe?", "tell me about accidents", "what goes wrong?"). Ask ONE short question with
  2-4 concrete directions, e.g. an event (runway incursions, altitude deviations, loss of control),
  a factor (fatigue, readback errors, automation), a phase, or airlines vs general aviation.
  If the question is specific enough to make a reasonable start, don't ask: research it.
- "cannot_answer": the WHOLE question needs what the data can't give (a safety ranking, a trend in
  risk, a ratio of near misses to accidents, an event before 2008, a topic outside aviation
  safety). In 1-3 sentences say why, then suggest one related question it CAN answer.
  If only PART of the question is out of reach, choose "research" and flag that part in the brief.
- "research": write the brief.
</actions>

<brief_format>
QUESTION: the user's question, restated specifically and self-contained.
SCOPE: the shared tags it maps to, e.g. factors=fatigue,night; event=altitude_deviation;
  phase=approach; operation=airline_121 (or "all"). Map the user's words to tags: "readback",
  "misheard" -> communication; "FMS", "autopilot" -> automation; "tired" -> fatigue;
  "TCAS", "loss of separation" -> midair_nmac; "airlines" -> airline_121; "private pilots",
  "GA" -> general_91.
SUB-QUESTIONS: 2-4 specific things to find out.
FLAG: any part the data can't answer, and why. "none" if nothing.
</brief_format>
Fill the unused fields with an empty string.""",
    model=MODEL,
    model_settings=SETTINGS,
    output_type=Triage,
)


# --- Writer ---------------------------------------------------------------------------------------

writer = Agent(
    name="Writer",
    instructions=f"""\
Write a short research report in markdown that answers the question, using ONLY the findings
you're given. Your reader is an aviation safety analyst or pilot: precise, skeptical, busy.

<structure>
### Answer
One paragraph that answers the question directly, with the two or three most important numbers.
### Recurring chains
The combinations of errors and conditions that keep appearing, with counts and example reports.
### What broke the chain
In the near misses: who or what caught it (crew cross-check, ATC, TCAS or terrain warning) and
what they did. Leave this section out if the findings don't cover it.
### When it wasn't broken
The same chain in NTSB accidents, with outcomes. Leave out if not covered.
### What this data can't tell you
1-3 bullets on the limits that matter for THIS question, plus any FLAG from the brief.
</structure>

<citation_format>
- A report: [ACN 1234567] or [NTSB ERA22LA123], copied exactly from the findings, ONE per bracket.
  Several reports: [ACN 1234567] [ACN 7654321]. Put a report ID only after a sentence that report
  itself supports, never after a count.
- A count: end the clause with a source tag in this exact form: [data: <tool>, <filters>, <scope>],
  e.g. [data: count_by detector, fatigue+night, ASRS all flying]. Keep it short.
</citation_format>

{EVIDENCE_RULES}

<style>
- Each number names what it covers: "39 of 58 fatigue + night near misses", not "39 reports".
- If the findings don't answer part of the question, say so in the matching section.
- Plain, specific sentences. No "profoundly", "pivotal", "it is worth noting", "Swiss cheese".
  Under 600 words.
</style>

{LIMITS}""",
    model=MODEL,
    model_settings=SETTINGS,
)

# Any bracket holding report IDs: [ACN 1234567], [NTSB ERA22LA123], [ACN 1, ACN 2], [NTSB A, NTSB B]
BRACKET = re.compile(r"\[([^\[\]]*?)\]")
ACN_ID = re.compile(r"\b(?:ACN|ASRS)\s*#?\s*(\d{5,8})\b")
NTSB_ID = re.compile(r"\b([A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]?)\b")


def cited_ids(report: str) -> set[str]:
    cited = set()
    for inner in BRACKET.findall(report):
        if inner.strip().lower().startswith("data:"):
            continue  # a source tag for a count, not a report
        cited |= {f"ACN {n}" for n in ACN_ID.findall(inner)} | set(NTSB_ID.findall(inner))
    return cited


def check_citations(report: str, seen: set[str]) -> dict:
    cited = cited_ids(report)
    return {"cited": sorted(cited), "verified": sorted(cited & seen), "unverified": sorted(cited - seen)}


# --- The run --------------------------------------------------------------------------------------


class LeadTurns(ResearchHooks):
    """Counts the Lead's model calls, so research() can tell one round of fan-out from the next."""

    lead_turns = 0

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        if agent.name == "Lead":
            self.lead_turns += 1
        await super().on_llm_start(context, agent, system_prompt, input_items)


async def deep_research(message: str, history: list[dict], hooks: LeadTurns) -> dict:
    run = Research(hooks=hooks)
    started = time.time()

    # 0. Triage: clarify, decline, or brief
    convo = "".join(f"{m.get('role', 'user')}: {m.get('content', '')}\n" for m in history[-8:])
    t = await Runner.run(triage, f"Conversation so far:\n{convo or '(none)'}\n\nLatest message: {message}",
                         context=run, hooks=hooks, max_turns=2)
    decision: Triage = t.final_output
    if decision.action != "research":
        return {"status": "clarify" if decision.action == "clarify" else "cannot_answer",
                "answer": decision.message, "run": run}

    # 1. Research: plan, fan out, check for gaps
    try:
        await asyncio.wait_for(
            Runner.run(lead, f"Research brief: {decision.brief}", context=run, hooks=hooks, max_turns=12),
            timeout=LEAD_SECONDS)
    except asyncio.TimeoutError:
        hooks.activity("Lead", f"time budget ({LEAD_SECONDS}s) reached: writing with the findings so far")
    except Exception as e:  # e.g. max turns: write with what was found
        hooks.activity("Lead", f"stopped early ({type(e).__name__}): writing with the findings so far")
    if not run.findings:
        return {"status": "error", "answer": "The researchers didn't return any findings. Try again, or narrow the question.",
                "run": run}

    # 2. Write: the Writer sees only the brief and the findings
    findings = "\n\n---\n\n".join(run.findings)
    prompt = f"Question: {message}\n\nResearch brief: {decision.brief}\n\nFindings:\n\n{findings}"
    report = str((await Runner.run(writer, prompt, context=run, hooks=hooks)).final_output)

    # 3. Check citations against what the tools returned; one repair pass if any don't check out
    citations = check_citations(report, run.seen_ids)
    if citations["unverified"]:
        hooks.activity("Citation check", f"not returned by any tool: {', '.join(citations['unverified'])}; asking the Writer to fix")
        fix = (f"{prompt}\n\nYour draft:\n\n{report}\n\nThese cited IDs were never returned by a tool: "
               f"{', '.join(citations['unverified'])}. Rewrite the report without them (drop or re-cite the "
               "claims using IDs from the findings, one per bracket). Change nothing else.")
        report = str((await Runner.run(writer, fix, context=run, hooks=hooks)).final_output)
        citations = check_citations(report, run.seen_ids)
    hooks.activity("Citation check", f"{len(citations['verified'])} of {len(citations['cited'])} cited reports were returned by tools")
    return {"status": "answered", "answer": report, "brief": decision.brief, "citations": citations,
            "researchers": run.researchers, "rounds": run.rounds, "seconds": round(time.time() - started, 1),
            "run": run}


# --- FastAPI --------------------------------------------------------------------------------------

HERE = Path(__file__).parent
sessions: dict[str, AgentContexts] = {}  # run_id -> every agent's context; the context panel reads it

app = FastAPI(title="Near-miss chains")
add_context_panel(app, sessions)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")  # vendored marked + DOMPurify


def _payload(result: dict, run_id: str, session_id: str) -> dict:
    """Project 1's /chat shape ({response, session_id, tool_calls}), plus the research extras."""
    run: Research = result.pop("run")
    answer = result.pop("answer")
    return {"response": answer, "session_id": session_id, "tool_calls": run.tool_calls, "run_id": run_id, **result}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


# session_id -> the conversation so far ([{role, content}]), so a reply to a clarifying question
# is read in context. In-memory, single process, like Project 1.
conversations: dict[str, list[dict]] = {}


def _start(request: ChatRequest) -> tuple[str, str, list[dict]]:
    session_id = request.session_id or str(uuid.uuid4())
    history = conversations.setdefault(session_id, [])
    return request.message.strip(), session_id, list(history)


def _remember(session_id: str, message: str, answer: str) -> None:
    conversations[session_id] += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/info")
def info():
    return data.taxonomy()


@app.get("/record/{report_id}")
def record(report_id: str):
    """One report, for the citation links on the page."""
    try:
        return data.read_report(report_id)
    except data.ToolError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.get("/matrix")
def matrix(operation: str | None = None):
    """Factor-pair counts in both datasets, for the Chain map. No model involved."""
    try:
        return data.factor_matrix(operation or None)
    except data.ToolError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/compare")
def compare(factors: str, operation: str | None = None):
    """compare_chain for a cell of the Chain map, e.g. /compare?factors=fatigue,night"""
    try:
        return data.compare_chain([f for f in factors.split(",") if f], operation=operation or None)
    except data.ToolError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/chat")
async def chat(request: ChatRequest):
    """Project 1's endpoint: {message, session_id} in; {response, session_id, tool_calls} out.
    Every tool call, the sub-agents' too, is {agent, name, args, result}."""
    message, session_id, history = _start(request)
    if not message:
        return JSONResponse({"error": "Send {\"message\": \"your question\"}"}, status_code=400)
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    hooks = LeadTurns(sessions[run_id], lambda event: None)
    try:
        result = await deep_research(message, history, hooks)
    except Exception as e:
        # Auth, quota, a model that won't stop: say so in the chat, not as a 500
        return {"response": f"Model call failed: {type(e).__name__}: {str(e)[:300]}", "session_id": session_id,
                "tool_calls": [], "status": "error", "run_id": run_id}
    payload = _payload(result, run_id, session_id)
    _remember(session_id, message, payload["response"])
    return payload


@app.post("/clear")
def clear(session_id: str | None = None):
    conversations.pop(session_id, None)
    return {"status": "ok"}


@app.post("/run")
async def run(request: ChatRequest):
    """Same as /chat, but streams what happens as Server-Sent Events for the page."""
    message, session_id, history = _start(request)
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = LeadTurns(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            result = await deep_research(message, history, hooks)
            payload = _payload(result, run_id, session_id)
            _remember(session_id, message, payload["response"])
            queue.put_nowait({"type": "done", **payload})
        except Exception as e:
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id, 'session_id': session_id})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # proxies drop silent streams
                    continue
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    # Cloud Run sets PORT; locally, http://localhost:8000
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
