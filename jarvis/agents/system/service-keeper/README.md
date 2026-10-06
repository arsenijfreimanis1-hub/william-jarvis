---
name: Service Keeper
group: system
purpose: List, restart or stop William's launchd services (core, helper, kiosk, desktop, map, ollama, openclaw).
engine: rules
entrypoint: jarvis.agents.rules.system:services
device_affinity: any
token_budget: 0
tools: [rules.run, system.services]
triggers:
  - restart core
  - restart the helper
  - restart ollama
  - service status
  - which services are running
  - stop the kiosk
---

# Service Keeper

Wraps `launchctl list|kickstart|bootout` for William's own labels only. Restarting the core
from inside the core is deferred so the reply can be sent first.
