AIRAC 导航数据库目录
====================

本目录需要放置 **nd.db3**（AIRAC 导航数据库，约 90–105MB）。

由于体积过大，`*.db3` 已在 `.gitignore` 中排除，不纳入版本控制。
换机器或回滚代码后，请单独把 nd.db3 放回本目录：

```
backend/navdata-planner/Navdata/nd.db3
```

说明：
- 当前数据为 Navigraph **AIRAC 2611 / Fenix v3**，源格式为 Jeppesen DFD v2
  SQLite（`.s3db`），经 `scripts/import-navdata-s3db.py` 转换为本目录的
  `nd.db3`。
- 转换器会生成 `nd.db3`，并同时复制 `cycle_info.txt` / `cycle.json`；
  这两个元数据文件包含 Navigraph 许可文本，默认不入库。
- 表结构包含 `Airports`、`Waypoints`、`Airways`、`AirwayLegs`、
  `Terminals`、`TerminalLegs`、`Runways`、`config`。
- 更换新周期数据时直接覆盖 nd.db3，sidecar 会在下次请求时自动检测并重载。
- 也可用环境变量 `NAVDATA_DB` 指定数据库的任意位置（见 backend/.env.example）。

## 从 Fenix v3 `.s3db` 重新生成

```powershell
python scripts/import-navdata-s3db.py `
  --source "D:\BaiduNetdiskDownload\Navigraph_AIRAC_Cycle_2611\~fenix_a320_V3_2611.zip" `
  --out "backend\navdata-planner\Navdata\nd.db3"
```

源路径可以是包含 `*.s3db` 的目录，也可以是 zip 压缩包。转换器会校验
`cycle.json` / `cycle_info.txt`，并从源数据重建机场、航路点、跑道、
SID/STAR/IAP、航路和 `config`。生成后建议用 sidecar 做一次状态与航路查询：

```powershell
$env:NAVDATA_DB = "backend\navdata-planner\Navdata\nd.db3"
node backend/navdata-planner/server.js
# GET http://127.0.0.1:4173/api/status
```

