#!/usr/bin/env python3
"""Shadow Book (Buku Bayangan) — Jalur C1 ROADMAP_DATA_VPS.md.

Tujuan:
  Mensimulasikan setiap sinyal v3 'READY' (dan watchlist skor >= 70)
  secara maju (*paper simulation*) dengan aturan exit yang sama persis dengan mesin live.
  Menghasilkan ratusan 'trade virtual' per bulan tanpa merisikokan saldo akun Hyperliquid,
  sebagai bahan koreksi forward test, evaluasi filter Jalur B, dan deteksi dini edge drift.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("ShortHunter.ShadowBook")


def init_shadow_table(conn: sqlite3.Connection) -> None:
    """Buat tabel shadow_trades jika belum ada."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                score REAL NOT NULL,
                status TEXT NOT NULL,  -- 'OPEN' or 'CLOSED'
                entry_time TEXT NOT NULL,
                entry_px REAL NOT NULL,
                sl_px REAL NOT NULL,
                tp1_px REAL,
                tp2_px REAL,
                risk_pct REAL NOT NULL,
                exit_time TEXT,
                exit_px REAL,
                exit_reason TEXT,
                realized_r REAL,
                peak_r REAL DEFAULT 0.0,
                hold_bars INTEGER DEFAULT 0,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


