---
name: Repo Scout
group: system
purpose: Report git branch, dirty files and recent commits for William or a project repo, zero tokens.
engine: rules
entrypoint: jarvis.agents.rules.repo:git_status
device_affinity: any
token_budget: 0
tools: [rules.run, git.status]
triggers:
  - git status
  - what changed in the repo
  - which branch are we on
  - uncommitted changes
---

# Repo Scout

Runs `git branch --show-current`, `git status --short`, `git log -5` in the William repo or
`~/Projects/<name>` when the task says "repo <name>".
