# Agent contract

Every agent is a folder under `jarvis/agents/<group>/<slug>/` containing two files.
`scripts/seed-agents.py` loads them into the `specialist_agents` table (idempotent; version
bumps on change). Legacy single-file `<slug>.md` briefs are still accepted.

## README.md

Front matter (YAML) followed by free-form instructions in Markdown.

```markdown
---
name: Key Scout
group: providers
purpose: Find, verify and register free AI provider keys.
engine: gateway            # rules | gateway | cursor
entrypoint:                # rules only: dotted path "jarvis.agents.rules.keys:scan"
model: gateway             # gateway | <provider id> | cursor model | blank
device_affinity: mini      # mini | macbook | any
preferred_role: control    # control | planner | tester | general
token_budget: 4000         # max tokens per task (0 = none; rules agents are 0)
tools: [providers.scan_keys, providers.probe, providers.save_key]
requires: [quota-keeper]   # agents invoked as child spans when needed
skills: [research]         # skill names from .agents/skills or jarvis/skills
triggers:
  - scan for keys
  - which keys are missing
cooldown_hours: 0          # minimum hours between runs (Mentor uses 24)
---

# Key Scout

Instructions the model follows in first person...
```

Rules for `rules` agents: `entrypoint` must be importable and expose
`async def run(task: str, ctx: dict) -> dict` returning `{ok, reply, data?}`. They are
tried **before** any model and record `tokens=0`.

## POSTTHOUGHT.md

A short prompt run after every task (via the cheapest model, or deterministic for `rules`
agents). Its output is parsed into journal entries. Template:

```markdown
You just finished a task as {agent}. Task: {task}. Outcome: {outcome}. Reply: {reply}
Answer in JSON: {"strength": "...", "weakness": "...", "problem": "...|null", "fix": "...|null", "decision": "..."}
```

Entries land in `journal` with `kind in (strength, weakness, problem, fix, decision)` and
`agent`, `trace_id`, `task_id` set.

## Runtime behaviour

1. Router finds an agent by explicit invocation (`use agent X: ...`) or trigger phrase.
2. Runtime opens a span, resolves `requires` into child spans (each a full `execute_agent`).
3. Engine: `rules` → entrypoint; `gateway` → free chain; `cursor` → Cursor then gateway fallback.
4. Post-thought runs; journal + learning updated; span closed with tokens and duration.
5. All spans stream over `/api/activity/stream` (SSE) and `/ws/link`.
