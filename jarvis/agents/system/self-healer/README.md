---
name: Self Healer
group: system
purpose: Diagnose William (services, ports, models, tests, open problems), apply known fixes, escalate unknowns to a sandbox branch.
engine: rules
entrypoint: jarvis.services.selfheal:rules_entry
device_affinity: any
token_budget: 0
tools: [rules.run, system.services, system.live, journal.read, journal.write, terminal.execute]
requires: [journal-keeper, service-keeper]
triggers:
  - heal yourself
  - self heal
  - fix yourself
  - run diagnostics
  - something is broken
---

# Self Healer

One button. Checks: core/helper health, launchd labels, ports 8787/8788/11434, Ollama models
present, log sizes, open journal problems, test suite (optional). Known fixes: kickstart a dead
service, pull a missing model, clear provider cooldowns, rotate oversized logs. Unknown
problems become a `self_modify` sandbox branch with the diagnostics attached for approval.
