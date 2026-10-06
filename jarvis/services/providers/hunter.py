"""Key Hunter: find new free AI API tiers / keys on the web and social feeds, notify the boss.

Zero model tokens: public JSON APIs + regex. Sources are chosen to be free and keyless:
Hacker News (Algolia), Reddit (public .json), GitHub search (unauthenticated, low rate),
DuckDuckGo HTML. Offers are deduplicated into `provider_offers` and surfaced via the
journal, Studio (activity stream) and a macOS notification.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import aiosqlite
import httpx

from jarvis.database import DB_PATH
from jarvis.services import activity_stream, journal, spans

log = logging.getLogger("jarvis.providers.hunter")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT,
    source TEXT NOT NULL,
    provider_hint TEXT,
    snippet TEXT,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);
"""

QUERIES = (
    "free AI API key", "free LLM API tier", "free inference API no credit card",
    "free tier LLM API 2026", "free API credits AI model", "open model API free",
)
POSITIVE = re.compile(r"\b(free (?:tier|api|credits?|plan|access|inference|usage)|no credit card|free forever|"
                      r"\$0|free api key|free llm|free tokens|free requests)\b", re.I)
NEGATIVE = re.compile(r"\b(trial expired|paid only|discontinued|deprecated|shutting down|scam|phishing)\b", re.I)
PROVIDER_HINTS = (
    "gemini", "google ai studio", "groq", "mistral", "codestral", "openrouter", "hugging ?face", "cloudflare",
    "workers ai", "cerebras", "sambanova", "together", "fireworks", "deepseek", "qwen", "nvidia nim", "github models",
    "cohere", "ai21", "perplexity", "replicate", "deepinfra", "hyperbolic", "novita", "lepton", "glhf", "kluster",
    "anthropic", "openai", "xai", "grok", "moonshot", "kimi", "zhipu", "glm", "minimax", "baseten", "modal",
    "lambda", "scaleway", "ovh", "ollama cloud", "vercel ai gateway", "azure", "aws bedrock", "oracle",
)
_HINT_RE = re.compile("|".join(PROVIDER_HINTS), re.I)
KNOWN_OFFERS: tuple[dict, ...] = (
    # Seed list so the hunter is useful before the first crawl. Verified landing pages, not keys.
    {"title": "Cloudflare Workers AI free allocation", "url": "https://developers.cloudflare.com/workers-ai/platform/pricing/", "provider_hint": "cloudflare"},
    {"title": "Cerebras Inference free tier", "url": "https://cloud.cerebras.ai/", "provider_hint": "cerebras"},
    {"title": "SambaNova Cloud free tier", "url": "https://cloud.sambanova.ai/", "provider_hint": "sambanova"},
    {"title": "GitHub Models (free with GitHub account)", "url": "https://github.com/marketplace/models", "provider_hint": "github models"},
    {"title": "NVIDIA NIM free API credits", "url": "https://build.nvidia.com/", "provider_hint": "nvidia nim"},
    {"title": "Mistral La Plateforme free experiment tier", "url": "https://console.mistral.ai/", "provider_hint": "mistral"},
    {"title": "Google AI Studio (Gemini) free tier", "url": "https://aistudio.google.com/apikey", "provider_hint": "gemini"},
    {"title": "Groq free developer tier", "url": "https://console.groq.com/keys", "provider_hint": "groq"},
    {"title": "OpenRouter :free models", "url": "https://openrouter.ai/models?q=free", "provider_hint": "openrouter"},
    {"title": "Hugging Face Inference free credits", "url": "https://huggingface.co/settings/tokens", "provider_hint": "hugging face"},
    {"title": "Together AI free credits on signup", "url": "https://api.together.ai/", "provider_hint": "together"},
    {"title": "Cohere trial keys (free rate-limited)", "url": "https://dashboard.cohere.com/api-keys", "provider_hint": "cohere"},
)
_UA = {"User-Agent": "william-key-hunter/1.0 (+personal agent; contact: local)"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _norm_key(title: str, url: str | None) -> str:
    base = (url or title).lower().strip()
    base = re.sub(r"^https?://(www\.)?", "", base).rstrip("/")
    return base[:200]


def _score(text: str) -> float:
    s = 0.0
    s += 1.0 * len(POSITIVE.findall(text))
    s += 0.8 if _HINT_RE.search(text) else 0.0
    s -= 2.0 * len(NEGATIVE.findall(text))
    return round(s, 2)


# --------------------------------------------------------------------------- sources

async def _hn(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f"https://hn.algolia.com/api/v1/search_by_date?query={quote_plus(query)}&tags=(story,show_hn)&hitsPerPage=25"
    try:
        data = (await client.get(url)).json()
    except Exception:
        return []
    out = []
    for hit in data.get("hits", []):
        title = hit.get("title") or hit.get("story_title") or ""
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        out.append({"title": title, "url": link, "source": "hackernews", "snippet": title})
    return out


async def _reddit(client: httpx.AsyncClient, query: str) -> list[dict]:
    out = []
    for sub in ("LocalLLaMA", "artificial", "SideProject", "selfhosted", "ChatGPTCoding"):
        url = f"https://www.reddit.com/r/{sub}/search.json?q={quote_plus(query)}&restrict_sr=1&sort=new&limit=15"
        try:
            data = (await client.get(url)).json()
        except Exception:
            continue
        for child in (data.get("data") or {}).get("children", []):
            d = child.get("data") or {}
            out.append({"title": d.get("title", ""), "url": "https://www.reddit.com" + d.get("permalink", ""),
                        "source": f"reddit/{sub}", "snippet": (d.get("selftext") or "")[:300]})
    return out


async def _github(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f"https://api.github.com/search/repositories?q={quote_plus(query + ' free api')}&sort=updated&per_page=15"
    try:
        resp = await client.get(url, headers={**_UA, "Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    return [{"title": it.get("full_name", ""), "url": it.get("html_url"), "source": "github",
             "snippet": it.get("description") or ""} for it in data.get("items", [])]


async def _ddg(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        text = (await client.get(url)).text
    except Exception:
        return []
    out = []
    for m in re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                         text, re.S):
        link, title, snippet = m.groups()
        out.append({"title": html.unescape(re.sub("<.*?>", "", title)), "url": html.unescape(link),
                    "source": "duckduckgo", "snippet": html.unescape(re.sub("<.*?>", "", snippet))[:300]})
    return out[:15]


# --------------------------------------------------------------------------- hunt

async def hunt(*, notify: bool = True, queries: tuple[str, ...] = QUERIES) -> dict[str, Any]:
    await ensure_tables()
    async with spans.span("agent", "key-hunter", agent="Key Hunter", input_text="hunt"):
        candidates: list[dict] = [{**o, "source": "seed", "snippet": o["title"]} for o in KNOWN_OFFERS]
        async with httpx.AsyncClient(timeout=12.0, headers=_UA, follow_redirects=True) as client:
            for q in queries:
                for fn in (_hn, _reddit, _github, _ddg):
                    try:
                        candidates.extend(await fn(client, q))
                    except Exception as exc:
                        log.debug("hunter source failed: %s", exc)
        new_offers: list[dict] = []
        seen_now: set[str] = set()
        now = _now()
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            for c in candidates:
                title = (c.get("title") or "").strip()
                if not title:
                    continue
                text = f"{title} {c.get('snippet') or ''}"
                score = _score(text) if c.get("source") != "seed" else 2.0
                if score < 1.5:
                    continue
                key = _norm_key(title, c.get("url"))
                if key in seen_now:
                    continue
                seen_now.add(key)
                hint_m = _HINT_RE.search(text)
                hint = hint_m.group(0).lower() if hint_m else c.get("provider_hint")
                row = await (await db.execute("SELECT id, notified FROM provider_offers WHERE key = ?", (key,))).fetchone()
                if row:
                    await db.execute("UPDATE provider_offers SET last_seen = ?, score = MAX(score, ?) WHERE id = ?",
                                     (now, score, row["id"]))
                    continue
                await db.execute(
                    """INSERT INTO provider_offers (key, title, url, source, provider_hint, snippet, score, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, title[:200], c.get("url"), c.get("source", "web"), hint, (c.get("snippet") or "")[:400],
                     score, now, now),
                )
                new_offers.append({"title": title, "url": c.get("url"), "source": c.get("source"), "provider_hint": hint,
                                   "score": score})
            await db.commit()
        if new_offers:
            await journal.write("note", f"Key Hunter found {len(new_offers)} new free-AI offer(s)", agent="Key Hunter",
                                source="hunter", metadata={"offers": new_offers[:10]})
            await activity_stream.emit("offer", f"{len(new_offers)} new free AI offers", status="done", engine="hunter",
                                       detail="; ".join(o["title"][:50] for o in new_offers[:5]),
                                       metadata={"offers": new_offers[:20]})
            if notify:
                try:
                    from jarvis.services import macos

                    top = new_offers[0]
                    await macos.notify("Key Hunter", f"{len(new_offers)} new free AI offers. First: {top['title'][:60]}")
                except Exception:
                    pass
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE provider_offers SET notified = 1 WHERE notified = 0")
                    await db.commit()
        return {"ok": True, "scanned": len(candidates), "new": new_offers, "new_count": len(new_offers)}


async def list_offers(*, status: str | None = None, limit: int = 100) -> list[dict]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            rows = await (await db.execute(
                "SELECT * FROM provider_offers WHERE status = ? ORDER BY score DESC, last_seen DESC LIMIT ?",
                (status, limit))).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM provider_offers ORDER BY status = 'new' DESC, score DESC, last_seen DESC LIMIT ?",
                (limit,))).fetchall()
    return [dict(r) for r in rows]


async def set_offer_status(offer_id: int, status: str) -> bool:
    if status not in ("new", "claimed", "ignored", "dead"):
        return False
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE provider_offers SET status = ? WHERE id = ?", (status, offer_id))
        await db.commit()
        return bool(cur.rowcount)


async def rules_entry(task: str, ctx: dict) -> dict:
    lowered = task.lower()
    if re.search(r"\b(list|show|what did you find|offers)\b", lowered) and not re.search(r"\b(hunt|search|find new)\b", lowered):
        offers = await list_offers(status="new", limit=12)
        if not offers:
            return {"ok": True, "reply": "No unclaimed offers. Say 'hunt for keys' to search again.", "data": []}
        lines = [f"- {o['title']} ({o['provider_hint'] or o['source']}) → {o['url']}" for o in offers]
        return {"ok": True, "reply": "Unclaimed free AI offers:\n" + "\n".join(lines), "data": offers}
    result = await hunt(notify=True)
    if result["new_count"]:
        lines = [f"- {o['title']} → {o['url']}" for o in result["new"][:8]]
        reply = f"Found {result['new_count']} new offer(s) out of {result['scanned']} scanned:\n" + "\n".join(lines)
    else:
        reply = f"Scanned {result['scanned']} items; nothing new. Known offers are in the shopping list."
    return {"ok": True, "reply": reply, "data": result}
