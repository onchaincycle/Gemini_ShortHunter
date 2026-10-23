"""
Buku besar kredit Nansen bersama untuk ShortHunter (ROADMAP 2.1, 2026-10-22).

Setiap panggilan API Nansen dari ShortHunter (screener live `nansen_client.py` dan
recorder terjadwal `nansen_recorder.py`) dicatat di sini, termasuk sisa kredit dari
header respons. SQLite WAL: aman dipakai beberapa proses sekaligus.

  from src.nansen_ledger import log_call, credits_ok, last_remaining
"""
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_DB = os.path.join(ROOT_DIR, "data", "nansen_ledger.db")
MIN_CREDITS = 10000  # 2026-10-24: kredit Nansen sekali beli & dipakai bersama SCR -> INTRADAY berhenti di 10.000 (10rb terakhir = cadangan SCR, berhenti di 2.000)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(LEDGER_DB), exist_ok=True)
    c = sqlite3.connect(LEDGER_DB, timeout=10.0, factory=_ClosingConnection)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS calls (
        ts TEXT NOT NULL, source TEXT NOT NULL, endpoint TEXT NOT NULL, http_status INTEGER,
        credits_cost INTEGER, credits_remaining INTEGER, rows_count INTEGER, latency_ms INTEGER, error TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts)")
    return c


def _int(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def log_call(source: str, endpoint: str, http_status: Optional[int] = None, credits_cost=None,
             credits_remaining=None, rows_count: Optional[int] = None, latency_ms: Optional[int] = None,
             error: Optional[str] = None) -> None:
    """Best-effort: kegagalan menulis ledger tidak boleh menghentikan trading."""
    try:
        with _conn() as c:
            c.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?)", (
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), source, endpoint, http_status,
                _int(credits_cost), _int(credits_remaining), rows_count, latency_ms, (error or None) and str(error)[:300]))
    except Exception:
        pass


def last_remaining() -> Optional[int]:
    try:
        with _conn() as c:
            r = c.execute("SELECT credits_remaining FROM calls WHERE credits_remaining IS NOT NULL "
                          "ORDER BY ts DESC LIMIT 1").fetchone()
            return int(r[0]) if r else None
    except Exception:
        return None


def credits_ok(min_credits: int = MIN_CREDITS) -> bool:
    rem = last_remaining()
    return rem is None or rem >= min_credits


def usage_summary(days: int = 7):
    """Pemakaian kredit per hari & per sumber (untuk menu / audit)."""
    with _conn() as c:
        return c.execute("""SELECT substr(ts,1,10) d, source, COUNT(*) calls, COALESCE(SUM(credits_cost),0) credits,
                                   MIN(credits_remaining) FROM calls
                            WHERE ts >= datetime('now', ?) GROUP BY 1,2 ORDER BY 1,2""", (f"-{days} days",)).fetchall()
