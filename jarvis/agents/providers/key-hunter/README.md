---
name: Key Hunter
group: providers
purpose: Search the web and social feeds for new free AI API tiers and keys, and notify the boss with signup links.
engine: rules
entrypoint: jarvis.services.providers.hunter:rules_entry
device_affinity: mini
token_budget: 0
tools: [rules.run, web.search, notify.user, journal.write, providers.save_key]
requires: []
cooldown_hours: 0
triggers:
  - hunt for keys
  - find new free api
  - any new free ai providers
  - key hunter
---

# Key Hunter

Scheduled every 6 hours and on demand. Sources: Hacker News (Algolia API), Reddit JSON
(r/LocalLLaMA, r/artificial, r/SideProject), GitHub search (awesome-free-ai lists), DuckDuckGo
HTML. Matches phrases like "free tier", "free API key", "no credit card", "free credits" with
provider names. New offers are deduplicated into `provider_offers`, journaled, and pushed as a
macOS notification + Studio card with the signup URL. The boss adds the key; Key Scout probes it
and Quota Keeper admits it to the chain.
