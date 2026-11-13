"""审计日志写入：统一记录"谁在何时对什么做了什么"，尽力而为，不影响主流程"""
import os
import threading

from .database import get_conn

CATEGORIES = ["auth", "permission", "content", "airline", "risk", "admin", "system"]

CATEGORY_LABELS = {
    "auth": "认证类",
    "permission": "权限类",
    "content": "内容类",
    "airline": "航司类",
    "risk": "越权与风控",
    "admin": "管理操作",
    "system": "系统类",
}

# 连续触发风控的自动封禁策略：同一用户或同一 IP 在窗口内累计 5 条 risk 记录
# 即执行封禁。阈值和窗口可用环境变量调整，避免写死在代码里。
RISK_AUTO_BAN_THRESHOLD = int(os.getenv("RISK_AUTO_BAN_THRESHOLD", "5"))
RISK_AUTO_BAN_WINDOW_MIN = int(os.getenv("RISK_AUTO_BAN_WINDOW_MIN", "10"))
RISK_AUTO_BAN_IP_DAYS = int(os.getenv("RISK_AUTO_BAN_IP_DAYS", "30"))

# 429 等高频事件降噪：同一 key 每分钟只记一次
_dedupe: dict[str, float] = {}
_lock = threading.Lock()
_DEDUPE_WINDOW = 60.0


def _should_dedupe(key: str) -> bool:
    import time

    now = time.time()
    with _lock:
        last = _dedupe.get(key)
        if last and now - last < _DEDUPE_WINDOW:
            return True
        _dedupe[key] = now
        return False


def _auto_ban_after_risk(conn, *, actor_id, actor, ip: str, ua: str) -> None:
    """风控累计到阈值后执行封禁。

    只统计 risk 类事件；自动封禁本身写 permission 类日志，避免自触发循环。
    admin 账号不自动封禁，避免误伤关键管理账号，仍保留风控日志供人工处理。
    """
    if not actor_id and not ip:
        return

    since = f"-{RISK_AUTO_BAN_WINDOW_MIN} minutes"
    threshold = RISK_AUTO_BAN_THRESHOLD

    if actor_id:
        user = conn.execute(
            "SELECT id, username, role, status, last_login_ip FROM users WHERE id = ?",
            (actor_id,),
        ).fetchone()
        if not user or user["status"] == "banned":
            return

        count = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_logs"
            " WHERE category = 'risk' AND actor_id = ?"
            " AND created_at >= datetime('now', ?, 'localtime')",
            (actor_id, since),
        ).fetchone()["c"]

        if count < threshold:
            return

        if user["role"] == "admin":
            conn.execute(
                "INSERT INTO audit_logs (actor_id, actor_name, actor_role, category, action,"
                " target_type, target_id, detail, ip, ua)"
                " VALUES (NULL, '风控系统', 'system', 'risk', '管理员触发风控阈值，需人工复核',"
                " 'user', ?, ?, ?, ?)",
                (str(actor_id), f"{RISK_AUTO_BAN_WINDOW_MIN} 分钟内 {count} 次风控事件", ip, ua[:300]),
            )
            return

        changed = conn.execute(
            "UPDATE users SET status = 'banned', token_ver = token_ver + 1"
            " WHERE id = ? AND status != 'banned'",
            (actor_id,),
        ).rowcount
        if not changed:
            return

        conn.execute(
            "INSERT INTO audit_logs (actor_id, actor_name, actor_role, category, action,"
            " target_type, target_id, detail, ip, ua)"
            " VALUES (NULL, '风控系统', 'system', 'permission', '自动封禁账号',"
            " 'user', ?, ?, ?, ?)",
            (
                str(actor_id),
                f"{RISK_AUTO_BAN_WINDOW_MIN} 分钟内 {count} 次风控事件，账号与登录令牌已封禁",
                ip,
                ua[:300],
            ),
        )

        ban_ip = ip or user["last_login_ip"]
        if ban_ip:
            conn.execute(
                "INSERT INTO banned_ips (ip, reason, created_at, expires_at, source, note)"
                " VALUES (?, ?, datetime('now','localtime'),"
                " datetime('now','localtime', ?), 'auto', ?)"
                " ON CONFLICT(ip) DO UPDATE SET"
                " reason = excluded.reason, expires_at = excluded.expires_at,"
                " source = excluded.source, note = excluded.note",
                (
                    ban_ip,
                    f"风控自动封禁：{RISK_AUTO_BAN_WINDOW_MIN} 分钟内 {count} 次风险事件",
                    f"+{RISK_AUTO_BAN_IP_DAYS} days",
                    f"用户 {actor_id} 累计触发风控",
                ),
            )
        return

    # 匿名请求：按 IP 计数后封禁 IP
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_logs"
        " WHERE category = 'risk' AND target_type = 'ip' AND target_id = ?"
        " AND created_at >= datetime('now', ?, 'localtime')",
        (ip, since),
    ).fetchone()["c"]
    if count < threshold:
        return
    conn.execute(
        "INSERT INTO banned_ips (ip, reason, created_at, expires_at, source, note)"
        " VALUES (?, ?, datetime('now','localtime'), datetime('now','localtime', ?), 'auto', ?)"
        " ON CONFLICT(ip) DO UPDATE SET"
        " reason = excluded.reason, expires_at = excluded.expires_at,"
        " source = excluded.source, note = excluded.note",
        (
            ip,
            f"风控自动封禁：{RISK_AUTO_BAN_WINDOW_MIN} 分钟内 {count} 次风险事件",
            f"+{RISK_AUTO_BAN_IP_DAYS} days",
            "匿名请求累计触发风控",
        ),
    )
    conn.execute(
        "INSERT INTO audit_logs (actor_id, actor_name, actor_role, category, action,"
        " target_type, target_id, detail, ip, ua)"
        " VALUES (NULL, '风控系统', 'system', 'admin', '自动封禁 IP',"
        " 'ip', ?, ?, ?, ?)",
        (ip, f"{RISK_AUTO_BAN_WINDOW_MIN} 分钟内 {count} 次风险事件", ip, ua[:300]),
    )


def log_event(category: str, action: str, *, actor=None, target_type: str = "",
              target_id="", detail: str = "", ip: str = "", ua: str = "",
              dedupe_key: str = "") -> None:
    """写入审计日志。actor 为 None 时表示系统/匿名事件。"""
    if dedupe_key and _should_dedupe(dedupe_key):
        return
    try:
        from .requestinfo import classify_ua

        ua_label, ua_bot = classify_ua(ua)
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        actor_name = (actor.get("username") if isinstance(actor, dict) else "") or ""
        actor_role = (actor.get("role") if isinstance(actor, dict) else "") or ""
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_logs (actor_id, actor_name, actor_role, category, action,"
                " target_type, target_id, detail, ip, ua, ua_label, ua_bot, pid)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (actor_id, actor_name, actor_role, category, action,
                 target_type, str(target_id or ""), detail[:500], ip, ua[:300],
                 ua_label, 1 if ua_bot else 0, os.getpid()),
            )
            if category == "risk":
                _auto_ban_after_risk(
                    conn, actor_id=actor_id, actor=actor, ip=ip, ua=ua
                )
    except Exception:
        pass  # 审计失败不影响业务

