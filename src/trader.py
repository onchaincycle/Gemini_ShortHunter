"""
Hyperliquid Automated Trader Module:
Handles automated or 1-click execution for SHORT positions with built-in Stop-Loss and Take-Profit.
Supports Testnet and Mainnet via secure Agent Wallets (cannot withdraw funds).
"""

import os
from typing import Dict, Optional
import eth_account
from dotenv import load_dotenv
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()

# Global shared clients to eliminate duplicate Info and Exchange initializations
_SHARED_INFO = None
_SHARED_EXCHANGE = None
_SHARED_EXCHANGE_KEY = None

class HyperliquidTrader:
    def __init__(self,
                 account_address: Optional[str] = None,
                 secret_key: Optional[str] = None,
                 testnet: bool = True):
        self.account_address = account_address or os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS")
        self.secret_key = secret_key or os.getenv("HYPERLIQUID_SECRET_KEY")
        self.testnet = testnet if os.getenv("HYPERLIQUID_TESTNET") is None else (os.getenv("HYPERLIQUID_TESTNET", "true").lower() == "true")
        
        self.base_url = constants.TESTNET_API_URL if self.testnet else constants.MAINNET_API_URL
        
        global _SHARED_INFO, _SHARED_EXCHANGE, _SHARED_EXCHANGE_KEY
        import time
        if _SHARED_INFO is None:
            try:
                _SHARED_INFO = Info(self.base_url, skip_ws=True)
            except Exception:
                time.sleep(1.0)
                _SHARED_INFO = Info(self.base_url, skip_ws=True)
        self.info = _SHARED_INFO
        self.exchange = None

        # Rate-limiting & burst protection cache (TTL: 6 seconds)
        self._state_cache = None
        self._state_cache_time = 0.0
        self._mids_cache = {}
        self._mids_cache_time = 0.0
        self._orders_cache = []
        self._orders_cache_time = 0.0
        self._meta_cache = None
        self._meta_cache_time = 0.0
        self._cache_ttl = 6.0

        if self.is_configured():
            if _SHARED_EXCHANGE is None or _SHARED_EXCHANGE_KEY != self.secret_key:
                account = eth_account.Account.from_key(self.secret_key)
                _SHARED_EXCHANGE = Exchange(
                    account,
                    self.base_url,
                    account_address=self.account_address
                )
                _SHARED_EXCHANGE_KEY = self.secret_key
            self.exchange = _SHARED_EXCHANGE

    def get_meta(self, force_refresh: bool = False) -> Dict:
        """Returns perp universe metadata with 10-minute caching to eliminate heavy redundant API calls."""
        import time
        now = time.time()
        if not force_refresh and self._meta_cache and (now - self._meta_cache_time) < 600.0:
            return self._meta_cache
        try:
            meta = self.info.meta()
            self._meta_cache = meta
            self._meta_cache_time = now
            return meta
        except Exception:
            return self._meta_cache or {}

    def is_configured(self) -> bool:
        return bool(self.account_address and self.secret_key and len(self.secret_key) >= 64)

    def get_account_state(self, force_refresh: bool = False) -> Dict:
        """Returns account equity, free collateral, spot balance, and open positions with 6s TTL caching."""
        if not self.is_configured():
            return {"configured": False, "equity": 0.0, "available": 0.0, "positions": []}

        import time
        now = time.time()
        if not force_refresh and self._state_cache is not None and (now - self._state_cache_time) < self._cache_ttl:
            return self._state_cache

        try:
            # 1. Perp clearinghouse state
            user_state = self.info.user_state(self.account_address)
            margin = user_state.get("marginSummary", {})
            perp_equity = float(margin.get("accountValue", 0.0))
            perp_margin_used = float(margin.get("totalMarginUsed", 0.0))
            perp_available = float(user_state.get("withdrawable", 0.0))

            # 2. Spot clearinghouse state
            spot_state = self.info.spot_user_state(self.account_address)
            spot_total = 0.0
            spot_hold = 0.0
            for b in spot_state.get("balances", []):
                if b.get("coin") == "USDC":
                    spot_total = float(b.get("total", 0.0))
                    spot_hold = float(b.get("hold", 0.0))

            # In Hyperliquid unified clearinghouse:
            # - spot_total represents total USDC capital of the account
            # - spot_hold represents margin currently in use by active Perp positions
            # - Free margin available to open new trades = max(0, spot_total - spot_hold)
            free_margin = max(0.0, spot_total - spot_hold)
            hl_equity = max(perp_equity, spot_total)
            available_trade = free_margin

            # External Spot & On-Chain Holdings Integration (portfolio.csv / luar Hyperliquid)
            spot_external_invested = 0.0
            spot_external_value = 0.0
            spot_holdings_count = 0
            try:
                from src.spot_macro_vault import SpotMacroVault
                audit = SpotMacroVault.get_spot_portfolio_audit()
                spot_external_invested = float(audit.get("total_invested", 0.0))
                spot_external_value = float(audit.get("total_current_value", 0.0) or spot_external_invested)
                spot_holdings_count = len(audit.get("holdings", []))
            except Exception:
                pass

            grand_total_equity = round(hl_equity + spot_external_value, 2)

            positions = []
            for p in user_state.get("assetPositions", []):
                pos = p.get("position", {})
                sz = float(pos.get("szi", 0.0))
                if sz != 0:
                    positions.append({
                        "coin": pos.get("coin"),
                        "size": abs(sz),
                        "side": "SHORT" if sz < 0 else "LONG",
                        "entry_px": float(pos.get("entryPx", 0.0)),
                        "pos_value": float(pos.get("positionValue", 0.0)),
                        "margin_used": float(pos.get("marginUsed", 0.0)),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0.0)),
                        "roe_pct": float(pos.get("returnOnEquity", 0.0)) * 100,
                        "liq_px": float(pos.get("liquidationPx", 0.0) or 0.0),
                        "leverage": pos.get("leverage", {}).get("value", 2)
                    })

            res = {
                "configured": True,
                "testnet": self.testnet,
                "equity": round(hl_equity, 2),  # Saldo Tradable Murni di Hyperliquid (dasar sizing & margin bursa)
                "hl_equity": round(hl_equity, 2),
                "total_portfolio_equity": grand_total_equity,  # Net Worth Portofolio Gabungan (HL + Spot Luar)
                "grand_total_equity": grand_total_equity,
                "spot_external_value": round(spot_external_value, 2),
                "spot_external_invested": round(spot_external_invested, 2),
                "spot_holdings_count": spot_holdings_count,
                "spot_usdc": round(spot_total, 2),
                "perp_equity": round(perp_equity, 2),
                "margin_used": round(perp_margin_used, 2),
                "free_collateral": round(free_margin, 2),
                "available": round(available_trade, 2),
                "positions": positions
            }
            self._state_cache = res
            self._state_cache_time = now
            return res
        except Exception as e:
            # If rate limited (HTTP 429), return cached state safely instead of crashing
            if self._state_cache is not None:
                return self._state_cache
            return {"configured": True, "error": str(e), "positions": []}

    def get_all_mids(self, force_refresh: bool = False) -> Dict:
        """Returns mid prices with caching to prevent 429 rate limit."""
        import time
        now = time.time()
        if not force_refresh and self._mids_cache and (now - self._mids_cache_time) < self._cache_ttl:
            return self._mids_cache
        try:
            mids = self.info.all_mids()
            self._mids_cache = mids
            self._mids_cache_time = now
            return mids
        except Exception:
            return self._mids_cache or {}

    def get_open_orders(self, force_refresh: bool = False) -> list:
        """Returns open frontend orders with caching to prevent 429 rate limit."""
        import time
        now = time.time()
        if not force_refresh and self._orders_cache and (now - self._orders_cache_time) < self._cache_ttl:
            return self._orders_cache
        try:
            orders = self.info.frontend_open_orders(self.account_address)
            self._orders_cache = orders
            self._orders_cache_time = now
            return orders
        except Exception:
            return self._orders_cache or []



    def execute_short(self,
                      coin: str,
                      usd_size: float = 50.0,
                      leverage: int = 2,
                      sl_pct: float = 10.0,
                      tp_pct: float = 20.0,
                      sl_px: Optional[float] = None,
                      strategy: str = "SWING") -> Dict:
        """
        Executes a Short Position on Hyperliquid:
        1. Sets cross/isolated leverage (max 3x for safety).
        2. Market sells the coin to open Short.
        3. Sets Stop-Loss trigger order at +sl_pct (atau di harga absolut `sl_px`, mis. pucuk jarum 15m).
        4. Returns target levels sesuai model exit strategi (SWING: % tetap, DAYTRADE: kelipatan R).
        """
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured in .env"}

        # Safety Guard: Cap leverage at 3x for volatile altcoins
        leverage = min(leverage, 3)

        import time
        for attempt in range(1, 4):
            try:
                # 1. Update leverage
                self.exchange.update_leverage(leverage, coin, is_cross=True)
                time.sleep(0.2)

                # 2. Get coin metadata and mark price to calculate exact size
                meta = self.get_meta()
                sz_dec = 0
                for asset in meta.get("universe", []):
                    if asset.get("name") == coin:
                        sz_dec = asset.get("szDecimals", 0)
                        break

                all_mids = self.get_all_mids()
                mark_px = float(all_mids.get(coin, 0.0))
                if mark_px <= 0:
                    mark_px = float(self.info.all_mids().get(coin, 0.0))
                if mark_px <= 0:
                    return {"success": False, "error": f"Invalid mark price for {coin}"}

                # Intraday: tolak jika harga sudah melewati SL teknis (setup basi)
                if sl_px is not None and sl_px <= mark_px:
                    return {"success": False, "error": f"SL teknis ${sl_px} sudah di bawah harga sekarang ${mark_px} (setup kadaluarsa)."}

                raw_sz = usd_size / mark_px
                sz_coin = round(raw_sz, sz_dec) if sz_dec > 0 else int(raw_sz)

                # 3. Market Short Order (is_buy=False)
                order_res = self.exchange.market_open(
                    name=coin,
                    is_buy=False,
                    sz=sz_coin,
                    slippage=0.02
                )

                # Check if Hyperliquid returned an error in the response
                if isinstance(order_res, dict) and order_res.get("status") == "err":
                    return {"success": False, "error": f"Hyperliquid Error: {order_res.get('response')}"}

                if isinstance(order_res, dict) and "response" in order_res:
                    data = order_res.get("response", {}).get("data", {})
                    statuses = data.get("statuses", [])
                    if statuses and "error" in statuses[0]:
                        return {"success": False, "error": f"Order Rejected: {statuses[0]['error']}"}

                # Calculate stop & targets according to the strategy's exit model and pre-tag immediately
                # BEFORE sleeping so background daemon never sees an untagged DAYTRADE position and defaults it to SWING!
                from src.position_tags import build_plan, tag_position
                plan = build_plan(strategy, mark_px, sl_px=sl_px, sl_pct=sl_pct, side="SHORT")
                try:
                    tag_position(coin, plan["strategy"], plan, meta={"side": "SHORT", "source": "TRADER_EXEC_PRETAG"})
                except Exception:
                    pass

                time.sleep(0.2)

                sl_price = self.round_price(coin, plan["sl_px"])
                trim_price = self.round_price(coin, plan["tp1_px"])
                tp2_price = self.round_price(coin, plan["tp2_px"])
                runner_price = self.round_price(coin, plan["runner_px"]) if plan.get("runner_px") else None

                # Auto-place Native Server-Side Stop Loss Trigger on Hyperliquid
                sl_trigger_res = self.set_stop_loss_trigger(coin=coin, sz=sz_coin, trigger_px=sl_price)

                # Auto-place Native Server-Side Take Profit 1 (50% size) on Hyperliquid
                tp1_sz = round(sz_coin * 0.50, sz_dec) if sz_dec > 0 else int(sz_coin * 0.50)
                tp1_trigger_res = {"success": False}
                if tp1_sz > 0 and trim_price > 0:
                    time.sleep(0.2)
                    tp1_trigger_res = self.set_take_profit_trigger(coin=coin, sz=tp1_sz, trigger_px=trim_price)

                return {
                    "success": True,
                    "coin": coin,
                    "strategy": plan["strategy"],
                    "plan": plan,
                    "size_usd": usd_size,
                    "size_coin": sz_coin,
                    "entry_est": mark_px,
                    "stop_loss_px": sl_price,
                    "trim_10pct_px": trim_price,
                    "tp_20pct_px": tp2_price,
                    "runner_35pct_px": runner_price,
                    "native_sl_placed": sl_trigger_res.get("success", False),
                    "native_tp1_placed": tp1_trigger_res.get("success", False),
                    "order_result": order_res
                }
            except Exception as e:
                err_str = str(e)
                if "429" in err_str and attempt < 3:
                    wait_sec = 2.0 * attempt
                    print(f"\n  [!] Rate limit CloudFront 429 terdeteksi. Menunggu {wait_sec:.1f}s lalu mencoba ulang (Attempt {attempt}/3)...")
                    time.sleep(wait_sec)
                    continue
                return {"success": False, "error": f"Hyperliquid Rate Limit 429: Server sedang padat, silakan coba 5 detik lagi." if "429" in err_str else str(e)}

    def round_price(self, coin: str, px: float) -> float:
        """
        Formats price according to Hyperliquid perp specification:
        - Maximum 5 significant figures
        - Maximum (6 - szDecimals) decimal places
        """
        try:
            sz_dec = self.get_asset_precision(coin)
            max_decimals = max(0, 6 - sz_dec)
            formatted = float(f"{px:.5g}")
            return round(formatted, max_decimals)
        except Exception:
            return round(px, 4)

    def get_asset_precision(self, coin: str) -> int:
        """Returns szDecimals for coin using cached metadata."""
        try:
            meta = self.get_meta()
            for asset in meta.get("universe", []):
                if asset.get("name") == coin:
                    return asset.get("szDecimals", 0)
        except Exception:
            pass
        return 0

    def get_funding_rate(self, coin: str) -> Dict:
        """
        Fetches live funding rate from Hyperliquid and evaluates squeeze risk.
        Funding rate on Hyperliquid is hourly.
        """
        try:
            meta, ctxs = self.info.meta_and_asset_ctxs()
            for m, c in zip(meta.get("universe", []), ctxs):
                if m.get("name") == coin:
                    funding_hourly = float(c.get("funding", 0.0))
                    funding_8h_pct = funding_hourly * 8 * 100
                    annualized_pct = funding_hourly * 24 * 365 * 100
                    mark_px = float(c.get("markPx", 0.0))

                    # Squeeze risk evaluation
                    # If funding is deeply negative (<-0.01% / hr), shorts pay longs heavily -> squeeze trap!
                    if funding_hourly < -0.0001:
                        risk_level = "HIGH_SQUEEZE_RISK"
                        desc = f"BAHAYA! Funding Rate Sangat Negatif ({funding_8h_pct:.3f}%/8h). Retail terlalu banyak short, rawan Squeeze!"
                    elif funding_hourly < -0.00003:
                        risk_level = "MODERATE_SQUEEZE_RISK"
                        desc = f"Waspada: Funding rate negatif ({funding_8h_pct:.3f}%/8h). Short seller membayar bunga ke Long."
                    elif funding_hourly > 0.00002:
                        risk_level = "EXCELLENT_SHORT"
                        desc = f"SANGAT BAGUS: Longs membayar bunga ke Shorts ({funding_8h_pct:.3f}%/8h). Anda dibayar untuk menahan posisi short!"
                    else:
                        risk_level = "NORMAL"
                        desc = f"Netral / Normal ({funding_8h_pct:.3f}%/8h)."

                    return {
                        "coin": coin,
                        "funding_hourly": funding_hourly,
                        "funding_8h_pct": funding_8h_pct,
                        "annualized_pct": annualized_pct,
                        "mark_price": mark_px,
                        "risk_level": risk_level,
                        "description": desc
                    }
        except Exception as e:
            return {"coin": coin, "funding_hourly": 0.0, "risk_level": "UNKNOWN", "description": str(e)}

        return {"coin": coin, "funding_hourly": 0.0, "risk_level": "NOT_FOUND", "description": "Coin not found in perp universe"}

    @staticmethod
    def get_equity_slot_plan(equity: float, reserve_pct: float = 15.0, thermostat_mult: float = 1.0, strategy: str = "SWING", **kwargs) -> Dict:
        """
        Computes dynamic slot capacity N_max(E), reserve floor (%), dynamic dust/runner threshold,
        and per-slot margin allocation as a pure mathematical function of Total Account Equity (USDC).
        """
        if equity <= 0:
            return {
                "equity": 0.0,
                "reserve_pct": reserve_pct,
                "reserve_usd": 0.0,
                "deployable_usd": 0.0,
                "max_positions": 5,
                "max_swing": 3,
                "max_intraday": 2,
                "slot_base_margin": 14.0,
                "min_slot_margin": 10.5,
                "max_slot_margin": 17.5,
                "dust_threshold_usd": 3.0,
                "runner_exempt_margin_usd": 6.0,
            }

        # Tiered High-Conviction Slot Curve (prevents slot dilution when equity doubles from $100 to $200):
        # - Equity < $120  -> 5 slots (e.g., $100 -> ~$17/slot base)
        # - Equity $120-$260 -> 6 slots (e.g., $200 -> ~$28.10/slot base, Grade A/A+ -> $32-$40/slot!)
        # - Equity $260-$420 -> 7 slots
        # - Equity > $420  -> 8 to 10 slots
        if equity < 120.0:
            max_positions = 5
        elif equity < 260.0:
            max_positions = 6
        elif equity < 420.0:
            max_positions = 7
        else:
            max_positions = min(10, int(5 + (equity / 110.0)))

        # Split between Swing (~65%) and Intraday (~35%)
        max_swing = max(3, int(round(max_positions * 0.65)))
        max_intraday = max(2, max_positions - max_swing)

        reserve_usd = round(equity * (reserve_pct / 100.0), 2)
        deployable_usd = round(max(0.0, equity - reserve_usd), 2)
        slot_base_margin = round(deployable_usd / max_positions, 2)

        min_slot_margin = round(max(12.0, slot_base_margin * 0.80), 2)
        # Allow Grade A / A+ & Under-Deployed Portfolio Boost up to 1.45x of slot_base_margin
        max_slot_margin = round(max(18.0, slot_base_margin * 1.45 * thermostat_mult), 2)

        # Dynamic dust threshold (2.5% of equity, clamped [$3.0, $8.0]) -> ~$4.96 at $198 equity
        dust_threshold_usd = round(max(3.0, min(8.0, equity * 0.025)), 2)
        # Risk-free runner exemption ceiling (50% of slot_base_margin) for positions already trimmed / BE-locked
        runner_exempt_margin_usd = round(slot_base_margin * 0.50, 2)

        return {
            "equity": round(equity, 2),
            "reserve_pct": round(reserve_pct, 1),
            "reserve_usd": reserve_usd,
            "deployable_usd": deployable_usd,
            "max_positions": max_positions,
            "max_swing": max_swing,
            "max_intraday": max_intraday,
            "slot_base_margin": slot_base_margin,
            "min_slot_margin": min_slot_margin,
            "max_slot_margin": max_slot_margin,
            "dust_threshold_usd": dust_threshold_usd,
            "runner_exempt_margin_usd": runner_exempt_margin_usd,
        }

    def calculate_risk_margin(self,
                              equity: float,
                              risk_pct: float = 2.0,
                              sl_pct: float = 10.0,
                              leverage: int = 2,
                              multiplier: float = 1.0,
                              reserve_pct: float = 65.0,
                              free_above_reserve: Optional[float] = None,
                              open_slots_remaining: Optional[int] = None,
                              strategy: Optional[str] = None,
                              available: Optional[float] = None,
                              **kwargs) -> Dict:
        """
        Calculates mathematically sound margin sizing as a function of Total Equity (E),
        anchored directly to the Equity-Scaled Slot Base (`slot_base_margin`) so that
        when account equity doubles (e.g. $100 -> $200), position sizing scales 2x
        proportionally ($17 -> $32-$40 for Grade A/A+) instead of being artificially
        capped at $19.80 by static unadjusted SL risk formulas.
        Also incorporates an Idle-Capital Recycler Boost when legacy positions are small.
        """
        if equity <= 0:
            return {"recommended_margin": 12.0, "base_margin": 12.0, "max_risk_usd": 1.2, "pos_value": 24.0}

        strat = strategy or ("DAYTRADE" if (risk_pct <= 1.2 or sl_pct < 6.0) else "SWING")
        slot_plan = self.get_equity_slot_plan(equity, reserve_pct=reserve_pct, strategy=strat)
        slot_base = slot_plan["slot_base_margin"]
        min_slot = slot_plan["min_slot_margin"]
        max_slot = slot_plan["max_slot_margin"]

        # Effective Risk % accounts for Periodic Step-Trailing SL (Half-Risk @ +2.2% & Early BE @ +4.0%),
        # which reduces realized average loss distance by ~35% compared to raw initial SL.
        effective_sl_pct = max(2.5, sl_pct * 0.68)
        scaled_risk_pct = max(risk_pct, 2.8 if sl_pct >= 8.0 else 1.5)
        base_risk_usd = equity * (scaled_risk_pct / 100.0)
        base_pos_value = base_risk_usd / max(0.005, (effective_sl_pct / 100.0))
        raw_base_margin = base_pos_value / max(1, leverage)

        # Primary Anchor: Use slot_base directly for standard/high-conviction trades so sizing scales 1:1 with equity!
        base_margin = max(min_slot, min(max(raw_base_margin, slot_base), max_slot))

        # Idle-Capital Recycler: If existing positions were opened when balance was smaller ($10-$15),
        # and there is abundant free margin above the 15% reserve, blend in the fair share of idle capital!
        if free_above_reserve is not None and open_slots_remaining is not None and open_slots_remaining > 0:
            fair_idle_slot = free_above_reserve / max(1, open_slots_remaining)
            if fair_idle_slot > base_margin:
                # Blend 50% of the excess idle slot capacity into base_margin (capped by max_slot)
                base_margin = min(max_slot, round((base_margin * 0.55) + (fair_idle_slot * 0.45), 2))

        # Apply AI conviction & thermostat multiplier, clamped to [min_slot, max_slot]
        recommended_margin = round(max(min_slot, min(base_margin * multiplier, max_slot)), 2)

        # Hard Constraint Sizing: Selalu aware & monitor Total Saldo Available vs Buffer 10%
        avail_val = available if available is not None else kwargs.get("available")
        if avail_val is not None:
            try:
                avail_float = float(avail_val)
                buffer_floor = equity * 0.10
                safe_deployable = max(0.0, avail_float - buffer_floor)
                if safe_deployable < recommended_margin and safe_deployable >= min_slot:
                    recommended_margin = round(safe_deployable, 2)
                elif safe_deployable < min_slot and avail_float >= min_slot:
                    recommended_margin = min_slot
            except Exception:
                pass

        pos_value = round(recommended_margin * leverage, 2)
        # Nominal initial SL risk and step-compressed effective risk
        actual_risk_usd = pos_value * (sl_pct / 100.0)
        effective_risk_pct = (actual_risk_usd / equity) * 100.0 if equity > 0 else risk_pct

        return {
            "equity": round(equity, 2),
            "risk_pct": risk_pct,
            "effective_risk_pct": round(effective_risk_pct, 2),
            "max_risk_usd": round(actual_risk_usd, 2),
            "pos_value": round(pos_value, 2),
            "base_margin": round(base_margin, 2),
            "recommended_margin": round(recommended_margin, 2),
            "slot_base_margin": slot_base,
            "max_positions": slot_plan["max_positions"],
            "reserve_pct": slot_plan["reserve_pct"],
            "reserve_usd": slot_plan["reserve_usd"],
            "multiplier": round(multiplier, 2),
            "leverage": leverage
        }

    def close_position(self, coin: str, sz: Optional[float] = None) -> Dict:
        """
        Closes full or partial position using market_close.
        """
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured in .env"}

        try:
            sz_dec = self.get_asset_precision(coin)
            if sz is not None:
                sz = round(sz, sz_dec) if sz_dec > 0 else int(sz)

            order_res = self.exchange.market_close(
                coin=coin,
                sz=sz,
                slippage=0.03
            )

            if isinstance(order_res, dict) and order_res.get("status") == "err":
                return {"success": False, "error": f"Hyperliquid Error: {order_res.get('response')}"}

            return {
                "success": True,
                "coin": coin,
                "closed_sz": sz if sz else "FULL",
                "result": order_res
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute_long(self,
                     coin: str,
                     usd_size: float = 50.0,
                     leverage: int = 2,
                     sl_pct: float = 10.0,
                     tp_pct: float = 20.0,
                     sl_px: Optional[float] = None,
                     strategy: str = "SWING") -> Dict:
        """
        Executes a LONG position on Hyperliquid:
        1. Sets cross/isolated leverage (max 3x for safety).
        2. Market buys the coin to open Long.
        3. Sets native Stop-Loss trigger order at -sl_pct (or absolute technical level sl_px).
        4. Returns target levels according to strategy exit model.
        """
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured in .env"}

        leverage = min(leverage, 3)

        import time
        for attempt in range(1, 4):
            try:
                self.exchange.update_leverage(leverage, coin, is_cross=True)
                time.sleep(0.2)

                meta = self.get_meta()
                sz_dec = 0
                for asset in meta.get("universe", []):
                    if asset.get("name") == coin:
                        sz_dec = asset.get("szDecimals", 0)
                        break

                all_mids = self.get_all_mids()
                mark_px = float(all_mids.get(coin, 0.0))
                if mark_px <= 0:
                    mark_px = float(self.info.all_mids().get(coin, 0.0))
                if mark_px <= 0:
                    return {"success": False, "error": f"Invalid mark price for {coin}"}

                if sl_px is not None and sl_px >= mark_px:
                    return {"success": False, "error": f"SL teknis ${sl_px} sudah di atas harga sekarang ${mark_px} (setup Long kadaluarsa)."}

                raw_sz = usd_size / mark_px
                sz_coin = round(raw_sz, sz_dec) if sz_dec > 0 else int(raw_sz)

                # Market Buy Order (is_buy=True)
                order_res = self.exchange.market_open(
                    name=coin,
                    is_buy=True,
                    sz=sz_coin,
                    slippage=0.02
                )

                if isinstance(order_res, dict) and order_res.get("status") == "err":
                    return {"success": False, "error": f"Hyperliquid Error: {order_res.get('response')}"}

                if isinstance(order_res, dict) and "response" in order_res:
                    data = order_res.get("response", {}).get("data", {})
                    statuses = data.get("statuses", [])
                    if statuses and "error" in statuses[0]:
                        return {"success": False, "error": f"Order Rejected: {statuses[0]['error']}"}

                from src.position_tags import build_plan, tag_position
                plan = build_plan(strategy, mark_px, sl_px=sl_px, sl_pct=sl_pct, side="LONG")
                try:
                    tag_position(coin, plan["strategy"], plan, meta={"side": "LONG", "source": "TRADER_EXEC_PRETAG"})
                except Exception:
                    pass

                time.sleep(0.2)

                sl_price = self.round_price(coin, plan["sl_px"])
                trim_price = self.round_price(coin, plan["tp1_px"])
                tp2_price = self.round_price(coin, plan["tp2_px"])
                runner_price = self.round_price(coin, plan["runner_px"]) if plan.get("runner_px") else None

                # Auto-place Native Server-Side Stop Loss Trigger on Hyperliquid (side="LONG")
                sl_trigger_res = self.set_stop_loss_trigger(coin=coin, sz=sz_coin, trigger_px=sl_price, side="LONG")

                # Auto-place Native Server-Side Take Profit 1 (50% size) on Hyperliquid (side="LONG")
                tp1_sz = round(sz_coin * 0.50, sz_dec) if sz_dec > 0 else int(sz_coin * 0.50)
                tp1_trigger_res = {"success": False}
                if tp1_sz > 0 and trim_price > 0:
                    time.sleep(0.2)
                    tp1_trigger_res = self.set_take_profit_trigger(coin=coin, sz=tp1_sz, trigger_px=trim_price, side="LONG")

                return {
                    "success": True,
                    "coin": coin,
                    "side": "LONG",
                    "strategy": plan["strategy"],
                    "plan": plan,
                    "size_usd": usd_size,
                    "size_coin": sz_coin,
                    "entry_est": mark_px,
                    "stop_loss_px": sl_price,
                    "trim_10pct_px": trim_price,
                    "tp_20pct_px": tp2_price,
                    "runner_35pct_px": runner_price,
                    "native_sl_placed": sl_trigger_res.get("success", False),
                    "native_tp1_placed": tp1_trigger_res.get("success", False),
                    "order_result": order_res
                }
            except Exception as e:
                err_str = str(e)
                if "429" in err_str and attempt < 3:
                    wait_sec = 2.0 * attempt
                    print(f"\n  [!] Rate limit CloudFront 429 terdeteksi. Menunggu {wait_sec:.1f}s lalu mencoba ulang (Attempt {attempt}/3)...")
                    time.sleep(wait_sec)
                    continue
                return {"success": False, "error": f"Hyperliquid Rate Limit 429: Server sedang padat, silakan coba 5 detik lagi." if "429" in err_str else str(e)}

    def set_stop_loss_trigger(self, coin: str, sz: float, trigger_px: float, side: str = "SHORT") -> Dict:
        """
        Places a native Stop Market trigger order directly onto Hyperliquid's validator engine.
        Acts as offline black-swan insurance even if laptop is powered off.
        """
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured"}

        try:
            sz_dec = self.get_asset_precision(coin)
            sz_val = round(sz, sz_dec) if sz_dec > 0 else int(sz)
            trigger_px_val = self.round_price(coin, trigger_px)
            side = str(side or "SHORT").upper()

            # For Short position, closing SL is a BUY (is_buy=True)
            # For Long position, closing SL is a SELL (is_buy=False)
            is_buy = (side == "SHORT")
            limit_px = self.round_price(coin, trigger_px_val * (1.05 if is_buy else 0.95))

            order_type = {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": trigger_px_val,
                    "tpsl": "sl"
                }
            }

            res = self.exchange.order(
                name=coin,
                is_buy=is_buy,
                sz=sz_val,
                limit_px=limit_px,
                order_type=order_type,
                reduce_only=True
            )

            if isinstance(res, dict) and res.get("status") == "err":
                return {"success": False, "error": f"Hyperliquid Trigger Error: {res.get('response')}"}

            if isinstance(res, dict) and "response" in res:
                data = res.get("response", {}).get("data", {})
                statuses = data.get("statuses", [])
                if statuses and "error" in statuses[0]:
                    return {"success": False, "error": f"Trigger Order Rejected: {statuses[0]['error']}"}

            return {"success": True, "coin": coin, "trigger_px": trigger_px_val, "sz": sz_val, "response": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_take_profit_trigger(self, coin: str, sz: float, trigger_px: float, side: str = "SHORT") -> Dict:
        """
        Places a native Take Profit Market trigger order directly onto Hyperliquid's validator engine.
        Executes on-chain even if laptop/Wi-Fi disconnects during a fast move.
        """
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured"}

        try:
            sz_dec = self.get_asset_precision(coin)
            sz_val = round(sz, sz_dec) if sz_dec > 0 else int(sz)
            if sz_val <= 0:
                return {"success": False, "error": f"Invalid TP size {sz_val}"}
            trigger_px_val = self.round_price(coin, trigger_px)
            side = str(side or "SHORT").upper()

            # For Short position, closing TP is a BUY (is_buy=True)
            # For Long position, closing TP is a SELL (is_buy=False)
            is_buy = (side == "SHORT")
            limit_px = self.round_price(coin, trigger_px_val * (1.05 if is_buy else 0.95))

            order_type = {
                "trigger": {
                    "isMarket": True,
                    "triggerPx": trigger_px_val,
                    "tpsl": "tp"
                }
            }

            res = self.exchange.order(
                name=coin,
                is_buy=is_buy,
                sz=sz_val,
                limit_px=limit_px,
                order_type=order_type,
                reduce_only=True
            )

            if isinstance(res, dict) and res.get("status") == "err":
                return {"success": False, "error": f"Hyperliquid TP Trigger Error: {res.get('response')}"}

            if isinstance(res, dict) and "response" in res:
                data = res.get("response", {}).get("data", {})
                statuses = data.get("statuses", [])
                if statuses and "error" in statuses[0]:
                    return {"success": False, "error": f"TP Trigger Rejected: {statuses[0]['error']}"}

            return {"success": True, "coin": coin, "trigger_px": trigger_px_val, "sz": sz_val, "response": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def cancel_order_by_oid(self, coin: str, oid: int) -> Dict:
        """Cancels a single specific order by oid."""
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured"}
        try:
            res = self.exchange.cancel(coin, int(oid))
            return {"success": True, "oid": oid, "response": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def cancel_coin_orders(self, coin: str) -> Dict:
        """
        Cancels all active open / resting orders for a specific coin on Hyperliquid.
        """
        if not self.is_configured():
            return {"success": False, "error": "Hyperliquid API keys not configured"}

        try:
            orders = self.info.frontend_open_orders(self.account_address)
            cancelled = 0
            for o in orders:
                if o.get("coin") == coin:
                    self.exchange.cancel(coin, o["oid"])
                    cancelled += 1
            return {"success": True, "cancelled_count": cancelled}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_all_funding_and_oi(self) -> Dict[str, Dict]:
        """
        Fetches live Funding Rate (1h & 8h annualized) and Open Interest for all perpetuals.
        Uses 15s TTL caching.
        """
        import time
        now = time.time()
        if hasattr(self, "_funding_cache") and self._funding_cache and (now - getattr(self, "_funding_cache_time", 0)) < 15:
            return self._funding_cache

        try:
            meta_and_ctx = self.info.meta_and_asset_ctxs()
            meta = meta_and_ctx[0]
            asset_ctxs = meta_and_ctx[1]
            res = {}
            for i, u in enumerate(meta.get("universe", [])):
                coin = u.get("name")
                if i < len(asset_ctxs):
                    ctx = asset_ctxs[i]
                    f_1h = float(ctx.get("funding", 0.0)) * 100
                    f_8h = f_1h * 8
                    oi = float(ctx.get("openInterest", 0.0))
                    px = float(ctx.get("markPx", 0.0))
                    res[coin] = {
                        "funding_1h_pct": f_1h,
                        "funding_8h_pct": f_8h,
                        "open_interest": oi,
                        "mark_price": px,
                        "is_negative_funding": f_1h < 0.0,
                        "is_high_squeeze_risk": f_1h < -0.015  # < -0.015% per hour = high short squeeze risk
                    }
            self._funding_cache = res
            self._funding_cache_time = now
            return res
        except Exception as e:
            return {}

    def get_funding_rate(self, coin: str) -> Dict:
        """Returns funding info for a specific coin."""
        all_funding = self.get_all_funding_and_oi()
        return all_funding.get(coin, {
            "funding_1h_pct": 0.0,
            "funding_8h_pct": 0.0,
            "open_interest": 0.0,
            "mark_price": 0.0,
            "is_negative_funding": False,
            "is_high_squeeze_risk": False
        })


def categorize_trigger_order(order: dict, position_side: str = "SHORT", entry_px: float = 0.0) -> str:
    """
    Categorizes a Hyperliquid trigger order as 'STOP_LOSS', 'TAKE_PROFIT', or 'UNKNOWN'.

    For SHORT positions:
      - Trigger price > entry_px is a STOP_LOSS.
      - Trigger price < entry_px is a TAKE_PROFIT.
      - orderType containing 'stop' or triggerCondition containing 'above' is STOP_LOSS.
      - orderType containing 'take profit' or triggerCondition containing 'below' is TAKE_PROFIT.
    For LONG positions:
      - Trigger price < entry_px is a STOP_LOSS.
      - Trigger price > entry_px is a TAKE_PROFIT.
      - orderType containing 'stop' or triggerCondition containing 'below' is STOP_LOSS.
      - orderType containing 'take profit' or triggerCondition containing 'above' is TAKE_PROFIT.
    """
    if not order or not order.get("isTrigger"):
        return "UNKNOWN"

    otype = str(order.get("orderType", "")).lower()
    tcond = str(order.get("triggerCondition", "")).lower()
    tpx = float(order.get("triggerPx") or order.get("limitPx") or 0.0)
    side = str(position_side).upper()

    # 1. Primary check: Explicit orderType string
    if "take profit" in otype or "tp" in otype:
        return "TAKE_PROFIT"
    if "stop" in otype or "sl" in otype:
        return "STOP_LOSS"

    # 2. Secondary check: triggerCondition text ("Price above ..." vs "Price below ...")
    if side == "SHORT":
        if "above" in tcond:
            return "STOP_LOSS"
        if "below" in tcond:
            return "TAKE_PROFIT"
    else:  # LONG
        if "below" in tcond:
            return "STOP_LOSS"
        if "above" in tcond:
            return "TAKE_PROFIT"

    # 3. Fallback: Price relationship to entry price
    if entry_px > 0 and tpx > 0:
        if side == "SHORT":
            return "STOP_LOSS" if tpx > entry_px else "TAKE_PROFIT"
        else:
            return "STOP_LOSS" if tpx < entry_px else "TAKE_PROFIT"

    return "UNKNOWN"

HyperliquidTrader.categorize_trigger_order = staticmethod(categorize_trigger_order)






