# Link protocol (Mini ⇄ MacBook)

Persistent, full-duplex WebSocket. The MacBook (role `macbook`) dials the Mini (role
`mini`) at `ws://<mini-tailscale-or-lan>:8787/ws/link`. Either side may send at any time.

## Transport and auth

1. Tailscale address (`JARVIS_LINK_PEER_URL`, e.g. `ws://mini.tailnet.ts.net:8787/ws/link`).
2. Fallback: LAN host (`JARVIS_FLEET_MINI_LAN_HOST`).
3. Header `X-Fleet-Token: <JARVIS_FLEET_TOKEN>`; the Mini also records the peer address.

## Envelope

```json
{
  "id": "uuid",
  "type": "hello | heartbeat | prompt | reply | span | journal | system | control | ack",
  "ts": "2026-10-06T16:00:00Z",
  "from_device": "macbook",
  "to_device": "mini",
  "trace_id": "uuid",
  "span_id": "uuid",
  "parent_span_id": "uuid | null",
  "from_agent": "scout | steward | <agent slug> | null",
  "to_agent": "...",
  "payload": {}
}
```

| type | direction | payload |
|------|-----------|---------|
| `hello` | both | `{role, hostname, version, capabilities, last_seen_id}` |
| `heartbeat` | both, every 10 s | `{load, mem_pressure, thermal}` |
| `prompt` | MacBook → Mini | `{text, session_id, voice, trace_id}` |
| `reply` | Mini → MacBook | `{text, engine, provider, tokens, trace_id}` |
| `span` | both | span open/close events for the call graph |
| `journal` | Mini → MacBook | journal rows |
| `system` | both | governor sample + decisions |
| `control` | MacBook → Mini | `{action: cancel|pause|resume|selfheal, target}` |
| `ack` | both | `{ack_id}` |

## Resume

Each side keeps the last 500 outbound messages. On reconnect `hello.last_seen_id` lets the
peer replay what was missed. Heartbeat timeout 30 s marks the peer `offline` in the fleet
registry without failing local work.
