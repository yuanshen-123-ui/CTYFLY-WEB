"""供 QQ 机器人等外部调用方使用的航路查询接口。

与前端使用的 /api/nav/routes/plan 不同：这里不要求登录，也不做单接口限流；
如果配置了 BOT_API_TOKEN，则要求请求头 X-API-Key。建议生产环境至少配置一个
随机 Token，避免被公开扫描后无限刷航路计算。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import BOT_API_TOKEN
from . import navdata

router = APIRouter(prefix="/api/bot/nav", tags=["bot-nav"])


class BotRouteBody(BaseModel):
    origin: str = Field(..., min_length=3, max_length=4)
    destination: str = Field(..., min_length=3, max_length=4)
    originRunway: str | None = Field(None, max_length=8)
    destinationRunway: str | None = Field(None, max_length=8)
    sid: str | None = Field(None, max_length=16)
    star: str | None = Field(None, max_length=16)


def _auth(x_api_key: str | None) -> None:
    if not BOT_API_TOKEN:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, BOT_API_TOKEN):
        raise HTTPException(401, "X-API-Key 无效")


def _norm_icao(value: str) -> str:
    value = (value or "").strip().upper()
    if len(value) < 3 or len(value) > 4 or not all(ch.isalnum() for ch in value):
        raise HTTPException(400, "机场代码必须是 3–4 位 ICAO/IATA 代码")
    return value


def _payload(body: BotRouteBody) -> dict:
    return {
        "origin": _norm_icao(body.origin),
        "destination": _norm_icao(body.destination),
        **body.model_dump(exclude_none=True, exclude={"origin", "destination"}),
    }


def _compact(data: dict) -> dict:
    alternatives = [
        item.get("routeString")
        for item in data.get("alternatives", [])
        if isinstance(item, dict) and item.get("routeString")
    ]
    return {
        "ok": True,
        "cycle": data.get("cycle"),
        "origin": data.get("origin"),
        "destination": data.get("destination"),
        "route": data.get("routeString"),
        "sid": data.get("sid"),
        "star": data.get("star"),
        "costs": data.get("costs"),
        "alternatives": alternatives,
    }


async def _plan(body: BotRouteBody, full: bool) -> dict:
    data = await navdata.proxy_post("/api/routes", _payload(body))
    return data if full else _compact(data)


@router.get("/route")
async def bot_route_get(
    origin: str = Query(..., min_length=3, max_length=4),
    destination: str = Query(..., min_length=3, max_length=4),
    originRunway: str | None = Query(None, max_length=8),
    destinationRunway: str | None = Query(None, max_length=8),
    sid: str | None = Query(None, max_length=16),
    star: str | None = Query(None, max_length=16),
    full: bool = Query(False),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """GET 版本，适合 QQ 机器人直接拼 URL 调用。"""
    _auth(x_api_key)
    body = BotRouteBody(
        origin=origin,
        destination=destination,
        originRunway=originRunway,
        destinationRunway=destinationRunway,
        sid=sid,
        star=star,
    )
    return await _plan(body, full)


@router.post("/route")
async def bot_route_post(
    body: BotRouteBody,
    full: bool = Query(False),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """POST 版本，适合需要提交较长程序名或完整结构的调用方。"""
    _auth(x_api_key)
    return await _plan(body, full)
