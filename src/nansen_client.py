"""
Nansen.ai API Client:
Fetches Smart Money token holdings, netflow changes, and whale distribution data.
"""

import os
from typing import Dict, List, Optional
import requests
from dotenv import load_dotenv

load_dotenv()

class NansenClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("NANSEN_API_KEY")
        self.base_url = "https://api.nansen.ai/api/v1"
        self.headers = {
            "accept": "application/json",
            "apiKey": self.api_key or "",
            "content-type": "application/json"
        }

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10)

    def get_smart_money_holdings(self, chains: Optional[List[str]] = None) -> Dict[str, Dict]:
        """
        Fetches current Smart Money token holdings across specified chains.
        Returns a dict mapped by token symbol:
        {
           'PUMP': {'change_24h_pct': -2.83, 'value_usd': 8555816, 'holders_count': 45},
           ...
        }
        """
        if not self.is_configured():
            return {}

        if chains is None:
            chains = ["ethereum", "solana", "arbitrum", "base", "bsc", "polygon", "avalanche"]

        payload = {
            "chains": chains,
            "pagination": {"limit": 100}
        }

        results = {}

        # 1. Fetch from smart-money/holdings
        try:
            resp = requests.post(
                f"{self.base_url}/smart-money/holdings",
                headers=self.headers,
                json=payload,
                timeout=8
            )
            if resp.status_code == 200:
                for item in resp.json().get("data", []):
                    sym = item.get("token_symbol", "").upper()
                    if not sym:
                        continue
                    pct = (item.get("balance_24h_percent_change") or 0.0) * 100
                    val = item.get("value_usd") or 0.0
                    holders = item.get("holders_count") or 0

                    results[sym] = {
                        "change_24h_pct": round(pct, 2),
                        "value_usd": val,
                        "holders_count": holders,
                        "action": "DUMPING" if pct < -1.0 else ("ACCUMULATING" if pct > 1.0 else "NEUTRAL")
                    }
        except Exception:
            pass

        # 2. Enrich from smart-money/netflow (adds extra tokens)
        try:
            resp2 = requests.post(
                f"{self.base_url}/smart-money/netflow",
                headers=self.headers,
                json=payload,
                timeout=8
            )
            if resp2.status_code == 200:
                for item in resp2.json().get("data", []):
                    sym = item.get("token_symbol", "").upper()
                    if not sym or sym in results:
                        continue
                    net_flow = item.get("net_flow_usd") or 0.0
                    results[sym] = {
                        "change_24h_pct": round(-2.5 if net_flow < 0 else 2.5, 2),
                        "value_usd": abs(net_flow),
                        "holders_count": 0,
                        "action": "DUMPING" if net_flow < 0 else ("ACCUMULATING" if net_flow > 0 else "NEUTRAL")
                    }
        except Exception:
            pass

        return results


    def get_historical_smart_money(self, from_date: str, to_date: str,
                                   chains: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Fetches historical Smart Money holdings across specified chains and date range.
        Returns a DataFrame ready for insertion into nansen_smart_money_history.
        """
        if not self.is_configured():
            return pd.DataFrame()

        if chains is None:
            chains = ["ethereum", "solana", "arbitrum", "base"]

        payload = {
            "chains": chains,
            "date_range": {
                "from": from_date,
                "to": to_date
            },
            "pagination": {"limit": 100}
        }

        try:
            resp = requests.post(
                f"{self.base_url}/smart-money/historical-holdings",
                headers=self.headers,
                json=payload,
                timeout=15
            )
            if resp.status_code != 200:
                return pd.DataFrame()

            items = resp.json().get("data", [])
            rows = []
            for it in items:
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
        except Exception:
            return pd.DataFrame()
