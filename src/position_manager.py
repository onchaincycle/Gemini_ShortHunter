"""
ShortHunter Auto-Pilot Position Manager (Dual-Stream):
Monitors active Hyperliquid short positions and manages EACH position according to
the strategy it was opened with (tag di src/position_tags.py), bukan profil global.

Stream 1 — 🌊 SWING (FIXED_PCT, multi-hari):
  * Server SL +10% on-chain.
  * Trimming 1: Close 50% at -10% drop & move server SL to Breakeven.
  * Target 2  : Close 30% at -20% drop & trail server SL to +10% locked profit.
  * Runner 3  : Close remaining 20% at -35% deep dump.
  * Time-stop 72 jam.

Stream 2 — ⚡ INTRADAY / DAYTRADE (R_MULTIPLE, 15m):
  * Server SL teknis (pucuk jarum 15m + buffer, 2.8%-5.5%) on-chain.
  * TP1 = 1.0R : Close 50% & geser SL ke BE (+buffer fee).
  * TP2 = 2.0R : Close sisa posisi.
  * Time-stop 6 jam (tanpa risiko overnight).

Layer keamanan:
  - Layer 1 (Server-side Trigger): Native Stop Market order di Hyperliquid (aman saat laptop mati).
  - Layer 2 (Background Daemon / Live Guard): Eksekusi trimming/TP/time-stop.
"""

import os
import sys
import json
import time
import signal
import subprocess
import unicodedata
import select
try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None
from datetime import datetime
from typing import Dict, List, Optional, Tuple
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.trader import HyperliquidTrader, categorize_trigger_order
from src.forward_test import ForwardTestManager
from src.profiles import get_profile
from src import position_tags
from src.trade_db import trade_db

STATE_FILE = os.path.join(ROOT_DIR, "data", "positions_lifecycle.json")
PID_FILE = os.path.join(ROOT_DIR, "data", "autopilot.pid")
LOG_FILE = os.path.join(ROOT_DIR, "logs", "autopilot.log")


