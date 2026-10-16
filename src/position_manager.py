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
from typing import Dict, List, Optional
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
        """Upgrades legacy lifecycle entries (sebelum dual-stream) dengan field strategi & plan."""
        side = state.get("side", side)
        state["side"] = side
        if state.get("strategy") and state.get("tp1_px"):
            return state
        strategy = position_tags.get_strategy(coin)
        entry = float(state["entry_px"])
        plan = position_tags.build_plan(strategy, entry, sl_px=float(state.get("initial_sl_px") or 0) or None, side=side)
        if not position_tags.get_tag(coin):
            position_tags.tag_position(coin, strategy, plan, meta={"source": "LEGACY_BACKFILL", "side": side})
        state.update({
            "strategy": strategy,
            "side": side,
            "tp1_px": plan["tp1_px"],
            "tp2_px": plan["tp2_px"],
            "runner_px": plan.get("runner_px"),
            "sl_pct": plan["sl_pct"],
            "tp1_pct": plan["tp1_pct"],
            "tp2_pct": plan["tp2_pct"],
            "max_hold_hours": plan["max_hold_hours"],
        })
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
                if side == "LONG":
                    fee_buf = prof.get("fee_roundtrip_pct", 0.09) / 100.0 if strategy == "DAYTRADE" else 0.0
                    be_px = self.trader.round_price(coin, entry_px * (1 + fee_buf))
                else:
                    fee_buf = prof.get("fee_roundtrip_pct", 0.09) / 100.0 if strategy == "DAYTRADE" else 0.0
                    be_px = self.trader.round_price(coin, entry_px * (1 - fee_buf))
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

            # Guardrails: For SHORT, SL must never be lower than entry_px unless trimmed_10pct.
            #             For LONG, SL must never be higher than entry_px unless trimmed_10pct.
            from src.position_tags import build_plan
            if side == "SHORT":
                if not state.get("trimmed_10pct") and target_sl_px < entry_px:
                    target_sl_px = float(state.get("initial_sl_px") or 0.0)
                    if target_sl_px < entry_px:
                        plan = build_plan(strategy, entry_px, side="SHORT")
                        target_sl_px = self.trader.round_price(coin, plan["sl_px"])
                    state["current_sl_px"] = target_sl_px
            else:  # LONG
                if not state.get("trimmed_10pct") and (target_sl_px > entry_px or target_sl_px <= 0):
                    target_sl_px = float(state.get("initial_sl_px") or 0.0)
                    if target_sl_px > entry_px or target_sl_px <= 0:
                        plan = build_plan(strategy, entry_px, side="LONG")
                        target_sl_px = self.trader.round_price(coin, plan["sl_px"])
                    state["current_sl_px"] = target_sl_px

            # Filter out any anomalous SL orders
            if not state.get("trimmed_10pct") and coin_sl_orders:
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

    # ------------------------------------------------------------------
    # STREAM 1: SWING
    # ------------------------------------------------------------------
    def _manage_swing(self, coin, state, pos_info, current_px, actions):
        prof = get_profile("SWING")
        entry_px = state["entry_px"]
        initial_sz = state["initial_sz"]
        side = state.get("side", "SHORT")
        sz_dec = self.trader.get_asset_precision(coin)

        if side == "LONG":
            profit_pct = ((current_px - entry_px) / entry_px) * 100
            adverse_pct = ((entry_px - current_px) / entry_px) * 100
            tp1_hit = profit_pct >= prof["trim_pct"]
            tp2_hit = profit_pct >= prof["tp2_pct"]
            runner_hit = bool(prof.get("runner_pct") and profit_pct >= prof["runner_pct"])
            sl_hit = current_px <= state["current_sl_px"]
            trailing_sl_px = self.trader.round_price(coin, entry_px * 1.10)
        else:  # SHORT
            profit_pct = ((entry_px - current_px) / entry_px) * 100
            adverse_pct = ((current_px - entry_px) / entry_px) * 100
            tp1_hit = profit_pct >= prof["trim_pct"]
            tp2_hit = profit_pct >= prof["tp2_pct"]
            runner_hit = bool(prof.get("runner_pct") and profit_pct >= prof["runner_pct"])
            sl_hit = current_px >= state["current_sl_px"]
            trailing_sl_px = self.trader.round_price(coin, entry_px * 0.90)

        max_hold = state.get("max_hold_hours", prof["max_hold_hours"])

        # RULE 0: Time-stop 72 jam
        if prof.get("time_exit_enabled") and self._age_hours(state) >= max_hold:
            msg = f"⏰ SWING TIME-STOP {max_hold}J ({self._age_hours(state):.1f} jam): {coin} ({side}) ditutup. Gerak: {profit_pct:+.2f}%"
            if self._close_full(coin, state, pos_info, current_px, f"SWING_TIME_STOP_{max_hold}H", msg, "TIME_EXIT", actions):
                return

        # RULE 1: Trimming 1 (±10%)
        if tp1_hit and not state.get("trimmed_10pct"):
            trim_sz = initial_sz * 0.50
            trim_sz = round(trim_sz, sz_dec) if sz_dec > 0 else int(trim_sz)
            be_px = self.trader.round_price(coin, entry_px)
            ok, remaining = self._partial_close_and_move_sl(coin, state, trim_sz, be_px)
            if ok:
                state["trimmed_10pct"] = True
                msg = f"✂️ TRIMMING 1 SUKSES ({profit_pct:.1f}%): 50% posisi ({trim_sz} {coin} {side}) ditutup! SL digeser ke Breakeven (${be_px}) di bursa. 0% Risiko!"
                self.log_event(msg, "SWING")
                self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                actions.append({"type": "TRIMMING_1", "coin": coin, "strategy": "SWING", "msg": msg})

        # RULE 2: Target 2 (±20%)
        elif tp2_hit and not state.get("tp_20pct"):
            tp2_sz = initial_sz * 0.30
            tp2_sz = round(tp2_sz, sz_dec) if sz_dec > 0 else int(tp2_sz)
            ok, remaining = self._partial_close_and_move_sl(coin, state, tp2_sz, trailing_sl_px)
            if ok:
                state["tp_20pct"] = True
                msg = f"🎯 TARGET 2 SUKSES ({profit_pct:.1f}%): 30% posisi ({tp2_sz} {coin} {side}) diamankan! Trailing SL dikunci di profit +10% (${trailing_sl_px})."
                self.log_event(msg, "SWING")
                self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                actions.append({"type": "TARGET_2", "coin": coin, "strategy": "SWING", "msg": msg})

        # RULE 3: Runner (±35%)
        elif runner_hit and not state.get("runner_35pct"):
            msg = f"🚀 RUNNER SUKSES ({profit_pct:.1f}%): Sisa posisi {coin} ({side}) ditutup dengan profit maksimal!"
            if self._close_full(coin, state, pos_info, current_px, "RUNNER_35_MAX_PROFIT", msg, "RUNNER_35", actions):
                state["runner_35pct"] = True

        # RULE 4: Local SL check (backup dari SL on-chain)
        elif sl_hit:
            is_be = state.get("trimmed_10pct", False)
            msg = (f"🛡️ BREAKEVEN DEFENSE: Harga kembali ke modal (${entry_px}). Sisa {coin} ({side}) ditutup tanpa rugi!"
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

        # RULE 1: Gap langsung tembus TP2 sebelum TP1 tereksekusi -> tutup penuh
        if tp2_hit and not state.get("tp_20pct"):
            msg = f"🎯 INTRADAY TP2 ({prof['tp2_r']}R) TERCAPAI ({profit_pct:.1f}%): Sisa posisi {coin} ({side}) ditutup penuh!"
            if self._close_full(coin, state, pos_info, current_px, "INTRADAY_TP2_2R", msg, "TARGET_2", actions):
                state["tp_20pct"] = True
            return

        # RULE 2: TP1 (1.0R) -> Trim 50% & SL ke BE (+ buffer fee agar benar-benar 0 rugi)
        if tp1_hit and not state.get("trimmed_10pct"):
            trim_sz = initial_sz * 0.50
            trim_sz = round(trim_sz, sz_dec) if sz_dec > 0 else int(trim_sz)
            ok, remaining = self._partial_close_and_move_sl(coin, state, trim_sz, be_px)
            if ok:
                state["trimmed_10pct"] = True
                msg = f"✂️ INTRADAY TP1 ({prof['tp1_r']}R) SUKSES ({profit_pct:.1f}%): 50% {coin} ({side}) ditutup! SL digeser ke BE+fee (${be_px})."
                self.log_event(msg, "DAYTRADE")
                self._ft_update(coin, state, pos_info, current_px, size=remaining, note=msg)
                actions.append({"type": "TRIMMING_1", "coin": coin, "strategy": "DAYTRADE", "msg": msg})
            return

        # RULE 3: Local SL check (backup dari SL on-chain)
        if sl_hit:
            is_be = state.get("trimmed_10pct", False)
            msg = (f"🛡️ INTRADAY BE DEFENSE: {coin} ({side}) kembali ke modal setelah TP1. Sisa ditutup tanpa rugi."
                   if is_be else f"🛑 INTRADAY SL TEKNIS HIT (-{adverse_pct:.1f}%): Cut loss {coin} ({side}).")
            self._close_full(coin, state, pos_info, current_px,
                             "INTRADAY_BE_DEFENSE" if is_be else "INTRADAY_SL_HIT", msg, "STOP_LOSS", actions)

    # ------------------------------------------------------------------
    # MAIN CYCLE
    # ------------------------------------------------------------------
    def check_and_execute(self) -> List[Dict]:
        """
        Performs one check cycle across all monitored positions.
        Setiap posisi dikelola oleh stream strateginya sendiri (SWING / DAYTRADE).
        """
        lifecycle = self.sync_active_positions()
        if not lifecycle:
            return []

        acc = self.trader.get_account_state()
        if acc.get("error"):
            return []
        live_positions = {p["coin"]: p for p in acc.get("positions", []) if p.get("side") in ("SHORT", "LONG")}
        all_mids = self.trader.get_all_mids()

        actions_taken = []

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

            trim_p = float(s.get("tp1_pct", 10.0 if strat == "SWING" else 3.0))
            tp2_p = float(s.get("tp2_pct", 20.0 if strat == "SWING" else 6.0))
            sl_p = float(s.get("sl_pct", 10.0 if strat == "SWING" else 3.0))

            t1_target = float(s.get("tp1_px", 0.0))
            t2_target = float(s.get("tp2_px", 0.0))
            sl_px = float(s.get("current_sl_px", 0.0))

            srv = "🟢" if s.get("native_sl_active") else "🟡"
            if t1_done:
                sl_label = f"${self._fmt_price(sl_px)} [BE {srv}]"
            else:
                sl_sign = "-" if is_long else "+"
                sl_label = f"${self._fmt_price(sl_px)} [{sl_sign}{sl_p:.1f}% {srv}]"

            side_icon = "🔴 SHORT" if side == "SHORT" else "🟢 LONG "
            st_icon = "⚡" if strat == "DAYTRADE" else "🌊"
            pos_badge = f"{side_icon} {st_icon}"

            sz_fmt = f"{sz:,.4f}".rstrip('0').rstrip('.') if sz < 1 else f"{sz:,.1f}"
            margin_str = f"${margin:,.2f} ({sz_fmt})"

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
            return {"success": True, "message": f"Auto-Pilot Background sudah berjalan (PID: {pid}).", "pid": pid}

        # Launch detached background process
        python_bin = sys.executable
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

        return {"success": True, "message": f"Auto-Pilot Background berhasil dinyalakan! (PID: {p.pid})", "pid": p.pid}

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
        If active SHORT positions exist on Hyperliquid and daemon is dead,
        auto-starts the daemon immediately.
        """
        trader = HyperliquidTrader()
        if not trader.is_configured():
            return False

        if cls.is_daemon_running():
            return True

        try:
            state = trader.get_account_state()
            shorts = [p for p in state.get("positions", []) if p.get("side") == "SHORT"]
            if shorts:
                res = cls.start_daemon()
                cls(trader).log_event(f"🛡️ Watchdog: Mendeteksi {len(shorts)} posisi aktif, Auto-Pilot otomatis dihidupkan (PID: {res.get('pid')})")
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
