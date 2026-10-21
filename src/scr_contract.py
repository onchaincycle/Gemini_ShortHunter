"""
Pembaca file kontrak SCR v10 (ROADMAP_INTEGRASI 2.2, 2026-10-22).

ShortHunter TIDAK lagi membaca data/scr.db milik Claude_Crypto secara langsung. Sumber datanya
adalah ../Claude_Crypto/SCR_v10/output/scr_signals.json yang ditulis oleh
SCR_v10/tools/export_contract.py setiap run harian SCR.

connect() memuat isi kontrak ke SQLite in-memory dengan nama tabel yang sama seperti dulu
(coins, scores, market, runs), sehingga query lama tetap berjalan tanpa perubahan logika.
Data basi / tidak ada -> fungsi mengembalikan None/False; JANGAN diganti angka tebakan.
"""
import json
import os
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "Claude_Crypto", "SCR_v10", "output", "scr_signals.json"))
SUPPORTED_SCHEMA = 1
MAX_AGE_DAYS = 3

_cache: Dict[str, Any] = {"mtime": None, "data": None}


def load() -> Optional[Dict[str, Any]]:
    """Isi kontrak (di-cache per mtime) atau None bila file tidak ada / rusak / versi tak dikenal."""
    try:
        mt = os.path.getmtime(CONTRACT_PATH)
    except OSError:
        return None
    if _cache["mtime"] == mt:
        return _cache["data"]
    try:
        with open(CONTRACT_PATH) as f:
            data = json.load(f)
        if int(data.get("schema_version", 0)) != SUPPORTED_SCHEMA:
            print(f"[scr_contract] schema_version {data.get('schema_version')} tidak didukung (butuh {SUPPORTED_SCHEMA})")
            data = None
    except Exception as e:
        print(f"[scr_contract] gagal membaca kontrak: {e}")
        data = None
    _cache.update(mtime=mt, data=data)
    return data


def age_days() -> Optional[int]:
    d = load()
    if not d or not d.get("asof_date"):
        return None
    try:
        return (date.today() - datetime.strptime(d["asof_date"], "%Y-%m-%d").date()).days
    except Exception:
        return None


def is_fresh(max_age_days: int = MAX_AGE_DAYS) -> bool:
    a = age_days()
    return a is not None and a <= max_age_days


def status() -> Dict[str, Any]:
    d = load()
    return {
        "path": CONTRACT_PATH, "exists": os.path.exists(CONTRACT_PATH), "loaded": d is not None,
        "asof_date": d.get("asof_date") if d else None, "age_days": age_days(), "fresh": is_fresh(),
        "n_coins": len(d.get("coins", [])) if d else 0,
    }


def cycle() -> Optional[Dict[str, Any]]:
    d = load()
    c = (d or {}).get("cycle") or {}
    return c if c.get("ok") else None


def connect(require_fresh: bool = True) -> Optional[sqlite3.Connection]:
    """SQLite in-memory berisi tabel coins/scores/market/runs dari kontrak, atau None."""
    d = load()
    if d is None or (require_fresh and not is_fresh()):
        return None
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE coins (coin_id TEXT, symbol TEXT, name TEXT, rank INTEGER, price REAL, mcap REAL, "
                "vol24 REAL, ath_chg REAL, kelas TEXT, semua_kelas TEXT)")
    con.execute("CREATE TABLE scores (coin_id TEXT, date TEXT, ss REAL, es REAL, drs REAL, ph REAL, action TEXT)")
    con.execute("CREATE TABLE market (date TEXT, fng REAL, fng_class TEXT, btc_dom REAL, total_mcap REAL)")
    con.execute("CREATE TABLE runs (date TEXT, meta TEXT)")
    asof = d.get("asof_date")
    for c in d.get("coins", []):
        con.execute("INSERT INTO coins VALUES (?,?,?,?,?,?,?,?,?,?)", (
            c.get("coin_id"), c.get("symbol"), c.get("name"), c.get("rank"), c.get("price"), c.get("mcap"),
            c.get("vol24"), c.get("ath_chg"), c.get("kelas"), c.get("semua_kelas")))
        if c.get("ss") is not None or c.get("action") is not None:
            con.execute("INSERT INTO scores VALUES (?,?,?,?,?,?,?)", (
                c.get("coin_id"), asof, c.get("ss"), c.get("es"), c.get("drs"), c.get("ph"), c.get("action")))
    m = d.get("market") or {}
    if m:
        con.execute("INSERT INTO market VALUES (?,?,?,?,?)", (m.get("date"), m.get("fng"), m.get("fng_class"),
                                                              m.get("btc_dom"), m.get("total_mcap")))
        con.execute("INSERT INTO runs VALUES (?,?)", (asof, json.dumps({"market": {"label": m.get("regime_label")}})))
    con.commit()
    return con
