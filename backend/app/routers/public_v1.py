"""面向第三方开发者的公开只读 API。

设计约束：
- 只返回已公开的数据，不回传用户 ID、IP、UA、审核备注等内部字段。
- 匿名可访问，按 IP 限流；后续可在此层叠加 API Key 与更高配额。
- 路径固定在 /api/v1，内部接口升级不会直接破坏这里。
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..cache import cache
from ..database import get_conn
from ..ratelimit import enforce
from ..text import clean
from . import archive, navdata, weather
from .airlines import public_airline
from .misc import public_changelogs, public_partners, public_status


def _public_rate_limit(request: Request):
    """整个 v1 共享一个宽松的匿名配额，具体接口可再收紧。"""
    enforce(request, "v1", 120, 60)


router = APIRouter(prefix="/api/v1", tags=["v1"], dependencies=[Depends(_public_rate_limit)])

PUBLIC_EVENT_STATUSES = ("upcoming", "ongoing", "ended")


def _page(value, default=1, maximum=100):
    try:
        value = int(value or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


@router.get("")
def api_root():
    return {
        "ok": True,
        "name": "CTYFLY Public API",
        "version": "v1",
        "baseUrl": "https://www.ccflysim.cn/api/v1",
        "docs": "https://dev.ccflysim.cn",
    }


@router.get("/meta")
def api_meta():
    """公开 API 元信息：版本、文档与能力边界。"""
    return {
        "ok": True,
        "name": "CTYFLY Public API",
        "version": "v1",
        "baseUrl": "https://www.ccflysim.cn/api/v1",
        "docs": "https://dev.ccflysim.cn",
        "auth": "anonymous read-only",
        "rateLimit": "按 IP 限流，具体额度见文档",
    }


@router.get("/status")
def api_status(request: Request):
    """脱敏后的平台运行状态。"""
    return cache.get_or_set("v1-status", "public", 5, lambda: public_status(request))


@router.get("/changelogs")
def api_changelogs(request: Request):
    """公开更新日志。"""
    return cache.get_or_set("v1-changelogs", "latest", 60, lambda: public_changelogs(request))


@router.get("/partners")
def api_partners():
    """公开合作伙伴列表。"""
    return cache.get_or_set("v1-partners", "all", 300, public_partners)


@router.get("/airlines")
def api_airlines(page: int = 1, limit: int = 50, q: str = ""):
    """已上线航司列表，按名称排序。"""
    page = _page(page)
    limit = _page(limit, 50)
    q = clean(q, 40)
    where = ["a.status = 'approved'"]
    params = []
    if q:
        where.append("(a.icao LIKE ? OR a.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    where_sql = " AND ".join(where)
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM airlines a WHERE {where_sql}", params
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT a.icao, a.name, a.description, a.logo_url, a.hub,"
            " a.public_slogan AS slogan, a.created_at,"
            " (SELECT COUNT(*) FROM airline_members m"
            "  WHERE m.airline_id = a.id AND m.status = 'active') AS member_count,"
            " (SELECT COUNT(*) FROM airline_routes r WHERE r.airline_id = a.id) AS route_count"
            f" FROM airlines a WHERE {where_sql}"
            " ORDER BY a.name COLLATE NOCASE, a.id LIMIT ? OFFSET ?",
            [*params, limit, (page - 1) * limit],
        ).fetchall()
    return {
        "ok": True,
        "items": [dict(row) for row in rows],
        "page": page,
        "limit": limit,
        "total": total,
    }


@router.get("/airlines/leaderboard")
def api_airline_leaderboard(limit: int = 20):
    """航司活跃度排行：成员数与累计飞行小时。"""
    limit = _page(limit, 20)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.icao, a.name, COUNT(m.id) AS members,"
            " COALESCE(SUM(w.total_hours), 0) AS hours"
            " FROM airlines a"
            " LEFT JOIN airline_members m ON m.airline_id = a.id AND m.status = 'active'"
            " LEFT JOIN wallets w ON w.user_id = m.user_id"
            " WHERE a.status = 'approved'"
            " GROUP BY a.id ORDER BY hours DESC, members DESC, a.id LIMIT ?",
            (limit,),
        ).fetchall()
    return {"ok": True, "items": [dict(row) for row in rows]}


@router.get("/airlines/{icao}")
def api_airline_detail(icao: str):
    """单家航司的公开主页数据。"""
    key = clean(icao, 4).upper()
    data = cache.get_or_set("v1-airline", key, 60, lambda: public_airline(key))
    return {
        "ok": True,
        "airline": data["airline"],
    }


@router.get("/airlines/{icao}/routes")
def api_airline_routes(icao: str):
    """航司公开航线；航司关闭航线板块时返回空数组。"""
    key = clean(icao, 4).upper()
    data = cache.get_or_set("v1-airline", key, 60, lambda: public_airline(key))
    return {"ok": True, "icao": data["airline"]["icao"], "routes": data.get("routes", [])}


@router.get("/airlines/{icao}/fleet")
def api_airline_fleet(icao: str):
    """航司机队；航司关闭机队板块时返回空数组。"""
    key = clean(icao, 4).upper()
    data = cache.get_or_set("v1-airline", key, 60, lambda: public_airline(key))
    return {"ok": True, "icao": data["airline"]["icao"], "fleet": data["airline"].get("fleet", [])}


@router.get("/events")
def api_events(status: str = "", limit: int = 20):
    """公开活动列表，不含报名用户和内部创建人信息。"""
    limit = _page(limit, 20)
    status = status if status in PUBLIC_EVENT_STATUSES else ""
    where = ["e.status != 'cancelled'"]
    params = []
    if status:
        where.append("e.status = ?")
        params.append(status)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT e.id, e.title, e.description, e.dep, e.arr, e.start_time,"
            " e.end_time, e.status, e.route, e.distance_nm, e.notams,"
            " (SELECT COUNT(*) FROM event_signups s WHERE s.event_id = e.id) AS signup_count,"
            " (SELECT COUNT(*) FROM event_briefings b WHERE b.event_id = e.id) AS has_briefing"
            " FROM events e WHERE " + " AND ".join(where) +
            " ORDER BY e.start_time DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return {"ok": True, "items": [dict(row) for row in rows]}


@router.get("/events/{event_id}")
def api_event_detail(event_id: int):
    """公开活动详情，不含报名名单。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT e.id, e.title, e.description, e.dep, e.arr, e.start_time,"
            " e.end_time, e.status, e.route, e.distance_nm, e.notams, e.created_at,"
            " (SELECT COUNT(*) FROM event_signups s WHERE s.event_id = e.id) AS signup_count,"
            " (SELECT COUNT(*) FROM event_briefings b WHERE b.event_id = e.id) AS has_briefing"
            " FROM events e WHERE e.id = ? AND e.status != 'cancelled'",
            (event_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "活动不存在")
    return {"ok": True, "event": dict(row)}


