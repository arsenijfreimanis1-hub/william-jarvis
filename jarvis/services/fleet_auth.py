"""Shared-token gate for LAN fleet endpoints."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Query

from jarvis.config import settings


def fleet_token_configured() -> bool:
    return bool((settings.fleet_token or "").strip())


def verify_fleet_token(provided: str | None) -> None:
    expected = (settings.fleet_token or "").strip()
    if not expected:
        # Local-only / unset: allow when bound to loopback; callers still pass token when set.
        return
    got = (provided or "").strip()
    if not got or not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid fleet token")


def token_from_request(
    *,
    body_token: str | None = None,
    header_token: str | None = None,
    query_token: str | None = None,
) -> str | None:
    return body_token or header_token or query_token


async def require_fleet_token(
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
    token: str | None = Query(default=None),
) -> None:
    verify_fleet_token(token_from_request(header_token=x_jarvis_fleet_token, query_token=token))
