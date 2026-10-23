#!/usr/bin/env python3
"""
ShortHunter Nansen Smart-Money Recorder (dipindah dari ImpulseHunter, ROADMAP 2.1, 2026-10-22)
Merekam smart-money perp trades, DEX trades, netflow & holdings secara point-in-time
ke data/nansen_recorder.duckdb (histori untuk fitur/label Tahap 3). Tiap panggilan juga
dicatat di buku besar kredit bersama data/nansen_ledger.db (src/nansen_ledger.py).

Usage (dari folder Gemini_ShortHunter):
    .venv/bin/python3 -m src.nansen_recorder --all
    .venv/bin/python3 -m src.nansen_recorder --test          # 5 rows/endpoint, 1 call each
Jadwal: launchd com.shorthunter.nansen setiap 4 jam (scripts/launchd/).

Credit safety:
    - Every call is logged to ingest_log (credits cost/used/remaining, latency).
    - Aborts before calling if the last known credits_remaining < MIN_CREDITS,
      and stops remaining endpoints as soon as a response reports < MIN_CREDITS.
"""
import os
import re
import sys
import time
import uuid
import argparse
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

NANSEN_BASE_URL = "https://api.nansen.ai/api/v1"
KEYCHAIN_SERVICE = "scr-nansen"
STORAGE_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = STORAGE_DIR / "nansen_recorder.duckdb"

MIN_CREDITS = 10000  # 2026-10-24: sama dengan src/nansen_ledger.py (cadangan 10rb untuk SCR)
REQUEST_TIMEOUT_S = 90       # netflow can take >30s
MAX_RETRIES = 1              # one retry on timeout / 429 / 5xx
DB_LOCK_RETRIES = 12         # tunggu bila file sedang dibuka proses lain
DB_LOCK_WAIT_S = 5

DEFAULT_CHAINS = ["ethereum", "solana", "base", "bnb", "arbitrum"]
PERP_MIN_USD = 5000
PERP_LOOKBACK_H = 3          # 2026-10-23: jendela perp per putaran (timer tiap jam -> tumpang tindih 2 jam, duplikat dibuang PK)
PERP_MAX_PAGES = 20          # batas halaman perp per putaran (5 kredit/halaman); tercapai = peringatan (mungkin terpotong)
DEX_MIN_USD = 1000
PAGE_SIZE = 1000
NETFLOW_PAGE_SIZE = 100      # top-N per sort direction
CHAIN_ALIASES = {"bsc": "bnb", "binance-smart-chain": "bnb"}


class CreditGuardError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    f = safe_float(val)
    return default if f is None else int(f)


def parse_ts(val: Any) -> Optional[datetime]:
    """ISO-8601 (with Z / offset) -> naive UTC datetime."""
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def normalize_chain(chain: Any) -> Optional[str]:
    if not chain:
        return None
    c = str(chain).strip().lower()
    return CHAIN_ALIASES.get(c, c)


_EMOJI_CATEGORIES = {"So", "Sk", "Cf", "Cs", "Co"}
_INVISIBLE = {"‍", "︎", "️", "⃣"}


def _is_emoji_char(ch: str) -> bool:
    return ch in _INVISIBLE or unicodedata.category(ch) in _EMOJI_CATEGORIES


