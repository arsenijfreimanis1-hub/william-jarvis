"""Session list / create / delete API."""

import pytest
from fastapi.testclient import TestClient

from jarvis.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_session_lifecycle(client):
    created = client.post("/api/sessions", json={"source": "web"})
    assert created.status_code == 200
    session = created.json()["session"]
    sid = session["id"]
    assert session["title"] == "New chat"
    assert session["preview"] == "No messages yet"

    listed = client.get("/api/sessions?source=web&limit=10")
    assert listed.status_code == 200
    ids = [s["id"] for s in listed.json()["sessions"]]
    assert sid in ids

    chat = client.post(
        "/api/chat",
        json={"message": "hello from test", "source": "web", "session_id": sid},
    )
    assert chat.status_code == 200
    assert chat.json().get("session_id") == sid

    listed2 = client.get("/api/sessions?source=web&limit=10")
    row = next(s for s in listed2.json()["sessions"] if s["id"] == sid)
    assert "hello" in row["title"].lower()
    assert row["message_count"] >= 2

    deleted = client.delete(f"/api/sessions/{sid}")
    assert deleted.status_code == 200
    assert client.get(f"/api/sessions/{sid}/messages").status_code == 404
