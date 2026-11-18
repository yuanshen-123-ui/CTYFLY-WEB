# CTYFLY 机器人航路查询 API

供 QQ 机器人、自动化脚本等外部调用方使用。

## 基本信息

| 项目 | 值 |
|---|---|
| 生产地址 | `https://www.ccflysim.cn` |
| 接口路径 | `/api/bot/nav/route` |
| 方法 | `GET` / `POST` |
| 登录 | 不需要 |
| 应用层限流 | 无；具体网络层限制以服务器配置为准 |
| 当前 AIRAC | 2611（2026-10-29 至 2026-11-25） |
| 返回格式 | `application/json` |

> 该接口用于机器人查询，不套用普通用户接口的登录和 300 次/分钟全局限流。
> 如果生产环境配置了 `BOT_API_TOKEN`，则必须带 `X-API-Key` 请求头。

## 鉴权

默认不要求登录。配置了 `BOT_API_TOKEN` 时，请求必须带：

```http
X-API-Key: <BOT_API_TOKEN>
```

没有配置 Token 时，可以不带该请求头，直接调用。

建议生产环境配置 Token，避免公网被扫描后无限刷航路计算：

```env
# backend/.env
BOT_API_TOKEN=生成一个随机长字符串
```

生成方式：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

修改后重启后端服务。

## GET 调用

```http
GET /api/bot/nav/route?origin=ZBAA&destination=ZSPD
```

完整示例：

```http
GET https://www.ccflysim.cn/api/bot/nav/route?origin=ZBAA&destination=ZSPD&originRunway=36R&destinationRunway=34L
```

### 查询参数

| 参数 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `origin` | 是 | string | 起飞机场 ICAO，3–4 位 |
| `destination` | 是 | string | 到达机场 ICAO，3–4 位 |
| `originRunway` | 否 | string | 起飞跑道，例如 `36R` |
| `destinationRunway` | 否 | string | 到达跑道，例如 `34L` |
| `sid` | 否 | string | 指定 SID 程序名 |
| `star` | 否 | string | 指定 STAR 程序名 |
| `full` | 否 | boolean | `true` 返回完整规划结果；默认精简结果 |

机场代码不区分大小写，服务端会统一转为大写。

## POST 调用

```http
POST /api/bot/nav/route
Content-Type: application/json
```

请求体：

```json
{
  "origin": "ZBAA",
  "destination": "ZSPD",
  "originRunway": "36R",
  "destinationRunway": "34L"
}
```

字段与 GET 参数一致。`sid`、`star` 可省略。

需要完整结果时：

```http
POST /api/bot/nav/route?full=true
```

## 精简返回

默认 `full=false`，返回机器人最常用的字段：

```json
{
  "ok": true,
  "cycle": "2611",
  "origin": {
    "icao": "ZBAA",
    "name": "CAPITAL",
    "lat": 40.07333333,
    "lon": 116.59833333,
    "elevation": 116
  },
  "destination": {
    "icao": "ZSPD",
    "name": "PUDONG",
    "lat": 31.145,
    "lon": 121.79333333,
    "elevation": 11
  },
  "route": "MUGLO W34 ANRAT A326 IKEKA A591 AGAVO Z55 NONOS Y655 BONSO Y711 MUGUS Y742 SALMI L1 KADLO A1 MAGOG V512 DOTMI A470 LJG B221 PAMVU V74 BK",
  "sid": {
    "procedure": null,
    "runway": null,
    "exit": "MUGLO",
    "distance": 89.6883183227597,
    "mode": "DIRECT"
  },
  "star": {
    "procedure": null,
    "runway": null,
    "entry": "BK",
    "distance": 78.64255983397145,
    "mode": "DIRECT"
  },
  "costs": {
    "sidDistance": 89.6883183227597,
    "airwayDistance": 2068.3030762972617,
    "switchCount": 12,
    "switchPenalty": 60,
    "starDistance": 78.64255983397145,
    "total": 2296.6339544539924
  },
  "alternatives": [
    "MUGLO W34 ANRAT W575 GOTSU W201 HCH W4 IKEKA A591 AGAVO ...",
    "LULTA V9 VAPGU W34 ANRAT A326 IKEKA A591 AGAVO ..."
  ]
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `ok` | 是否查询成功 |
| `cycle` | 当前 AIRAC 周期 |
| `origin` / `destination` | 机场信息 |
| `route` | 最终航路字符串，机器人直接展示这个字段即可 |
| `sid` | SID 信息，`procedure=null` 表示直飞加入航路 |
| `star` | STAR 信息，`procedure=null` 表示直飞进近 |
| `costs` | 各段距离与总成本 |
| `alternatives` | 备选航路字符串列表，可能为空 |

## 完整返回

`full=true` 时返回规划器原始结构，主要字段：

```text
method
cycle
origin
destination
routeString
sid
star
segments
costs
geometry
alternatives
```

`segments` 是逐段航路，`geometry` 包含 SID / 航路 / STAR 的坐标点，适合需要画图的调用方。
普通 QQ 机器人不建议使用 `full=true`，返回体积较大。

## 错误返回

错误统一为 JSON：

```json
{
  "error": "错误说明"
}
```

常见状态码：

| 状态码 | 说明 |
|---|---|
| `400` | 参数缺失或格式错误 |
| `401` | 配置了 Token 但 `X-API-Key` 缺失/错误 |
| `404` | 导航数据中没有该机场 |
| `503` | 航路规划服务暂时不可用 |
| `504` | 航路计算超时 |

示例：

```json
{
  "error": "导航数据中没有机场 ZZZZ"
}
```

## Python / QQ 机器人调用示例

```python
import httpx

API_URL = "https://www.ccflysim.cn/api/bot/nav/route"
API_KEY = ""  # 如果服务器配置了 BOT_API_TOKEN，填入对应值


async def query_route(origin: str, destination: str, **options) -> dict:
    params = {
        "origin": origin.upper(),
        "destination": destination.upper(),
        **options,
    }
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.get(API_URL, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()


# 使用
result = await query_route("ZBAA", "ZSPD")
if result.get("ok"):
    print("航路：", result["route"])
    print("AIRAC：", result["cycle"])
```

同步版本：

```python
import requests

resp = requests.get(
    "https://www.ccflysim.cn/api/bot/nav/route",
    params={"origin": "ZBAA", "destination": "ZSPD"},
    timeout=90,
)
resp.raise_for_status()
data = resp.json()
print(data["route"])
```

## 调试命令

```bash
curl "https://www.ccflysim.cn/api/bot/nav/route?origin=ZBAA&destination=ZSPD"
```

带跑道：

```bash
curl "https://www.ccflysim.cn/api/bot/nav/route?origin=ZBAA&destination=ZSPD&originRunway=36R&destinationRunway=34L"
```

带 Token：

```bash
curl -H "X-API-Key: <BOT_API_TOKEN>" \
  "https://www.ccflysim.cn/api/bot/nav/route?origin=ZBAA&destination=ZSPD"
```

## 注意事项

- 航路字符串含空格，机器人组装消息时不要按空格截断。
- QQ 单条消息有长度限制，超长航路建议分段发送或只发送 `route` 的首段/尾段。
- 该接口不做应用层频率限制，建议生产环境配置 `BOT_API_TOKEN`。
- 航路计算是实时计算，单次可能需要数秒；机器人调用超时建议设置 90 秒。
- 当前数据只适用于模拟飞行，不可用于真实飞行导航。