def clean_symbol(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """'🌱 ELON' -> ('ELON', '🌱'). Keeps non-Latin letters (e.g. CJK tickers)."""
    if raw is None:
        return None, None
    s = str(raw)
    flag = "".join(ch for ch in s if _is_emoji_char(ch) and ch not in _INVISIBLE)
    clean = "".join(ch for ch in s if not _is_emoji_char(ch))
    clean = re.sub(r"\s+", " ", clean).strip().upper()
    return (clean or None), (flag or None)


def perp_dex_prefix(symbol: Optional[str]) -> str:
    """HIP-3 builder perps are 'dex:SYMBOL' (e.g. 'xyz:TSLA'); native HL -> ''."""
    if symbol and ":" in symbol:
        return symbol.split(":", 1)[0].lower()
    return ""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def load_api_key() -> str:
    key = os.getenv("NANSEN_API_KEY", "").strip()
    if key:
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        key = out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        key = ""
    if not key:
        raise RuntimeError(
            f"NANSEN_API_KEY not found in env or Keychain (service '{KEYCHAIN_SERVICE}')"
        )
    return key


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingest_log (
    run_id VARCHAR,
    endpoint VARCHAR,
    http_status INTEGER,
    credits_cost INTEGER,
    credits_used INTEGER,
    credits_remaining BIGINT,
    rows_count INTEGER,
    latency_ms INTEGER,
    fetched_at TIMESTAMP,
    error VARCHAR,
    PRIMARY KEY (run_id, endpoint, fetched_at)
);
CREATE TABLE IF NOT EXISTS sm_perp_trade (
    tx_hash VARCHAR,
    trader VARCHAR,
    trader_label VARCHAR,
    dex_prefix VARCHAR,
    symbol VARCHAR,
    side VARCHAR,
    action VARCHAR,
    order_type VARCHAR,
    amount DOUBLE,
    price DOUBLE,
    value_usd DOUBLE,
    block_ts TIMESTAMP,
    first_seen_at TIMESTAMP,
    PRIMARY KEY (tx_hash, trader, symbol, side, action)
);
CREATE TABLE IF NOT EXISTS sm_netflow_snap (
    snapshot_ts TIMESTAMP,
    sort_dir VARCHAR,
    rank INTEGER,
    chain VARCHAR,
    token_address VARCHAR,
    symbol_clean VARCHAR,
    risk_flag VARCHAR,
    nf_1h DOUBLE,
    nf_24h DOUBLE,
    nf_7d DOUBLE,
    nf_30d DOUBLE,
    trader_count INTEGER,
    mcap DOUBLE,
    run_id VARCHAR,
    PRIMARY KEY (snapshot_ts, sort_dir, chain, token_address)
);
CREATE TABLE IF NOT EXISTS sm_dex_trade (
    chain VARCHAR,
    tx_hash VARCHAR,
    trader VARCHAR,
    trader_label VARCHAR,
    block_ts TIMESTAMP,
    bought_addr VARCHAR,
    sold_addr VARCHAR,
    value_usd DOUBLE,
    first_seen_at TIMESTAMP,
    PRIMARY KEY (chain, tx_hash, trader, bought_addr, sold_addr)
);
CREATE TABLE IF NOT EXISTS sm_holdings_snap (
    snapshot_ts TIMESTAMP,
    chain VARCHAR,
    token_address VARCHAR,
    symbol_clean VARCHAR,
    value_usd DOUBLE,
    bal_chg_24h DOUBLE,
    holders_count INTEGER,
    share_pct DOUBLE,
    mcap DOUBLE,
    run_id VARCHAR,
    PRIMARY KEY (snapshot_ts, chain, token_address)
);
CREATE INDEX IF NOT EXISTS idx_sm_perp_ts ON sm_perp_trade(symbol, block_ts);
CREATE INDEX IF NOT EXISTS idx_sm_dex_ts ON sm_dex_trade(chain, block_ts);
"""

TABLE_COLUMNS: Dict[str, List[str]] = {
    "ingest_log": ["run_id", "endpoint", "http_status", "credits_cost", "credits_used",
                   "credits_remaining", "rows_count", "latency_ms", "fetched_at", "error"],
    "sm_perp_trade": ["tx_hash", "trader", "trader_label", "dex_prefix", "symbol", "side",
                      "action", "order_type", "amount", "price", "value_usd", "block_ts",
                      "first_seen_at"],
    "sm_netflow_snap": ["snapshot_ts", "sort_dir", "rank", "chain", "token_address",
                        "symbol_clean", "risk_flag", "nf_1h", "nf_24h", "nf_7d", "nf_30d",
                        "trader_count", "mcap", "run_id"],
    "sm_dex_trade": ["chain", "tx_hash", "trader", "trader_label", "block_ts", "bought_addr",
                     "sold_addr", "value_usd", "first_seen_at"],
    "sm_holdings_snap": ["snapshot_ts", "chain", "token_address", "symbol_clean", "value_usd",
                         "bal_chg_24h", "holders_count", "share_pct", "mcap", "run_id"],
}

TABLE_PK: Dict[str, List[str]] = {
    "ingest_log": ["run_id", "endpoint", "fetched_at"],
    "sm_perp_trade": ["tx_hash", "trader", "symbol", "side", "action"],
    "sm_netflow_snap": ["snapshot_ts", "sort_dir", "chain", "token_address"],
    "sm_dex_trade": ["chain", "tx_hash", "trader", "bought_addr", "sold_addr"],
    "sm_holdings_snap": ["snapshot_ts", "chain", "token_address"],
}


class Store:
    """Short-lived DuckDB connections with lock retry (file is shared with recorder.py)."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        import duckdb
        last_err = None
        for _ in range(DB_LOCK_RETRIES):
            try:
                return duckdb.connect(str(self.db_path))
            except duckdb.IOException as e:   # "Could not set lock on file"
                last_err = e
                time.sleep(DB_LOCK_WAIT_S)
        raise RuntimeError(f"DuckDB locked after {DB_LOCK_RETRIES} attempts: {last_err}")

    def init_schema(self) -> None:
        con = self._connect()
        try:
            con.execute(SCHEMA_SQL)
        finally:
            con.close()

    def last_credits_remaining(self) -> Optional[int]:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT credits_remaining FROM ingest_log "
                "WHERE credits_remaining IS NOT NULL ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        return int(row[0]) if row else None

    def insert(self, table: str, rows: List[Dict[str, Any]]) -> int:
        """INSERT OR IGNORE; returns the number of newly inserted rows."""
        if not rows:
            return 0
        cols = TABLE_COLUMNS[table]
        pk = TABLE_PK[table]
        seen, values = set(), []
        for r in rows:                      # in-batch dedup on PK
            k = tuple(r.get(c) for c in pk)
            if k in seen:
                continue
            seen.add(k)
            values.append(tuple(r.get(c) for c in cols))
        sql = (f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        con = self._connect()
        try:
            before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            con.executemany(sql, values)
            after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.close()
        return after - before


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

@dataclass
class ApiResult:
    endpoint: str
    http_status: int
    data: List[Dict[str, Any]]
    credits_cost: Optional[int]
    credits_used: Optional[int]
    credits_remaining: Optional[int]
    latency_ms: int
    fetched_at: datetime
    error: Optional[str] = None


class NansenClient:
    def __init__(self, api_key: str, base_url: str = NANSEN_BASE_URL,
                 session: Optional[requests.Session] = None):
        self.base_url = base_url
        self.session = session or requests.Session()
        self.session.headers.update({
            "apiKey": api_key,
            "accept": "application/json",
            "content-type": "application/json",
        })

    def post(self, endpoint: str, body: Dict[str, Any]) -> ApiResult:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        attempt = 0
        while True:
            fetched_at = utc_now()
            t0 = time.monotonic()
            try:
                resp = self.session.post(url, json=body, timeout=REQUEST_TIMEOUT_S)
            except requests.RequestException as e:
                latency = int((time.monotonic() - t0) * 1000)
                if attempt < MAX_RETRIES:
                    attempt += 1
                    time.sleep(2)
                    continue
                return ApiResult(endpoint, 0, [], None, None, None, latency, fetched_at,
                                 error=f"{type(e).__name__}: {e}"[:500])
            latency = int((time.monotonic() - t0) * 1000)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                attempt += 1
                time.sleep(3)
                continue
            break

        h = resp.headers
        data, error = [], None
        _ledger_ep = endpoint
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if resp.status_code == 200 and isinstance(payload, dict):
            data = payload.get("data") or []
        else:
            if isinstance(payload, dict):
                error = f"{payload.get('code') or payload.get('error')}: {payload.get('message')}"
            else:
                error = resp.text[:300]
        try:
            from src.nansen_ledger import log_call
            log_call("recorder", _ledger_ep, resp.status_code, h.get("x-nansen-credits-cost"),
                     h.get("x-nansen-credits-remaining"), len(data), latency, error)
        except Exception:
            pass
        return ApiResult(
            endpoint=endpoint,
            http_status=resp.status_code,
            data=data,
            credits_cost=safe_int(h.get("x-nansen-credits-cost")),
            credits_used=safe_int(h.get("x-nansen-credits-used")),
            credits_remaining=safe_int(h.get("x-nansen-credits-remaining")),
            latency_ms=latency,
            fetched_at=fetched_at,
            error=error,
        )


# ---------------------------------------------------------------------------
# Row parsers (API item -> table row)
# ---------------------------------------------------------------------------

def parse_perp_trade(it: Dict[str, Any], seen_at: datetime) -> Optional[Dict[str, Any]]:
    tx = it.get("transaction_hash")
    if not tx:
        return None
    symbol = it.get("token_symbol")
    return {
        "tx_hash": tx,
        "trader": it.get("trader_address"),
        "trader_label": it.get("trader_address_label") or None,
        "dex_prefix": perp_dex_prefix(symbol),
        "symbol": symbol,
        "side": it.get("side"),
        "action": it.get("action"),
        "order_type": it.get("type"),
        "amount": safe_float(it.get("token_amount")),
        "price": safe_float(it.get("price_usd")),
        "value_usd": safe_float(it.get("value_usd")),
        "block_ts": parse_ts(it.get("block_timestamp")),
        "first_seen_at": seen_at,
    }


def parse_dex_trade(it: Dict[str, Any], seen_at: datetime) -> Optional[Dict[str, Any]]:
    tx = it.get("transaction_hash")
    if not tx:
        return None
    return {
        "chain": normalize_chain(it.get("chain")),
        "tx_hash": tx,
        "trader": it.get("trader_address"),
        "trader_label": it.get("trader_address_label") or None,
        "block_ts": parse_ts(it.get("block_timestamp")),
        "bought_addr": it.get("token_bought_address"),
        "sold_addr": it.get("token_sold_address"),
        "value_usd": safe_float(it.get("trade_value_usd", it.get("value_usd"))),
        "first_seen_at": seen_at,
    }


def parse_netflow(it: Dict[str, Any], snapshot_ts: datetime, sort_dir: str, rank: int,
                  run_id: str) -> Optional[Dict[str, Any]]:
    addr = it.get("token_address")
    if not addr:
        return None
    sym, flag = clean_symbol(it.get("token_symbol"))
    return {
        "snapshot_ts": snapshot_ts,
        "sort_dir": sort_dir,
        "rank": rank,
        "chain": normalize_chain(it.get("chain")),
        "token_address": addr,
        "symbol_clean": sym,
        "risk_flag": flag,
        "nf_1h": safe_float(it.get("net_flow_1h_usd")),
        "nf_24h": safe_float(it.get("net_flow_24h_usd")),
        "nf_7d": safe_float(it.get("net_flow_7d_usd")),
        "nf_30d": safe_float(it.get("net_flow_30d_usd")),
        "trader_count": safe_int(it.get("trader_count")),
        "mcap": safe_float(it.get("market_cap_usd")),
        "run_id": run_id,
    }


def parse_holding(it: Dict[str, Any], snapshot_ts: datetime,
                  run_id: str) -> Optional[Dict[str, Any]]:
    addr = it.get("token_address")
    if not addr:
        return None
    sym, _ = clean_symbol(it.get("token_symbol"))
    return {
        "snapshot_ts": snapshot_ts,
        "chain": normalize_chain(it.get("chain")),
        "token_address": addr,
        "symbol_clean": sym,
        "value_usd": safe_float(it.get("value_usd")),
        "bal_chg_24h": safe_float(it.get("balance_24h_percent_change")),
        "holders_count": safe_int(it.get("holders_count")),
        "share_pct": safe_float(it.get("share_of_holdings_percent")),
        "mcap": safe_float(it.get("market_cap_usd")),
        "run_id": run_id,
    }


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

@dataclass
class JobReport:
    name: str
    calls: int = 0
    fetched: int = 0
    inserted: int = 0
    errors: List[str] = field(default_factory=list)


class NansenRecorder:
    def __init__(self, client: NansenClient, store: Store, test_limit: Optional[int] = None,
                 chains: Optional[List[str]] = None, max_pages: int = 1,
                 min_credits: int = MIN_CREDITS, perp_lookback_hours: int = PERP_LOOKBACK_H,
                 perp_max_pages: int = PERP_MAX_PAGES):
        self.client = client
        self.store = store
        self.test_limit = test_limit
        self.chains = [normalize_chain(c) for c in (chains or DEFAULT_CHAINS)]
        self.max_pages = 1 if test_limit else max(1, max_pages)
        self.min_credits = min_credits
        self.perp_lookback_hours = min(max(int(perp_lookback_hours), 1), 168)
        self.perp_max_pages = max(1, int(perp_max_pages))
        self.run_id = ("test-" if test_limit else "") + \
            utc_now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.snapshot_ts = utc_now()
        self.credits_remaining: Optional[int] = None

    # -- credit guard -------------------------------------------------------
    def preflight(self) -> None:
        last = self.store.last_credits_remaining()
        self.credits_remaining = last
        if last is not None and last < self.min_credits:
            raise CreditGuardError(
                f"last known credits_remaining={last} < {self.min_credits}; aborting"
            )

    def _check_guard(self) -> None:
        if self.credits_remaining is not None and self.credits_remaining < self.min_credits:
            raise CreditGuardError(
                f"credits_remaining={self.credits_remaining} < {self.min_credits}; aborting"
            )

    def _call(self, endpoint: str, body: Dict[str, Any], log_name: str) -> ApiResult:
        self._check_guard()
        res = self.client.post(endpoint, body)
        if res.credits_remaining is not None:
            self.credits_remaining = res.credits_remaining
        self.store.insert("ingest_log", [{
            "run_id": self.run_id,
            "endpoint": log_name,
            "http_status": res.http_status,
            "credits_cost": res.credits_cost,
            "credits_used": res.credits_used,
            "credits_remaining": res.credits_remaining,
            "rows_count": len(res.data),
            "latency_ms": res.latency_ms,
            "fetched_at": res.fetched_at,
            "error": res.error,
        }])
        print(f"  [{log_name}] http={res.http_status} rows={len(res.data)} "
              f"cost={res.credits_cost} used={res.credits_used} "
              f"remaining={res.credits_remaining} {res.latency_ms}ms"
              + (f" err={res.error}" if res.error else ""))
        return res

    def _per_page(self, default: int) -> int:
        return self.test_limit or default

    def _paged(self, endpoint: str, body: Dict[str, Any], per_page: int, log_name: str,
               report: JobReport) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            b = dict(body, pagination={"page": page, "per_page": per_page})
            name = log_name if page == 1 else f"{log_name}#p{page}"
            res = self._call(endpoint, b, name)
            report.calls += 1
            if res.error:
                report.errors.append(f"{name}: {res.error}")
                break
            items.extend(res.data)
            if len(res.data) < per_page:
                break
        return items

    # -- jobs ---------------------------------------------------------------
    def record_perp(self) -> JobReport:
        """Ambil SELURUH perp trade smart money dalam jendela `lookback_hours` (paginasi sampai halaman pendek).
        2026-10-23: sebelumnya hanya 1 halaman tanpa lookback -> tiap putaran tepat 1000 baris = data terpotong."""
        rep = JobReport("perp")
        per_page = self._per_page(PAGE_SIZE)
        pages = 1 if self.test_limit else self.perp_max_pages
        items: List[Dict[str, Any]] = []
        complete = False
        for page in range(1, pages + 1):
            body = {"lookback_hours": self.perp_lookback_hours,
                    "filters": {"value_usd": {"min": PERP_MIN_USD}},
                    "order_by": [{"field": "block_timestamp", "direction": "DESC"}],
                    "pagination": {"page": page, "per_page": per_page}}
            name = "smart-money/perp-trades" if page == 1 else f"smart-money/perp-trades#p{page}"
            res = self._call("smart-money/perp-trades", body, name)
            rep.calls += 1
            if res.error:
                rep.errors.append(f"{name}: {res.error}")
                break
            items.extend(res.data)
            if len(res.data) < per_page:
                complete = True
                break
        if not complete and not self.test_limit and not rep.errors:
            rep.errors.append(f"perp: batas {pages} halaman tercapai — jendela {self.perp_lookback_hours} jam mungkin terpotong")
        seen = utc_now()
        rows = [r for r in (parse_perp_trade(it, seen) for it in items)
                if r and (r["value_usd"] or 0) >= PERP_MIN_USD]
        rep.fetched = len(rows)
        rep.inserted = self.store.insert("sm_perp_trade", rows)
        return rep

    def record_dex(self) -> JobReport:
        rep = JobReport("dex")
        items = self._paged(
            "smart-money/dex-trades",
            {"chains": self.chains,
             "filters": {"trade_value_usd": {"min": DEX_MIN_USD}},
             "order_by": [{"field": "block_timestamp", "direction": "DESC"}]},
            self._per_page(PAGE_SIZE), "smart-money/dex-trades", rep,
        )
        seen = utc_now()
        rows = [r for r in (parse_dex_trade(it, seen) for it in items)
                if r and (r["value_usd"] or 0) >= DEX_MIN_USD]
        rep.fetched = len(rows)
        rep.inserted = self.store.insert("sm_dex_trade", rows)
        return rep

    def record_netflow(self) -> JobReport:
        rep = JobReport("netflow")
        rows: List[Dict[str, Any]] = []
        for direction in ("DESC", "ASC"):   # top inflow, top outflow
            items = self._paged(
                "smart-money/netflow",
                {"chains": self.chains,
                 "order_by": [{"field": "net_flow_24h_usd", "direction": direction}]},
                self._per_page(NETFLOW_PAGE_SIZE),
                f"smart-money/netflow:{direction.lower()}", rep,
            )
            for i, it in enumerate(items, start=1):
                r = parse_netflow(it, self.snapshot_ts, direction.lower(), i, self.run_id)
                if r:
                    rows.append(r)
        rep.fetched = len(rows)
        rep.inserted = self.store.insert("sm_netflow_snap", rows)
        return rep

    def record_holdings(self) -> JobReport:
        rep = JobReport("holdings")
        items = self._paged(
            "smart-money/holdings",
            {"chains": self.chains,
             "order_by": [{"field": "value_usd", "direction": "DESC"}]},
            self._per_page(PAGE_SIZE), "smart-money/holdings", rep,
        )
        rows = [r for r in (parse_holding(it, self.snapshot_ts, self.run_id) for it in items) if r]
        rep.fetched = len(rows)
        rep.inserted = self.store.insert("sm_holdings_snap", rows)
        return rep

    def run(self, jobs: List[str]) -> Tuple[List[JobReport], Optional[str]]:
        dispatch: Dict[str, Callable[[], JobReport]] = {
            "perp": self.record_perp,
            "netflow": self.record_netflow,
            "dex": self.record_dex,
            "holdings": self.record_holdings,
        }
        reports: List[JobReport] = []
        aborted = None
        try:
            self.preflight()
            for j in jobs:
                print(f"[*] {j}")
                reports.append(dispatch[j]())
        except CreditGuardError as e:
            aborted = str(e)
            print(f"[!] CREDIT GUARD: {aborted}")
        return reports, aborted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Record Nansen smart-money data into DuckDB")
    p.add_argument("--all", action="store_true", help="perp + netflow + dex + holdings")
    p.add_argument("--perp", action="store_true", help="smart-money/perp-trades (>= $5k)")
    p.add_argument("--netflow", action="store_true", help="smart-money/netflow snapshot")
    p.add_argument("--dex", action="store_true", help="smart-money/dex-trades (>= $1k)")
    p.add_argument("--holdings", action="store_true", help="smart-money/holdings snapshot")
    p.add_argument("--test", nargs="?", const=5, type=int, metavar="N",
                   help="dry-run: N rows (5-10, default 5), 1 page per endpoint; "
                        "implies --all if no endpoint selected")
    p.add_argument("--chains", default=",".join(DEFAULT_CHAINS),
                   help="comma-separated chains for netflow/dex/holdings")
    p.add_argument("--max-pages", type=int, default=1,
                   help="pages per endpoint (each page costs credits)")
    p.add_argument("--perp-lookback-hours", type=int, default=PERP_LOOKBACK_H,
                   help="jendela perp-trades per putaran, 1-168 jam")
    p.add_argument("--perp-max-pages", type=int, default=PERP_MAX_PAGES,
                   help="batas halaman perp-trades per putaran (paginasi sampai habis)")
    p.add_argument("--db", default=str(DB_PATH), help="DuckDB path")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    jobs = [j for j in ("perp", "netflow", "dex", "holdings") if getattr(args, j)]
    if args.all or (args.test and not jobs):
        jobs = ["perp", "netflow", "dex", "holdings"]
    if not jobs:
        build_parser().print_help()
        return 2

    test_limit = None
    if args.test is not None:
        test_limit = min(max(args.test, 5), 10)

    try:
        api_key = load_api_key()
    except RuntimeError as e:
        print(f"[!] {e}")
        return 2

    store = Store(Path(args.db))
    store.init_schema()
    rec = NansenRecorder(
        NansenClient(api_key), store, test_limit=test_limit,
        chains=[c.strip() for c in args.chains.split(",") if c.strip()],
        max_pages=args.max_pages,
        perp_lookback_hours=args.perp_lookback_hours, perp_max_pages=args.perp_max_pages,
    )
    print(f"[Nansen] run_id={rec.run_id} jobs={jobs} test_limit={test_limit} db={store.db_path}")
    reports, aborted = rec.run(jobs)

    total_calls = sum(r.calls for r in reports)
    print("\n[Nansen] Summary")
    for r in reports:
        print(f"  {r.name:<9} calls={r.calls} fetched={r.fetched} inserted={r.inserted}"
              + (f" errors={r.errors}" if r.errors else ""))
    print(f"  calls={total_calls} credits_remaining={rec.credits_remaining}")
    if aborted:
        return 3
    return 1 if any(r.errors for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
