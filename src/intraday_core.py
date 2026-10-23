"""
Inti logika intraday 15m — v3 (2026-10-23, permintaan user: evaluasi & perbaiki, BELUM TERUJI backtest).

Konsep v3 = "pullback searah tren + penolakan + konfirmasi candle TUTUP" (dipakai SHORT & LONG, simetris).
Perbaikan terhadap v1/v2 (lihat ROADMAP_INTEGRASI.md log 2026-10-23 & docs/INTRADAY_V3.md):
  1. Hanya candle 15m yang SUDAH TUTUP dipakai untuk sinyal (candle berjalan dibuang) -> tidak membeli pucuk jarum sesaat.
  2. Filter tren timeframe besar: LONG hanya bila harga > EMA200 15m (~50 jam) & EMA50 naik; SHORT kebalikannya.
     v1 menangkap pisau jatuh / melawan tren; riset (H12, backtest long 22 Okt) menunjukkan pembalikan melawan tren
     intraday kalah dari acak.
  3. Filter rezim BTC: LONG altcoin diblokir bila BTC turun >1,5% dalam 4 jam; SHORT diblokir bila BTC naik >1,5%.
  4. Zona pullback (sentuh EMA20/EMA50) menggantikan bonus "dekat low/high 12 jam" (sumber falling-knife v1).
  5. Penolakan wajib kuat: ekor >= 55% (v1: 35%) atau sweep low/high 2 jam + close kembali.
  6. Volume candle sinyal >= 1,35x MA20 (+), < 0,7x (-15) — v1 LONG tidak punya filter volume sama sekali.
  7. Struktur: LONG butuh Higher Low, SHORT butuh Lower High.
  8. SL struktural di balik ekstrem pola + buffer max(0,35%, 0,25 ATR). Bila > batas maks -> TOLAK
     (v1 menggeser SL ke DALAM struktur = kena noise). Bila < batas min -> dilebarkan (lebih aman).
  9. Anti-kejar: bila harga live sudah lari > 0,5R dari close candle sinyal -> setup basi.
 10. Bobot sesi diperkecil (riset: killzone bukan edge) dan sesi Asia tetap penalti.
"""
import time
from typing import Dict, Optional

import pandas as pd

from src.indicators import calculate_ema, calculate_rsi

ENGINE_VERSION = "intraday_v3"
BAR_MS = 15 * 60 * 1000

PARAMS = {
    "min_closed_bars": 220,
    "wick_min": 0.55,
    "vol_climax": 1.35,
    "vol_low": 0.70,
    "sl_min_pct": 2.5,
    "sl_max_pct": 4.5,
    "sl_buffer_pct": 0.35,
    "sl_buffer_atr": 0.25,
    "tp1_r": 1.0,
    "tp2_r": 2.0,
    "tp2_max_pct": 9.0,
    "pullback_touch_pct": 0.35,      # low/high candle sinyal menyentuh EMA20/EMA50 dalam 0,35%
    "max_extension_atr": 1.5,        # close sinyal tidak boleh > 1,5 ATR dari EMA20
    "chase_max_r": 0.5,
    "btc_block_4h_pct": 1.5,
    "funding_veto_short": -0.015,    # %/jam: short dilarang bila funding lebih negatif dari ini
    "funding_veto_long": 0.035,      # %/jam: long dilarang bila funding lebih positif dari ini
    "ready_score": 75.0,
}


def get_market_session(now_utc_hour: Optional[int] = None) -> Dict:
    h = time.gmtime().tm_hour if now_utc_hour is None else int(now_utc_hour)
    wib = (h + 7) % 24
    if 19 <= wib <= 23:
        return {"session": "LONDON_NY_OVERLAP", "label": "🔥 Sesi London+NY", "boost": 10, "is_prime": True, "wib_hour": wib}
    if 14 <= wib < 19:
        return {"session": "LONDON_OPEN", "label": "⚡ Sesi London", "boost": 8, "is_prime": True, "wib_hour": wib}
    if 0 <= wib < 4:
        return {"session": "NY_AFTERNOON", "label": "🌙 Sesi NY malam", "boost": 3, "is_prime": False, "wib_hour": wib}
    return {"session": "ASIA_SESSION", "label": "☕ Sesi Asia (volume rendah)", "boost": -10, "is_prime": False, "wib_hour": wib}


