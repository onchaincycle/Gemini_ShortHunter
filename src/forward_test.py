"""
ShortHunter Forward Test Tracker & Journal:
Tracks, records, and evaluates live forward test positions (e.g. PUMP & ZRO)
monitoring MFE (Maximum Favorable Excursion), MAE (Maximum Adverse Excursion),
milestone execution, funding friction, and performance logs.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional
from src.trade_db import trade_db

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL_FILE = os.path.join(ROOT_DIR, "data", "forward_test_journal.json")

class ForwardTestManager:
    def __init__(self, journal_file: str = JOURNAL_FILE):
        self.journal_file = journal_file
        self._ensure_file()

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self.journal_file), exist_ok=True)
        if not os.path.exists(self.journal_file):
            with open(self.journal_file, "w") as f:
                json.dump({}, f, indent=2)

    def load_journal(self) -> Dict:
        try:
            # Primary: Load from SQLite WAL TradeDatabase
            journal = trade_db.load_journal()
            if journal:
                return journal
        except Exception:
            pass
        # Fallback to local JSON
        try:
            if os.path.exists(self.journal_file):
                with open(self.journal_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_journal(self, journal: Dict):
        # Primary: Persist each position record to SQLite
        try:
            for key, pos in journal.items():
                trade_db.save_journal_position(pos)
        except Exception as e:
            print(f"[!] Warning: Gagal simpan jurnal ke SQLite: {e}")

        # Mirror: Non-blocking atomic JSON backup
        try:
            tmp = self.journal_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(journal, f, indent=2)
            os.replace(tmp, self.journal_file)
        except Exception as e:
            print(f"[!] Gagal menyimpan mirror JSON jurnal: {e}")

    def purge_closed_trades(self) -> int:
        """
        Purges all historical CLOSED trades from SQLite and JSON,
        keeping ONLY active OPEN positions to start fresh from current live positions.
        """
        journal = self.load_journal()
        initial_count = len(journal)
        active_only = {k: v for k, v in journal.items() if v.get("status") == "OPEN"}
        purged = initial_count - len(active_only)

        # Update SQLite
        try:
            import sqlite3
            db_path = os.path.join(ROOT_DIR, "data", "shorthunter_trade.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM trade_journal WHERE status = 'CLOSED'")
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[!] Warning SQLite purge: {e}")

        # Update JSON mirror
        try:
            tmp = self.journal_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(active_only, f, indent=2)
            os.replace(tmp, self.journal_file)
        except Exception as e:
            print(f"[!] Gagal simpan json: {e}")

        return purged

    def update_position(self,
                        coin: str,
                        entry_px: float,
                        current_px: float,
                        size: float,
                        leverage: int = 2,
                        margin_used: float = 0.0,
                        unrealized_pnl: float = 0.0,
                        roe_pct: float = 0.0,
                        is_on_chain_sl: bool = True,
                        event_note: Optional[str] = None,
                        strategy: Optional[str] = None,
                        plan: Optional[Dict] = None):
        """
        Updates the forward test tracking state for a specific coin.
        `strategy` (SWING / DAYTRADE) memisahkan jurnal per stream; `plan` berisi
        level SL/TP aktual posisi sehingga milestone intraday (1R/2R) tercatat benar.
        """
        journal = self.load_journal()
        now_iso = datetime.now().isoformat()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if strategy is None:
            try:
                from src.position_tags import get_strategy
                strategy = get_strategy(coin)
            except Exception:
                strategy = "SWING"
        plan = plan or {}
        side = plan.get("side") or pos.get("side", "SHORT") if 'pos' in locals() else plan.get("side", "SHORT")

        # Favorable / adverse movements (drop_pct represents favorable gain for both sides)
        if side == "LONG":
            drop_pct = ((current_px - entry_px) / entry_px) * 100.0 if entry_px > 0 else 0.0
            adverse_pct = ((entry_px - current_px) / entry_px) * 100.0 if entry_px > 0 else 0.0
            init_sl = float(plan.get("initial_sl_px") or plan.get("sl_px") or entry_px * 0.90)
        else:
            drop_pct = ((entry_px - current_px) / entry_px) * 100.0 if entry_px > 0 else 0.0
            adverse_pct = ((current_px - entry_px) / entry_px) * 100.0 if entry_px > 0 else 0.0
            init_sl = float(plan.get("initial_sl_px") or plan.get("sl_px") or entry_px * 1.10)

        if coin not in journal:
            journal[coin] = {
                "coin": coin,
                "side": side,
                "strategy": strategy,
                "status": "OPEN",
                "entry_time": now_str,
                "entry_px": entry_px,
                "size": size,
                "leverage": leverage,
                "margin_used": margin_used,
                "initial_sl_px": round(init_sl, 6),
                "current_sl_px": round(init_sl, 6),
                "is_on_chain_sl": is_on_chain_sl,
                "max_favorable_drop_pct": max(0.0, drop_pct),  # Best profit reached
                "max_adverse_pump_pct": max(0.0, adverse_pct), # Worst drawdown reached
                "current_drop_pct": round(drop_pct, 2),
                "current_px": current_px,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "roe_pct": round(roe_pct, 2),
                "milestones": {
                    "trimmed_10pct": False,
                    "trimmed_time": None,
                    "tp_20pct": False,
                    "tp_time": None,
                    "runner_35pct": False,
                    "runner_time": None,
                    "sl_hit": False,
                    "sl_time": None
                },
                "snapshots": [],
                "event_logs": []
            }

        pos = journal[coin]
        pos.setdefault("side", side)
        pos.setdefault("strategy", strategy)
        m = pos.setdefault("milestones", {
            "trimmed_10pct": False,
            "trimmed_time": None,
            "tp_20pct": False,
            "tp_time": None,
            "runner_35pct": False,
            "runner_time": None,
            "sl_hit": False,
            "sl_time": None
        })
        events = pos.setdefault("event_logs", [])
        pos.setdefault("snapshots", [])

        if plan.get("current_sl_px"):
            pos["current_sl_px"] = plan["current_sl_px"]
        pos["current_px"] = current_px
        pos["current_drop_pct"] = round(drop_pct, 2)
        pos["unrealized_pnl"] = round(unrealized_pnl, 2)
        pos["roe_pct"] = round(roe_pct, 2)
        pos["margin_used"] = margin_used
        pos["is_on_chain_sl"] = is_on_chain_sl
        pos["last_updated"] = now_str

        # Update MFE and MAE
        if drop_pct > pos.get("max_favorable_drop_pct", 0.0):
            pos["max_favorable_drop_pct"] = round(drop_pct, 2)
        if adverse_pct > pos.get("max_adverse_pump_pct", 0.0):
            pos["max_adverse_pump_pct"] = round(adverse_pct, 2)

        # Milestone detection (berbeda per stream)
        if pos.get("strategy") == "DAYTRADE":
            sl_pct = float(plan.get("sl_pct") or 0.0)
            if sl_pct <= 0:
                if side == "LONG":
                    sl_pct = max(0.01, (entry_px - float(pos.get("initial_sl_px", entry_px))) / entry_px * 100.0)
                else:
                    sl_pct = max(0.01, (float(pos.get("initial_sl_px", entry_px)) - entry_px) / entry_px * 100.0)
            pos["risk_pct_1r"] = round(sl_pct, 3)
            pos["current_r"] = round(drop_pct / sl_pct, 2) if sl_pct > 0 else 0.0
            pos["mfe_r"] = round(pos.get("max_favorable_drop_pct", 0.0) / sl_pct, 2) if sl_pct > 0 else 0.0
            pos["mae_r"] = round(pos.get("max_adverse_pump_pct", 0.0) / sl_pct, 2) if sl_pct > 0 else 0.0
            tp1_pct = float(plan.get("tp1_pct") or sl_pct)
            tp2_pct = float(plan.get("tp2_pct") or sl_pct * 2)
            sign_str = "+" if side == "LONG" else "-"
            if drop_pct >= tp1_pct and not m.get("trimmed_10pct"):
                m["trimmed_10pct"] = True
                m["trimmed_time"] = now_str
                events.append(f"[{now_str}] ✂️ TP1 1R tercapai ({sign_str}{tp1_pct:.2f}%). 50% diamankan, SL ke BE.")
            if drop_pct >= tp2_pct and not m.get("tp_20pct"):
                m["tp_20pct"] = True
                m["tp_time"] = now_str
                events.append(f"[{now_str}] 🎯 TP2 2R tercapai ({sign_str}{tp2_pct:.2f}%). Posisi intraday selesai.")
        else:
            sign_str = "+" if side == "LONG" else "-"
            if drop_pct >= 10.0 and not m.get("trimmed_10pct"):
                m["trimmed_10pct"] = True
                m["trimmed_time"] = now_str
                events.append(f"[{now_str}] ✂️ Trimming 1 tercapai ({sign_str}10%). 50% Posisi diamankan.")

            if drop_pct >= 20.0 and not m.get("tp_20pct"):
                m["tp_20pct"] = True
                m["tp_time"] = now_str
                events.append(f"[{now_str}] 🎯 Target Utama 2 tercapai ({sign_str}20%). 30% Posisi diamankan.")

            if drop_pct >= 35.0 and not m.get("runner_35pct"):
                m["runner_35pct"] = True
                m["runner_time"] = now_str
                events.append(f"[{now_str}] 🚀 Runner 3 tercapai (-35% Deep Dump).")

        if event_note:
            events.append(f"[{now_str}] {event_note}")

        # Record hourly snapshot if 55+ minutes elapsed since last snapshot
        snapshots = pos.get("snapshots", [])
        record_snap = False
        if not snapshots:
            record_snap = True
        else:
            last_time = datetime.fromisoformat(snapshots[-1]["timestamp"])
            if (datetime.now() - last_time).total_seconds() >= 1800: # Every 30 mins
                record_snap = True

        if record_snap:
            snapshots.append({
                "timestamp": now_iso,
                "price": current_px,
                "drop_pct": round(drop_pct, 2),
                "pnl_usd": round(unrealized_pnl, 2),
                "roe_pct": round(roe_pct, 2)
            })
            # Keep max 50 snapshots
            pos["snapshots"] = snapshots[-50:]

        journal[coin] = pos
        self.save_journal(journal)

    def close_position_record(self, coin: str, exit_px: float, reason: str, pnl_usd: float, exit_time_str: Optional[str] = None):
        journal = self.load_journal()
        if coin in journal:
            now_str = exit_time_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pos = journal[coin]
            if pos.get("status") != "CLOSED":
                pos["status"] = "CLOSED"
                pos["exit_time"] = now_str
                pos["exit_px"] = exit_px
                pos["exit_reason"] = reason
                pos["final_pnl_usd"] = round(pnl_usd, 2)
                pos["realized_pnl"] = round(pnl_usd, 2)
                pos["pending_fill_reconcile"] = True  # akan dikoreksi dari user_fills (termasuk partial close & fee)
                pos["event_logs"].append(f"[{now_str}] 🏁 Posisi DITUTUP di ${exit_px}. Alasan: {reason}. PnL: ${pnl_usd:+.2f}")
                
                # Trigger Loop 1: Post-Mortem AI Review
                try:
                    from src.ai_engine import ai_advisor
                    review = ai_advisor.analyze_closed_trade(pos)
                    pos["ai_review"] = review
                    pos["event_logs"].append(f"[{now_str}] 🧠 AI Post-Mortem: Grade [{review.get('execution_grade')}] | {review.get('root_cause')} | {review.get('lessons_learned')}")
                except Exception:
                    pass

                journal[coin] = pos
                self.save_journal(journal)

    def sync_with_exchange(self, trader=None) -> Dict:
        """
        Synchronizes local forward_test_journal.json directly with live Hyperliquid
        open positions, open trigger orders (on-chain SL), and recent closed fills.
        """
        from src.trader import HyperliquidTrader, categorize_trigger_order
        trader = trader or HyperliquidTrader()
        acc = trader.get_account_state()
        if not acc.get("configured"):
            return self.load_journal()

        live_positions = {p["coin"]: p for p in acc.get("positions", []) if p.get("side") in ("SHORT", "LONG")}
        open_orders = []
        try:
            open_orders = trader.get_open_orders()
        except Exception:
            pass

        sl_orders_by_coin = {}
        for o in open_orders:
            if o.get("isTrigger") and o.get("coin"):
                tpx = float(o.get("triggerPx") or o.get("limitPx") or 0.0)
                if tpx > 0:
                    c_name = o.get("coin")
                    pos_info = live_positions.get(c_name, {})
                    entry_est = float(pos_info.get("entry_px", 0.0))
                    pos_side = pos_info.get("side", "SHORT")
                    if categorize_trigger_order(o, position_side=pos_side, entry_px=entry_est) == "STOP_LOSS":
                        if pos_side == "LONG":
                            sl_orders_by_coin[c_name] = max(sl_orders_by_coin.get(c_name, tpx), tpx)
                        else:
                            sl_orders_by_coin[c_name] = min(sl_orders_by_coin.get(c_name, tpx), tpx)

        all_mids = trader.get_all_mids()
        journal = self.load_journal()

        # 1. Sync all currently OPEN live positions (add new ones automatically)
        try:
            from src.position_tags import get_strategy
        except Exception:
            get_strategy = lambda c: "SWING"

        for coin, p in live_positions.items():
            entry_px = float(p["entry_px"])
            cur_px = float(all_mids.get(coin, entry_px))
            has_on_chain = coin in sl_orders_by_coin
            pos_side = p.get("side", "SHORT")
            # If coin was previously CLOSED in journal and reopened -> arsipkan record lama, mulai record baru
            journal = self.load_journal()
            if coin in journal and journal[coin].get("status") == "CLOSED":
                old = journal.pop(coin)
                archive_key = f"{coin}@{old.get('exit_time') or old.get('entry_time') or 'old'}"
                journal[archive_key] = old
                self.save_journal(journal)
            self.update_position(
                coin=coin,
                entry_px=entry_px,
                current_px=cur_px,
                size=float(p["size"]),
                leverage=int(p.get("leverage", 2)),
                margin_used=float(p.get("margin_used", 0.0)),
                unrealized_pnl=float(p.get("unrealized_pnl", 0.0)),
                roe_pct=float(p.get("roe_pct", 0.0)),
                is_on_chain_sl=has_on_chain,
                strategy=get_strategy(coin),
                plan={"side": pos_side}
            )
            if has_on_chain and sl_orders_by_coin[coin] > 0:
                journal = self.load_journal()
                journal[coin]["current_sl_px"] = sl_orders_by_coin[coin]
                journal[coin]["is_on_chain_sl"] = True
                self.save_journal(journal)

        # 2. Check Hyperliquid user_fills to reconcile any position that closed on-chain
        journal = self.load_journal()
        try:
            # Sesi forward test aktif dimulai sejak batch posisi live saat ini (2026-10-16 19:56:40)
            # Fills sebelum timestamp ini diabaikan agar trade riwayat lama yang sudah close tidak masuk kembali.
            SESSION_START_MS = 1792155400000
            fills = [f for f in raw_fills if f.get("time", 0) >= SESSION_START_MS]

            # Update accurate entry_time from Open fill if available
            for f in fills:
                f_coin = f.get("coin")
                f_dir = str(f.get("dir", ""))
                f_ts = f.get("time", 0)
                f_dt = datetime.fromtimestamp(f_ts / 1000.0).strftime("%Y-%m-%d %H:%M:%S") if f_ts else None
                if f_coin in journal and ("Open Short" in f_dir or "Open Long" in f_dir) and f_dt and journal[f_coin].get("status") == "OPEN":
                    if abs(float(f.get("px", 0)) - float(journal[f_coin].get("entry_px", 0))) / max(1e-9, float(journal[f_coin].get("entry_px", 1))) < 0.02:
                        journal[f_coin]["entry_time"] = f_dt

            # Discover any coin in user_fills that has a Close fill but is not in live_positions
            traded_coins = {f.get("coin") for f in fills if f.get("coin")}
            for coin in traded_coins:
                if coin not in live_positions and (coin not in journal or journal[coin].get("status") != "CLOSED" or journal[coin].get("pending_fill_reconcile")):
                    coin_fills = sorted([f for f in fills if f.get("coin") == coin], key=lambda x: x.get("time", 0))
                    open_fills = [f for f in coin_fills if ("Open Short" in str(f.get("dir", "")) or "Open Long" in str(f.get("dir", "")))]
                    if not open_fills:
                        continue
                    last_open = open_fills[-1]
                    is_long = "Open Long" in str(last_open.get("dir", ""))
                    open_dir_str = "Open Long" if is_long else "Open Short"
                    close_dir_str = "Close Long" if is_long else "Close Short"

                    # Siklus trade terakhir: semua fill sejak open pertama setelah close sebelumnya
                    last_open_ts = last_open.get("time", 0)
                    cycle_start = last_open_ts
                    for f in reversed(coin_fills):
                        if f.get("time", 0) > last_open_ts:
                            continue
                        if open_dir_str in str(f.get("dir", "")):
                            cycle_start = f.get("time", 0)
                        else:
                            break
                    cycle = [f for f in coin_fills if f.get("time", 0) >= cycle_start]
                    cyc_open = [f for f in cycle if open_dir_str in str(f.get("dir", ""))]
                    cyc_close = [f for f in cycle if close_dir_str in str(f.get("dir", ""))]
                    if not cyc_close:
                        continue

                    open_sz = sum(float(f.get("sz", 0.0)) for f in cyc_open)
                    entry_px = (sum(float(f.get("px", 0.0)) * float(f.get("sz", 0.0)) for f in cyc_open) / open_sz) if open_sz > 0 else float(cyc_close[-1].get("px", 0.0))
                    close_sz = sum(float(f.get("sz", 0.0)) for f in cyc_close)
                    exit_px = (sum(float(f.get("px", 0.0)) * float(f.get("sz", 0.0)) for f in cyc_close) / close_sz) if close_sz > 0 else entry_px
                    closed_pnl = sum(float(f.get("closedPnl", 0.0)) for f in cyc_close)
                    fee_usd = sum(float(f.get("fee", 0.0)) for f in cycle)
                    exit_dt = datetime.fromtimestamp(cyc_close[-1].get("time", 0) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
                    entry_dt = datetime.fromtimestamp(cyc_open[0].get("time", 0) / 1000.0).strftime("%Y-%m-%d %H:%M:%S") if cyc_open else None
                    if is_long:
                        move_pct = ((exit_px - entry_px) / entry_px) * 100.0 if entry_px > 0 else 0.0
                    else:
                        move_pct = ((entry_px - exit_px) / entry_px) * 100.0 if entry_px > 0 else 0.0

                    prev = journal.get(coin, {})
                    strategy = prev.get("strategy") or get_strategy(coin)
                    sl_loss_thresh = -2.5 if strategy == "DAYTRADE" else -8.0
                    if prev.get("exit_reason"):
                        reason = prev["exit_reason"]
                    elif move_pct <= sl_loss_thresh:
                        reason = "INTRADAY_SL_HIT" if strategy == "DAYTRADE" else "STOP_LOSS_10PCT"
                    else:
                        reason = "TAKE_PROFIT" if closed_pnl > 0 else "MANUAL_OR_SL_CLOSE"

                    rec = dict(prev)
                    rec.update({
                        "coin": coin,
                        "strategy": strategy,
                        "status": "CLOSED",
                        "pending_fill_reconcile": False,
                        "entry_time": entry_dt or prev.get("entry_time"),
                        "entry_px": entry_px,
                        "size": open_sz,
                        "leverage": int(prev.get("leverage", 2)),
                        "margin_used": prev.get("margin_used") or round(entry_px * open_sz / 2, 2),
                        "exit_time": exit_dt,
                        "exit_px": exit_px,
                        "exit_reason": reason,
                        "final_pnl_usd": round(closed_pnl - fee_usd, 2),
                        "gross_pnl_usd": round(closed_pnl, 2),
                        "fee_usd": round(fee_usd, 4),
                        "realized_move_pct": round(move_pct, 2),
                        "realized_roe_pct": round(move_pct * int(prev.get("leverage", 2)), 2),
                        "snapshots": prev.get("snapshots", []),
                        "event_logs": prev.get("event_logs", [])
                    })
                    if strategy == "DAYTRADE":
                        r1 = float(prev.get("risk_pct_1r") or 0.0)
                        if r1 > 0:
                            rec["realized_r"] = round(move_pct / r1, 2)
                    try:
                        hrs = (datetime.strptime(exit_dt, "%Y-%m-%d %H:%M:%S") - datetime.strptime(rec["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
                        rec["hold_hours"] = round(hrs, 2)
                    except Exception:
                        pass

                    if not rec.get("ai_review"):
                        try:
                            from src.ai_engine import ai_advisor
                            rec["ai_review"] = ai_advisor.analyze_closed_trade(rec)
                        except Exception:
                            pass

                    journal[coin] = rec

            self.save_journal(journal)
        except Exception:
            pass

        return self.load_journal()

    # ------------------------------------------------------------------
    # PER-STREAM ANALYTICS
    # ------------------------------------------------------------------
    @staticmethod
    def _strategy_of(p: Dict) -> str:
        return "DAYTRADE" if p.get("strategy") == "DAYTRADE" else "SWING"

    def stream_stats(self, journal: Optional[Dict] = None) -> Dict[str, Dict]:
        """Returns scorecard per stream: trades, win rate, PF, expectancy, avg hold, avg R."""
        journal = journal if journal is not None else self.load_journal()
        out = {}
        for key in ("SWING", "DAYTRADE"):
            items = [p for p in journal.values() if self._strategy_of(p) == key]
            closed = [p for p in items if p.get("status") == "CLOSED"]
            opened = [p for p in items if p.get("status") == "OPEN"]
            pnls = [float(p.get("final_pnl_usd", 0.0)) for p in closed]
            wins = [x for x in pnls if x > 0]
            losses = [x for x in pnls if x <= 0]
            gross_w, gross_l = sum(wins), abs(sum(losses))
            holds = [float(p["hold_hours"]) for p in closed if p.get("hold_hours") is not None]
            rs = [float(p["realized_r"]) for p in closed if p.get("realized_r") is not None]
            out[key] = {
                "open": len(opened),
                "closed": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
                "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else (999.0 if gross_w > 0 else 0.0),
                "realized_usd": round(sum(pnls), 2),
                "expectancy_usd": round(sum(pnls) / len(closed), 2) if closed else 0.0,
                "floating_usd": round(sum(float(p.get("unrealized_pnl", 0.0)) for p in opened), 2),
                "avg_hold_hours": round(sum(holds) / len(holds), 1) if holds else None,
                "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            }
        return out

    def count_losses_today(self, strategy: str = "DAYTRADE") -> int:
        """Jumlah trade rugi hari ini untuk satu stream (dipakai circuit breaker intraday)."""
        today = datetime.now().strftime("%Y-%m-%d")
        n = 0
        for p in self.load_journal().values():
            if self._strategy_of(p) != strategy or p.get("status") != "CLOSED":
                continue
            if str(p.get("exit_time", "")).startswith(today) and float(p.get("final_pnl_usd", 0.0)) < 0:
                n += 1
        return n

    def render_summary_dashboard(self, stream_filter: Optional[str] = None) -> str:
        """
        Generates a terminal-formatted dashboard report for Forward Testing,
        clearly separating ACTIVE (OPEN) positions and CLOSED positions history.
        `stream_filter`: None (semua), "SWING", atau "DAYTRADE".
        """
        journal = self.sync_with_exchange()
        if not journal:
            return "Belum ada posisi forward test yang tercatat."

        stats = self.stream_stats(journal)
        if stream_filter:
            journal = {k: v for k, v in journal.items() if self._strategy_of(v) == stream_filter}

        open_list = [p for p in journal.values() if p.get("status") == "OPEN"]
        closed_list = [p for p in journal.values() if p.get("status") == "CLOSED"]

        total_realized_usd = sum(float(p.get("final_pnl_usd", 0.0)) for p in closed_list)
        total_floating_usd = sum(float(p.get("unrealized_pnl", 0.0)) for p in open_list)
        wins_closed = sum(1 for p in closed_list if float(p.get("final_pnl_usd", 0.0)) > 0)
        loss_closed = sum(1 for p in closed_list if float(p.get("final_pnl_usd", 0.0)) <= 0)

        badge = {"SWING": "🌊 SWING", "DAYTRADE": "⚡ INTRADAY"}
        title_sfx = f" — {badge[stream_filter]}" if stream_filter else ""

        lines = []
        lines.append("=" * 72)
        lines.append(f"       📓 BUKU JURNAL FORWARD TEST & RIWAYAT POSISI CLOSE{title_sfx}")
        lines.append("=" * 72)
        lines.append(f"Pembaruan Terakhir : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Ringkasan Posisi   : 🟢 {len(open_list)} Aktif (OPEN)  |  🏁 {len(closed_list)} Selesai (CLOSED: {wins_closed} Win / {loss_closed} Loss)")
        lines.append(f"Total Realized PnL : ${total_realized_usd:+.2f} USDC  |  Floating PnL Aktif: ${total_floating_usd:+.2f} USDC")
        lines.append("-" * 72)

        # SCORECARD PER STREAM
        lines.append("📊 SCORECARD PER STREAM (dievaluasi terpisah, jangan dicampur):")
        lines.append(f"  {'Stream':<12}| {'Open':>4} | {'Closed':>6} | {'WinRate':>7} | {'PF':>6} | {'Realized':>9} | {'Exp/Trade':>9} | {'AvgHold':>7} | {'AvgR':>5}")
        for key in ("SWING", "DAYTRADE"):
            s = stats[key]
            hold = f"{s['avg_hold_hours']}j" if s["avg_hold_hours"] is not None else "-"
            avg_r = f"{s['avg_r']:+.2f}" if s["avg_r"] is not None else "-"
            pf = "∞" if s["profit_factor"] >= 999 else f"{s['profit_factor']:.2f}"
            lines.append(f"  {badge[key]:<11}| {s['open']:>4} | {s['closed']:>6} | {s['win_rate']:>6.1f}% | {pf:>6} | ${s['realized_usd']:>+8.2f} | ${s['expectancy_usd']:>+8.2f} | {hold:>7} | {avg_r:>5}")
        lines.append("  Target acuan backtest -> Swing: WR ≥ 60%, hold ~1-3 hari | Intraday: WR ≥ 50%, PF ≥ 1.2, hold ≤ 6j")
        lines.append("-" * 72)

        # SECTION 1: OPEN POSITIONS
        lines.append(f"\n🟢 DAFTAR POSISI FORWARD TEST AKTIF ({len(open_list)}):")
        if not open_list:
            lines.append("  (Tidak ada posisi terbuka saat ini)")
        for p in sorted(open_list, key=self._strategy_of):
            coin = p.get("coin")
            strat = self._strategy_of(p)
            entry = float(p.get("entry_px", 0.0))
            cur = float(p.get("current_px", 0.0))
            drop = float(p.get("current_drop_pct", 0.0))
            mfe = float(p.get("max_favorable_drop_pct", 0.0))
            mae = float(p.get("max_adverse_pump_pct", 0.0))
            pnl = float(p.get("unrealized_pnl", 0.0))
            roe = float(p.get("roe_pct", 0.0))
            margin = float(p.get("margin_used", 0.0))
            lev = int(p.get("leverage", 2))
            on_chain = "🟢 RESTING ON-CHAIN" if p.get("is_on_chain_sl") else "🟡 LOKAL"
            pnl_badge = f"+${pnl:.2f} (+{roe:.2f}%)" if pnl >= 0 else f"-${abs(pnl):.2f} ({roe:.2f}%)"

            lines.append(f"\n  ┌─ 🪙 [{coin}] {badge[strat]} | SHORT {lev}x | Status: 🟢 OPEN | Margin: ${margin:.2f} USDC")
            lines.append(f"  │  • Waktu Entry         : {p.get('entry_time', '-')}")
            lines.append(f"  │  • Harga Entry ➔ Skrg  : ${entry:.5f} ➔ ${cur:.5f} ({drop:+.2f}%)")
            lines.append(f"  │  • Floating PnL        : {pnl_badge}")
            lines.append(f"  │  • 🛡️ Server Stop Loss : ${float(p.get('current_sl_px', entry*1.10)):.5f} [{on_chain}]")
            m = p.get("milestones", {})
            t1 = "✅ SELESAI" if m.get("trimmed_10pct") else "⏳ Menunggu"
            t2 = "✅ SELESAI" if m.get("tp_20pct") else "⏳ Menunggu"
            if strat == "DAYTRADE":
                lines.append(f"  │  • 📈 MFE / MAE (R)    : +{p.get('mfe_r', 0):.2f}R / -{p.get('mae_r', 0):.2f}R  (1R = {p.get('risk_pct_1r', 0):.2f}%) | Skrg: {p.get('current_r', 0):+.2f}R")
                lines.append(f"  │  • 🎯 Progres Target   : TP1 1R: {t1} | TP2 2R: {t2}")
            else:
                r3 = "✅ SELESAI" if m.get("runner_35pct") else "⏳ Menunggu"
                lines.append(f"  │  • 📈 Rekor MFE / MAE  : Profit Terdalam -{mfe:.2f}% | Drawdown Terburuk +{mae:.2f}%")
                lines.append(f"  │  • 🎯 Progres Target   : Trim 1: {t1} | Target 2: {t2} | Runner: {r3}")
            lines.append("  └" + "─" * 68)

        # SECTION 2: CLOSED POSITIONS HISTORY
        lines.append(f"\n🏁 RIWAYAT POSISI YANG SUDAH CLOSE / SELESAI ({len(closed_list)}):")
        if not closed_list:
            lines.append("  (Belum ada posisi yang ditutup)")
        for p in sorted(closed_list, key=lambda x: str(x.get("exit_time", ""))):
            coin = p.get("coin")
            strat = self._strategy_of(p)
            entry = float(p.get("entry_px", 0.0))
            exit_px = float(p.get("exit_px", p.get("current_px", 0.0)))
            move_pct = float(p.get("realized_move_pct", ((entry - exit_px) / entry * 100.0) if entry > 0 else 0.0))
            lev = int(p.get("leverage", 2))
            roe_real = float(p.get("realized_roe_pct", move_pct * lev))
            final_pnl = float(p.get("final_pnl_usd", 0.0))
            fee_usd = float(p.get("fee_usd", 0.0))
            reason = p.get("exit_reason", "CLOSED")
            mfe = float(p.get("max_favorable_drop_pct", 0.0))
            mae = float(p.get("max_adverse_pump_pct", 0.0))
            res_badge = "🏆 WIN / PROFIT" if final_pnl > 0 else "🛑 LOSS / CUT LOSS"

            lines.append(f"\n  ┌─ 🏁 [{coin}] {badge[strat]} | SHORT {lev}x | Status: ⚪ CLOSED ({res_badge})")
            lines.append(f"  │  • Waktu Buka ➔ Tutup  : {p.get('entry_time', '-')}  ➔  {p.get('exit_time', '-')}" + (f"  ({p['hold_hours']}j)" if p.get("hold_hours") is not None else ""))
            lines.append(f"  │  • Harga Entry ➔ Exit  : ${entry:.5f}  ➔  ${exit_px:.5f} (Gerak: {move_pct:+.2f}%)")
            lines.append(f"  │  • Alasan Penutupan    : {reason}")
            r_str = f" | {p['realized_r']:+.2f}R" if p.get("realized_r") is not None else ""
            lines.append(f"  │  • 💰 Realized PnL     : ${final_pnl:+.2f} USDC (ROE: {roe_real:+.2f}% | Fee: ${fee_usd:.4f}{r_str})")
            lines.append(f"  │  • 📊 Rekor MFE / MAE  : MFE Drop -{mfe:.2f}% | MAE Pump +{mae:.2f}%")
            ai_rev = p.get("ai_review") or {}
            if ai_rev:
                grade = ai_rev.get("execution_grade", "N/A")
                cause = ai_rev.get("root_cause", "N/A")
                lesson = ai_rev.get("lessons_learned", "")
                lines.append(f"  │  • 🧠 AI Audit Grade   : [{grade}] {cause} — {lesson}")
            logs = p.get("event_logs", [])
            if logs:
                lines.append(f"  │  • 📝 Log Terakhir     : {logs[-1]}")
            lines.append("  └" + "─" * 68)

        lines.append("\n" + "=" * 72)
        return "\n".join(lines)

