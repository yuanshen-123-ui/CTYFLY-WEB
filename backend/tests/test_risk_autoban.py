from app.audit import log_event
from tests.conftest import make_user


def _risk_events(user_id: int, ip: str, n: int = 5) -> None:
    for i in range(n):
        log_event(
            "risk",
            "越权访问被拒绝",
            actor={"id": user_id, "username": "risk_tester", "role": "user"},
            target_type="capability",
            target_id="view_users",
            detail=f"GET /api/admin/users #{i}",
            ip=ip,
            ua="pytest",
        )


def test_five_risk_events_auto_ban_user_and_ip(db, conn):
    user_id = make_user(conn, cid="9901", username="risk_tester")
    ip = "203.0.113.10"

    _risk_events(user_id, ip, 4)
    assert conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()["status"] == "active"

    _risk_events(user_id, ip, 1)
    row = conn.execute("SELECT status, token_ver FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row["status"] == "banned"
    assert row["token_ver"] == 1
    assert conn.execute("SELECT 1 FROM banned_ips WHERE ip = ?", (ip,)).fetchone()


def test_anonymous_ip_five_risk_events_auto_bans_ip(db, conn):
    ip = "203.0.113.11"
    for i in range(5):
        log_event(
            "risk",
            "疑似自动化行为",
            target_type="ip",
            target_id=ip,
            detail=f"burst #{i}",
            ip=ip,
            ua="pytest",
        )
    assert conn.execute("SELECT 1 FROM banned_ips WHERE ip = ?", (ip,)).fetchone()
