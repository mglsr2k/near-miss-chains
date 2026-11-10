"""Offline check of the orchestration with a scripted stand-in for Gemini (no credentials needed).

    uv run python tests/test_flow.py

Checks: Triage asks for clarification on a vague question; the Lead's research calls in one turn run
in parallel; every sub-agent tool call lands in tool_calls with its agent; the citation check catches
an invented ID and the Writer's repair pass removes it.
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

import app
from context_panel import AgentContexts

SLEEP = 1.0  # each researcher model call takes this long, so parallel vs serial is measurable


def text(t):
    return ResponseOutputMessage(id="m", role="assistant", status="completed", type="message",
                                 content=[ResponseOutputText(text=t, type="output_text", annotations=[])])


def call(name, args, i=0):
    return ResponseFunctionToolCall(type="function_call", call_id=f"c{time.time_ns()}{i}", name=name,
                                    arguments=json.dumps(args), id=f"f{i}")


class Scripted(Model):
    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs,
                           tracing, **kwargs):
        sysp = system_instructions or ""
        items = input if isinstance(input, list) else [{"role": "user", "content": input}]
        blob = json.dumps(items, default=str)
        outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
        if sysp.startswith("You are the front door"):
            vague = "is flying safe" in blob.lower()
            out = {"action": "clarify", "message": "Which kind of event?", "brief": ""} if vague else \
                {"action": "research", "message": "", "brief": "Fatigue at night: chains in near misses vs accidents."}
            r = [text(json.dumps(out))]
        elif sysp.startswith("You lead"):
            r = [call("research", {"task": "ASRS: fatigue + night chains"}, 0),
                 call("research", {"task": "NTSB: fatigue + night accidents"}, 1)] if not outputs else [text("DONE")]
        elif sysp.startswith("You research"):
            await asyncio.sleep(SLEEP)
            if not outputs:
                r = [call("compare_chain", {"factors": ["fatigue", "night"]})]
            else:
                acn = re.search(r"ACN \d+", blob).group(0)
                ntsb = re.findall(r'"examples": \[([^\]]*)\]', blob.replace("\\", ""))
                r = [text(f"- Fatigue+night near miss [{acn}]. Accident example {ntsb[-1] if ntsb else ''}")]
        elif sysp.startswith("Write a short research report"):
            acn = re.search(r"ACN \d+", blob).group(0)
            bad = "never returned by a tool" not in blob
            ntsb = re.findall(r"\b[A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]?\b", blob)
            body = (f"### Answer\nFlight crews caught 39 of 58 fatigue + night near misses "
                    f"[data: count_by detector, fatigue+night, ASRS all flying]. Examples [{acn}, NTSB {ntsb[0]}]")
            r = [text(body + (" and [ACN 9999999, ACN 8888888]." if bad else "."))]
        else:
            raise AssertionError("unknown agent: " + sysp[:60])
        return ModelResponse(output=r, usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def install():
    fake = Scripted()
    for agent in (app.triage, app.lead, app.researcher, app.writer):
        agent.model = fake


async def main():
    install()
    events = []
    hooks = app.LeadTurns(AgentContexts(), events.append)
    r = await app.deep_research("Is flying safe?", [], hooks)
    assert r["status"] == "clarify", r
    print("clarify ok:", r["answer"])

    hooks = app.LeadTurns(AgentContexts(), events.append)
    t = time.time()
    r = await app.deep_research("Does fatigue at night show up in near misses and accidents?", [], hooks)
    took = time.time() - t
    payload = app._payload(dict(r), "test", "s")
    agents = {c["agent"] for c in payload["tool_calls"]}
    print("status:", r["status"], f"in {took:.1f}s", "agents with tool calls:", sorted(agents))
    print("citations:", r["citations"])
    print("answer:", r["answer"])
    assert r["status"] == "answered"
    assert {"Lead", "Researcher 1", "Researcher 2"} <= agents
    assert took < 4 * SLEEP, f"researchers ran serially ({took:.1f}s)"  # serial would be >= 4s
    assert not r["citations"]["unverified"] and "9999999" not in r["answer"], "repair pass failed"
    assert len(r["citations"]["cited"]) == 2, r["citations"]  # both IDs in one bracket were found
    assert r["rounds"] == 1 and r["researchers"] == 2
    print("tool event sample:", next(e for e in events if e["type"] == "tool"))
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