def split_closed(df: pd.DataFrame, now_ms: Optional[int] = None):
    """Mengembalikan (df_candle_tutup, harga_live). Candle yang belum tutup dibuang dari sinyal."""
    df = df.sort_values("timestamp").reset_index(drop=True)
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    live_px = float(df["close"].iloc[-1])
    if int(df["timestamp"].iloc[-1]) + BAR_MS > now_ms:
        df = df.iloc[:-1].reset_index(drop=True)
    return df, live_px


def btc_context(df_btc: Optional[pd.DataFrame], now_ms: Optional[int] = None) -> Dict:
    """Perubahan BTC 4 jam (16 candle tutup) & posisi terhadap EMA200 15m."""
    try:
        d, live = split_closed(df_btc, now_ms)
        if len(d) < 30:
            return {"available": False, "chg_4h_pct": 0.0, "above_ema200": None}
        ref = float(d["close"].iloc[-17])
        ema200 = float(calculate_ema(d["close"], span=200).iloc[-1])
        return {"available": True, "chg_4h_pct": round((live / ref - 1) * 100, 2), "above_ema200": live > ema200}
    except Exception:
        return {"available": False, "chg_4h_pct": 0.0, "above_ema200": None}


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def evaluate(df_15m: pd.DataFrame, side: str, coin: str = "", funding_1h_pct: float = 0.0,
             change_24h_pct: float = 0.0, btc_ctx: Optional[Dict] = None, now_ms: Optional[int] = None,
             session: Optional[Dict] = None, params: Optional[Dict] = None) -> Dict:
    P = dict(PARAMS, **(params or {}))
    side = side.upper()
    is_long = side == "LONG"
    if df_15m is None or df_15m.empty:
        return {"valid": False, "error": "Tidak ada data candle"}
    d, live_px = split_closed(df_15m, now_ms)
    if len(d) < P["min_closed_bars"]:
        return {"valid": False, "error": f"Data candle 15m tertutup tidak cukup ({len(d)})"}
    if live_px <= 0:
        return {"valid": False, "error": "Harga tidak valid"}

    d = d.copy()
    d["ema9"] = calculate_ema(d["close"], span=9)
    d["ema20"] = calculate_ema(d["close"], span=20)
    d["ema50"] = calculate_ema(d["close"], span=50)
    d["ema200"] = calculate_ema(d["close"], span=200)
    d["rsi"] = calculate_rsi(d["close"], period=14)
    d["atr"] = _atr(d)
    d["vol_ma20"] = d["volume"].rolling(20).mean().shift(1)   # MA volume SEBELUM candle sinyal

    s = d.iloc[-1]          # candle sinyal = candle terakhir yang sudah tutup
    p = d.iloc[-2]
    o, h, l, c = float(s["open"]), float(s["high"]), float(s["low"]), float(s["close"])
    rng = max(h - l, 1e-12)
    atr = float(s["atr"]) if pd.notna(s["atr"]) else rng
    ema20, ema50, ema200 = float(s["ema20"]), float(s["ema50"]), float(s["ema200"])
    ema50_prev = float(d["ema50"].iloc[-9])
    rsi = float(s["rsi"]) if pd.notna(s["rsi"]) else 50.0
    vol_ma = float(s["vol_ma20"]) if pd.notna(s["vol_ma20"]) and s["vol_ma20"] > 0 else 0.0
    vol_ratio = float(s["volume"]) / vol_ma if vol_ma > 0 else 0.0

    # 1. Tren timeframe besar
    if is_long:
        trend_ok = c > ema200 and ema50 >= ema50_prev and ema50 > ema200
    else:
        trend_ok = c < ema200 and ema50 <= ema50_prev and ema50 < ema200

    # 2. Rezim BTC
    btc_ctx = btc_ctx or {}
    btc_chg = float(btc_ctx.get("chg_4h_pct", 0.0) or 0.0)
    btc_ok = (btc_chg > -P["btc_block_4h_pct"]) if is_long else (btc_chg < P["btc_block_4h_pct"])
    if coin == "BTC":
        btc_ok = True

    # 3. Zona pullback ke EMA20/EMA50 + tidak terlalu jauh dari EMA20
    tol = P["pullback_touch_pct"] / 100.0
    if is_long:
        touched = (l <= ema20 * (1 + tol)) or (l <= ema50 * (1 + tol))
        extension_atr = (c - ema20) / atr if atr > 0 else 0.0
    else:
        touched = (h >= ema20 * (1 - tol)) or (h >= ema50 * (1 - tol))
        extension_atr = (ema20 - c) / atr if atr > 0 else 0.0
    not_extended = extension_atr <= P["max_extension_atr"]

    # 4. Penolakan: ekor kuat atau sweep 2 jam (8 candle) lalu close kembali
    if is_long:
        wick = (min(o, c) - l) / rng
        prior_ext = float(d["low"].iloc[-9:-1].min())
        sweep = l < prior_ext and c > prior_ext
    else:
        wick = (h - max(o, c)) / rng
        prior_ext = float(d["high"].iloc[-9:-1].max())
        sweep = h > prior_ext and c < prior_ext
    has_pinbar = wick >= P["wick_min"]
    rejection = has_pinbar or sweep

    # 5. Konfirmasi struktur pada candle TUTUP
    if is_long:
        bos = c > float(s["ema9"]) and c > float(p["high"]) * 0.999 and c > o
        recent_ext = float(d["low"].iloc[-3:].min())
        prior_swing = float(d["low"].iloc[-24:-6].min())
        structure_ok = recent_ext > prior_swing          # Higher Low
    else:
        bos = c < float(s["ema9"]) and c < float(p["low"]) * 1.001 and c < o
        recent_ext = float(d["high"].iloc[-3:].max())
        prior_swing = float(d["high"].iloc[-24:-6].max())
        structure_ok = recent_ext < prior_swing          # Lower High

    # 6. Volume
    vol_climax = vol_ratio >= P["vol_climax"]
    vol_low = vol_ma > 0 and vol_ratio < P["vol_low"]

    # 7. SL struktural, TP berbasis R
    buf = max(P["sl_buffer_pct"] / 100.0 * live_px, P["sl_buffer_atr"] * atr)
    struct_sl = (recent_ext - buf) if is_long else (recent_ext + buf)
    raw_sl_pct = ((live_px - struct_sl) / live_px * 100.0) if is_long else ((struct_sl - live_px) / live_px * 100.0)
    sl_invalid = raw_sl_pct <= 0          # harga live sudah menembus level SL
    sl_too_wide = raw_sl_pct > P["sl_max_pct"]
    sl_pct = max(P["sl_min_pct"], raw_sl_pct) if not sl_invalid else P["sl_min_pct"]
    sl_px = live_px * (1 - sl_pct / 100.0) if is_long else live_px * (1 + sl_pct / 100.0)
    tp1_pct = round(sl_pct * P["tp1_r"], 2)
    tp2_pct = round(min(P["tp2_max_pct"], sl_pct * P["tp2_r"]), 2)
    sgn = 1 if is_long else -1
    tp1_px = live_px * (1 + sgn * tp1_pct / 100.0)
    tp2_px = live_px * (1 + sgn * tp2_pct / 100.0)
    rrr = round(tp2_pct / sl_pct, 2) if sl_pct > 0 else 0.0

    # 8. Anti-kejar: harga live sudah lari dari close candle sinyal
    run_pct = ((live_px - c) / c * 100.0) * sgn
    stale = run_pct > P["chase_max_r"] * sl_pct

    # 9. Funding
    funding_veto = (funding_1h_pct > P["funding_veto_long"]) if is_long else (funding_1h_pct < P["funding_veto_short"])

    # 10. Skor
    sess = session or get_market_session()
    score, reasons = 0.0, []

    def add(pts, txt):
        nonlocal score
        score += pts
        reasons.append(txt)

    add(20 if trend_ok else -25, "Searah tren 15m-EMA200" if trend_ok else "Melawan tren besar (EMA200/EMA50)")
    if touched and not_extended:
        add(15, "Pullback ke EMA20/50")
    elif not not_extended:
        add(-15, f"Terlalu jauh dari EMA20 ({extension_atr:.1f} ATR)")
    if has_pinbar:
        add(12, f"Ekor penolakan {wick*100:.0f}%")
    if sweep:
        add(10, "Sweep 2 jam lalu close kembali")
    add(15 if bos else -10, "Konfirmasi candle tutup (BOS)" if bos else "Belum ada konfirmasi candle tutup")
    if vol_climax:
        add(15, f"Volume {vol_ratio:.1f}x MA20")
    elif vol_low:
        add(-15, f"Volume rendah {vol_ratio:.1f}x (rawan fakeout)")
    add(10 if structure_ok else -10, ("Higher Low" if is_long else "Lower High") if structure_ok else "Struktur belum mendukung")
    rsi_ok = (40 <= rsi <= 65) if is_long else (35 <= rsi <= 60)
    if rsi_ok:
        add(5, f"RSI sehat {rsi:.0f}")
    if is_long and funding_1h_pct < 0:
        add(5, f"Funding negatif {funding_1h_pct:+.4f}%/jam")
    if (not is_long) and funding_1h_pct > 0.005:
        add(5, f"Funding positif {funding_1h_pct:+.4f}%/jam")
    add(sess["boost"], sess["label"])

    vetoes = []
    if not btc_ok:
        vetoes.append(f"BTC {btc_chg:+.1f}%/4j melawan arah")
    if sl_too_wide:
        vetoes.append(f"SL struktural terlalu lebar ({raw_sl_pct:.1f}% > {P['sl_max_pct']}%)")
    if sl_invalid:
        vetoes.append("Harga sudah menembus level SL")
    if stale:
        vetoes.append(f"Setup basi: harga sudah lari {run_pct:.1f}%")
    if funding_veto:
        vetoes.append(f"Funding ekstrem {funding_1h_pct:+.4f}%/jam")
    for v in vetoes:
        reasons.append("⛔ " + v)

    gates_ok = trend_ok and rejection and bos and not vol_low and not vetoes
    score = max(0.0, min(100.0, score))
    if not gates_ok:
        score = min(score, 70.0 if not vetoes else 45.0)

    if score >= P["ready_score"] and gates_ok:
        verdict = "🚀 SIAP LONG INTRADAY" if is_long else "🔥 SIAP SHORT INTRADAY"
    elif score >= 60.0:
        verdict = "⏳ WATCHLIST INTRADAY"
    else:
        verdict = "⛔ BELUM LAYAK"

    win = d.iloc[-48:]
    win_hi, win_lo = float(win["high"].max()), float(win["low"].min())
    return {
        "valid": True, "coin": coin, "side": side, "engine_version": ENGINE_VERSION,
        "score": round(score, 1), "verdict": verdict, "ready": bool(score >= P["ready_score"] and gates_ok),
        "current_px": live_px, "signal_close": c, "signal_bar_ts": int(s["timestamp"]),
        "change_24h_pct": change_24h_pct,
        "dist_from_low_pct": round((live_px - win_lo) / win_lo * 100.0, 2) if win_lo > 0 else 0.0,
        "dist_from_high_pct": round((win_hi - live_px) / win_hi * 100.0, 2) if win_hi > 0 else 0.0,
        "trend_ok": bool(trend_ok), "btc_ok": bool(btc_ok), "btc_chg_4h_pct": btc_chg,
        "pullback_touch": bool(touched), "extension_atr": round(extension_atr, 2),
        "sweep_detected": bool(sweep), "has_pinbar": bool(has_pinbar), "wick_pct": round(wick * 100, 1),
        "bos_detected": bool(bos), "structure_ok": bool(structure_ok), "rsi_div": False,
        "vol_ratio": round(vol_ratio, 2), "curr_rsi": round(rsi, 1),
        "sl_px": round(sl_px, 6), "sl_pct": round(sl_pct, 2), "raw_sl_pct": round(raw_sl_pct, 2),
        "tp1_px": round(tp1_px, 6), "tp1_pct": tp1_pct, "tp2_px": round(tp2_px, 6), "tp2_pct": tp2_pct,
        "rrr": rrr, "funding_1h_pct": funding_1h_pct,
        "is_high_squeeze": bool((not is_long) and funding_veto),
        "stale": bool(stale), "vetoes": vetoes, "session": sess["label"], "reasons": reasons,
    }
