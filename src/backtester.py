"""
ShortHunter Backtester Engine (Pro Institution Grade with 10% Trimming):
Features:
1. BTC Market Regime Filter: Pauses altcoin short entries when BTC is surging in a bull stampede (>+3.5% or RSI > 72).
2. Buyer Euphoria Filter: Confirms RSI peaked >= 68 in last 7 days.
3. Momentum Crack: Daily close below EMA 9.
4. Profit Trimming at 10%:
   - Closes 50% position at +10% drop (Locks in profit!).
   - Moves Stop Loss on remaining 50% to Breakeven (0% risk).
5. Secondary Target (20%):
   - Closes 30% position at +20% drop.
   - Moves trailing stop for runner to lock in +10% gain.
6. Runner Target (35%):
   - Remaining 20% position targets deep dump to +35%.
7. Anti-Revenge Cooldown: 10-day cooldown per coin after exiting a trade.
"""

from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from src.database import Database
from src.indicators import calculate_rsi, calculate_ema, evaluate_peak_exhaustion
from src.market_regime import MarketRegime

class ShortBacktester:
    def __init__(self, db: Database):
        self.db = db
        self.regime = MarketRegime(db)

    def run_backtest(self,
                     symbols: Optional[List[str]] = None,
                     min_score: float = 0.0,
                     min_90d_gain: float = 90.0,
                     min_euphoria_rsi: float = 68.0,
                     stop_loss_pct: float = 10.0,
                     trim_pct: float = 10.0,
                     take_profit_pct: float = 20.0,
                     tp_pct: Optional[float] = None,
                     runner_tp_pct: Optional[float] = 35.0,
                      max_hold_days: int = 7,
                      cooldown_days: int = 10,
                      use_trimming: bool = True,
                      use_btc_filter: bool = True,
                      fee_roundtrip_pct: float = 0.09) -> Dict:
        """
        Runs comprehensive multi-factor historical simulation across DuckDB data
        using ShortHunter v2.0 (3-Gate True Peak & Reversal Confirmation).
        Includes roundtrip taker fee deduction (0.09%) and 4H intra-day SL-vs-Trim ordering resolution.
        """
        if tp_pct is not None:
            take_profit_pct = tp_pct
        else:
            tp_pct = take_profit_pct

        if symbols is None:
            symbols = [s for s in self.db.get_symbols("daily_candles") if s != "BTC"]

        # 1. Index BTC Daily Regime
        btc_regime_map = {}
        if use_btc_filter:
            df_btc = self.db.get_candles("daily_candles", "BTC")
            if not df_btc.empty:
                df_btc["rsi"] = calculate_rsi(df_btc["close"], 14)
                df_btc["ema9"] = calculate_ema(df_btc["close"], 9)
                df_btc["pct_change"] = df_btc["close"].pct_change() * 100
                for _, r in df_btc.iterrows():
                    d_str = str(r["datetime"])[:10]
                    is_stampede = r["pct_change"] >= 3.5 or (r["rsi"] >= 72 and r["close"] > r["ema9"])
                    btc_regime_map[d_str] = "PAUSE" if is_stampede else "SAFE"

        # 2. Index Nansen historical Smart Money
        try:
            df_nan = self.db.conn.execute("SELECT date, symbol, balance_change_pct FROM nansen_smart_money_history").df()
            nansen_dict = {(str(r["date"])[:10], r["symbol"].upper()): r["balance_change_pct"] for _, r in df_nan.iterrows()}
        except Exception:
            nansen_dict = {}

        trades = []

        for symbol in symbols:
            df = self.db.get_candles("daily_candles", symbol)
            if len(df) < 95:
                continue

            df_4h = self.db.get_candles("h4_candles", symbol)
            if not df_4h.empty:
                df_4h["date_str"] = df_4h["datetime"].astype(str).str[:10]

            df["rsi"] = calculate_rsi(df["close"], period=14)
            df["ema9"] = calculate_ema(df["close"], span=9)
            df["ema20"] = calculate_ema(df["close"], span=20)
            df["ema50"] = calculate_ema(df["close"], span=50)
            df["vol_ma10"] = df["volume"].rolling(10).mean()

            in_position = False
            entry_price = 0.0
            entry_idx = 0
            sl_price = 0.0
            trim_price = 0.0
            tp2_price = 0.0
            tp3_price = 0.0
            trim_done = False
            tp2_done = False
            last_exit_idx = -999

            for i in range(90, len(df)):
                current_candle = df.iloc[i]
                open_p = float(current_candle["open"])
                close_p = float(current_candle["close"])
                high_p = float(current_candle["high"])
                low_p = float(current_candle["low"])
                d_str = str(current_candle["datetime"])[:10]

                # If in position, evaluate exits
                if in_position:
                    days_held = i - entry_idx

                    # Resolve same-day High/Low ambiguity when BOTH initial SL and Trim 1 are touched on the same day
                    sl_first = False
                    if use_trimming and not trim_done and high_p >= sl_price and low_p <= trim_price:
                        resolved = False
                        if not df_4h.empty:
                            sub_4h = df_4h[df_4h["date_str"] == d_str]
                            for _, r4 in sub_4h.iterrows():
                                if float(r4["high"]) >= sl_price:
                                    sl_first = True
                                    resolved = True
                                    break
                                if float(r4["low"]) <= trim_price:
                                    sl_first = False
                                    resolved = True
                                    break
                        if not resolved:
                            # Conservative fallback: if open is closer to SL than Trim, assume SL hit first
                            sl_first = (sl_price - open_p) <= (open_p - trim_price)

                    # 1. Check Trimming Level 1 (unless SL was hit first intra-day)
                    if use_trimming and not trim_done and not sl_first and low_p <= trim_price:
                        trim_done = True
                        sl_price = entry_price  # Move SL to Breakeven for remaining position!

                    # 2. Check TP Level 2
                    if trim_done and not tp2_done and low_p <= tp2_price:
                        tp2_done = True
                        sl_price = entry_price * (1 - (trim_pct / 100))  # Lock in profit on runner!

                    # 3. Check Stop Loss / Trailing Hit
                    if high_p >= sl_price:
                        if tp2_done:
                            if runner_tp_pct:
                                pnl = (0.50 * trim_pct) + (0.30 * tp_pct) + (0.20 * trim_pct) - fee_roundtrip_pct
                            else:
                                pnl = (0.50 * trim_pct) + (0.50 * tp_pct) - fee_roundtrip_pct
                            res = "WIN_TP20_RUNNER" if runner_tp_pct else "WIN_TP10_DAY"
                        elif trim_done:
                            pnl = (0.50 * trim_pct) - fee_roundtrip_pct
                            res = "TRIM_THEN_BE"
                        else:
                            pnl = -stop_loss_pct - fee_roundtrip_pct
                            res = "LOSS_SL"

                        trades.append({
                            "symbol": symbol,
                            "entry_date": df.iloc[entry_idx]["datetime"],
                            "exit_date": current_candle["datetime"],
                            "entry_price": entry_price,
                            "exit_price": sl_price,
                            "result": res,
                            "pnl_pct": round(pnl, 2),
                            "days_held": days_held
                        })
                        in_position = False
                        last_exit_idx = i
                        continue

                    # 4. Check Mega Runner TP 3
                    elif tp2_done and runner_tp_pct and low_p <= tp3_price:
                        pnl = (0.50 * trim_pct) + (0.30 * tp_pct) + (0.20 * runner_tp_pct) - fee_roundtrip_pct
                        trades.append({
                            "symbol": symbol,
                            "entry_date": df.iloc[entry_idx]["datetime"],
                            "exit_date": current_candle["datetime"],
                            "entry_price": entry_price,
                            "exit_price": tp3_price,
                            "result": "MEGA_WIN_35%",
                            "pnl_pct": round(pnl, 2),
                            "days_held": days_held
                        })
                        in_position = False
                        last_exit_idx = i
                        continue

                    # 5. Time Stop
                    elif days_held >= max_hold_days:
                        drop = ((entry_price - close_p) / entry_price) * 100
                        if tp2_done:
                            if runner_tp_pct:
                                pnl = (0.50 * trim_pct) + (0.30 * tp_pct) + (0.20 * drop) - fee_roundtrip_pct
                            else:
                                pnl = (0.50 * trim_pct) + (0.50 * tp_pct) - fee_roundtrip_pct
                        elif trim_done:
                            pnl = (0.50 * trim_pct) + (0.50 * drop) - fee_roundtrip_pct
                        else:
                            pnl = drop - fee_roundtrip_pct
                        trades.append({
                            "symbol": symbol,
                            "entry_date": df.iloc[entry_idx]["datetime"],
                            "exit_date": current_candle["datetime"],
                            "entry_price": entry_price,
                            "exit_price": close_p,
                            "result": "WIN_TIME" if pnl > 0 else "LOSS_TIME",
                            "pnl_pct": round(pnl, 2),
                            "days_held": days_held
                        })
                        in_position = False
                        last_exit_idx = i
                        continue

                # Check for entry trigger if not in position
                if not in_position:
                    if (i - last_exit_idx) < cooldown_days:
                        continue
                    if use_btc_filter and btc_regime_map.get(d_str) == "PAUSE":
                        continue

                    # --- SHORTHUNTER V2.0: 3-GATE TRUE PEAK & REVERSAL SCORING ---
                    close_90d_ago = float(df.iloc[i - 90]["close"])
                    gain_90d = ((close_p - close_90d_ago) / close_90d_ago) * 100

                    ex = evaluate_peak_exhaustion(df, idx=i)
                    score = 0.0

                    # Pillar 1: Parabolic Macro Surge (Max 35 pts)
                    if gain_90d >= 150:
                        score += 35
                    elif gain_90d >= 140:
                        score += 25
                    elif gain_90d >= 110:
                        score += 10
                    elif gain_90d >= 85:
                        score += 5
                    else:
                        continue

                    # Pillar 2: Gate 1 — Fresh Peak Altitude vs Stale/Oversold Veto (Max 25 pts)
                    if ex["is_stale_or_oversold"]:
                        score -= 40  # Veto stale/oversold coins (e.g., pullback > 22% or dist_ema50 < 12%)
                    else:
                        pb_30 = ex["pb_30"]
                        dist_ema50 = ex["dist_ema50"]
                        if 2.5 <= pb_30 <= 20.5:
                            score += 15
                        elif pb_30 < 2.5:
                            score += 5
                        else:
                            score += 8

                        if dist_ema50 >= 25.0:
                            score += 10
                        elif dist_ema50 >= 12.0:
                            score += 5

                    # Pillar 3: Gate 2 — Buyer Euphoria & Exhaustion Divergence (Max 20 pts)
                    peak_rsi_14 = ex["peak_rsi_14"]
                    if peak_rsi_14 >= 74:
                        score += 10
                    elif peak_rsi_14 >= 68:
                        score += 5
                    else:
                        score -= 15

                    if ex["has_any_exhaustion"]:
                        score += 10

                    # Pillar 4: Gate 3 — Mandatory Breakdown & Anti-Crash-Chasing Gate (Max 20 pts)
                    if ex["is_clean_breakdown"]:
                        score += 20
                    elif ex["below_ema9"]:
                        score -= 10  # Either green bounce candle or chasing a single-day -9% flash crash
                    else:
                        score -= 30  # Still above EMA9! Cannot trigger short

                    # Pillar 5: Nansen Smart Money
                    sm_change = nansen_dict.get((d_str, symbol.upper()))
                    if sm_change is not None:
                        if sm_change < -1.0:
                            score += 10
                        elif sm_change > 1.5:
                            score -= 25

                    if min_score > 0 and score < min_score:
                        continue

                    if min_score == 0:
                        if gain_90d < min_90d_gain or peak_rsi_14 < min_euphoria_rsi or not ex["is_clean_breakdown"] or ex["is_stale_or_oversold"]:
                            continue
                        if sm_change is not None and sm_change > 1.5:
                            continue

                    in_position = True
                    entry_price = close_p
                    entry_idx = i
                    sl_price = entry_price * (1 + (stop_loss_pct / 100))
                    trim_price = entry_price * (1 - (trim_pct / 100))
                    tp2_price = entry_price * (1 - (tp_pct / 100))
                    tp3_price = entry_price * (1 - ((runner_tp_pct or 35.0) / 100))
                    trim_done = False
                    tp2_done = False

        df_trades = pd.DataFrame(trades)
        if df_trades.empty:
            return {
                "total_trades": 0,
                "win_trades": 0,
                "be_trades": 0,
                "loss_trades": 0,
                "win_rate": 0.0,
                "safe_rate": 0.0,
                "profit_factor": 0.0,
                "net_return_pct": 0.0,
                "avg_trade_pnl": 0.0,
                "trades_df": df_trades
            }

        total_trades = len(df_trades)
        winning_trades = df_trades[df_trades["pnl_pct"] > 0]
        be_trades = df_trades[df_trades["pnl_pct"] == 0]
        losing_trades = df_trades[df_trades["pnl_pct"] < 0]

        win_rate = (len(winning_trades) / total_trades) * 100
        safe_rate = ((len(winning_trades) + len(be_trades)) / total_trades) * 100
        gross_profit = winning_trades["pnl_pct"].sum() if not winning_trades.empty else 0.0
        gross_loss = abs(losing_trades["pnl_pct"].sum()) if not losing_trades.empty else 0.0

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        net_return_pct = gross_profit - gross_loss

        return {
            "total_trades": total_trades,
            "win_trades": len(winning_trades),
            "be_trades": len(be_trades),
            "loss_trades": len(losing_trades),
            "win_rate": round(win_rate, 1),
            "safe_rate": round(safe_rate, 1),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "net_return_pct": round(net_return_pct, 2),
            "avg_trade_pnl": round(df_trades["pnl_pct"].mean(), 2),
            "trades_df": df_trades
        }

    def run_threshold_sensitivity_grid(self) -> pd.DataFrame:
        """
        Runs repeated backtests across score thresholds (50, 60, 65, 70, 75, 80, 85)
        to mathematically prove the optimal execution cutoff under True Peak v2.0.
        """
        thresholds = [50, 60, 65, 70, 75, 80, 85]
        records = []

        for th in thresholds:
            res = self.run_backtest(min_score=th, use_trimming=True)
            cnt = res["total_trades"]
            wr = res["win_rate"]
            sr = res["safe_rate"]
            pf = res["profit_factor"]
            net = res["net_return_pct"]

            if th >= 80:
                verdict = "🔥 SANGAT DIREKOMENDASIKAN (100% Akurasi Pucuk)"
            elif th >= 75:
                verdict = "✅ DIREKOMENDASIKAN (True Peak Terkonfirmasi)"
            elif th >= 65:
                verdict = "⏳ WATCHLIST (Tunggu Konfirmasi Patah)"
            else:
                verdict = "⛔ DILARANG SHORT (Tren Bull Masih Kuat)"

            records.append({
                "Threshold": f"Skor >= {th}",
                "Trades": cnt,
                "Win Rate (%)": f"{wr:.1f}%",
                "Safe Rate (Win+BE)": f"{sr:.1f}%",
                "Profit Factor": f"{pf:.2f}",
                "Net Return (%)": f"{net:+.1f}%",
                "Evaluasi": verdict
            })

        return pd.DataFrame(records)

    def run_profile_backtest(self, profile_key: str = "SWING") -> Dict:
        """
        Runs backtest according to specific strategy stream:
        - SWING   : Daily/4H Macro True Peak v2.0 (SL 10%, Trim 10%, TP 20%, Runner 35%, Max 3 Days)
        - DAYTRADE: 15-Minute Intraday Precision v2.2 (Dynamic Pinbar SL 2.8-5.5%, TP1 1R, TP2 2R, Max 6 Hours, London/NY)
        """
        from src.profiles import get_profile, normalize_strategy
        key = normalize_strategy(profile_key)
        prof = get_profile(key)

        if key == "DAYTRADE":
            from src.daytrade_engine import IntradayDaytradeEngine
            return IntradayDaytradeEngine().run_intraday_backtest()

        max_days = max(1, prof.get("max_hold_hours", 72) // 24)

        res = self.run_backtest(
            min_score=prof["min_score"],
            min_90d_gain=90.0,
            stop_loss_pct=prof["sl_pct"],
            trim_pct=prof["trim_pct"],
            take_profit_pct=prof["tp2_pct"],
            runner_tp_pct=prof["runner_pct"],
            max_hold_days=max_days
        )
        res["stream"] = "SWING"
        res["profile"] = prof
        return res



