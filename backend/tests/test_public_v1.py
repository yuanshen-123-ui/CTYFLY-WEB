"""公开 v1 API：匿名可读、字段脱敏、不依赖站内 JWT。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_airline, make_user


@pytest.fixture()
def client(db):
    return TestClient(app)


def test_v1_meta_is_anonymous(client):
    response = client.get("/api/v1/meta")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == "v1"
    assert body["baseUrl"].endswith("/api/v1")


def test_v1_airlines_only_returns_approved_public_fields(client, conn):
    owner = make_user(conn, cid="9101", username="v1_owner")
    make_airline(conn, owner_id=owner, icao="V1AA", name="公开航司")
    make_airline(conn, owner_id=owner, icao="V1BB", name="审核中航司", status="pending")

    response = client.get("/api/v1/airlines")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["icao"] for item in body["items"]] == ["V1AA"]
    airline = body["items"][0]
    assert airline["name"] == "公开航司"
    assert "owner_id" not in airline
    assert "review_note" not in airline


def test_v1_events_do_not_expose_creator(client, conn):
    owner = make_user(conn, cid="9102", username="event_creator")
    conn.execute(
        "INSERT INTO events (title, description, dep, arr, start_time, end_time, status, created_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("公开活动", "活动说明", "ZBAA", "ZSPD",
         "2030-01-01 20:00", "2030-01-01 22:00", "upcoming", owner),
    )
    conn.commit()

    response = client.get("/api/v1/events")
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["title"] == "公开活动"
    assert "created_by" not in item
    assert "creator" not in item


def test_v1_rejects_unknown_airline_and_event(client):
    assert client.get("/api/v1/airlines/ZZZZ").status_code == 404
    assert client.get("/api/v1/events/999999").status_code == 404
