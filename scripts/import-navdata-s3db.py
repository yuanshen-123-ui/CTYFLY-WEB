#!/usr/bin/env python3
"""Convert a Navigraph/Fenix DFD v2 `.s3db` into the CTYFLY `nd.db3` schema.

The old Fenix packages shipped a ready-made `nd.db3`; the new Fenix v3
packages ship Jeppesen DFD v2 as a SQLite `.s3db`.  This script builds the
smaller schema consumed by `backend/navdata-planner` from those source tables.

Usage:
    python scripts/import-navdata-s3db.py --source <dir-or-zip> --out <nd.db3>

The source may be:
  * a directory containing `*.s3db` plus `cycle.json` / `cycle_info.txt`
  * a zip containing those files

The output is deliberately not committed; `Navdata/*.db3` is gitignored.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable

CORE_SCHEMA = """
CREATE TABLE Airports (
    ID INTEGER PRIMARY KEY,
    Name TEXT,
    ICAO TEXT,
    PrimaryID INTEGER,
    Latitude REAL DEFAULT 0,
    Longtitude REAL DEFAULT 0,
    Elevation INTEGER DEFAULT 0,
    TransitionAltitude INTEGER DEFAULT 0,
    TransitionLevel INTEGER DEFAULT 0,
    SpeedLimit INTEGER DEFAULT 0,
    SpeedLimitAltitude INTEGER DEFAULT 0
);
CREATE TABLE Waypoints (
    ID INTEGER PRIMARY KEY,
    Ident TEXT NOT NULL,
    Collocated INTEGER NOT NULL DEFAULT 0,
    Name TEXT,
    Latitude REAL DEFAULT 0,
    Longtitude REAL DEFAULT 0,
    NavaidID INTEGER
);
CREATE TABLE Airways (
    ID INTEGER PRIMARY KEY,
    Ident TEXT NOT NULL
);
CREATE TABLE AirwayLegs (
    ID INTEGER PRIMARY KEY,
    AirwayID INTEGER NOT NULL,
    Level TEXT,
    Waypoint1ID INTEGER,
    Waypoint2ID INTEGER,
    IsStart INTEGER NOT NULL DEFAULT 0,
    IsEnd INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE Runways (
    ID INTEGER PRIMARY KEY,
    AirportID INTEGER NOT NULL,
    Ident TEXT,
    TrueHeading REAL DEFAULT 0,
    Length INTEGER DEFAULT 0,
    Width INTEGER DEFAULT 0,
    Surface TEXT,
    Latitude REAL DEFAULT 0,
    Longtitude REAL DEFAULT 0,
    Elevation INTEGER DEFAULT 0
);
CREATE TABLE Terminals (
    ID INTEGER PRIMARY KEY,
    AirportID INTEGER NOT NULL,
    Proc TEXT,
    ICAO TEXT NOT NULL,
    FullName TEXT NOT NULL,
    Name TEXT NOT NULL,
    Rwy TEXT,
    RwyID INTEGER,
    IlsID INTEGER
);
CREATE TABLE TerminalLegs (
    ID INTEGER PRIMARY KEY,
    TerminalID INTEGER NOT NULL,
    Type TEXT,
    Transition TEXT,
    TrackCode TEXT,
    WptID INTEGER,
    WptLat REAL,
    WptLon REAL,
    TurnDir TEXT,
    NavID INTEGER,
    NavLat REAL,
    NavLon REAL,
    NavBear REAL,
    NavDist REAL,
    Course REAL,
    Distance REAL,
    Alt TEXT,
    Vnav REAL,
    CenterID INTEGER,
    CenterLat REAL,
    CenterLon REAL,
    WptDescCode TEXT
);
CREATE TABLE config (
    key TEXT PRIMARY KEY,
    val TEXT
);
CREATE INDEX idx_airports_icao ON Airports(ICAO);
CREATE INDEX idx_waypoints_ident ON Waypoints(Ident);
CREATE INDEX idx_terminals_airport_proc ON Terminals(AirportID, Proc);
CREATE INDEX idx_terminallegs_terminal ON TerminalLegs(TerminalID);
CREATE INDEX idx_runways_airport ON Runways(AirportID);
CREATE INDEX idx_airwaylegs_airway ON AirwayLegs(AirwayID);
CREATE INDEX idx_airwaylegs_level ON AirwayLegs(Level);
"""


def _as_int(value, default=0):
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean(value):
    return str(value).strip() if value is not None else ""


def _key(ident, lat, lon):
    ident = _clean(ident).upper()
    if lat is None or lon is None:
        return (ident, None, None)
    return (ident, round(float(lat), 6), round(float(lon), 6))


def _find_source(source: Path) -> tuple[Path, Path | None]:
    if source.is_file() and source.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="navdata-s3db-"))
        with zipfile.ZipFile(source) as zf:
            zf.extractall(tmp)
        source = tmp
    if not source.is_dir():
        raise SystemExit(f"source not found: {source}")
    db_files = sorted(source.rglob("*.s3db"))
    if not db_files:
        raise SystemExit(f"no .s3db found under: {source}")
    return source, db_files[0]


def _cycle_info(source: Path) -> tuple[str, str, str, dict]:
    cycle_json = source / "cycle.json"
    cycle_txt = source / "cycle_info.txt"
    meta = {}
    if cycle_json.exists():
        meta = json.loads(cycle_json.read_text(encoding="utf-8"))
    text = cycle_txt.read_text(encoding="utf-8", errors="replace") if cycle_txt.exists() else ""
    m = re.search(r"AIRAC cycle\s*:\s*(\d+)", text, re.I)
    cycle = m.group(1) if m else str(meta.get("cycle") or "")
    valid = re.search(r"Valid \(from/to\)\s*:\s*([0-9]{2}/[A-Z]{3}/[0-9]{4})\s*-\s*([0-9]{2}/[A-Z]{3}/[0-9]{4})", text, re.I)
    d1 = d2 = ""
    if not valid:
        period = str(meta.get("validityPeriod") or "")
        vm = re.match(r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})", period)
        if vm:
            from datetime import datetime
            d1 = datetime.strptime(vm.group(1), "%Y-%m-%d").strftime("%d%b%y").upper()
            d2 = datetime.strptime(vm.group(2), "%Y-%m-%d").strftime("%d%b%y").upper()
    else:
        d1 = valid.group(1).replace("/", "")[0:2] + valid.group(1).split("/")[1] + valid.group(1).split("/")[2][2:]
        d2 = valid.group(2).replace("/", "")[0:2] + valid.group(2).split("/")[1] + valid.group(2).split("/")[2][2:]
    if not cycle:
        raise SystemExit("cannot determine AIRAC cycle")
    if not d1 or not d2:
        raise SystemExit("cannot determine AIRAC validity period")
    return cycle, d1.upper(), d2.upper(), meta


def _insert_chunks(con: sqlite3.Connection, sql: str, rows: Iterable[tuple], chunk=5000):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= chunk:
            con.executemany(sql, batch)
            batch.clear()
    if batch:
        con.executemany(sql, batch)


def convert(source: Path, out: Path, replace: bool = False):
    src_dir, s3db = _find_source(source)
    cycle, start, end, meta = _cycle_info(src_dir)
    print(f"source : {s3db}")
    print(f"cycle  : {cycle}  valid {start} - {end}  format={meta.get('format', '?')}")

    if out.exists() and not replace:
        raise SystemExit(f"output exists: {out} (use --replace)")
    if out.exists():
        backup = out.with_name(out.name + f".bak-{cycle}")
        if backup.exists():
            backup = out.with_name(out.name + f".bak-{cycle}-{int(__import__('time').time())}")
        shutil.copy2(out, backup)
        print(f"backup : {backup}")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out.with_name(out.name + ".building")
    tmp_out.unlink(missing_ok=True)

    src = sqlite3.connect(f"file:{s3db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(tmp_out)
    dst.execute("PRAGMA journal_mode=OFF")
    dst.execute("PRAGMA synchronous=OFF")
    dst.execute("PRAGMA temp_store=MEMORY")
    dst.executescript(CORE_SCHEMA)

    # ---------- airports ----------
    airport_id = {}
    airports = []
    for i, r in enumerate(src.execute(
        "SELECT airport_identifier, airport_name, airport_ref_latitude, airport_ref_longitude, "
        "elevation, transition_altitude, transition_level, speed_limit, speed_limit_altitude "
        "FROM tbl_pa_airports ORDER BY airport_identifier"
    ), 1):
        ident = _clean(r["airport_identifier"]).upper()
        if not ident:
            continue
        airport_id[ident] = i
        airports.append((
            i, _clean(r["airport_name"]), ident, None,
            _as_float(r["airport_ref_latitude"]) or 0,
            _as_float(r["airport_ref_longitude"]) or 0,
            _as_int(r["elevation"]), _as_int(r["transition_altitude"]),
            _as_int(r["transition_level"]), _as_int(r["speed_limit"]),
            _as_int(r["speed_limit_altitude"]),
        ))
    _insert_chunks(dst, "INSERT INTO Airports VALUES (?,?,?,?,?,?,?,?,?,?,?)", airports)
    print(f"airports: {len(airports)}")

    # ---------- waypoints ----------
    waypoint_id = {}
    waypoint_ids_by_ident = defaultdict(list)
    waypoint_rows = []

    def add_waypoint(ident, name, lat, lon):
        ident = _clean(ident).upper()
        lat = _as_float(lat)
        lon = _as_float(lon)
        if not ident:
            return None
        key = _key(ident, lat, lon)
        existing = waypoint_id.get(key)
        if existing:
            return existing
        wid = len(waypoint_rows) + 1
        waypoint_id[key] = wid
        waypoint_ids_by_ident[ident].append(wid)
        waypoint_rows.append((wid, ident, 0, _clean(name) or None, lat or 0, lon or 0, None))
        return wid

    for r in src.execute("SELECT waypoint_identifier, waypoint_name, waypoint_latitude, waypoint_longitude FROM tbl_ea_enroute_waypoints"):
        add_waypoint(r["waypoint_identifier"], r["waypoint_name"], r["waypoint_latitude"], r["waypoint_longitude"])
    for r in src.execute("SELECT waypoint_identifier, waypoint_name, waypoint_latitude, waypoint_longitude FROM tbl_pc_terminal_waypoints"):
        add_waypoint(r["waypoint_identifier"], r["waypoint_name"], r["waypoint_latitude"], r["waypoint_longitude"])
    for table, ident_col, name_col, lat_col, lon_col in (
        ("tbl_d_vhfnavaids", "navaid_identifier", "navaid_name", "navaid_latitude", "navaid_longitude"),
        ("tbl_db_enroute_ndbnavaids", "navaid_identifier", "navaid_name", "navaid_latitude", "navaid_longitude"),
        ("tbl_pn_terminal_ndbnavaids", "navaid_identifier", "navaid_name", "navaid_latitude", "navaid_longitude"),
    ):
        for r in src.execute(f"SELECT {ident_col}, {name_col}, {lat_col}, {lon_col} FROM {table}"):
            add_waypoint(r[ident_col], r[name_col], r[lat_col], r[lon_col])
    print(f"waypoints: {len(waypoint_rows)}")

    def get_waypoint(ident, lat, lon, create=True):
        ident = _clean(ident).upper()
        lat = _as_float(lat)
        lon = _as_float(lon)
        if not ident:
            return None
        key = _key(ident, lat, lon)
        wid = waypoint_id.get(key)
        if wid:
            return wid
        # exact coordinate often differs by tiny source rounding; use nearest same-ident.
        candidates = waypoint_ids_by_ident.get(ident) or []
        if candidates and lat is not None and lon is not None:
            best = None
            best_d = 1e9
            for cid in candidates:
                row = waypoint_rows[cid - 1]
                d = (float(row[4]) - lat) ** 2 + (float(row[5]) - lon) ** 2
                if d < best_d:
                    best, best_d = cid, d
            if best_d < 0.01:  # ~1 km in degrees at low latitude; source rounding only
                return best
        if create and lat is not None and lon is not None:
            return add_waypoint(ident, None, lat, lon)
        if candidates:
            return candidates[0]
        return None

    _insert_chunks(dst, "INSERT INTO Waypoints VALUES (?,?,?,?,?,?,?)", waypoint_rows)
    initial_waypoint_count = len(waypoint_rows)

    # ---------- runways ----------
    runway_id = {}
    runway_rows = []
    for r in src.execute(
        "SELECT airport_identifier, runway_identifier, runway_true_bearing, runway_length, "
        "runway_width, surface_code, runway_latitude, runway_longitude, landing_threshold_elevation "
        "FROM tbl_pg_runways ORDER BY airport_identifier, runway_identifier"
    ):
        apt = _clean(r["airport_identifier"]).upper()
        aid = airport_id.get(apt)
        if not aid:
            continue
        ident = _clean(r["runway_identifier"]).upper()
        if ident.startswith("RW"):
            ident = ident[2:]
        if not ident:
            continue
        rid = len(runway_rows) + 1
        runway_id[(aid, ident)] = rid
        runway_rows.append((
            rid, aid, ident, _as_float(r["runway_true_bearing"]) or 0,
            _as_int(r["runway_length"]), _as_int(r["runway_width"]),
            _clean(r["surface_code"]) or None,
            _as_float(r["runway_latitude"]) or 0, _as_float(r["runway_longitude"]) or 0,
            _as_int(r["landing_threshold_elevation"]),
        ))
    _insert_chunks(dst, "INSERT INTO Runways VALUES (?,?,?,?,?,?,?,?,?,?)", runway_rows)
    print(f"runways : {len(runway_rows)}")

    # ---------- terminal procedures ----------
    proc_tables = (
        ("tbl_pd_sids", "2"),
        ("tbl_pe_stars", "1"),
        ("tbl_pf_iaps", "3"),
    )
    terminal_rows = []
    terminal_ids = {}
    leg_rows = []
    unmatched_legs = 0

    for table, proc_code in proc_tables:
        grouped = defaultdict(list)
        for r in src.execute(f"SELECT * FROM {table}"):
            apt = _clean(r["airport_identifier"]).upper()
            proc = _clean(r["procedure_identifier"])
            if apt and proc:
                grouped[(apt, proc)].append(r)
        for (apt, proc), rows in grouped.items():
            aid = airport_id.get(apt)
            if not aid:
                continue
            key = (aid, proc_code, proc)
            if key in terminal_ids:
                continue
            rwy = None
            rwy_id = None
            for r in rows:
                tr = _clean(r["transition_identifier"]).upper()
                if tr.startswith("RW"):
                    rwy = tr[2:]
                    break
            if rwy:
                rwy_id = runway_id.get((aid, rwy))
            tid = len(terminal_rows) + 1
            terminal_ids[key] = tid
            terminal_rows.append((tid, aid, proc_code, apt, proc, proc, rwy, rwy_id, None))
            # Keep each transition contiguous and ordered by source seqno.
            for r in sorted(rows, key=lambda x: (_clean(x["transition_identifier"]), _as_int(x["seqno"]))):
                ident = _clean(r["waypoint_identifier"]).upper()
                lat = _as_float(r["waypoint_latitude"])
                lon = _as_float(r["waypoint_longitude"])
                wid = get_waypoint(ident, lat, lon) if ident else None
                if wid is None and lat is not None and lon is not None:
                    wid = get_waypoint(ident or "WPT", lat, lon)
                if wid is None and ident:
                    unmatched_legs += 1
                tr = _clean(r["transition_identifier"]).upper() or "ALL"
                alt1 = r["altitude1"]
                alt_desc = _clean(r["altitude_description"])
                alt_code = {"+": "A", "-": "B", "@": "A"}.get(alt_desc, alt_desc)
                alt = f"{_as_int(alt1):05d}{alt_code}" if alt1 not in (None, "") else None
                distance = _as_float(r["distance_time"])
                leg_rows.append((
                    len(leg_rows) + 1, tid, _clean(r["route_type"]) or None, tr,
                    _clean(r["path_termination"]).upper() or None, wid, lat, lon,
                    _clean(r["turn_direction"]) or None, None,
                    _as_float(r["recommended_navaid_latitude"]), _as_float(r["recommended_navaid_longitude"]),
                    None, None, _as_float(r["course"]), distance, alt,
                    _as_float(r["vertical_angle"]), None,
                    _as_float(r["center_waypoint_latitude"]), _as_float(r["center_waypoint_longitude"]),
                    _clean(r["waypoint_description_code"]) or None,
                ))

    _insert_chunks(dst, "INSERT INTO Terminals VALUES (?,?,?,?,?,?,?,?,?)", terminal_rows)
    _insert_chunks(dst, "INSERT INTO TerminalLegs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", leg_rows)
    print(f"terminals: {len(terminal_rows)}  legs: {len(leg_rows)}  unmatched-legs: {unmatched_legs}")

    # ---------- airways ----------
    airway_id = {}
    airway_rows = []
    airway_legs = []
    grouped_airways = defaultdict(list)
    for r in src.execute(
        "SELECT route_identifier, flightlevel, area_code, seqno, waypoint_identifier, "
        "waypoint_latitude, waypoint_longitude FROM tbl_er_enroute_airways "
        "ORDER BY route_identifier, flightlevel, area_code, seqno"
    ):
        route = _clean(r["route_identifier"]).upper()
        if not route:
            continue
        grouped_airways[(route, _clean(r["flightlevel"]).upper(), _clean(r["area_code"]).upper())].append(r)

    for (route, level, _area), rows in grouped_airways.items():
        aid = airway_id.get(route)
        if not aid:
            aid = len(airway_rows) + 1
            airway_id[route] = aid
            airway_rows.append((aid, route))
        points = []
        for r in rows:
            wid = get_waypoint(r["waypoint_identifier"], r["waypoint_latitude"], r["waypoint_longitude"])
            if wid is not None:
                points.append(wid)
        # Remove consecutive duplicates.
        clean_points = []
        for wid in points:
            if not clean_points or clean_points[-1] != wid:
                clean_points.append(wid)
        if len(clean_points) < 2:
            continue
        for i in range(len(clean_points) - 1):
            airway_legs.append((
                len(airway_legs) + 1, aid, level, clean_points[i], clean_points[i + 1],
                1 if i == 0 else 0, 1 if i == len(clean_points) - 2 else 0,
            ))

    _insert_chunks(dst, "INSERT INTO Airways VALUES (?,?)", airway_rows)
    _insert_chunks(dst, "INSERT INTO AirwayLegs VALUES (?,?,?,?,?,?,?)", airway_legs)
    print(f"airways : {len(airway_rows)}  airway-legs: {len(airway_legs)}")

    # Some terminal/airway waypoints are absent from the source waypoint tables
    # and are materialized from their leg coordinates during the pass above.
    # They get IDs after the initial INSERT, so flush the tail now.
    if len(waypoint_rows) > initial_waypoint_count:
        _insert_chunks(
            dst,
            "INSERT INTO Waypoints VALUES (?,?,?,?,?,?,?)",
            waypoint_rows[initial_waypoint_count:],
        )
        print(f"waypoints +{len(waypoint_rows) - initial_waypoint_count} materialized from legs/airways")

    # ---------- config ----------
    dst.executemany("INSERT INTO config VALUES (?,?)", [
        ("CycleName", cycle),
        ("CycleStartDate", start),
        ("CycleEndDate", end),
    ])
    dst.commit()
    dst.execute("ANALYZE")
    dst.commit()
    dst.close()
    src.close()

    tmp_out.replace(out)
    # The sidecar/installer expects the cycle metadata next to nd.db3.
    for name in ("cycle_info.txt", "cycle.json"):
        candidate = src_dir / name
        if candidate.exists():
            shutil.copy2(candidate, out.parent / name)
            print(f"metadata: {out.parent / name}")
    print(f"built  : {out}")
    print(f"size   : {out.stat().st_size / 1024 / 1024:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args()
    convert(args.source, args.out, replace=args.replace)


if __name__ == "__main__":
    main()