class ShadowBook:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            root = Path(__file__).resolve().parent.parent
            self.db_path = str(root / "data" / "shorthunter_trade.db")
        else:
            self.db_path = db_path
        
        with sqlite3.connect(self.db_path) as conn:
            init_shadow_table(conn)

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def register_signal(self, sig: Dict[str, Any]) -> Optional[int]:
        """Buka shadow trade jika memenuhi syarat (skor >= 70 atau ready=True)."""
        sym = sig.get("symbol", sig.get("coin", ""))
        strat = sig.get("strategy", "INTRADAY")
        direction = sig.get("direction", "LONG" if "LONG" in strat else "SHORT")
        score = float(sig.get("score", 0))
        ready = bool(sig.get("ready", False))

        if not sym or (score < 70 and not ready):
            return None

        entry_px = float(sig.get("current_px", sig.get("entry_price", sig.get("signal_close", 0.0))))
        sl_px = float(sig.get("sl_px", sig.get("sl_price", 0.0)))
        tp1_px = float(sig.get("tp1_px", sig.get("tp1_price", 0.0)))
        tp2_px = float(sig.get("tp2_px", sig.get("tp2_price", 0.0)))
        risk_pct = float(sig.get("sl_pct", sig.get("risk_pct", 2.5)))

        if entry_px <= 0 or sl_px <= 0:
            return None

        now_str = datetime.now(timezone.utc).isoformat()
        sig_id = sig.get("signal_id", f"shadow_{sym}_{int(time.time()*1000)}")

        with self.get_connection() as conn:
            c = conn.cursor()
            # Hindari duplikat jika simbol sudah memiliki posisi OPEN di arah yang sama
            existing = c.execute("""
                SELECT id FROM shadow_trades
                WHERE symbol = ? AND status = 'OPEN' AND direction = ?
            """, (sym, direction)).fetchone()
            if existing:
                return None

            c.execute("""
                INSERT INTO shadow_trades (
                    signal_id, symbol, strategy, direction, score, status,
                    entry_time, entry_px, sl_px, tp1_px, tp2_px, risk_pct,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sig_id, sym, strat, direction, score,
                now_str, entry_px, sl_px, tp1_px, tp2_px, risk_pct,
                json.dumps(sig), now_str
            ))
            trade_id = c.lastrowid
            conn.commit()
            logger.info(f"[SHADOW BOOK] Trade #{trade_id} DIBUKA: {sym} {direction} @ {entry_px} (SL: {sl_px})")
            return trade_id

    def update_with_candle(
        self,
        trade_id: int,
        candle: Dict[str, float],
        candle_ts: str,
        early_be_r: float = 0.50,
    ) -> Optional[Dict[str, Any]]:
        """Update satu shadow trade dengan candle 15m terkini."""
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        open_px = float(candle["open"])

        with self.get_connection() as conn:
            c = conn.cursor()
            row = c.execute("SELECT * FROM shadow_trades WHERE id = ? AND status = 'OPEN'", (trade_id,)).fetchone()
            if not row:
                return None

            sgn = 1 if row["direction"] == "LONG" else -1
            entry = row["entry_px"]
            sl = row["sl_px"]
            tp1 = row["tp1_px"] or (entry * (1 + sgn * 0.025))
            tp2 = row["tp2_px"] or (entry * (1 + sgn * 0.050))
            risk_unit = abs(entry - sl)
            if risk_unit <= 0:
                return None

            hold_bars = (row["hold_bars"] or 0) + 1
            cur_peak_r = row["peak_r"] or 0.0

            fav = high if sgn > 0 else low
            adverse = low if sgn > 0 else high

            peak_move = sgn * (fav - entry)
            peak_r = max(cur_peak_r, peak_move / risk_unit if risk_unit > 0 else 0.0)

            # Evaluasi Exit:
            # 1. SL Hit (Konservatif: SL dicek lebih dulu)
            sl_hit = (adverse <= sl) if sgn > 0 else (adverse >= sl)
            if sl_hit:
                realized_r = (sl - entry) / risk_unit if sgn > 0 else (entry - sl) / risk_unit
                # Bila sudah pernah menyentuh threshold BE (early_be_r), SL dijamin impas
                if peak_r >= early_be_r:
                    realized_r = 0.0
                    reason = "BE_EXIT"
                else:
                    realized_r = -1.0
                    reason = "SL_HIT"

                c.execute("""
                    UPDATE shadow_trades
                    SET status = 'CLOSED', exit_time = ?, exit_px = ?, exit_reason = ?,
                        realized_r = ?, peak_r = ?, hold_bars = ?
                    WHERE id = ?
                """, (candle_ts, sl, reason, realized_r, peak_r, hold_bars, trade_id))
                conn.commit()
                return {"id": trade_id, "status": "CLOSED", "reason": reason, "realized_r": realized_r}

            # 2. TP2 Hit (Target penuh 2R)
            tp2_hit = (fav >= tp2) if sgn > 0 else (fav <= tp2)
            if tp2_hit:
                realized_r = 2.0
                reason = "TP2_HIT"
                c.execute("""
                    UPDATE shadow_trades
                    SET status = 'CLOSED', exit_time = ?, exit_px = ?, exit_reason = ?,
                        realized_r = ?, peak_r = ?, hold_bars = ?
                    WHERE id = ?
                """, (candle_ts, tp2, reason, realized_r, peak_r, hold_bars, trade_id))
                conn.commit()
                return {"id": trade_id, "status": "CLOSED", "reason": reason, "realized_r": realized_r}

            # 3. Time stop 6 jam (24 bar 15m)
            if hold_bars >= 24:
                realized_r = sgn * (close - entry) / risk_unit
                reason = "TIME_STOP_6H"
                c.execute("""
                    UPDATE shadow_trades
                    SET status = 'CLOSED', exit_time = ?, exit_px = ?, exit_reason = ?,
                        realized_r = ?, peak_r = ?, hold_bars = ?
                    WHERE id = ?
                """, (candle_ts, close, reason, realized_r, peak_r, hold_bars, trade_id))
                conn.commit()
                return {"id": trade_id, "status": "CLOSED", "reason": reason, "realized_r": realized_r}

            # Update state berjalan
            c.execute("""
                UPDATE shadow_trades
                SET peak_r = ?, hold_bars = ?
                WHERE id = ?
            """, (peak_r, hold_bars, trade_id))
            conn.commit()
            return {"id": trade_id, "status": "OPEN", "peak_r": peak_r, "hold_bars": hold_bars}

    def get_summary_stats(self) -> Dict[str, Any]:
        """Statistik performa buku bayangan."""
        with self.get_connection() as conn:
            c = conn.cursor()
            total = c.execute("SELECT count(*) FROM shadow_trades").fetchone()[0]
            closed = c.execute("SELECT count(*) FROM shadow_trades WHERE status = 'CLOSED'").fetchone()[0]
            open_cnt = c.execute("SELECT count(*) FROM shadow_trades WHERE status = 'OPEN'").fetchone()[0]
            
            trades = c.execute("""
                SELECT realized_r, exit_reason FROM shadow_trades WHERE status = 'CLOSED'
            """).fetchall()
            
            if not trades:
                return {"total": total, "closed": closed, "open": open_cnt, "avg_r": 0.0, "pf": 0.0}

            r_list = [r[0] for r in trades if r[0] is not None]
            wins = [r for r in r_list if r > 0]
            losses = [r for r in r_list if r < 0]
            
            avg_r = float(np.mean(r_list)) if r_list else 0.0
            win_rate = (len(wins) / len(r_list) * 100) if r_list else 0.0
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

            return {
                "total_shadow_trades": total,
                "closed_trades": closed,
                "open_trades": open_cnt,
                "win_rate_pct": round(win_rate, 1),
                "avg_r": round(avg_r, 4),
                "profit_factor": round(pf, 3),
                "net_r": round(sum(r_list), 2),
            }


shadow_book = ShadowBook()

