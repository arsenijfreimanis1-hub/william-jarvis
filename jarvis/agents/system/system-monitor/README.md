---
name: System Monitor
group: system
purpose: Report live CPU, memory, swap, thermal state and top processes of this device with zero tokens.
engine: rules
entrypoint: jarvis.agents.rules.system:live
device_affinity: any
token_budget: 0
tools: [rules.run, system.live]
triggers:
  - system status
  - how is the machine doing
  - cpu usage
  - memory usage
  - is the mac hot
  - what is using the cpu
  - system monitor
---

# System Monitor

Reads the resource governor's latest sample (top, vm_stat, pmset, ps, ollama ps) and answers
in plain language. Never guesses: if there is no sample yet it takes one.
