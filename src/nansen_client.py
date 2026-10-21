"""
Nansen.ai API Client:
Fetches Smart Money token holdings, netflow changes, and whale distribution data.

Tahap 1 (Unified Pipeline):
- Holdings + Netflow digabung per simbol.
- Normalisasi simbol Hyperliquid (kPEPE -> PEPE, dst.).
- Cache TTL in-memory + disk (data/nansen_cache.json).
- Tren Smart Money N hari dari tabel DuckDB nansen_smart_money_history.
"""

import os
import json
import time
from typing import Dict, List, Optional
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

LOCAL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
NANSEN_CACHE_FILE = os.path.join(LOCAL_DATA_DIR, "nansen_cache.json")
DEFAULT_CACHE_TTL_SEC = 15 * 60
DEFAULT_CHAINS = ["ethereum", "solana", "arbitrum", "base", "bsc", "polygon", "avalanche"]

# Hyperliquid perps yang diperdagangkan per 1000 unit (prefix "k")
HL_KILO_PREFIXED = {"KPEPE", "KBONK", "KSHIB", "KFLOKI", "KLUNC", "KDOGS", "KNEIRO"}


def normalize_symbol(symbol: str) -> str:
    """Maps a Hyperliquid perp symbol to the on-chain token symbol used by Nansen."""
    sym = (symbol or "").strip().upper()
    return sym[1:] if sym in HL_KILO_PREFIXED else sym


def classify_sm_action(change_24h_pct: float, net_flow_usd: float) -> str:
    """Balance change is primary; USD netflow breaks ties when balance is flat."""
    if change_24h_pct < -1.0:
        return "DUMPING"
    if change_24h_pct > 1.0:
        return "ACCUMULATING"
    if net_flow_usd <= -50_000:
        return "DUMPING"
    if net_flow_usd >= 50_000:
        return "ACCUMULATING"
    return "NEUTRAL"


