"""
Database manager for ShortHunter using DuckDB.
Handles high-performance local time-series storage for OHLCV, funding rates, and backtest results.
"""

import os
import shutil
from typing import List, Optional
import duckdb
import pandas as pd

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "shorthunter.duckdb")

class Database:
    def __init__(self, db_path: str = DEFAULT_DB_PATH, read_only: bool = False):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            self.conn = duckdb.connect(self.db_path, read_only=read_only)
            if not read_only:
                self.init_tables()
        except duckdb.IOException as e:
            if "lock" in str(e).lower():
                # Non-blocking snapshot fallback for parallel reads
                snap_path = f"{self.db_path}.read_snap"
                shutil.copy2(self.db_path, snap_path)
                self.conn = duckdb.connect(snap_path, read_only=True)
            else:
                raise e


    def init_tables(self):
        """Creates the required tables if they don't already exist."""
        # 1. Daily Candles Table (for multi-month momentum & 90D screening)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_candles (
                symbol VARCHAR,
                timestamp BIGINT,
                datetime TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                trades_count BIGINT,
                PRIMARY KEY (symbol, timestamp)
            )
        """)

        # 2. 4-Hour Candles Table (for reversal / CHoCH confirmation)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS h4_candles (
                symbol VARCHAR,
                timestamp BIGINT,
                datetime TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                trades_count BIGINT,
                PRIMARY KEY (symbol, timestamp)
            )
        """)

        # 3. Market Snapshots / Derivatives Stats (Funding Rate, OI, 24h Vol)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                symbol VARCHAR,
                timestamp BIGINT,
                datetime TIMESTAMP,
                mark_price DOUBLE,
                funding_rate DOUBLE,
                open_interest DOUBLE,
                volume_24h DOUBLE,
                PRIMARY KEY (symbol, timestamp)
            )
        """)

        # 4. Screening Signals (Saved historical alert candidates)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                signal_id VARCHAR PRIMARY KEY,
                symbol VARCHAR,
                timestamp BIGINT,
                datetime TIMESTAMP,
                score DOUBLE,
                gain_90d DOUBLE,
                gain_30d DOUBLE,
                rsi_daily DOUBLE,
                funding_rate DOUBLE,
                details JSON
            )
        """)

        # 5. Nansen Smart Money Historical Holdings
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS nansen_smart_money_history (
                date DATE,
                chain VARCHAR,
                symbol VARCHAR,
                token_address VARCHAR,
                value_usd DOUBLE,
                balance_change_pct DOUBLE,
                holders_count BIGINT,
                PRIMARY KEY (date, chain, token_address)
            )
        """)


    def insert_candles(self, table_name: str, df: pd.DataFrame):
        """
        Inserts or replaces candles in bulk using DuckDB's native fast insertion.
        DataFrame must contain: symbol, timestamp, datetime, open, high, low, close, volume, trades_count.
        """
        if df.empty:
            return

        # Use INSERT OR REPLACE / ON CONFLICT to avoid duplicates
        self.conn.register("df_temp", df)
        self.conn.execute(f"""
            INSERT OR REPLACE INTO {table_name}
            SELECT * FROM df_temp
        """)
        self.conn.unregister("df_temp")

    def insert_snapshot(self, df: pd.DataFrame):
        """Inserts real-time market snapshots."""
        if df.empty:
            return
        self.conn.register("df_snap_temp", df)
        self.conn.execute("""
            INSERT OR REPLACE INTO market_snapshots
            SELECT * FROM df_snap_temp
        """)
        self.conn.unregister("df_snap_temp")

    def insert_nansen_history(self, df: pd.DataFrame):
        """Inserts Nansen Smart Money historical holdings."""
        if df.empty:
            return
        self.conn.register("df_nan_temp", df)
        self.conn.execute("""
            INSERT OR REPLACE INTO nansen_smart_money_history
            SELECT * FROM df_nan_temp
        """)
        self.conn.unregister("df_nan_temp")

    def insert_signals(self, signals_list: List[Dict]):
        """Persists screener signals into DuckDB signals table."""
        if not signals_list:
            return
        import time
        import json
        from datetime import datetime
        now_ms = int(time.time() * 1000)
        now_dt = datetime.now()
        rows = []
        for s in signals_list:
            sym = s.get("symbol", s.get("coin"))
            sig_id = f"{sym}_{s.get('timestamp', now_ms)}"
            rows.append({
                "signal_id": str(sig_id),
                "symbol": str(sym),
                "timestamp": int(s.get("timestamp", now_ms)),
                "datetime": now_dt,
                "score": float(s.get("score", 0.0)),
                "gain_90d": float(s.get("gain_90d", 0.0)),
                "gain_30d": float(s.get("gain_30d", 0.0)),
                "rsi_daily": float(s.get("rsi_daily", s.get("rsi", 0.0))),
                "funding_rate": float(s.get("funding_rate", 0.0)),
                "details": json.dumps(s)
            })
        df_sig = pd.DataFrame(rows)
        try:
            self.conn.register("df_sig_temp", df_sig)
            self.conn.execute("""
                INSERT OR REPLACE INTO signals
                SELECT * FROM df_sig_temp
            """)
            self.conn.unregister("df_sig_temp")
        except Exception:
            pass

    def get_candles(self, table_name: str, symbol: str, limit: Optional[int] = None) -> pd.DataFrame:
        """Retrieves candles for a given symbol sorted by timestamp ascending."""
        query = f"SELECT * FROM {table_name} WHERE symbol = ? ORDER BY timestamp ASC"
        if limit:
            query = f"SELECT * FROM ({query} DESC LIMIT {limit}) ORDER BY timestamp ASC"
            return self.conn.execute(query, [symbol]).df()
        return self.conn.execute(query, [symbol]).df()

    def get_symbols(self, table_name: str = "daily_candles") -> List[str]:
        """Returns list of distinct symbols present in a table."""
        res = self.conn.execute(f"SELECT DISTINCT symbol FROM {table_name} ORDER BY symbol").fetchall()
        return [r[0] for r in res]

    def get_stats(self) -> dict:
        """Returns row counts and summary of the database."""
        stats = {}
        for tbl in ["daily_candles", "h4_candles", "market_snapshots", "signals"]:
            try:
                count = self.conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                symbols = self.conn.execute(f"SELECT COUNT(DISTINCT symbol) FROM {tbl}").fetchone()[0]
                stats[tbl] = {"rows": count, "symbols": symbols}
            except Exception:
                stats[tbl] = {"rows": 0, "symbols": 0}
        return stats

    def close(self):
        self.conn.close()
