---
name: Governor
group: system
purpose: Decide whether running work should be kept, paused or cancelled based on live resource pressure.
engine: rules
entrypoint: jarvis.agents.rules.system:governor
device_affinity: any
token_budget: 0
tools: [rules.run, system.live, journal.write]
requires: [system-monitor]
triggers:
  - governor status
  - pause background work
  - resume background work
  - why did you pause
  - what did the governor decide
---

# Governor

Policy owner for William's own workload. Samples every 5 s. Constrained mode when free memory
< 10%, swap > 1.8 GB, thermal throttling, or CPU > 90% for 60 s: worker parallelism drops to
1, Ollama is single-flight, and our runaway subprocesses are terminated after 5 min at
95% CPU. User apps are never touched. Every decision is journaled.