class NansenClient:
    _mem_cache: Dict[str, Dict] = {}

    def __init__(self, api_key: Optional[str] = None, cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC):
        self.api_key = api_key or os.getenv("NANSEN_API_KEY")
        self.base_url = "https://api.nansen.ai/api/v1"
        self.cache_ttl_sec = cache_ttl_sec
        self.headers = {
            "accept": "application/json",
            "apiKey": self.api_key or "",
            "content-type": "application/json"
        }

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10)

    # --- Cache helpers ---------------------------------------------------
    def _cache_key(self, chains: List[str]) -> str:
        return "sm_signals:" + ",".join(sorted(chains))

    def _load_cache(self, key: str) -> Optional[Dict[str, Dict]]:
        entry = NansenClient._mem_cache.get(key)
        if entry is None and os.path.exists(NANSEN_CACHE_FILE):
            try:
                with open(NANSEN_CACHE_FILE, "r") as f:
                    entry = json.load(f).get(key)
            except Exception:
                entry = None
        if entry and (time.time() - float(entry.get("ts", 0))) <= self.cache_ttl_sec:
            NansenClient._mem_cache[key] = entry
            return entry.get("data", {})
        return None

    def _save_cache(self, key: str, data: Dict[str, Dict]):
        entry = {"ts": time.time(), "data": data}
        NansenClient._mem_cache[key] = entry
        try:
            os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
            disk = {}
            if os.path.exists(NANSEN_CACHE_FILE):
                with open(NANSEN_CACHE_FILE, "r") as f:
                    disk = json.load(f)
            disk[key] = entry
            tmp = NANSEN_CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(disk, f)
            os.replace(tmp, NANSEN_CACHE_FILE)
        except Exception:
            pass

    def _post(self, endpoint: str, payload: Dict, timeout: int = 8) -> List[Dict]:
        # 2026-10-22 (ROADMAP 2.1): setiap panggilan dicatat di buku besar kredit bersama
        # (data/nansen_ledger.db) dan ditolak bila sisa kredit terakhir < MIN_CREDITS.
        from src.nansen_ledger import log_call, credits_ok
        if not credits_ok():
            log_call("screener", endpoint, error="SKIPPED_LOW_CREDITS")
            return []
        t0 = time.time()
        try:
            resp = requests.post(f"{self.base_url}/{endpoint}", headers=self.headers, json=payload, timeout=timeout)
            data = (resp.json().get("data", []) or []) if resp.status_code == 200 else []
            log_call("screener", endpoint, resp.status_code, resp.headers.get("x-nansen-credits-cost"),
                     resp.headers.get("x-nansen-credits-remaining"), len(data), int((time.time() - t0) * 1000),
                     None if resp.status_code == 200 else resp.text[:200])
            return data
        except Exception as e:
            log_call("screener", endpoint, error=f"{type(e).__name__}: {e}")
        return []

    # --- Live Smart Money ------------------------------------------------
    def get_smart_money_signals(self, chains: Optional[List[str]] = None, use_cache: bool = True) -> Dict[str, Dict]:
        """
        Unified Smart Money snapshot per symbol (holdings + netflow merged):
        {'PUMP': {'change_24h_pct': -2.83, 'value_usd': 8555816, 'holders_count': 45,
                  'net_flow_usd': -120000.0, 'action': 'DUMPING', 'source': 'HOLDINGS+NETFLOW'}}
        """
        if not self.is_configured():
            return {}

        chains = chains or DEFAULT_CHAINS
        key = self._cache_key(chains)
        if use_cache:
            cached = self._load_cache(key)
            if cached is not None:
                return cached

        payload = {"chains": chains, "pagination": {"limit": 100}}
        results: Dict[str, Dict] = {}

        # 1. Holdings: value-weighted balance change when a symbol spans multiple chains
        for item in self._post("smart-money/holdings", payload):
            sym = (item.get("token_symbol") or "").upper()
            if not sym:
                continue
            pct = float(item.get("balance_24h_percent_change") or 0.0) * 100
            val = float(item.get("value_usd") or 0.0)
            r = results.setdefault(sym, {"change_24h_pct": 0.0, "value_usd": 0.0, "holders_count": 0,
                                         "net_flow_usd": 0.0, "source": "HOLDINGS"})
            tot_val = r["value_usd"] + val
            r["change_24h_pct"] = ((r["change_24h_pct"] * r["value_usd"] + pct * val) / tot_val) if tot_val > 0 else pct
            r["value_usd"] = tot_val
            r["holders_count"] += int(item.get("holders_count") or 0)

        # 2. Netflow: enriches existing symbols and adds new ones
        for item in self._post("smart-money/netflow", payload):
            sym = (item.get("token_symbol") or "").upper()
            if not sym:
                continue
            net_flow = float(item.get("net_flow_usd") or 0.0)
            if sym in results:
                results[sym]["net_flow_usd"] += net_flow
                results[sym]["source"] = "HOLDINGS+NETFLOW"
            else:
                results[sym] = {"change_24h_pct": 0.0, "value_usd": abs(net_flow), "holders_count": 0,
                                "net_flow_usd": net_flow, "source": "NETFLOW"}

        for r in results.values():
            r["change_24h_pct"] = round(r["change_24h_pct"], 2)
            r["net_flow_usd"] = round(r["net_flow_usd"], 2)
            r["action"] = classify_sm_action(r["change_24h_pct"], r["net_flow_usd"])

        # Don't cache empty results, so a transient API failure doesn't blind the screener for 15 min
        if results:
            self._save_cache(key, results)
        return results

    def get_smart_money_holdings(self, chains: Optional[List[str]] = None) -> Dict[str, Dict]:
        """Backward-compatible alias (dipakai long_screener)."""
        return self.get_smart_money_signals(chains=chains)

    @staticmethod
    def lookup(sm_signals: Dict[str, Dict], symbol: str) -> Optional[Dict]:
        """Finds Smart Money data for a Hyperliquid symbol, handling kPEPE-style prefixes."""
        if not sm_signals:
            return None
        sym = (symbol or "").upper()
        return sm_signals.get(sym) or sm_signals.get(normalize_symbol(sym))

    # --- Historical Smart Money ------------------------------------------
    @staticmethod
    def get_history_trends(db, days: int = 7) -> Dict[str, Dict]:
        """
        N-day Smart Money trend per symbol from DuckDB (filled by downloader.py), one query:
        {'PUMP': {'value_change_pct': -18.4, 'days_distributing': 5, 'days_observed': 7}}
        """
        try:
            df = db.conn.execute(f"""
                WITH daily AS (
                    SELECT symbol, date, SUM(value_usd) AS value_usd, AVG(balance_change_pct) AS bal_chg
                    FROM nansen_smart_money_history
                    WHERE date >= CURRENT_DATE - INTERVAL {int(days)} DAY
                    GROUP BY symbol, date
                )
                SELECT symbol,
                       arg_min(value_usd, date) AS first_val,
                       arg_max(value_usd, date) AS last_val,
                       SUM(CASE WHEN bal_chg < -1.0 THEN 1 ELSE 0 END) AS days_distributing,
                       COUNT(*) AS days_observed
                FROM daily GROUP BY symbol
            """).df()
        except Exception:
            return {}

        trends = {}
        for _, row in df.iterrows():
            first_val = float(row["first_val"] or 0.0)
            last_val = float(row["last_val"] or 0.0)
            chg = ((last_val - first_val) / first_val * 100.0) if first_val > 0 else 0.0
            trends[str(row["symbol"]).upper()] = {
                "value_change_pct": round(chg, 2),
                "days_distributing": int(row["days_distributing"]),
                "days_observed": int(row["days_observed"]),
            }
        return trends

    def get_historical_smart_money(self, from_date: str, to_date: str,
                                   chains: Optional[List[str]] = None) -> pd.DataFrame:
        """Historical Smart Money holdings, ready for nansen_smart_money_history."""
        if not self.is_configured():
            return pd.DataFrame()
        if chains is None:
            chains = ["ethereum", "solana", "arbitrum", "base"]

        payload = {"chains": chains, "date_range": {"from": from_date, "to": to_date}, "pagination": {"limit": 100}}
        rows = []
        for it in self._post("smart-money/historical-holdings", payload, timeout=15):
            sym = (it.get("token_symbol") or "").upper()
            if not sym:
                continue
            rows.append({
                "date": it.get("date"),
                "chain": it.get("chain"),
                "symbol": sym,
                "token_address": it.get("token_address"),
                "value_usd": float(it.get("value_usd") or 0.0),
                "balance_change_pct": float(it.get("balance_24h_percent_change") or 0.0) * 100,
                "holders_count": int(it.get("holders_count") or 0)
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df.drop_duplicates(subset=["date", "chain", "token_address"], inplace=True)
        return df
