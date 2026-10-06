---
name: File Reader
group: system
purpose: Read a text file from the William repo or ~/Projects without spending tokens.
engine: rules
entrypoint: jarvis.agents.rules.repo:read_file
device_affinity: any
token_budget: 0
tools: [rules.run, files.read]
triggers:
  - read file
  - show file
  - cat file
---

# File Reader

Path must stay inside the workspace or the projects folder. Output is truncated at 6000 chars.
