"""
Data Downloader: Syncs historical OHLCV & market data from Hyperliquid into DuckDB.
Supports progress bar, batching, and error handling.
"""

import time
from typing import Optional
import pandas as pd
from tqdm import tqdm
from src.database import Database
from src.hyperliquid_client import HyperliquidClient
from src.nansen_client import NansenClient

class DataDownloader:
    def __init__(self, db: Database, client: Optional[HyperliquidClient] = None, nansen: Optional[NansenClient] = None):
        self.db = db
        self.client = client or HyperliquidClient()
        self.nansen = nansen or NansenClient()

    def sync_all(self, min_vol_usd: float = 100_000, days_history: int = 120, sync_4h: bool = True):
        """
        Downloads market snapshot and historical candles for all active coins meeting min volume.
        - Daily candles: past `days_history` days.
        - 4H candles: past 45 days.
        """
        print(f"[*] Fetching market overview and active coins...")
        meta_list = self.client.get_meta_and_contexts()
        df_meta = pd.DataFrame(meta_list)

        # Save snapshot to DB
        self.db.insert_snapshot(df_meta[["symbol", "timestamp", "datetime", "mark_price", "funding_rate", "open_interest", "volume_24h"]])
        print(f"[+] Saved market snapshot for {len(df_meta)} assets to DuckDB.")

        # Filter by minimum 24h volume
        active_coins = df_meta[df_meta["volume_24h"] >= min_vol_usd]["symbol"].tolist()
        print(f"[*] Found {len(active_coins)} active perpetuals with 24H volume >= ${min_vol_usd:,.0f}")

        now_ms = int(time.time() * 1000)
        daily_start_ms = now_ms - (days_history * 24 * 60 * 60 * 1000)
        h4_start_ms = now_ms - (45 * 24 * 60 * 60 * 1000)

        total_daily_candles = 0
        total_h4_candles = 0

        # Progress bar
        with tqdm(active_coins, desc="Downloading Market Data", unit="coin") as pbar:
            for coin in pbar:
                pbar.set_postfix_str(f"Syncing {coin}")
                try:
                    # 1. Fetch Daily Candles
                    df_daily = self.client.get_candles(
                        coin=coin,
                        interval="1d",
                        start_time_ms=daily_start_ms,
                        end_time_ms=now_ms
                    )
                    if not df_daily.empty:
                        self.db.insert_candles("daily_candles", df_daily)
                        total_daily_candles += len(df_daily)

                    # 2. Fetch 4H Candles (optional)
                    if sync_4h:
                        df_h4 = self.client.get_candles(
                            coin=coin,
                            interval="4h",
                            start_time_ms=h4_start_ms,
                            end_time_ms=now_ms
                        )
                        if not df_h4.empty:
                            self.db.insert_candles("h4_candles", df_h4)
                            total_h4_candles += len(df_h4)

                    # Gentle sleep to respect rate limits
                    time.sleep(0.05)
                except Exception as e:
                    # Continue on individual coin error
                    continue

        # 3. Fetch Nansen Smart Money History if configured
        if self.nansen.is_configured():
            print("\n[*] Syncing on-chain Smart Money history from Nansen.ai...")
            from datetime import datetime, timedelta
            to_d = datetime.now().strftime("%Y-%m-%d")
            from_d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            try:
                df_nan = self.nansen.get_historical_smart_money(from_date=from_d, to_date=to_d)
                if not df_nan.empty:
                    self.db.insert_nansen_history(df_nan)
                    print(f"[+] Saved {len(df_nan)} Smart Money historical records to DuckDB.")
            except Exception as e:
                print(f"[!] Warning: Nansen historical sync skipped: {e}")

        print(f"\n[✓] Sync Complete!")
        print(f"    - Daily Candles inserted: {total_daily_candles:,}")
        if sync_4h:
            print(f"    - 4H Candles inserted:    {total_h4_candles:,}")

    def sync_snapshot_only(self) -> int:
        """
        Fast 0.5s sync: Updates live prices, funding rates, open interest, and 24h volume.
        """
        try:
            meta_list = self.client.get_meta_and_contexts()
            df_meta = pd.DataFrame(meta_list)
            self.db.insert_snapshot(df_meta[["symbol", "timestamp", "datetime", "mark_price", "funding_rate", "open_interest", "volume_24h"]])
            return len(df_meta)
        except Exception as e:
            if "read-only mode" not in str(e).lower():
                print(f"[!] Warning: Gagal update snapshot pasar: {e}")
            return 0

    def sync_fast_update(self, min_vol_usd: float = 200_000.0, days_back: int = 3):
        """
        Quick 3-5s update: Updates recent candles (past 3 days) for active coins.
        Ensures 4H CHoCH and daily momentum are 100% up-to-date before screening.
        """
        self.sync_snapshot_only()

        meta_list = self.client.get_meta_and_contexts()
        df_meta = pd.DataFrame(meta_list)
        active_coins = df_meta[df_meta["volume_24h"] >= min_vol_usd]["symbol"].tolist()

        now_ms = int(time.time() * 1000)
        daily_start_ms = now_ms - (days_back * 24 * 60 * 60 * 1000)
        h4_start_ms = now_ms - (days_back * 24 * 60 * 60 * 1000)

        with tqdm(active_coins, desc="Fast Updating Candles", unit="coin") as pbar:
            for coin in pbar:
                pbar.set_postfix_str(coin)
                try:
                    df_daily = self.client.get_candles(coin=coin, interval="1d", start_time_ms=daily_start_ms, end_time_ms=now_ms)
                    if not df_daily.empty:
                        self.db.insert_candles("daily_candles", df_daily)

                    df_h4 = self.client.get_candles(coin=coin, interval="4h", start_time_ms=h4_start_ms, end_time_ms=now_ms)
                    if not df_h4.empty:
                        self.db.insert_candles("h4_candles", df_h4)
                    time.sleep(0.02)
                except Exception:
                    continue

    def sync_single_coin(self, coin: str, days_history: int = 120, h4_days: int = 45) -> bool:
        """
        Fast on-the-fly sync for a single target coin (takes ~0.3s):
        Downloads latest Daily and 4H candles and upserts them into DuckDB immediately.
        """
        now_ms = int(time.time() * 1000)
        daily_start_ms = now_ms - (days_history * 24 * 60 * 60 * 1000)
        h4_start_ms = now_ms - (h4_days * 24 * 60 * 60 * 1000)
        synced = False
        try:
            df_daily = self.client.get_candles(coin=coin, interval="1d", start_time_ms=daily_start_ms, end_time_ms=now_ms)
            if not df_daily.empty:
                self.db.insert_candles("daily_candles", df_daily)
                synced = True

            df_h4 = self.client.get_candles(coin=coin, interval="4h", start_time_ms=h4_start_ms, end_time_ms=now_ms)
            if not df_h4.empty:
                self.db.insert_candles("h4_candles", df_h4)
                synced = True
        except Exception as e:
            print(f"[!] Warning: Gagal live sync candle {coin}: {e}")
        return synced