class PositionManager:
    def __init__(self, trader: Optional[HyperliquidTrader] = None):
        self.trader = trader or HyperliquidTrader()
        self.state_file = STATE_FILE
        self.ft = ForwardTestManager()
        self._ensure_dirs()
        self._btc_history: List[Tuple[float, float]] = []  # [(timestamp, btc_price), ...]
        self._last_macro_shock_ts: float = 0.0
        self._last_macro_pulse_ts: float = 0.0

    def _ensure_dirs(self):
        os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
        os.makedirs(os.path.join(ROOT_DIR, "logs"), exist_ok=True)
        if not os.path.exists(self.state_file):
            with open(self.state_file, "w") as f:
                json.dump({}, f, indent=2)

    def load_lifecycle_state(self) -> Dict:
        try:
            # Primary: Load from SQLite WAL TradeDatabase
            state = trade_db.load_lifecycle_state()
            if state:
                return state
        except Exception:
            pass
        # Fallback to local JSON
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_lifecycle_state(self, state: Dict):
        # Primary: Persist to SQLite WAL TradeDatabase
        try:
            trade_db.save_lifecycle_state(state)
        except Exception as e:
            print(f"[!] Warning: Gagal simpan lifecycle ke SQLite: {e}")

        # Mirror: Non-blocking atomic JSON backup
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception as e:
            print(f"[!] Gagal menyimpan mirror JSON lifecycle: {e}")

    def log_event(self, msg: str, strategy: Optional[str] = None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tag = f"[{strategy}] " if strategy else ""
        line = f"[{timestamp}] {tag}{msg}\n"
        try:
            with open(LOG_FILE, "a") as f:
                f.write(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SYNC
    # ------------------------------------------------------------------
    def _new_lifecycle_entry(self, coin: str, p: Dict) -> Dict:
        """Builds lifecycle state for a newly detected position using its strategy tag."""
        entry = float(p["entry_px"])
        sz = float(p["size"])
        tag = position_tags.get_tag(coin) or {}
        strategy = position_tags.get_strategy(coin)
        tag_plan = tag.get("plan", {}) or {}
        side = tag_plan.get("side") or tag.get("meta", {}).get("side") or p.get("side", "SHORT")

        # Re-base plan to the ACTUAL fill price.
        # - INTRADAY: SL adalah level teknikal absolut (pucuk jarum 15m untuk short, dasar jarum untuk long).
        # - SWING   : SL adalah persentase tetap dari entry -> hitung ulang dari harga fill.
        sl_cand = float(tag_plan.get("sl_px") or 0.0)
        is_sl_valid = (sl_cand > entry) if side == "SHORT" else (sl_cand < entry and sl_cand > 0)
        if strategy == "DAYTRADE" and sl_cand and is_sl_valid:
            plan = position_tags.build_plan(strategy, entry, sl_px=sl_cand, side=side)
        else:
            plan = position_tags.build_plan(strategy, entry, side=side)

        sl_px = self.trader.round_price(coin, plan["sl_px"])
        if not tag:
            # Posisi tanpa tag (mis. dibuka manual di web) -> daftarkan sebagai SWING (paling aman)
            position_tags.tag_position(coin, strategy, plan, meta={"source": "AUTO_DETECTED_UNTAGGED", "side": side})
            self.log_event(f"ℹ️ {coin} ({side}) terdeteksi tanpa tag strategi -> otomatis didaftarkan sebagai SWING.", strategy)
        else:
            position_tags.tag_position(coin, strategy, plan, meta=tag.get("meta", {}))

        return {
            "coin": coin,
            "side": side,
            "strategy": strategy,
            "initial_sz": sz,
            "remaining_sz": sz,
            "entry_px": entry,
            "initial_sl_px": sl_px,
            "current_sl_px": sl_px,
            "tp1_px": plan["tp1_px"],
            "tp2_px": plan["tp2_px"],
            "runner_px": plan.get("runner_px"),
            "sl_pct": plan["sl_pct"],
            "tp1_pct": plan["tp1_pct"],
            "tp2_pct": plan["tp2_pct"],
            "max_hold_hours": plan["max_hold_hours"],
            "trimmed_10pct": False,   # = TP1 / Trimming 1 selesai (nama lama dipertahankan)
            "tp_20pct": False,        # = TP2 selesai
            "runner_35pct": False,
            "native_sl_active": False,
            "created_at": datetime.now().isoformat(),
            "last_check": datetime.now().isoformat()
        }

    def _backfill_strategy(self, coin: str, state: Dict, side: str = "SHORT") -> Dict:
        """Upgrades legacy lifecycle entries & reconciles strategy if position_tags was updated right after execution."""
        side = state.get("side", side)
        state["side"] = side
        tag = position_tags.get_tag(coin) or {}
        tag_strat = position_tags.get_strategy(coin) if tag else None
        tag_source = (tag.get("meta") or {}).get("source", "")

        # Reconcile if position_tags has an explicit strategy (not AUTO_DETECTED_UNTAGGED) that differs from state,
        # OR if DAYTRADE state accidentally has a Swing-sized tp2_pct (> 9.0%)
        needs_reconcile = (
            not state.get("strategy")
            or not state.get("tp1_px")
            or (tag_strat and tag_source != "AUTO_DETECTED_UNTAGGED" and tag_strat != state.get("strategy"))
            or (state.get("strategy") == "DAYTRADE" and float(state.get("tp2_pct") or 0) > 9.0)
        )
        if not needs_reconcile:
            return state

        strategy = tag_strat or state.get("strategy") or "SWING"
        entry = float(state["entry_px"])
        tag_sl = float((tag.get("plan") or {}).get("sl_px") or state.get("initial_sl_px") or 0) or None
        plan = position_tags.build_plan(strategy, entry, sl_px=tag_sl, side=side)
        if not tag:
            position_tags.tag_position(coin, strategy, plan, meta={"source": "LEGACY_BACKFILL", "side": side})
        state.update({
            "strategy": strategy,
            "side": side,
            "sl_pct": plan["sl_pct"],
            "tp1_px": plan["tp1_px"],
            "tp2_px": plan["tp2_px"],
            "runner_px": plan.get("runner_px"),
            "tp1_pct": plan["tp1_pct"],
            "tp2_pct": plan["tp2_pct"],
            "max_hold_hours": plan["max_hold_hours"],
        })
        if not state.get("trimmed_10pct") and not state.get("early_be_shield") and not state.get("half_risk_shield"):
            sl_rounded = self.trader.round_price(coin, plan["sl_px"])
            state["initial_sl_px"] = sl_rounded
            state["current_sl_px"] = sl_rounded
        return state

    def sync_active_positions(self) -> Dict:
        """
        Synchronizes live Hyperliquid positions with local lifecycle state
        and verifies that native server-side trigger SL & TP orders exist,
        auto-deduplicating SL orders and syncing size after on-chain TP fills.
        """
        acc = self.trader.get_account_state()
        lifecycle = self.load_lifecycle_state()

        # SAFETY: jika API error / tidak ada data akun, JANGAN prune posisi
        # (sebelumnya: error jaringan -> positions=[] -> semua SL on-chain ikut di-cancel!)
        if acc.get("error") or not acc.get("configured", True):
            self.log_event(f"⚠️ Gagal membaca akun ({acc.get('error', 'not configured')}). Sinkronisasi dilewati demi keamanan.")
            return lifecycle

        live_positions = {p["coin"]: p for p in acc.get("positions", []) if p.get("side") in ("SHORT", "LONG")}

        # Check existing open orders on Hyperliquid (force_refresh=True to avoid stale cache right after execution)
        open_orders = []
        try:
            open_orders = self.trader.get_open_orders(force_refresh=True)
        except Exception:
            pass

        sl_orders_by_coin: Dict[str, List[Dict]] = {}
        tp_orders_by_coin: Dict[str, List[Dict]] = {}
        for o in open_orders:
            if not (o.get("isTrigger") and o.get("coin")):
                continue
            coin_o = o["coin"]
            tpx = float(o.get("triggerPx") or o.get("limitPx") or 0.0)
            if tpx <= 0:
                continue
            pos_info = live_positions.get(coin_o, {})
            pos_side = pos_info.get("side", "SHORT")
            entry_est = float(pos_info.get("entry_px", 0.0))
            cat = categorize_trigger_order(o, position_side=pos_side, entry_px=entry_est)
            if cat == "TAKE_PROFIT":
                tp_orders_by_coin.setdefault(coin_o, []).append(o)
            elif cat == "STOP_LOSS":
                sl_orders_by_coin.setdefault(coin_o, []).append(o)
            else:
                if pos_side == "SHORT":
                    if entry_est > 0 and tpx < entry_est:
                        tp_orders_by_coin.setdefault(coin_o, []).append(o)
                    else:
                        sl_orders_by_coin.setdefault(coin_o, []).append(o)
                else:  # LONG
                    if entry_est > 0 and tpx > entry_est:
                        tp_orders_by_coin.setdefault(coin_o, []).append(o)
                    else:
                        sl_orders_by_coin.setdefault(coin_o, []).append(o)

        # 1. Register newly detected positions & ensure server SL + TP are synced
        for coin, p in live_positions.items():
            sz = float(p["size"])
            pos_side = p.get("side", "SHORT")
            if coin not in lifecycle:
                lifecycle[coin] = self._new_lifecycle_entry(coin, p)
            else:
                lifecycle[coin] = self._backfill_strategy(coin, lifecycle[coin], side=pos_side)

            state = lifecycle[coin]
            strategy = state.get("strategy", "SWING")
            side = state.get("side", pos_side)
            state["side"] = side
            entry_px = float(state["entry_px"])
            initial_sz = float(state.get("initial_sz", sz))
            sz_dec = self.trader.get_asset_precision(coin)
            state["remaining_sz"] = sz

            # 1a. Detect if On-Chain TP1 filled automatically while offline / between polls
            if not state.get("trimmed_10pct") and initial_sz > 0 and sz <= initial_sz * 0.65:
                state["trimmed_10pct"] = True
                prof = get_profile(strategy)
                fee_buf = prof.get("fee_roundtrip_pct", 0.09) / 100.0
                be_px = self.trader.round_price(coin, entry_px * (1 + fee_buf) if side == "LONG" else entry_px * (1 - fee_buf))
                prev_sl = float(state.get("current_sl_px") or 0.0)
                # Jangan longgarkan Tier-1/HWM lock yang sudah aktif saat TP1 on-chain terisi
                if state.get("early_be_shield") and prev_sl > 0:
                    be_px = max(be_px, prev_sl) if side == "LONG" else min(be_px, prev_sl)
                state["early_be_shield"] = True
                state["current_sl_px"] = be_px
                # Cancel old full-size SL orders and set Breakeven SL for remaining size
                for old_sl in sl_orders_by_coin.get(coin, []):
                    self.trader.cancel_order_by_oid(coin, old_sl["oid"])
                sl_orders_by_coin[coin] = []
                res_be = self.trader.set_stop_loss_trigger(coin, sz, be_px, side=side)
                state["native_sl_active"] = res_be.get("success", False)
                msg = f"⚡ ON-CHAIN TP1 TERDETEKSI ({coin} {side}): Size turun {initial_sz} -> {sz}. SL otomatis digeser ke Breakeven (${be_px})!"
                self.log_event(msg, strategy)

            # 1b. Deduplicate & size-sync Native Server-Side SL orders
            coin_sl_orders = sl_orders_by_coin.get(coin, [])
            target_sl_px = float(state.get("current_sl_px") or state.get("initial_sl_px") or 0.0)
            is_profit_sl_allowed = bool(
                state.get("trimmed_10pct") or state.get("early_be_shield") or state.get("periodic_sl_trailed")
            )

            # Guardrails: Unless BE/Profit-Trail is active, for SHORT SL must be > entry_px, for LONG SL must be < entry_px.
            from src.position_tags import build_plan
            if side == "SHORT":
                if not is_profit_sl_allowed and target_sl_px < entry_px:
                    target_sl_px = float(state.get("initial_sl_px") or 0.0)
                    if target_sl_px < entry_px:
                        plan = build_plan(strategy, entry_px, side="SHORT")
                        target_sl_px = self.trader.round_price(coin, plan["sl_px"])
                    state["current_sl_px"] = target_sl_px
            else:  # LONG
                if not is_profit_sl_allowed and (target_sl_px > entry_px or target_sl_px <= 0):
                    target_sl_px = float(state.get("initial_sl_px") or 0.0)
                    if target_sl_px > entry_px or target_sl_px <= 0:
                        plan = build_plan(strategy, entry_px, side="LONG")
                        target_sl_px = self.trader.round_price(coin, plan["sl_px"])
                    state["current_sl_px"] = target_sl_px

            # Filter out any anomalous SL orders
            if not is_profit_sl_allowed and coin_sl_orders:
                valid_sl_orders = []
                for o in coin_sl_orders:
                    tpx_val = float(o.get("triggerPx") or 0.0)
                    is_valid = (tpx_val > entry_px) if side == "SHORT" else (tpx_val < entry_px)
                    if is_valid:
                        valid_sl_orders.append(o)
                    else:
                        self.log_event(f"⚠️ {coin} ({side}): SL order on-chain dibatalkan karena tidak valid (${tpx_val} vs Entry ${entry_px}).", strategy)
                        self.trader.cancel_order_by_oid(coin, o["oid"])
                coin_sl_orders = valid_sl_orders

            if coin_sl_orders:
                # Choose the primary SL order closest to target_sl_px
                coin_sl_orders.sort(key=lambda o: abs(float(o.get("triggerPx", 0.0)) - target_sl_px))
                primary_sl = coin_sl_orders[0]
                # Cancel any duplicate SL orders
                for dup_sl in coin_sl_orders[1:]:
                    self.trader.cancel_order_by_oid(coin, dup_sl["oid"])
                    self.log_event(f"🧹 {coin} ({side}): Menghapus SL ganda @ ${dup_sl.get('triggerPx')} (oid: {dup_sl.get('oid')}).", strategy)

                primary_tpx = float(primary_sl.get("triggerPx", target_sl_px))
                primary_sz = float(primary_sl.get("sz", sz))
                # If SL size does not match current live position size, replace it
                if abs(primary_sz - sz) > max(1e-6, sz * 0.05):
                    self.trader.cancel_order_by_oid(coin, primary_sl["oid"])
                    res = self.trader.set_stop_loss_trigger(coin, sz, primary_tpx, side=side)
                    state["native_sl_active"] = res.get("success", False)
                    state["current_sl_px"] = primary_tpx
                    self.log_event(f"🔄 {coin} ({side}): Sinkron ukuran SL on-chain ({primary_sz} -> {sz}) @ ${primary_tpx}.", strategy)
                else:
                    state["native_sl_active"] = True
                    state["current_sl_px"] = primary_tpx
            else:
                curr_sl = self.trader.round_price(coin, target_sl_px)
                state["current_sl_px"] = curr_sl
                res = self.trader.set_stop_loss_trigger(coin, sz, curr_sl, side=side)
                if res.get("success"):
                    state["native_sl_active"] = True
                    self.log_event(f"🛡️ Native Server SL aktif untuk {coin} ({side}) @ ${curr_sl} (Size: {sz})", strategy)
                else:
                    state["native_sl_active"] = False
                    self.log_event(f"⚠️ Gagal pasang Native Server SL {coin} ({side}): {res.get('error')}", strategy)

            # 1c. Ensure Native Server-Side TP1 / TP2 exists on Hyperliquid
            coin_tp_orders = tp_orders_by_coin.get(coin, [])
            if not coin_tp_orders:
                if not state.get("trimmed_10pct") and state.get("tp1_px"):
                    tp1_sz = round(initial_sz * 0.50, sz_dec) if sz_dec > 0 else int(initial_sz * 0.50)
                    tp1_sz = min(tp1_sz, sz)
                    tp1_px = self.trader.round_price(coin, float(state["tp1_px"]))
                    if tp1_sz > 0 and tp1_px > 0:
                        res_tp = self.trader.set_take_profit_trigger(coin, tp1_sz, tp1_px, side=side)
                        if res_tp.get("success"):
                            state["native_tp_active"] = True
                            self.log_event(f"🎯 Native Server TP1 aktif untuk {coin} ({side}) @ ${tp1_px} (Size: {tp1_sz})", strategy)
                elif state.get("trimmed_10pct") and not state.get("tp_20pct") and state.get("tp2_px"):
                    tp2_sz = sz if strategy == "DAYTRADE" else (round(initial_sz * 0.30, sz_dec) if sz_dec > 0 else int(initial_sz * 0.30))
                    tp2_sz = min(tp2_sz, sz)
                    tp2_px = self.trader.round_price(coin, float(state["tp2_px"]))
                    if tp2_sz > 0 and tp2_px > 0:
                        res_tp2 = self.trader.set_take_profit_trigger(coin, tp2_sz, tp2_px, side=side)
                        if res_tp2.get("success"):
                            state["native_tp_active"] = True
                            self.log_event(f"🎯 Native Server TP2 aktif untuk {coin} ({side}) @ ${tp2_px} (Size: {tp2_sz})", strategy)
            else:
                state["native_tp_active"] = True

            lifecycle[coin] = state

        # 2. Prune positions that no longer exist on Hyperliquid & record closure in Forward Test
        to_delete = [c for c in lifecycle if c not in live_positions]
        if to_delete:
            try:
                # Forward test membaca tag strategi SEBELUM tag dihapus
                self.ft.sync_with_exchange(self.trader)
            except Exception:
                pass
        for c in to_delete:
            strategy = lifecycle[c].get("strategy", "SWING")
            del lifecycle[c]
            position_tags.remove_tag(c)
            # Cancel any orphaned orders
            self.trader.cancel_coin_orders(c)
            self.log_event(f"🏁 {c} sudah tidak aktif di bursa. Lifecycle & tag strategi dibersihkan.", strategy)

        self.save_lifecycle_state(lifecycle)
        if to_delete:
            try:
                from src.autonomous_engine import AutonomousEngine
                AutonomousEngine(self.trader).run_self_evaluation(trigger_reason=f"ON_CLOSE_{','.join(to_delete)}")
            except Exception as e:
                self.log_event(f"Warning: Gagal menjalankan Self-Evaluation pasca close: {e}")
        return lifecycle

    # ------------------------------------------------------------------
    # EXECUTION HELPERS
    # ------------------------------------------------------------------
    def _ft_update(self, coin, state, pos_info, current_px, size=None, note=None):
        self.ft.update_position(
            coin=coin,
            entry_px=state["entry_px"],
            current_px=current_px,
            size=size if size is not None else pos_info.get("size", state["initial_sz"]),
            leverage=pos_info.get("leverage", 2),
            margin_used=pos_info.get("margin_used", 0.0),
            unrealized_pnl=pos_info.get("unrealized_pnl", 0.0),
            roe_pct=pos_info.get("roe_pct", 0.0),
            is_on_chain_sl=state.get("native_sl_active", True),
            event_note=note,
            strategy=state.get("strategy", "SWING"),
            plan=state,
        )

    def _close_full(self, coin, state, pos_info, current_px, reason, msg, action_type, actions):
        res = self.trader.close_position(coin)
        if res.get("success"):
            self.trader.cancel_coin_orders(coin)
            self.log_event(msg, state.get("strategy"))
            self.ft.close_position_record(
                coin=coin, exit_px=current_px, reason=reason,
                pnl_usd=pos_info.get("unrealized_pnl", 0.0)
            )
            try:
                from src.autonomous_engine import AutonomousEngine
                AutonomousEngine(self.trader).run_self_evaluation(trigger_reason=f"ON_CLOSE_{coin}_{reason}")
            except Exception:
                pass
            actions.append({"type": action_type, "coin": coin, "strategy": state.get("strategy"), "msg": msg})
            return True
        self.log_event(f"⚠️ Gagal menutup {coin} ({reason}): {res.get('error')}", state.get("strategy"))
        return False

    def _partial_close_and_move_sl(self, coin, state, close_sz, new_sl_px):
        """Partial close + replace on-chain SL with the new level for the remaining size, and place next TP."""
        res = self.trader.close_position(coin, sz=close_sz)
        if not res.get("success"):
            return False, 0.0
        remaining = max(0.0, float(state["remaining_sz"]) - close_sz)
        state["remaining_sz"] = remaining
        side = state.get("side", "SHORT")
        if new_sl_px is not None:
            state["current_sl_px"] = new_sl_px
            self.trader.cancel_coin_orders(coin)
            if remaining > 0:
                self.trader.set_stop_loss_trigger(coin, remaining, new_sl_px, side=side)
                # Place TP2 on-chain if TP2 not yet reached
                if not state.get("tp_20pct") and state.get("tp2_px"):
                    strategy = state.get("strategy", "SWING")
                    sz_dec = self.trader.get_asset_precision(coin)
                    initial_sz = float(state.get("initial_sz", remaining))
                    tp2_sz = remaining if strategy == "DAYTRADE" else (round(initial_sz * 0.30, sz_dec) if sz_dec > 0 else int(initial_sz * 0.30))
                    tp2_sz = min(tp2_sz, remaining)
                    if tp2_sz > 0:
                        self.trader.set_take_profit_trigger(coin, tp2_sz, float(state["tp2_px"]), side=side)
        return True, remaining

    def _age_hours(self, state) -> float:
        try:
            return (datetime.now() - datetime.fromisoformat(state.get("created_at"))).total_seconds() / 3600.0
        except Exception:
            return 0.0

    def _move_sl_on_chain(self, coin, state, new_sl_px, current_sz) -> bool:
        """Moves the on-chain SL trigger order to new_sl_px without closing position size."""
        side = state.get("side", "SHORT")
        state["current_sl_px"] = new_sl_px
        try:
            open_orders = self.trader.get_open_orders()
            from src.trader import categorize_trigger_order
            for o in open_orders:
                if o.get("coin") == coin and o.get("isTrigger"):
                    if categorize_trigger_order(o, position_side=side, entry_px=state["entry_px"]) == "STOP_LOSS":
                        self.trader.cancel_order_by_oid(coin, o["oid"])
        except Exception:
            pass
        res = self.trader.set_stop_loss_trigger(coin, current_sz, new_sl_px, side=side)
        return res.get("success", False)

    # ------------------------------------------------------------------
    # STREAM 1: SWING
    # ------------------------------------------------------------------
    def _manage_swing(self, coin, state, pos_info, current_px, actions):
        prof = get_profile("SWING")
        entry_px = state["entry_px"]
        initial_sz = state["initial_sz"]
        side = state.get("side", "SHORT")
        sz_dec = self.trader.get_asset_precision(coin)
        fee_buf = prof.get("fee_roundtrip_pct", 0.09) / 100.0

        if side == "LONG":
            profit_pct = ((current_px - entry_px) / entry_px) * 100
            adverse_pct = ((entry_px - current_px) / entry_px) * 100
            early_be_hit = profit_pct >= prof.get("early_be_pct", 5.0)
            tp1_hit = profit_pct >= prof["trim_pct"]
            tp2_hit = profit_pct >= prof["tp2_pct"]
            runner_hit = bool(prof.get("runner_pct") and profit_pct >= prof["runner_pct"])
            sl_hit = current_px <= state["current_sl_px"]
            be_px = self.trader.round_price(coin, entry_px * (1 + fee_buf))
            trailing_sl_px = self.trader.round_price(coin, entry_px * (1 + prof["trim_pct"] / 100.0))
        else:  # SHORT
            profit_pct = ((entry_px - current_px) / entry_px) * 100
            adverse_pct = ((current_px - entry_px) / entry_px) * 100
            early_be_hit = profit_pct >= prof.get("early_be_pct", 5.0)
            tp1_hit = profit_pct >= prof["trim_pct"]
            tp2_hit = profit_pct >= prof["tp2_pct"]
            runner_hit = bool(prof.get("runner_pct") and profit_pct >= prof["runner_pct"])
            sl_hit = current_px >= state["current_sl_px"]
            be_px = self.trader.round_price(coin, entry_px * (1 - fee_buf))
            trailing_sl_px = self.trader.round_price(coin, entry_px * (1 - prof["trim_pct"] / 100.0))

        max_hold = state.get("max_hold_hours", prof["max_hold_hours"])

        # RULE 0: Time-stop 72 jam
        if prof.get("time_exit_enabled") and self._age_hours(state) >= max_hold:
            msg = f"⏰ SWING TIME-STOP {max_hold}J ({self._age_hours(state):.1f} jam): {coin} ({side}) ditutup. Gerak: {profit_pct:+.2f}%"
            if self._close_full(coin, state, pos_info, current_px, f"SWING_TIME_STOP_{max_hold}H", msg, "TIME_EXIT", actions):
                return

        # RULE 0.1: AI Dead-Momentum Buster (Scenario 1 - Optimized)
        # Evaluasi posisi stagnan / modal terkunci tanpa momentum
        age_h = self._age_hours(state)
        peak_profit_pct = float(state.get("peak_profit_pct", 0.0))
        now_ts = time.time()
        last_check_ts = float(state.get("last_stalemate_check_ts", 0.0))
        swing_cooldown_sec = 4 * 3600  # Evaluasi ulang tiap 4 jam jika masih stagnan

        # HARD GATEKEEPER 1: Anti-Clipping Rule (Jangan bunuh trade yang sedang/pernah untung sehat)
        # Jika sedang profit >= +1.0% atau MFE pernah mencapai >= +1.5%, biarkan Trailing SL / TP bekerja
        is_profit_protected = profit_pct >= 1.0 or peak_profit_pct >= 1.5 or state.get("early_be_shield") or state.get("trimmed_10pct")

        # Trigger hanya jika: Umur >= 36 jam, pergerakan loyo (|move| <= 1.5%), cooldown terpenuhi, dan BUKAN posisi pemenang
        if (age_h >= 36.0 and abs(profit_pct) <= 1.5 and
                not is_profit_protected and
                (now_ts - last_check_ts >= swing_cooldown_sec)):
            state["last_stalemate_check_ts"] = now_ts
            try:
                from src.ai_engine import ai_advisor
                market_info = {
                    "current_px": current_px,
                    "age_hours": age_h,
                    "max_hold_hours": max_hold,
                    "unrealized_pnl": state.get("unrealized_pnl", 0.0),
                    "move_pct": profit_pct,
                    "peak_profit_pct": peak_profit_pct,
                    "funding_rate": float(state.get("funding_rate", 0.0)),
                }
                diag = ai_advisor.evaluate_position_stalemate(coin, state, market_info)
                if diag.get("action") == "CLOSE_NOW" and diag.get("confidence", 0) >= 70:
                    msg = (f"🧠 AI DEAD-MOMENTUM BUSTER: {coin} ({side}) jalan di tempat {age_h:.1f} jam (gerak {profit_pct:+.2f}%, MFE: {peak_profit_pct:+.2f}%). "
                           f"Posisi ditutup dini untuk daur ulang modal! ({diag.get('reasoning')})")
                    if self._close_full(coin, state, pos_info, current_px, "AI_STALEMATE_EXIT", msg, "AI_EXIT", actions):
                        return
                else:
                    self.log_event(f"🧠 AI STALEMATE CHECK: {coin} ({side}) dipertahankan (AI: {diag.get('action')}, {diag.get('confidence')}%) - {diag.get('reasoning')}", "SWING")
            except Exception as e:
                self.log_event(f"Warning: Gagal evaluasi AI stalemate {coin}: {e}", "SWING")

        # Track Peak Profit % (High-Water Mark) for Periodic On-Chain SL Ratcheting ([MEM-004])
        peak_profit_pct = max(float(state.get("peak_profit_pct", 0.0)), profit_pct)
        state["peak_profit_pct"] = round(peak_profit_pct, 2)
        current_sz = float(pos_info.get("size", initial_sz))
        cur_sl = float(state.get("current_sl_px", 0.0))

        # Helper to verify SL only tightens (never widens) and sits safely on the stop side of current_px
        def _is_better_sl(candidate_sl: float) -> bool:
            if candidate_sl <= 0:
                return False
            if side == "LONG":
                return (candidate_sl > cur_sl) and (candidate_sl < current_px * 0.996)
            else:
                return (cur_sl <= 0 or candidate_sl < cur_sl) and (candidate_sl > current_px * 1.004)

        # RULE 0.2: Periodic Step 1 — Half-Risk Cushion Compress (+2.2% move / +4.4% ROE -> compress SL from -10% to -5%)
        half_risk_step = float(prof.get("half_risk_step_pct", 2.2))
        if (profit_pct >= half_risk_step and not state.get("half_risk_shield")
                and not state.get("early_be_shield") and not state.get("trimmed_10pct")):
            half_sl_pct = float(prof.get("sl_pct", 10.0)) * 0.50  # 5.0%
            half_sl_px = self.trader.round_price(
                coin, entry_px * (1 - half_sl_pct / 100.0) if side == "LONG" else entry_px * (1 + half_sl_pct / 100.0)
            )
            if _is_better_sl(half_sl_px):
                if self._move_sl_on_chain(coin, state, half_sl_px, current_sz):
                    state["half_risk_shield"] = True
                    state["current_sl_px"] = half_sl_px
                    cur_sl = half_sl_px
                    msg = (f"🛡️ SWING STEP-1 SL COMPRESS (+{profit_pct:.1f}% gerak / +{profit_pct*2:.1f}% ROE): "
                           f"SL on-chain diperketat 50% ke ${half_sl_px} (-{half_sl_pct:.1f}%) untuk pangkas risiko separuh!")
                    self.log_event(msg, "SWING")
                    actions.append({"type": "SL_STEP_HALF_RISK", "coin": coin, "strategy": "SWING", "msg": msg})

        # RULE 0.5: TIER-1 PROFIT LOCK (+4.0% gerak / +8.0% ROE) -> kunci min +1.5% NET (+3.0% ROE), bukan BE polos
        tier1_net_pct = float(prof.get("tier1_lock_net_pct", 1.5))
        tier1_lock_pct = tier1_net_pct + fee_buf * 100.0  # + fee roundtrip agar hasil bersih tetap +1.5%
        lev = float(prof.get("leverage", 2))
        if early_be_hit and not state.get("early_be_shield") and not state.get("trimmed_10pct"):
            tier1_px = self.trader.round_price(
                coin, entry_px * (1 + tier1_lock_pct / 100.0) if side == "LONG" else entry_px * (1 - tier1_lock_pct / 100.0)
            )
            # Fallback ke BE+fee hanya jika harga sudah terlalu dekat ke level Tier-1 (gap / wick balik)
            if _is_better_sl(tier1_px):
                target_sl, locked_pct, tier1_ok = tier1_px, tier1_lock_pct, True
            else:
                target_sl, locked_pct, tier1_ok = be_px, fee_buf * 100.0, False
            if _is_better_sl(target_sl) and self._move_sl_on_chain(coin, state, target_sl, current_sz):
                state["early_be_shield"] = True
                state["half_risk_shield"] = True
                state["tier1_locked"] = tier1_ok
                state["locked_profit_pct"] = round(locked_pct, 2)
                state["current_sl_px"] = target_sl
                cur_sl = target_sl
                lock_label = (f"+{tier1_net_pct:.1f}% NET (+{tier1_net_pct * lev:.1f}% ROE)" if tier1_ok
                              else "Breakeven+fee (fallback, harga terlalu dekat level Tier-1)")
                msg = (f"🔒 SWING TIER-1 PROFIT LOCK (+{profit_pct:.1f}% gerak / +{profit_pct * lev:.1f}% ROE): "
                       f"SL on-chain dikunci di {lock_label} (${target_sl})!")
                self.log_event(msg, "SWING")
                actions.append({"type": "TIER1_PROFIT_LOCK", "coin": coin, "strategy": "SWING", "msg": msg})

        # RULE 1: Trimming 1 (±7.0% gerak / ±14% ROE) -> Trim 50% & Lock +2.0% Profit Cushion on-chain
        if tp1_hit and not state.get("trimmed_10pct"):
            trim_sz = initial_sz * 0.50
            trim_sz = round(trim_sz, sz_dec) if sz_dec > 0 else int(trim_sz)
            lock_pct = 0.020  # Lock +2.0% price move (+4.0% ROE) after TP1
            tp1_lock_sl = self.trader.round_price(
                coin, entry_px * (1 + lock_pct) if side == "LONG" else entry_px * (1 - lock_pct)
            )
            use_tp1_lock = _is_better_sl(tp1_lock_sl)
            # Jangan pernah melonggarkan SL: jika lock +2.0% tak valid, pertahankan Tier-1 yang sudah aktif
            target_post_tp1_sl = tp1_lock_sl if use_tp1_lock else (cur_sl if state.get("early_be_shield") else be_px)
            ok, remaining = self._partial_close_and_move_sl(coin, state, trim_sz, target_post_tp1_sl)
            if ok:
                state["trimmed_10pct"] = True
                state["early_be_shield"] = True
                state["periodic_sl_trailed"] = True
                if use_tp1_lock:
                    state["locked_profit_pct"] = 2.0
                cur_sl = target_post_tp1_sl
                msg = f"✂️ TRIMMING 1 SUKSES (+{profit_pct:.1f}% gerak): 50% posisi ({trim_sz} {coin} {side}) diamankan! SL dikunci di profit +{state.get('locked_profit_pct', 0.0):.2f}% (${target_post_tp1_sl})."
                self.log_event(msg, "SWING")
                self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                actions.append({"type": "TRIMMING_1", "coin": coin, "strategy": "SWING", "msg": msg})

        # RULE 1.5: TIER-2 HWM RATCHET — aktif setelah TP1 & tetap berjalan untuk runner pasca-TP2
        periodic_start = float(prof.get("periodic_trail_start_pct", 8.5))
        hwm_ratio = float(prof.get("hwm_lock_ratio", 0.55))
        if state.get("trimmed_10pct") and not state.get("runner_35pct") and peak_profit_pct >= periodic_start:
            desired_lock_pct = round(peak_profit_pct * hwm_ratio, 2)
            prev_lock_pct = float(state.get("locked_profit_pct", 0.0))
            if (desired_lock_pct - prev_lock_pct) >= 1.0:
                ratchet_sl = self.trader.round_price(
                    coin,
                    entry_px * (1 + desired_lock_pct / 100.0) if side == "LONG" else entry_px * (1 - desired_lock_pct / 100.0)
                )
                if _is_better_sl(ratchet_sl):
                    rem_sz = float(pos_info.get("size", state.get("remaining_sz", initial_sz)))
                    if self._move_sl_on_chain(coin, state, ratchet_sl, rem_sz):
                        state["periodic_sl_trailed"] = True
                        state["locked_profit_pct"] = desired_lock_pct
                        state["current_sl_px"] = ratchet_sl
                        cur_sl = ratchet_sl
                        msg = (f"📈 SWING TIER-2 HWM RATCHET (Peak +{peak_profit_pct:.1f}% gerak): "
                               f"SL on-chain digeser mengunci +{desired_lock_pct:.1f}% profit (${ratchet_sl})!")
                        self.log_event(msg, "SWING")
                        actions.append({"type": "PERIODIC_SL_RATCHET", "coin": coin, "strategy": "SWING", "msg": msg})

        def _tighter_sl(a: float, b: float) -> float:
            if b <= 0:
                return a
            return max(a, b) if side == "LONG" else min(a, b)

        # RULE 2: Target 2 (±18.0% gerak / ±36% ROE) dengan AI Runner Extender (Scenario 2)
        if tp2_hit and not state.get("tp_20pct"):
            runner_decision = {"action": "EXTEND_RUNNER", "confidence": 75, "reasoning": "Standard swing policy: biarkan sisa runner."}
            try:
                from src.ai_engine import ai_advisor
                runner_decision = ai_advisor.evaluate_runner_extension(coin, state, current_px, {"profit_pct": profit_pct})
            except Exception:
                pass

            if runner_decision.get("action") == "FULL_HARVEST" and runner_decision.get("confidence", 0) >= 75:
                # AI mendeteksi momentum jenuh/exhaustion -> kunci 100% cuan
                msg = (f"🎯🧠 AI RUNNER HARVEST (+{profit_pct:.1f}% gerak): Trend melemah/jenuh! "
                       f"Sisa posisi {coin} ({side}) ditutup penuh mengamankan 100% cuan! ({runner_decision.get('reasoning')})")
                if self._close_full(coin, state, pos_info, current_px, "AI_FULL_HARVEST_TP2", msg, "TARGET_2", actions):
                    state["tp_20pct"] = True
                    state["runner_35pct"] = True
            else:
                tp2_sz = initial_sz * 0.30
                tp2_sz = round(tp2_sz, sz_dec) if sz_dec > 0 else int(tp2_sz)
                tightness = runner_decision.get("trailing_sl_tightness", "STANDARD")
                if tightness == "TIGHT":
                    tight_pct = (prof["trim_pct"] + prof["tp2_pct"]) / 2.0  # di tengah trim1 & tp2
                    trailing_sl_px = self.trader.round_price(coin, entry_px * (1 + tight_pct / 100.0) if side == "LONG" else entry_px * (1 - tight_pct / 100.0))
                trailing_sl_px = _tighter_sl(trailing_sl_px, cur_sl)  # jangan turunkan SL hasil ratchet Tier-2
                ok, remaining = self._partial_close_and_move_sl(coin, state, tp2_sz, trailing_sl_px)
                if ok:
                    state["tp_20pct"] = True
                    state["periodic_sl_trailed"] = True
                    state["locked_profit_pct"] = round(abs(trailing_sl_px - entry_px) / entry_px * 100.0, 2)
                    msg = (f"🎯 TARGET 2 SUKSES (+{profit_pct:.1f}% gerak): 30% posisi ({tp2_sz} {coin} {side}) diamankan! "
                           f"Trailing SL dikunci di profit (${trailing_sl_px}). Sisa runner dikawal! ({runner_decision.get('reasoning')})")
                    self.log_event(msg, "SWING")
                    self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                    actions.append({"type": "TARGET_2", "coin": coin, "strategy": "SWING", "msg": msg})

        # RULE 3: Runner (±30.0% gerak / ±60% ROE)
        elif runner_hit and not state.get("runner_35pct"):
            msg = f"🚀 RUNNER SUKSES (+{profit_pct:.1f}% gerak): Sisa posisi {coin} ({side}) ditutup dengan profit maksimal!"
            if self._close_full(coin, state, pos_info, current_px, "RUNNER_MAX_PROFIT", msg, "RUNNER", actions):
                state["runner_35pct"] = True

        # RULE 4: Local SL check (backup dari SL on-chain)
        elif sl_hit:
            is_be = state.get("trimmed_10pct", False) or state.get("early_be_shield", False)
            msg = (f"🛡️ BREAKEVEN DEFENSE: Harga kembali ke batas proteksi (${state.get('current_sl_px', entry_px)}). Sisa {coin} ({side}) ditutup aman!"
                   if is_be else f"🛑 STOP LOSS HIT (-{adverse_pct:.1f}%): Cut loss {coin} ({side}) otomatis dieksekusi.")
            self._close_full(coin, state, pos_info, current_px,
                             "BREAKEVEN_DEFENSE" if is_be else "STOP_LOSS_HIT", msg, "STOP_LOSS", actions)

    # ------------------------------------------------------------------
    # STREAM 2: INTRADAY / DAYTRADE
    # ------------------------------------------------------------------
    def _manage_intraday(self, coin, state, pos_info, current_px, actions):
        prof = get_profile("DAYTRADE")
        entry_px = state["entry_px"]
        initial_sz = state["initial_sz"]
        side = state.get("side", "SHORT")
        sz_dec = self.trader.get_asset_precision(coin)
        fee_buf = prof.get("fee_roundtrip_pct", 0.09) / 100.0

        if side == "LONG":
            profit_pct = ((current_px - entry_px) / entry_px) * 100
            adverse_pct = ((entry_px - current_px) / entry_px) * 100
            tp1_px = float(state.get("tp1_px") or entry_px * (1 + prof["trim_pct"] / 100))
            tp2_px = float(state.get("tp2_px") or entry_px * (1 + prof["tp2_pct"] / 100))
            tp2_hit = current_px >= tp2_px
            tp1_hit = current_px >= tp1_px
            be_px = self.trader.round_price(coin, entry_px * (1 + fee_buf))
            sl_hit = current_px <= state["current_sl_px"]
        else:  # SHORT
            profit_pct = ((entry_px - current_px) / entry_px) * 100
            adverse_pct = ((current_px - entry_px) / entry_px) * 100
            tp1_px = float(state.get("tp1_px") or entry_px * (1 - prof["trim_pct"] / 100))
            tp2_px = float(state.get("tp2_px") or entry_px * (1 - prof["tp2_pct"] / 100))
            tp2_hit = current_px <= tp2_px
            tp1_hit = current_px <= tp1_px
            be_px = self.trader.round_price(coin, entry_px * (1 - fee_buf))
            sl_hit = current_px >= state["current_sl_px"]

        max_hold = state.get("max_hold_hours", prof["max_hold_hours"])
        age_h = self._age_hours(state)

        # RULE 0: Time-stop 6 jam (wajib flat, tanpa overnight)
        if age_h >= max_hold:
            msg = f"⏰ INTRADAY TIME-STOP {max_hold}J ({age_h:.1f} jam): {coin} ({side}) ditutup otomatis. Gerak: {profit_pct:+.2f}%"
            self._close_full(coin, state, pos_info, current_px, f"INTRADAY_TIME_STOP_{max_hold}H", msg, "TIME_EXIT", actions)
            return

        # RULE 0.1: AI Dead-Momentum Buster (Scenario 1 - Optimized)
        # Evaluasi intraday stagnan dengan Cooldown berkala & Anti-Clipping Rule
        peak_profit_pct = float(state.get("peak_profit_pct", 0.0))
        now_ts = time.time()
        last_check_ts = float(state.get("last_stalemate_check_ts", 0.0))
        dt_cooldown_sec = 60 * 60  # Evaluasi ulang tiap 60 menit jika tetap stagnan

        # HARD GATEKEEPER 1: Anti-Clipping Rule (Jangan pernah potong trade intraday yang sedang profit)
        # Jika profit >= +0.7% atau peak profit pernah >= +0.9% (atau BE/Trim sudah aktif), serahkan ke Trailing SL
        is_dt_profit_protected = profit_pct >= 0.7 or peak_profit_pct >= 0.9 or state.get("early_be_shield") or state.get("trimmed_10pct")

        if (age_h >= 3.0 and abs(profit_pct) <= 0.8 and
                not is_dt_profit_protected and
                (now_ts - last_check_ts >= dt_cooldown_sec)):
            state["last_stalemate_check_ts"] = now_ts
            try:
                from src.ai_engine import ai_advisor
                market_info = {
                    "current_px": current_px,
                    "age_hours": age_h,
                    "max_hold_hours": max_hold,
                    "unrealized_pnl": state.get("unrealized_pnl", 0.0),
                    "move_pct": profit_pct,
                    "peak_profit_pct": peak_profit_pct,
                    "funding_rate": float(state.get("funding_rate", 0.0)),
                }
                diag = ai_advisor.evaluate_position_stalemate(coin, state, market_info)
                if diag.get("action") == "CLOSE_NOW" and diag.get("confidence", 0) >= 70:
                    msg = (f"🧠 AI INTRADAY DEAD-MOMENTUM: {coin} ({side}) stagnan {age_h:.1f} jam (gerak {profit_pct:+.2f}%, MFE: {peak_profit_pct:+.2f}%). "
                           f"Posisi ditutup dini untuk daur ulang modal! ({diag.get('reasoning')})")
                    if self._close_full(coin, state, pos_info, current_px, "AI_STALEMATE_EXIT", msg, "AI_EXIT", actions):
                        return
                else:
                    self.log_event(f"🧠 AI STALEMATE CHECK: {coin} ({side}) dipertahankan (AI: {diag.get('action')}, {diag.get('confidence')}%) - {diag.get('reasoning')}", "DAYTRADE")
            except Exception as e:
                self.log_event(f"Warning: Gagal evaluasi AI stalemate {coin}: {e}", "DAYTRADE")

        # Track MFE / High-Water Mark (sebelumnya tidak pernah di-update untuk DAYTRADE -> gate MFE Anti-Clipping mati)
        peak_profit_pct = max(peak_profit_pct, profit_pct)
        state["peak_profit_pct"] = round(peak_profit_pct, 2)

        # Periodic Intraday On-Chain SL Step-Trailing (0.45R Half-Risk & 0.65R Early BE)
        r_dist_pct = abs(tp1_px - entry_px) / entry_px * 100.0 if entry_px > 0 else float(prof.get("sl_pct", 3.2))
        current_sz = float(pos_info.get("size", initial_sz))
        cur_sl = float(state.get("current_sl_px", 0.0))
        half_risk_r = float(prof.get("half_risk_r", 0.45))
        early_be_r = float(prof.get("early_be_r", 0.65))

        def _is_better_dt_sl(candidate_sl: float) -> bool:
            if candidate_sl <= 0:
                return False
            if side == "LONG":
                return (candidate_sl > cur_sl) and (candidate_sl < current_px * 0.997)
            else:
                return (cur_sl <= 0 or candidate_sl < cur_sl) and (candidate_sl > current_px * 1.003)

        if not state.get("trimmed_10pct") and not state.get("early_be_shield"):
            if profit_pct >= (early_be_r * r_dist_pct):
                if _is_better_dt_sl(be_px) and self._move_sl_on_chain(coin, state, be_px, current_sz):
                    state["early_be_shield"] = True
                    state["half_risk_shield"] = True
                    state["current_sl_px"] = be_px
                    cur_sl = be_px
                    msg = f"🛡️ INTRADAY EARLY BE SHIELD (+{profit_pct:.1f}% / {early_be_r:.2f}R): SL {coin} ({side}) digeser ke Breakeven (${be_px})!"
                    self.log_event(msg, "DAYTRADE")
                    actions.append({"type": "EARLY_BREAKEVEN", "coin": coin, "strategy": "DAYTRADE", "msg": msg})
            elif profit_pct >= (half_risk_r * r_dist_pct) and not state.get("half_risk_shield"):
                half_r_pct = r_dist_pct * 0.50
                half_sl_px = self.trader.round_price(
                    coin, entry_px * (1 - half_r_pct / 100.0) if side == "LONG" else entry_px * (1 + half_r_pct / 100.0)
                )
                if _is_better_dt_sl(half_sl_px) and self._move_sl_on_chain(coin, state, half_sl_px, current_sz):
                    state["half_risk_shield"] = True
                    state["current_sl_px"] = half_sl_px
                    msg = f"🛡️ INTRADAY STEP-1 SL COMPRESS (+{profit_pct:.1f}% / {half_risk_r:.2f}R): SL {coin} ({side}) diperketat 50% ke ${half_sl_px}!"
                    self.log_event(msg, "DAYTRADE")
                    actions.append({"type": "SL_STEP_HALF_RISK", "coin": coin, "strategy": "DAYTRADE", "msg": msg})

        # RULE 0.7: TIER-1 PROFIT LOCK (profit >= min(+4.0%, 1.5R)) -> kunci min +1.5% NET, bukan BE polos
        tier1_trigger_pct = min(float(prof.get("tier1_lock_trigger_pct", 4.0)),
                                float(prof.get("tier1_lock_trigger_r", 1.5)) * r_dist_pct)
        tier1_net_pct = float(prof.get("tier1_lock_net_pct", 1.5))
        tier1_lock_pct = tier1_net_pct + fee_buf * 100.0
        lev = float(prof.get("leverage", 2))
        if (not state.get("tier1_locked") and profit_pct >= tier1_trigger_pct
                and float(state.get("locked_profit_pct", 0.0)) < tier1_lock_pct):
            tier1_px = self.trader.round_price(
                coin, entry_px * (1 + tier1_lock_pct / 100.0) if side == "LONG" else entry_px * (1 - tier1_lock_pct / 100.0)
            )
            if _is_better_dt_sl(tier1_px) and self._move_sl_on_chain(coin, state, tier1_px, current_sz):
                state["tier1_locked"] = True
                state["early_be_shield"] = True
                state["half_risk_shield"] = True
                state["locked_profit_pct"] = round(tier1_lock_pct, 2)
                state["current_sl_px"] = tier1_px
                cur_sl = tier1_px
                msg = (f"🔒 INTRADAY TIER-1 PROFIT LOCK (+{profit_pct:.1f}% / {profit_pct / r_dist_pct:.2f}R): "
                       f"SL {coin} ({side}) dikunci di +{tier1_net_pct:.1f}% NET (~+{tier1_net_pct * lev:.1f}% ROE) (${tier1_px})!")
                self.log_event(msg, "DAYTRADE")
                actions.append({"type": "TIER1_PROFIT_LOCK", "coin": coin, "strategy": "DAYTRADE", "msg": msg})

        # RULE 1: TP2 tercapai (±5.0-9.0% gerak) dengan AI Runner Extender (Scenario 2)
        if tp2_hit and not state.get("tp_20pct"):
            runner_decision = {"action": "FULL_HARVEST", "confidence": 80, "reasoning": "Standard intraday flat discipline"}
            try:
                from src.ai_engine import ai_advisor
                runner_decision = ai_advisor.evaluate_runner_extension(coin, state, current_px, {"profit_pct": profit_pct})
            except Exception:
                pass

            if runner_decision.get("action") == "EXTEND_RUNNER" and runner_decision.get("confidence", 0) >= 80:
                current_sz = float(pos_info.get("size", initial_sz))
                trim_sz = current_sz * 0.70
                trim_sz = round(trim_sz, sz_dec) if sz_dec > 0 else int(trim_sz)
                ok, remaining = self._partial_close_and_move_sl(coin, state, trim_sz, tp1_px)
                if ok:
                    state["tp_20pct"] = True
                    state["periodic_sl_trailed"] = True
                    msg = (f"🎯🚀 AI INTRADAY RUNNER EXTENDER (+{profit_pct:.1f}%): 70% sisa ({trim_sz} {coin}) diamankan! "
                           f"Sisa runner ({remaining}) dikawal dengan Trailing SL di TP1 (${tp1_px})! ({runner_decision.get('reasoning')})")
                    self.log_event(msg, "DAYTRADE")
                    self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                    actions.append({"type": "TARGET_2", "coin": coin, "strategy": "DAYTRADE", "msg": msg})
                    return

            msg = f"🎯 INTRADAY TP2 ({prof['tp2_r']}R) TERCAPAI ({profit_pct:.1f}%): Sisa posisi {coin} ({side}) ditutup penuh! (AI: {runner_decision.get('reasoning')})"
            if self._close_full(coin, state, pos_info, current_px, "INTRADAY_TP2_2R", msg, "TARGET_2", actions):
                state["tp_20pct"] = True
            return

        # RULE 2: TP1 (1.0R) -> Trim 50% & kunci SL di profit +0.30R (atau minimal BE+fee)
        if tp1_hit and not state.get("trimmed_10pct"):
            trim_sz = initial_sz * 0.50
            trim_sz = round(trim_sz, sz_dec) if sz_dec > 0 else int(trim_sz)
            lock_dt_pct = max(fee_buf * 100.0, 0.30 * r_dist_pct)
            tp1_lock_px = self.trader.round_price(
                coin, entry_px * (1 + lock_dt_pct / 100.0) if side == "LONG" else entry_px * (1 - lock_dt_pct / 100.0)
            )
            post_tp1_sl = tp1_lock_px if _is_better_dt_sl(tp1_lock_px) else be_px
            ok, remaining = self._partial_close_and_move_sl(coin, state, trim_sz, post_tp1_sl)
            if ok:
                state["trimmed_10pct"] = True
                state["early_be_shield"] = True
                msg = f"✂️ INTRADAY TP1 ({prof['tp1_r']}R) SUKSES ({profit_pct:.1f}%): 50% {coin} ({side}) ditutup! SL dikunci di profit (${post_tp1_sl})."
                self.log_event(msg, "DAYTRADE")
                self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                actions.append({"type": "TRIMMING_1", "coin": coin, "strategy": "DAYTRADE", "msg": msg})
            return

        # RULE 2.5: Post-TP1 Intraday Periodic Profit Ratchet (>= 1.45R -> lock +0.50R profit on-chain)
        if state.get("trimmed_10pct") and not state.get("tp_20pct") and not state.get("periodic_sl_trailed") and profit_pct >= (1.45 * r_dist_pct):
            lock_dt_pct = 0.50 * r_dist_pct
            ratchet_dt_sl = self.trader.round_price(
                coin, entry_px * (1 + lock_dt_pct / 100.0) if side == "LONG" else entry_px * (1 - lock_dt_pct / 100.0)
            )
            if _is_better_dt_sl(ratchet_dt_sl) and self._move_sl_on_chain(coin, state, ratchet_dt_sl, current_sz):
                state["periodic_sl_trailed"] = True
                state["current_sl_px"] = ratchet_dt_sl
                msg = f"📈 INTRADAY PERIODIC SL RATCHET (+{profit_pct:.1f}% / 1.45R): SL {coin} ({side}) digeser mengunci +0.5R profit (${ratchet_dt_sl})!"
                self.log_event(msg, "DAYTRADE")
                actions.append({"type": "PERIODIC_SL_RATCHET", "coin": coin, "strategy": "DAYTRADE", "msg": msg})

        # RULE 3: Local SL check (backup dari SL on-chain)
        if sl_hit:
            is_be = state.get("trimmed_10pct", False) or state.get("early_be_shield", False)
            msg = (f"🛡️ INTRADAY BE DEFENSE: {coin} ({side}) kembali ke modal setelah proteksi. Sisa ditutup tanpa rugi."
                   if is_be else f"🛑 INTRADAY SL TEKNIS HIT (-{adverse_pct:.1f}%): Cut loss {coin} ({side}).")
            self._close_full(coin, state, pos_info, current_px,
                             "INTRADAY_BE_DEFENSE" if is_be else "INTRADAY_SL_HIT", msg, "STOP_LOSS", actions)

    # ------------------------------------------------------------------
    # AI MACRO DEFENDER & PULSE AUDIT (SCENARIOS 3 & 4)
    # ------------------------------------------------------------------
    def _audit_macro_pulse_if_needed(self, now_ts: float):
        """
        Scenario 4: Periodic 4-hour AI Market Regime Pulse Check.
        Updates SQLite trade_db and recalibrates global risk multiplier.
        """
        try:
            latest = trade_db.get_latest_market_regime()
            should_run = False
            if not latest or not latest.get("timestamp"):
                should_run = True
            else:
                try:
                    last_dt = datetime.fromisoformat(latest["timestamp"])
                    age_seconds = (datetime.now() - last_dt).total_seconds()
                    if age_seconds >= (4 * 3600):
                        should_run = True
                except Exception:
                    should_run = True

            if should_run and (now_ts - self._last_macro_pulse_ts) >= (4 * 3600):
                self._last_macro_pulse_ts = now_ts
                from src.database import Database
                from src.market_regime import MarketRegime
                db = Database(read_only=True)
                mr = MarketRegime(db)
                regime = mr.get_ai_synthesized_regime()
                self.log_event(
                    f"🌐 AI MACRO PULSE CHECK (4H): Status={regime.get('regime_status')}, "
                    f"BTC Trend={regime.get('btc_trend')}, Multiplier={regime.get('risk_multiplier')}x - {regime.get('reasoning')}"
                )
        except Exception as e:
            self.log_event(f"Warning: Gagal audit AI Macro Pulse: {e}")

    def _check_macro_shock(self, all_mids: Dict[str, float], lifecycle: Dict, live_positions: Dict, actions: List[Dict]):
        """
        Scenario 3: Rapid Macro Shock Defender.
        Triggered when BTC moves > 2.8% within 60 minutes.
        Tightens on-chain stop-losses on counter-trend positions.
        """
        now_ts = time.time()
        btc_px = float(all_mids.get("BTC", 0.0))
        if btc_px <= 0:
            return

        self._btc_history.append((now_ts, btc_px))
        # Keep 1 hour (3600s) window
        self._btc_history = [x for x in self._btc_history if (now_ts - x[0]) <= 3600]

        if len(self._btc_history) < 2:
            return

        oldest_ts, oldest_btc = self._btc_history[0]
        # Require at least 5 minutes of data span to avoid microsecond noise
        if (now_ts - oldest_ts) < 300:
            return

        btc_move_pct = ((btc_px - oldest_btc) / oldest_btc) * 100.0

        if abs(btc_move_pct) >= 2.8 and (now_ts - self._last_macro_shock_ts) >= 3600:
            self._last_macro_shock_ts = now_ts
            try:
                from src.ai_engine import ai_advisor
                open_summary = []
                for c, s in lifecycle.items():
                    if c in live_positions:
                        open_summary.append({
                            "coin": c,
                            "side": s.get("side", "SHORT"),
                            "strategy": s.get("strategy", "SWING"),
                            "entry_px": s.get("entry_px"),
                            "current_px": float(all_mids.get(c, 0.0)),
                            "roe_pct": s.get("roe_pct", 0.0),
                            "unrealized_pnl": s.get("unrealized_pnl", 0.0),
                        })

                diag = ai_advisor.evaluate_macro_shock(btc_move_pct, btc_px, open_summary)
                self.log_event(
                    f"🚨 AI MACRO SHOCK DEFENDER: BTC {btc_move_pct:+.2f}% dlm {int((now_ts - oldest_ts)/60)}m! "
                    f"Level: {diag.get('shock_level')}. Tighten SL: {diag.get('tighten_sl')} - {diag.get('reasoning')}"
                )

                if diag.get("tighten_sl"):
                    target_mode = diag.get("target_sl_mode", "KEEP_CURRENT")
                    is_btc_surge = btc_move_pct > 0
                    for c, s in lifecycle.items():
                        if c not in live_positions:
                            continue
                        pos_side = s.get("side", "SHORT")
                        is_vulnerable = (is_btc_surge and pos_side == "SHORT") or (not is_btc_surge and pos_side == "LONG")
                        if not is_vulnerable:
                            continue

                        cur_sz = float(live_positions[c].get("size", s["initial_sz"]))
                        entry = float(s["entry_px"])
                        cur_sl = float(s.get("current_sl_px", entry))
                        cur_px = float(all_mids.get(c, 0.0))

                        new_sl = None
                        if target_mode == "BREAKEVEN":
                            fee_buf = 0.0009
                            be_px = self.trader.round_price(c, entry * (1 + fee_buf if pos_side == "LONG" else 1 - fee_buf))
                            if pos_side == "SHORT" and be_px < cur_sl:
                                new_sl = be_px
                            elif pos_side == "LONG" and be_px > cur_sl:
                                new_sl = be_px
                        elif target_mode == "TIGHT_50PCT":
                            mid_sl = (cur_px + cur_sl) / 2.0
                            new_sl = self.trader.round_price(c, mid_sl)

                        if new_sl and new_sl != cur_sl:
                            ok = self._move_sl_on_chain(c, s, new_sl, cur_sz)
                            if ok:
                                s["current_sl_px"] = new_sl
                                msg = f"🛡️ MACRO SHOCK DEFENSE: SL {c} ({pos_side}) diperketat on-chain ke ${new_sl} untuk proteksi modal!"
                                self.log_event(msg)
                                actions.append({"type": "MACRO_SHOCK_SL_TIGHTEN", "coin": c, "msg": msg})
            except Exception as e:
                self.log_event(f"Warning: Gagal eksekusi Macro Shock Defender: {e}")

    def _run_autonomous_cycle_if_due(self, actions_taken: List[Dict]):
        """
        Scenario 5: Zero-Touch Autonomous Hunter & Closed-Loop Self-Evaluation.
        Scans & opens Grade A setups when slots and free margin (> reserve %) permit.
        """
        try:
            from src.autonomous_engine import AutonomousEngine
            auto_eng = AutonomousEngine(self.trader)
            res = auto_eng.run_autonomous_hunt_if_due(force=False)
            if res.get("executed"):
                msg = res.get("msg", f"🚀 AUTO-OPEN {res.get('side')} {res.get('coin')}")
                self.log_event(msg, res.get("strategy", "SWING"))
                actions_taken.append({
                    "type": "AUTONOMOUS_OPEN_GRADE_A",
                    "coin": res.get("coin"),
                    "strategy": res.get("strategy", "SWING"),
                    "msg": msg
                })
        except Exception as e:
            self.log_event(f"Warning: Gagal siklus Autonomous Hunter: {e}")

    # ------------------------------------------------------------------
    # MAIN CYCLE
    # ------------------------------------------------------------------
    def check_and_execute(self) -> List[Dict]:
        """
        Performs one check cycle across all monitored positions + Autonomous Hunter & Self-Evaluation.
        Setiap posisi dikelola oleh stream strateginya sendiri (SWING / DAYTRADE).
        """
        now_ts = time.time()
        actions_taken = []

        # Scenario 4: Periodic 4-hour AI Market Regime Pulse Check
        self._audit_macro_pulse_if_needed(now_ts)

        lifecycle = self.sync_active_positions()
        if not lifecycle:
            # Even with 0 open positions, run Autonomous Hunter so it can open Grade A positions!
            self._run_autonomous_cycle_if_due(actions_taken)
            return actions_taken

        acc = self.trader.get_account_state()
        if acc.get("error"):
            return []
        live_positions = {p["coin"]: p for p in acc.get("positions", []) if p.get("side") in ("SHORT", "LONG")}
        all_mids = self.trader.get_all_mids()

        # Scenario 3: AI Macro Shock Defender (Rapid BTC Volatility Surge > 2.8%/1h)
        self._check_macro_shock(all_mids, lifecycle, live_positions, actions_taken)

        for coin, state in list(lifecycle.items()):
            if coin not in live_positions:
                continue

            current_px = float(all_mids.get(coin, 0.0))
            if current_px <= 0:
                continue

            entry_px = state["entry_px"]
            side = state.get("side", live_positions[coin].get("side", "SHORT"))
            state["side"] = side
            if side == "LONG":
                gain_pct = ((current_px - entry_px) / entry_px) * 100
            else:
                gain_pct = ((entry_px - current_px) / entry_px) * 100
            pos_info = live_positions[coin]

            state["last_check"] = datetime.now().isoformat()
            state["last_px"] = current_px
            state["drop_pct"] = round(gain_pct, 2)
            state["gain_pct"] = round(gain_pct, 2)
            state["unrealized_pnl"] = round(float(pos_info.get("unrealized_pnl", 0.0)), 2)
            state["roe_pct"] = round(float(pos_info.get("roe_pct", 0.0)), 2)
            state["margin_used"] = round(float(pos_info.get("margin_used", 0.0)), 2)

            # Update Forward Test Journal on each cycle
            self._ft_update(coin, state, pos_info, current_px)

            if state.get("strategy") == "DAYTRADE":
                self._manage_intraday(coin, state, pos_info, current_px, actions_taken)
            else:
                self._manage_swing(coin, state, pos_info, current_px, actions_taken)

            lifecycle[coin] = state

        self.save_lifecycle_state(lifecycle)

        # Scenario 5: Check if Autonomous Hunter & Self-Evaluation cycle is due
        self._run_autonomous_cycle_if_due(actions_taken)

        # Keep Two-Way Bridge (AGENTS.md) synchronized with live Trading Tools state & War Room chats
        try:
            from src.antigravity_bridge import antigravity_bridge
            antigravity_bridge.sync_agents_md()
        except Exception:
            pass

        if actions_taken:
            try:
                from src.html_reporter import generate_html_report
                generate_html_report()
            except Exception:
                pass
        return actions_taken

    def has_intraday_positions(self) -> bool:
        return any(s.get("strategy") == "DAYTRADE" for s in self.load_lifecycle_state().values())

    # ------------------------------------------------------------------
    # LIVE DASHBOARD
    # ------------------------------------------------------------------
    @staticmethod
    def _display_width(text: str) -> int:
        w = 0
        for ch in text:
            cat = unicodedata.category(ch)
            eaw = unicodedata.east_asian_width(ch)
            if eaw in ('W', 'F'):
                w += 2
            elif ord(ch) >= 0x1F300:
                w += 2
            elif ch in ['⚡', '🔴', '🟢', '✅', '✂️', '🎯', '🚀', '🛑', '⏰', '🪙', '🌊', '🏆', '🛡️']:
                w += 2
            elif cat == 'Mn':
                continue
            else:
                w += 1
        return w

    @staticmethod
    def _pad_str(text: str, target_width: int, align: str = 'left') -> str:
        dw = PositionManager._display_width(text)
        pad = max(0, target_width - dw)
        if align == 'right':
            return (' ' * pad) + text
        elif align == 'center':
            left_pad = pad // 2
            right_pad = pad - left_pad
            return (' ' * left_pad) + text + (' ' * right_pad)
        else:
            return text + (' ' * pad)

    @staticmethod
    def _fmt_price(p: float) -> str:
        if p >= 100:
            return f"{p:.2f}"
        elif p >= 10:
            return f"{p:.3f}"
        elif p >= 1:
            return f"{p:.4f}"
        else:
            return f"{p:.5f}"

    def render_status_lines(self, lifecycle: Dict, now_str: str) -> List[str]:
        lines = [f"\n--- 📊 STATUS LIVE POSISI AKTIF [{now_str}] ---"]
        if not lifecycle:
            lines.append("  Tidak ada posisi terbuka (Mode Cash).")
            lines.append("-" * 68)
            return lines

        cols = [
            ('KOIN', 6, 'center'),
            ('POSISI', 12, 'center'),
            ('MARGIN (SZ)', 16, 'right'),
            ('ENTRY ➔ HARGA', 28, 'left'),
            ('PnL (ROE%)', 16, 'right'),
            ('STOP LOSS (SL)', 20, 'left'),
            ('TP1 / TRIM', 15, 'center'),
            ('TP2 / TARGET', 15, 'center'),
        ]

        col_widths = [c[1] for c in cols]
        top_border = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
        mid_border = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
        bot_border = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

        lines.append(top_border)
        header_cells = [f" {self._pad_str(c[0], c[1], 'center')} " for c in cols]
        lines.append("│" + "│".join(header_cells) + "│")
        lines.append(mid_border)

        tot_pnl = 0.0
        tot_margin = 0.0
        time_stops = []

        for coin, s in lifecycle.items():
            side = s.get("side", "SHORT")
            strat = s.get("strategy") or position_tags.get_strategy(coin) or "SWING"
            entry = float(s.get("entry_px", 0.0))
            cur_px = float(s.get("last_px", entry))
            sz = float(s.get("remaining_sz") or s.get("initial_sz", 0.0))
            lev = int(s.get("leverage", 2))
            margin = float(s.get("margin_used", (sz * entry) / lev if lev > 0 else 0.0))
            pnl = float(s.get("unrealized_pnl", 0.0))
            roe = float(s.get("roe_pct", 0.0))

            tot_pnl += pnl
            tot_margin += margin

            is_long = (side == "LONG")
            move_pct = s.get("gain_pct" if is_long else "drop_pct", 0.0)

            t1_done = s.get("trimmed_10pct", False)
            t2_done = s.get("tp_20pct", False)
            early_be = s.get("early_be_shield", False)

            trim_p = float(s.get("tp1_pct", 7.0 if strat == "SWING" else 3.0))
            tp2_p = float(s.get("tp2_pct", 18.0 if strat == "SWING" else 6.0))
            sl_p = float(s.get("sl_pct", 10.0 if strat == "SWING" else 3.0))

            t1_target = float(s.get("tp1_px", 0.0))
            t2_target = float(s.get("tp2_px", 0.0))
            sl_px = float(s.get("current_sl_px", 0.0))

            srv = "🟢" if s.get("native_sl_active") else "🟡"
            if t1_done:
                sl_label = f"${self._fmt_price(sl_px)} [BE {srv}]"
            elif early_be:
                sl_label = f"${self._fmt_price(sl_px)} [🛡️BE {srv}]"
            else:
                sl_sign = "-" if is_long else "+"
                sl_label = f"${self._fmt_price(sl_px)} [{sl_sign}{sl_p:.1f}% {srv}]"

            side_icon = "🔴 SHORT" if side == "SHORT" else "🟢 LONG "
            st_icon = "⚡" if strat == "DAYTRADE" else "🌊"
            pos_badge = f"{side_icon} {st_icon}"

            sz_fmt = f"{sz:,.4f}".rstrip('0').rstrip('.') if sz < 1 else f"{sz:,.1f}"
            margin_str = f"${margin:,.2f} 🧹DUST" if margin < 3.0 else f"${margin:,.2f} ({sz_fmt})"

            e_str = self._fmt_price(entry)
            c_str = self._fmt_price(cur_px)
            px_str = f"${e_str} ➔ ${c_str} ({move_pct:+.1f}%)"

            pnl_sign = "+" if pnl >= 0 else "-"
            pnl_str = f"{pnl_sign}${abs(pnl):.2f} ({roe:+.2f}%)"

            dist_tp1 = max(0.0, trim_p - move_pct)
            dist_tp2 = max(0.0, tp2_p - move_pct)
            tp1_str = "✅ SELESAI" if t1_done else f"${self._fmt_price(t1_target)} ({dist_tp1:.1f}%)"
            if t2_done:
                r3_target = float(s.get("runner_px") or 0.0)
                if r3_target > 0:
                    tp2_str = f"🚀 RUN (${self._fmt_price(r3_target)})"
                else:
                    tp2_str = "✅ SELESAI"
            else:
                tp2_str = f"${self._fmt_price(t2_target)} ({dist_tp2:.1f}%)"

            # Check time-stop remaining
            remain_h = max(0.0, float(s.get("max_hold_hours", 6 if strat == "DAYTRADE" else 72)) - self._age_hours(s))
            if strat == "DAYTRADE":
                time_stops.append(f"{coin} (⚡ sisa {remain_h:.1f}j)")
            else:
                time_stops.append(f"{coin} (🌊 sisa {remain_h:.1f}j)")

            cells = [
                f" {self._pad_str(coin, cols[0][1], cols[0][2])} ",
                f" {self._pad_str(pos_badge, cols[1][1], cols[1][2])} ",
                f" {self._pad_str(margin_str, cols[2][1], cols[2][2])} ",
                f" {self._pad_str(px_str, cols[3][1], cols[3][2])} ",
                f" {self._pad_str(pnl_str, cols[4][1], cols[4][2])} ",
                f" {self._pad_str(sl_label, cols[5][1], cols[5][2])} ",
                f" {self._pad_str(tp1_str, cols[6][1], cols[6][2])} ",
                f" {self._pad_str(tp2_str, cols[7][1], cols[7][2])} ",
            ]
            lines.append("│" + "│".join(cells) + "│")

        lines.append(mid_border)
        tot_roe = (tot_pnl / tot_margin * 100.0) if tot_margin > 0 else 0.0
        tot_sign = "+" if tot_pnl >= 0 else "-"
        tot_pnl_str = f"{tot_sign}${abs(tot_pnl):.2f} ({tot_roe:+.2f}%)"
        tot_margin_str = f"${tot_margin:.2f}"

        sum_cells = [
            f" {self._pad_str('TOTAL', cols[0][1], 'center')} ",
            f" {self._pad_str(f'{len(lifecycle)} Posisi', cols[1][1], 'center')} ",
            f" {self._pad_str(tot_margin_str, cols[2][1], 'right')} ",
            f" {self._pad_str('Net Floating PnL ➔', cols[3][1], 'right')} ",
            f" {self._pad_str(tot_pnl_str, cols[4][1], 'right')} ",
            f" {self._pad_str('Proteksi On-Chain', cols[5][1], 'center')} ",
            f" {self._pad_str('-', cols[6][1], 'center')} ",
            f" {self._pad_str('-', cols[7][1], 'center')} ",
        ]
        lines.append("│" + "│".join(sum_cells) + "│")
        lines.append(bot_border)
        if time_stops:
            lines.append(f"  ⏰ Time-Stop Auto-Close: {' | '.join(time_stops)}")
        lines.append("  💡 Keterangan: 🟢 SL On-Chain Hyperliquid | BE = Breakeven (0% Risiko) | ⚡ Intraday | 🌊 Swing")
        try:
            from src.autonomous_engine import AutonomousEngine
            auto_eng = AutonomousEngine(self.trader)
            st = auto_eng.load_state()
            gov = auto_eng.get_live_governor_snapshot()
            th = gov.get("thermostat", {})
            lines.append("-" * 68)
            lines.append(
                f"  🤖 AI Mandiri: {th.get('badge', '🟢 OPTIMAL')} | "
                f"Cadangan {gov.get('reserve_pct', 15):.0f}% (${gov.get('reserve_usd', 15):.2f}) | "
                f"Siap Tembak: ${gov.get('free_above_reserve', 0):.2f} | "
                f"Slot: {gov.get('active_count', 0)}/{gov.get('max_positions', 5)}"
            )
            lines.append(f"  📡 Status Hunt ({st.get('last_hunt_time', '-')}): {st.get('last_hunt_status', '-')[:90]}")
        except Exception:
            pass
        lines.append("-" * 68)
        return lines

    @staticmethod
    def _wait_with_exit_listener(seconds: float) -> bool:
        """
        Wait for `seconds` while listening for single keypress (q, 0, ENTER, ESC, etc.).
        Returns True if user requested exit, False if timeout expired.
        """
        if not sys.stdin.isatty() or termios is None or tty is None:
            try:
                time.sleep(seconds)
            except KeyboardInterrupt:
                return True
            return False

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            start = time.time()
            prompt_shown = False
            while (time.time() - start) < seconds:
                remaining = int(seconds - (time.time() - start))
                sys.stdout.write(f"\r  [🔄 Refresh dalam {remaining}s... Tekan [Q] atau [ENTER] untuk kembali ke Menu Utama] ")
                sys.stdout.flush()
                prompt_shown = True

                r, _, _ = select.select([sys.stdin], [], [], 0.5)
                if r:
                    ch = sys.stdin.read(1)
                    if ch in ['q', 'Q', '0', '\n', '\r', '\x1b', 'x', 'X']:
                        sys.stdout.write("\r" + " " * 85 + "\r")
                        sys.stdout.flush()
                        return True
            if prompt_shown:
                sys.stdout.write("\r" + " " * 85 + "\r")
                sys.stdout.flush()
            return False
        except KeyboardInterrupt:
            return True
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def run_live_guard(self, poll_interval: Optional[int] = None):
        """
        Interactive live dashboard that monitors positions and auto-triggers orders.
        Poll otomatis 10 detik bila ada posisi intraday, 25 detik bila hanya swing.
        Keluar kapan saja dengan menekan [Q], [ENTER], [0], [Ctrl+C], atau [Ctrl+Z].
        """
        print("\n" + "=" * 65)
        print("    🛡️  SHORT & LONG HUNTER AUTO-PILOT POSITION GUARD")
        print("=" * 65)
        print("🌊 SWING    : SL ±10% | Trim 50% @±10% (SL→BE) | 30% @±20% | Runner @±35% | Max 72j")
        print("⚡ INTRADAY : SL teknis 15m | TP1 1R (Trim 50%, SL→BE) | TP2 2R | Auto-close 6j")
        print("Server Trigger: Stop Market on-chain (aman jika laptop mati)")
        print("-" * 65)
        print("👉 Tekan [Q] atau [ENTER] kapan saja untuk kembali ke Menu Utama (atau [0] / Ctrl+C)\n")

        orig_tstp = None
        try:
            if hasattr(signal, "SIGTSTP"):
                def _handle_tstp(signum, frame):
                    raise KeyboardInterrupt()
                orig_tstp = signal.signal(signal.SIGTSTP, _handle_tstp)

            while True:
                actions = self.check_and_execute()
                for a in actions:
                    print("\n" + "!" * 65)
                    print(f"  [EKSEKUSI OTOMATIS - {a.get('strategy', '?')}] {a['msg']}")
                    print("!" * 65 + "\n")

                lifecycle = self.load_lifecycle_state()
                now_str = datetime.now().strftime("%H:%M:%S")

                if not lifecycle:
                    print(f"[{now_str}] Tidak ada posisi aktif di Hyperliquid. Menunggu posisi baru... (Tekan Q atau ENTER untuk keluar)", end="\r")
                else:
                    print("\n".join(self.render_status_lines(lifecycle, now_str)))

                interval = poll_interval or (10 if self.has_intraday_positions() else 25)
                should_exit = self._wait_with_exit_listener(interval)
                if should_exit:
                    print("\n[✓] Keluar dari Live Guard. Kembali ke Menu Utama...")
                    time.sleep(0.5)
                    break
        except KeyboardInterrupt:
            print("\n\n[✓] Live Guard dihentikan. Kembali ke Menu Utama...")
            time.sleep(0.5)
        finally:
            if orig_tstp is not None and hasattr(signal, "SIGTSTP"):
                try:
                    signal.signal(signal.SIGTSTP, orig_tstp)
                except Exception:
                    pass

    # --- DAEMON (BACKGROUND RUNNER) METHODS ---
    @staticmethod
    def is_daemon_running() -> Optional[int]:
        if not os.path.exists(PID_FILE):
            return None
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            # Check if process is running
            os.kill(pid, 0)
            return pid
        except PermissionError:
            # Process definitely exists (running outside sandboxed subshell)
            return pid
        except (ProcessLookupError, ValueError):
            return None
        except OSError:
            return None

    @staticmethod
    def start_daemon() -> Dict:
        pid = PositionManager.is_daemon_running()
        if pid:
            return {"success": True, "message": f"Auto-Pilot & Autonomous AI sudah berjalan (PID: {pid}).", "pid": pid}

        # Launch detached background process (wrapped with macOS caffeinate -i so system won't sleep unattended)
        python_bin = sys.executable
        if os.path.exists("/usr/bin/caffeinate"):
            cmd = ["/usr/bin/caffeinate", "-i", python_bin, "-m", "src.position_manager", "--daemon-loop"]
        else:
            cmd = [python_bin, "-m", "src.position_manager", "--daemon-loop"]

        with open(LOG_FILE, "a") as log_f:
            p = subprocess.Popen(
                cmd,
                cwd=ROOT_DIR,
                stdout=log_f,
                stderr=log_f,
                start_new_session=True
            )

        with open(PID_FILE, "w") as f:
            f.write(str(p.pid))
        time.sleep(0.3)
        actual_pid = PositionManager.is_daemon_running() or p.pid

        return {"success": True, "message": f"Auto-Pilot & Autonomous AI berhasil dinyalakan 24/7! (PID: {actual_pid})", "pid": actual_pid}

    @staticmethod
    def stop_daemon() -> Dict:
        pid = PositionManager.is_daemon_running()
        if not pid:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            return {"success": True, "message": "Auto-Pilot Background memang sedang tidak aktif."}

        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            return {"success": True, "message": f"Auto-Pilot Background (PID: {pid}) berhasil dihentikan."}
        except Exception as e:
            return {"success": False, "message": f"Gagal menghentikan proses: {e}"}


    @classmethod
    def ensure_daemon_running_if_positions(cls) -> bool:
        """
        Watchdog check:
        If active positions exist on Hyperliquid OR Autonomous AI Mode is enabled,
        auto-starts the daemon immediately if dead.
        """
        trader = HyperliquidTrader()
        if not trader.is_configured():
            return False

        if cls.is_daemon_running():
            return True

        try:
            from src.autonomous_engine import AutonomousEngine
            auto_enabled = AutonomousEngine(trader).load_state().get("enabled", True)
            state = trader.get_account_state()
            active_pos = [p for p in state.get("positions", []) if p.get("side") in ("SHORT", "LONG")]
            if active_pos or auto_enabled:
                res = cls.start_daemon()
                cls(trader).log_event(f"🛡️ Watchdog: Menghidupkan Auto-Pilot & Autonomous AI otomatis (PID: {res.get('pid')})")
                return True
        except Exception:
            pass
        return False


if __name__ == "__main__":
    if "--ensure-running" in sys.argv:
        PositionManager.ensure_daemon_running_if_positions()
        sys.exit(0)

    if "--daemon-loop" in sys.argv:
        # Write PID file so status checks know the daemon is running
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        pm = PositionManager()
        pm.log_event(f"Auto-Pilot Background Daemon (Dual-Stream) dimulai (PID: {os.getpid()}).")
        try:
            while True:
                try:
                    pm.check_and_execute()
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str:
                        pm.log_event("Waspada: Rate limit 429 dari Hyperliquid, istirahat 15 detik...")
                        time.sleep(15)
                    else:
                        pm.log_event(f"Error pada loop daemon: {e}")
                # Intraday butuh reaksi lebih cepat (TP 1R hanya ~3%)
                time.sleep(10 if pm.has_intraday_positions() else 25)
        finally:
            if os.path.exists(PID_FILE):
                try:
                    os.remove(PID_FILE)
                except Exception:
                    pass
