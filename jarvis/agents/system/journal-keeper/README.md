---
name: Journal Keeper
group: system
purpose: Summarize the journal — open problems, recent weaknesses and strengths — with zero tokens.
engine: rules
entrypoint: jarvis.agents.rules.repo:journal_digest
device_affinity: any
token_budget: 0
tools: [rules.run, journal.read]
triggers:
  - journal
  - what went wrong today
  - open problems
  - what are your weaknesses
  - what did you learn
---

# Journal Keeper

Reads `journal` (input, decision, problem, weakness, strength, fix) and reports counts, open
problems and the last ten entries. Self-Healer and Mentor depend on it.
