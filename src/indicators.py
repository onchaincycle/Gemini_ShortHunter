"""
Technical Indicators: RSI, EMA, ATR, and Market Structure tools.
"""

import numpy as np
import pandas as pd

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculates Wilder's Relative Strength Index (RSI)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    """Calculates Exponential Moving Average."""
    return series.ewm(span=span, adjust=False).mean()

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculates Average True Range (ATR)."""
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def evaluate_peak_exhaustion(df: pd.DataFrame, idx: int = -1) -> dict:
    """
    Evaluates ShortHunter v2.0 'True Peak & Reversal Confirmation' metrics at row `idx`:
    - Gate 1 (Altitude & Fresh Peak): Ensures coin is genuinely elevated above EMA50,
      has not already dumped >22% from its 30d high, and is not oversold.
    - Gate 2 (Buyer Exhaustion): Upper wick rejection, Bearish RSI divergence, Volume climax/dry-up.
    - Gate 3 (Clean Breakdown Trigger): Close < EMA9, bearish/doji session, not chasing a flash crash.
    """
    if idx < 0:
        idx = len(df) + idx

    c = df.iloc[idx]
    close_p = float(c["close"])
    high_p = float(c["high"])
    low_p = float(c["low"])
    open_p = float(c["open"])

    prev_c = df.iloc[max(0, idx - 1)]
    prev2_c = df.iloc[max(0, idx - 2)]

    day_ret = ((close_p - float(prev_c["close"])) / float(prev_c["close"])) * 100 if float(prev_c["close"]) > 0 else 0.0
    prev_ret = ((float(prev_c["close"]) - float(prev2_c["close"])) / float(prev2_c["close"])) * 100 if float(prev2_c["close"]) > 0 else 0.0

    # Upper wick ratio on current & previous candles
    rng = high_p - low_p
    u_wick = (high_p - max(open_p, close_p)) / rng if rng > 0 else 0.0

    prev_rng = float(prev_c["high"]) - float(prev_c["low"])
    prev_u_wick = (float(prev_c["high"]) - max(float(prev_c["open"]), float(prev_c["close"]))) / prev_rng if prev_rng > 0 else 0.0

    has_wick = (u_wick >= 0.30) or (prev_u_wick >= 0.40)

    # RSI & Divergence
    curr_rsi = float(c["rsi"]) if "rsi" in df.columns else 50.0
    start_14 = max(0, idx - 14)
    peak_rsi_14 = float(df.iloc[start_14:idx + 1]["rsi"].max()) if "rsi" in df.columns else curr_rsi
    has_rsi_div = (curr_rsi <= peak_rsi_14 - 8.0) and (peak_rsi_14 >= 72.0)

    # Volume Climax or Low-Volume Bounce
    vol_exhaustion = False
    if "vol_ma10" in df.columns and pd.notna(c["vol_ma10"]) and float(c["vol_ma10"]) > 0:
        vol_ma10 = float(c["vol_ma10"])
        vol_exhaustion = (float(c["volume"]) < vol_ma10 * 0.85) or (float(prev_c["volume"]) > vol_ma10 * 1.30)

    # Distance to EMA50 & 30D High Pullback
    ema50_val = float(c["ema50"]) if "ema50" in df.columns and pd.notna(c["ema50"]) else 0.0
    dist_ema50 = ((close_p - ema50_val) / ema50_val) * 100 if ema50_val > 0 else 0.0

    start_30 = max(0, idx - 30)
    high_30 = float(df.iloc[start_30:idx + 1]["high"].max())
    pb_30 = ((high_30 - close_p) / high_30) * 100 if high_30 > 0 else 0.0

    # Gate 1: Fresh Peak vs Stale/Oversold
    is_stale_or_oversold = (pb_30 > 22.0) or (dist_ema50 < 12.0) or (curr_rsi < 48.0)
    is_fresh_peak = not is_stale_or_oversold

    # Gate 3: Clean Breakdown (below EMA9, bearish/doji, not chasing a single-day flash crash)
    ema9_val = float(c["ema9"]) if "ema9" in df.columns and pd.notna(c["ema9"]) else close_p
    below_ema9 = close_p < ema9_val
    is_red_or_doji = (close_p <= open_p * 1.005) and (day_ret <= 0.5)
    not_flash_crash = (day_ret >= -8.5) and (prev_ret >= -9.0)
    is_clean_breakdown = below_ema9 and is_red_or_doji and not_flash_crash

    tags = []
    if has_wick:
        tags.append("WICK_REJECT")
    if has_rsi_div:
        tags.append("RSI_DIV")
    if vol_exhaustion:
        tags.append("VOL_EXHAUST")

    return {
        "u_wick": round(u_wick, 2),
        "prev_u_wick": round(prev_u_wick, 2),
        "has_wick": has_wick,
        "has_rsi_div": has_rsi_div,
        "vol_exhaustion": vol_exhaustion,
        "has_any_exhaustion": (has_wick or has_rsi_div or vol_exhaustion),
        "exhaustion_label": "+".join(tags) if tags else "NONE",
        "dist_ema50": round(dist_ema50, 1),
        "pb_30": round(pb_30, 1),
        "curr_rsi": round(curr_rsi, 1),
        "peak_rsi_14": round(peak_rsi_14, 1),
        "day_ret": round(day_ret, 2),
        "prev_ret": round(prev_ret, 2),
        "is_stale_or_oversold": is_stale_or_oversold,
        "is_fresh_peak": is_fresh_peak,
        "below_ema9": below_ema9,
        "is_red_or_doji": is_red_or_doji,
        "not_flash_crash": not_flash_crash,
        "is_clean_breakdown": is_clean_breakdown,
    }


def evaluate_bottom_absorption(df: pd.DataFrame, idx: int = -1) -> dict:
    """
    Evaluates LongHunter '3-Gate Bottom Absorption & Squeeze Reversal' metrics at row `idx`:
    - Gate 1 (Base & Healthy Discount): Corrected 12% - 45% from 30d high, not a dead falling knife (>60% dump).
      RSI floor in 28 - 50 territory.
    - Gate 2 (Whale Absorption): Lower wick absorption >= 30%, Bullish RSI divergence, Volume absorption at bottom.
    - Gate 3 (Clean Reclaim Trigger): Close > EMA9, green candle, not chasing an overextended >12% pump.
    """
    if idx < 0:
        idx = len(df) + idx

    c = df.iloc[idx]
    close_p = float(c["close"])
    high_p = float(c["high"])
    low_p = float(c["low"])
    open_p = float(c["open"])

    prev_c = df.iloc[max(0, idx - 1)]
    prev2_c = df.iloc[max(0, idx - 2)]

    day_ret = ((close_p - float(prev_c["close"])) / float(prev_c["close"])) * 100 if float(prev_c["close"]) > 0 else 0.0
    prev_ret = ((float(prev_c["close"]) - float(prev2_c["close"])) / float(prev2_c["close"])) * 100 if float(prev2_c["close"]) > 0 else 0.0

    # Lower wick ratio on current & previous candles
    rng = high_p - low_p
    l_wick = (min(open_p, close_p) - low_p) / rng if rng > 0 else 0.0

    prev_rng = float(prev_c["high"]) - float(prev_c["low"])
    prev_l_wick = (min(float(prev_c["open"]), float(prev_c["close"])) - float(prev_c["low"])) / prev_rng if prev_rng > 0 else 0.0

    has_wick = (l_wick >= 0.30) or (prev_l_wick >= 0.38)

    # RSI & Bullish Divergence
    curr_rsi = float(c["rsi"]) if "rsi" in df.columns else 50.0
    start_14 = max(0, idx - 14)
    low_rsi_14 = float(df.iloc[start_14:idx + 1]["rsi"].min()) if "rsi" in df.columns else curr_rsi
    has_rsi_div = (curr_rsi >= low_rsi_14 + 5.0) and (low_rsi_14 <= 42.0)

    # Volume Climax Absorption
    vol_absorption = False
    if "vol_ma10" in df.columns and pd.notna(c["vol_ma10"]) and float(c["vol_ma10"]) > 0:
        vol_ma10 = float(c["vol_ma10"])
        vol_absorption = (float(c["volume"]) > vol_ma10 * 1.25) or (float(prev_c["volume"]) > vol_ma10 * 1.35)

    # Distance to 30D High (Healthy Discount vs Falling Knife)
    start_30 = max(0, idx - 30)
    high_30 = float(df.iloc[start_30:idx + 1]["high"].max())
    discount_pct = ((high_30 - close_p) / high_30) * 100 if high_30 > 0 else 0.0

    # Falling knife: down > 60% with dying volume, or RSI < 24
    is_falling_knife = (discount_pct > 60.0) or (curr_rsi < 24.0)
    # Healthy base: corrected 12% to 45% from recent high
    is_healthy_discount = (12.0 <= discount_pct <= 45.0) and not is_falling_knife

    # Gate 3: Clean Reclaim (close above EMA9, green candle, not chasing massive pump > 12%)
    ema9_val = float(c["ema9"]) if "ema9" in df.columns and pd.notna(c["ema9"]) else close_p
    above_ema9 = close_p > ema9_val
    is_green = close_p >= open_p * 0.998
    not_chasing = day_ret <= 12.0
    is_clean_reclaim = above_ema9 and is_green and not_chasing and (close_p >= float(prev_c["high"]) * 0.995 or above_ema9)

    tags = []
    if has_wick:
        tags.append("LOWER_WICK_ABSORB")
    if has_rsi_div:
        tags.append("BULL_RSI_DIV")
    if vol_absorption:
        tags.append("VOL_ABSORPTION")

    return {
        "l_wick": round(l_wick, 2),
        "prev_l_wick": round(prev_l_wick, 2),
        "has_wick": has_wick,
        "has_rsi_div": has_rsi_div,
        "vol_absorption": vol_absorption,
        "has_any_absorption": (has_wick or has_rsi_div or vol_absorption),
        "absorption_label": "+".join(tags) if tags else "NONE",
        "discount_pct": round(discount_pct, 1),
        "curr_rsi": round(curr_rsi, 1),
        "low_rsi_14": round(low_rsi_14, 1),
        "day_ret": round(day_ret, 2),
        "prev_ret": round(prev_ret, 2),
        "is_falling_knife": is_falling_knife,
        "is_healthy_discount": is_healthy_discount,
        "above_ema9": above_ema9,
        "is_green": is_green,
        "is_clean_reclaim": is_clean_reclaim,
    }