@router.get("/archive")
def api_archive(
    q: str = "",
    category: str = "",
    year: str = "",
    sort: str = "latest",
    window: str = "week",
    page: int = 1,
):
    """ACIS 公开事件检索，只返回已发布内容。"""
    key = f"{q}|{category}|{year}|{sort}|{window}|{page}"
    return cache.get_or_set(
        "v1-archive",
        key,
        30,
        lambda: archive.list_events(
            q=q, category=category, year=year, sort=sort, window=window, page=page, user=None
        ),
    )


@router.get("/archive/{event_id}")
def api_archive_detail(event_id: int, request: Request):
    """ACIS 公开事件详情。"""
    return cache.get_or_set(
        "v1-archive-detail",
        str(event_id),
        60,
        lambda: archive.event_detail(event_id=event_id, request=request, user=None),
    )


@router.get("/archive/{event_id}/comments")
def api_archive_comments(event_id: int):
    """ACIS 公开事件评论。"""
    return cache.get_or_set(
        "v1-archive-comments",
        str(event_id),
        30,
        lambda: archive.list_comments(event_id=event_id, user=None),
    )


@router.get("/weather/metar")
async def api_metar(request: Request, ids: str = ""):
    """机场 METAR 报文，支持多个 ICAO 逗号分隔。"""
    enforce(request, "v1weather", 60, 60)
    icao_list = weather._validate_ids(ids)
    rows, _degraded = await weather.fetch_metar(icao_list)
    return {"ok": True, "reports": weather._simplify(weather._latest_per_station(rows))}


@router.get("/weather/taf")
async def api_taf(request: Request, ids: str = ""):
    """机场 TAF 报文，支持多个 ICAO 逗号分隔。"""
    enforce(request, "v1weather", 60, 60)
    icao_list = weather._validate_ids(ids)
    rows, _degraded = await weather.fetch_taf(icao_list)
    return {"ok": True, "reports": weather._simplify(weather._latest_per_station(rows))}


@router.get("/nav/airports")
async def api_nav_airports(request: Request, q: str = ""):
    """机场搜索。"""
    enforce(request, "v1nav", 60, 60)
    q = clean(q, 32)
    if not q:
        return {"airports": []}
    data = await navdata.proxy_get("/api/airports", {"q": q})
    return {"airports": [navdata._norm_airport(item) for item in data.get("airports", [])]}


@router.get("/nav/airports/{icao}")
async def api_nav_airport_detail(request: Request, icao: str):
    """机场详情。"""
    enforce(request, "v1nav", 60, 60)
    icao = clean(icao, 4).upper()
    if len(icao) != 4:
        raise HTTPException(400, "ICAO 格式无效")
    data = await navdata.proxy_get(f"/api/airports/{icao}/details")
    if isinstance(data.get("airport"), dict):
        data["airport"] = navdata._norm_airport(data["airport"])
    return data


@router.get("/nav/runways/{icao}")
async def api_nav_runways(request: Request, icao: str):
    """机场跑道列表。"""
    enforce(request, "v1nav", 60, 60)
    icao = clean(icao, 4).upper()
    if len(icao) != 4:
        raise HTTPException(400, "ICAO 格式无效")
    data = await navdata.proxy_get(f"/api/airports/{icao}/details")
    return {"ok": True, "icao": icao, "runways": data.get("runways", [])}
