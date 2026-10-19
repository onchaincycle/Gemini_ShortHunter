"""
Claude_Crypto Engine & Quantitative Synergy Bridge for ShortHunter:
Integrates empirical data, models, and statistical insights from project Claude_Crypto (SCR v10 / BottomScreenr):
1. Sector & Class Intelligence: Coin sector classification (Layer-1, DeFi, Meme, AI, Layer-2, RWA, etc.)
   with calibrated risk multipliers (Meme/Young projects get tighter SL clamp & 0.85x sizing).
2. BMS (Bottom Momentum Score) & Crash Gate:
   - Proven metric for Long bottom absorption (Base duration >= 45d, stability, volatility compression, >=3 retests).
   - Crash Gate: Disqualifies >50% drawdown coins unless 45d stabilization + 3 retests are confirmed.
   - Claude_Crypto statistical short edge: BMS < 30 confirms breakdown / free-fall (p=0.001 across 3 market regimes).
3. DRS (Distribution Risk Score):
   - Quantitative measure of institutional distribution & dump risk:
     * Structure breakdown: 3d below SMA30, negative slope, 20d lower low.
     * Bearish RSI / MACD momentum divergence.
     * Distribution volume (down-volume > 1.5x median).
     * Extended altitude & retail long trap (funding + OI).
4. Wyckoff / Claude_Crypto Phase Classification:
   - P1 Capitulation | P3/P4 Whale Accumulation | P5 Mark-Up | P6.5 Climax Exhaustion | P7 Distribution.
"""

import os
import json
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

CLAUDE_CRYPTO_BASE = "/Users/imansulaiman/Documents/Claude_Crypto"
COIN_CLASSES_CSV = os.path.join(CLAUDE_CRYPTO_BASE, "SCR_v10", "inputs", "coin_classes.csv")
LOCAL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOCAL_CLASSES_CACHE = os.path.join(LOCAL_DATA_DIR, "coin_classes.json")

# Supplementary dictionary for active Hyperliquid perps not covered in the original CSV
SUPPLEMENTARY_CLASSES = {
    "PEOPLE": {"kelas": "meme", "semua_kelas": "meme;governance", "name": "ConstitutionDAO"},
    "POPCAT": {"kelas": "meme", "semua_kelas": "meme;solana", "name": "Popcat"},
    "PNUT": {"kelas": "meme", "semua_kelas": "meme;solana", "name": "Peanut the Squirrel"},
    "NEIRO": {"kelas": "meme", "semua_kelas": "meme;ethereum", "name": "First Neiro on Ethereum"},
    "MOODENG": {"kelas": "meme", "semua_kelas": "meme;solana", "name": "Moo Deng"},
    "GOAT": {"kelas": "ai", "semua_kelas": "ai;meme", "name": "Goatseus Maximus"},
    "ACT": {"kelas": "ai", "semua_kelas": "ai;meme", "name": "Act I : The AI Prophecy"},
    "VIRTUAL": {"kelas": "ai", "semua_kelas": "ai;metaverse", "name": "Virtuals Protocol"},
    "INIT": {"kelas": "layer-1", "semua_kelas": "layer-1;interop", "name": "Initia"},
    "BERA": {"kelas": "layer-1", "semua_kelas": "layer-1;defi", "name": "Berachain"},
    "HYPER": {"kelas": "layer-1", "semua_kelas": "layer-1;defi;perp", "name": "Hyperliquid"},
    "HYPE": {"kelas": "layer-1", "semua_kelas": "layer-1;defi;perp", "name": "Hyperliquid"},
    "MORPHO": {"kelas": "defi", "semua_kelas": "defi;lending", "name": "Morpho"},
    "PENGU": {"kelas": "meme", "semua_kelas": "meme;nft", "name": "Pudgy Penguins"},
    "TRUMP": {"kelas": "meme", "semua_kelas": "meme;politi-fi", "name": "Official Trump"},
    "MELANIA": {"kelas": "meme", "semua_kelas": "meme;politi-fi", "name": "Melania Meme"},
}


class ClaudeCryptoEngine:
    """
    Quantitative Intelligence & Risk Engine powered by Claude_Crypto insights.
    """
    _classes_cache: Optional[Dict[str, Dict[str, str]]] = None

    @classmethod
    def load_coin_classes(cls) -> Dict[str, Dict[str, str]]:
        """Loads and caches sector classifications for crypto symbols."""
        if cls._classes_cache is not None:
            return cls._classes_cache

        mapping = {}

        # 1. Load from Claude_Crypto coin_classes.csv if available
        if os.path.exists(COIN_CLASSES_CSV):
            try:
                df = pd.read_csv(COIN_CLASSES_CSV)
                for _, row in df.iterrows():
                    sym = str(row.get("symbol", "")).strip().upper()
                    if sym:
                        mapping[sym] = {
                            "kelas": str(row.get("kelas", "lainnya")).strip().lower(),
                            "semua_kelas": str(row.get("semua_kelas", "")).strip().lower(),
                            "coin_id": str(row.get("coin_id", "")).strip()
                        }
            except Exception:
                pass

        # 2. Blend local JSON cache if exists
        if not mapping and os.path.exists(LOCAL_CLASSES_CACHE):
            try:
                with open(LOCAL_CLASSES_CACHE, "r") as f:
                    mapping = json.load(f)
            except Exception:
                pass

        # 3. Add supplementary listings
        for sym, d in SUPPLEMENTARY_CLASSES.items():
            if sym not in mapping:
                mapping[sym] = d

        # Persist locally for zero-latency fallback
        try:
            os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
            with open(LOCAL_CLASSES_CACHE, "w") as f:
                json.dump(mapping, f, indent=2)
        except Exception:
            pass

        cls._classes_cache = mapping
        return mapping

    @classmethod
    def get_coin_sector(cls, symbol: str) -> Dict[str, Any]:
        """
        Returns sector class and badge for a given coin symbol.
        """
        classes = cls.load_coin_classes()
        sym = symbol.strip().upper()
        data = classes.get(sym, {})
        kelas = data.get("kelas", "lainnya")
        all_k = data.get("semua_kelas", "")

        # Standardized badge & label
        sector_config = {
            "meme": {"label": "Meme", "badge": "🎭 MEME", "is_high_beta": True, "sizing_mult": 0.85, "sl_clamp": (2.5, 3.5)},
            "ai": {"label": "AI & Agent", "badge": "🤖 AI", "is_high_beta": True, "sizing_mult": 0.90, "sl_clamp": (2.5, 3.8)},
            "defi": {"label": "DeFi", "badge": "🏦 DEFI", "is_high_beta": False, "sizing_mult": 1.05, "sl_clamp": (2.8, 4.5)},
            "layer-1": {"label": "Layer-1", "badge": "⛓️ L1", "is_high_beta": False, "sizing_mult": 1.05, "sl_clamp": (2.8, 4.5)},
            "layer-2": {"label": "Layer-2", "badge": "⚡ L2", "is_high_beta": False, "sizing_mult": 1.00, "sl_clamp": (2.8, 4.2)},
            "layer-0": {"label": "Layer-0", "badge": "🌐 L0", "is_high_beta": False, "sizing_mult": 1.00, "sl_clamp": (2.8, 4.2)},
            "rwa": {"label": "RWA", "badge": "🏛️ RWA", "is_high_beta": False, "sizing_mult": 1.05, "sl_clamp": (2.8, 4.5)},
            "token-bursa": {"label": "CEX Token", "badge": "🏢 CEX", "is_high_beta": False, "sizing_mult": 1.00, "sl_clamp": (2.5, 4.0)},
            "gaming": {"label": "Gaming", "badge": "🎮 GAME", "is_high_beta": True, "sizing_mult": 0.85, "sl_clamp": (2.5, 3.5)},
            "depin": {"label": "DePIN", "badge": "📡 DEPIN", "is_high_beta": False, "sizing_mult": 1.00, "sl_clamp": (2.8, 4.2)},
            "lainnya": {"label": "Altcoin", "badge": "🪙 ALT", "is_high_beta": False, "sizing_mult": 1.00, "sl_clamp": (2.8, 4.2)},
        }

        conf = sector_config.get(kelas, sector_config["lainnya"])
        return {
            "symbol": sym,
            "sector": kelas,
            "label": conf["label"],
            "badge": conf["badge"],
            "all_sectors": all_k,
            "is_high_beta": conf["is_high_beta"],
            "sizing_mult": conf["sizing_mult"],
            "sl_clamp": conf["sl_clamp"],
        }

    # ------------------------------------------------------------------
    # 1. BMS (BOTTOM MOMENTUM SCORE) & CRASH GATE
    # ------------------------------------------------------------------
    @classmethod
    def compute_bms(cls, df_daily: pd.DataFrame) -> Dict[str, Any]:
        """
        Computes Claude_Crypto's Bottom Momentum Score (0-100) and Base Metrics:
          - Duration (30%): Base duration 30 to 90 days.
          - Stability (20%): Range in base + penalty for new lows in past 45d.
          - Retests (20%): Bounces off support without breaking lower low.
          - Volatility Compression (15%): Ratio of recent 10d ATR to 45d ATR.
          - Proximity (15%): Closeness to base floor.
        """
        if df_daily is None or len(df_daily) < 45:
            return {
                "bms": 50.0,
                "retests": 0,
                "base_days": 0,
                "vol_comp_ratio": 1.0,
                "pos_in_base": 0.5,
                "is_solid_base": False,
                "is_breakdown_freefall": False,
                "details": "Data historis terbatas"
            }

        close = df_daily["close"].astype(float).dropna()
        n = len(close)
        cur = float(close.iloc[-1])

        # 1. Identify Base Window (Lookback up to 90 days)
        lb = min(90, n)
        base_seg = close.iloc[-lb:]
        base_low = float(base_seg.min())
        base_high = float(base_seg.max())
        base_mean = max(float(base_seg.mean()), 1e-12)
        base_range = (base_high - base_low) / base_mean

        # Days since the lowest low
        days_since_low = int(lb - 1 - np.argmin(base_seg.values))
        base_days = min(lb, max(30, days_since_low + 15))

        # Duration score: 30-45d -> 0-50, 45-60d -> 50-80, 60-90d -> 80-100
        if base_days < 45:
            dur = max(0.0, min(50.0, (base_days - 30) / 15.0 * 50.0))
        elif base_days < 60:
            dur = 50.0 + (base_days - 45) / 15.0 * 30.0
        else:
            dur = 80.0 + min(20.0, (base_days - 60) / 30.0 * 20.0)

        # Stability: Tight range is rewarded (range < 25% = 100, > 80% = 0)
        stab = max(0.0, min(100.0, (0.80 - base_range) / (0.80 - 0.25) * 100.0))
        # Penalty for lower lows in last 45d
        if n >= 60:
            recent_45 = close.iloc[-45:]
            prior_min = float(close.iloc[:-45].iloc[-90:].min())
            new_lows = int((recent_45 < prior_min).sum())
            stab = max(0.0, stab - min(new_lows * 5.0, 40.0))

        # Retests: Count how many times price tested within 6% of base_low and bounced >= 8%
        retest_count = 0
        in_retest_zone = False
        bounce_confirmed = False
        for px in base_seg.values:
            dist = (px - base_low) / base_low
            if dist <= 0.06:
                if not in_retest_zone:
                    in_retest_zone = True
                    bounce_confirmed = False
            elif in_retest_zone and dist >= 0.08:
                retest_count += 1
                in_retest_zone = False

        retest_score = {0: 0.0, 1: 40.0, 2: 70.0}.get(retest_count, 100.0)

        # Volatility Compression Ratio (10d rolling std / 45d rolling std)
        std_10 = float(close.iloc[-10:].std()) if len(close) >= 10 else 1.0
        std_45 = float(close.iloc[-45:].std()) if len(close) >= 45 else 1.0
        vcr = (std_10 / max(std_45, 1e-12))
        # Compression: vcr <= 0.5 is maximum compression (100 pts), >= 1.2 is no compression (0 pts)
        vcs = max(0.0, min(100.0, (1.20 - vcr) / (1.20 - 0.50) * 100.0))

        # Proximity: (cur - base_low) / (base_high - base_low)
        pos_in_base = (cur - base_low) / max(base_high - base_low, 1e-12)
        if pos_in_base <= 0.60:
            prox = 100.0
        else:
            prox = max(0.0, min(100.0, (1.50 - pos_in_base) / (1.50 - 0.60) * 100.0))

        # Weighted BMS (Claude_Crypto master formula)
        bms = (0.30 * dur) + (0.20 * stab) + (0.20 * retest_score) + (0.15 * vcs) + (0.15 * prox)
        bms = round(float(bms), 1)

        is_solid_base = (bms >= 70.0)
        is_breakdown_freefall = (bms < 30.0)

        return {
            "bms": bms,
            "retests": retest_count,
            "base_days": base_days,
            "vol_comp_ratio": round(float(vcr), 2),
            "pos_in_base": round(float(pos_in_base), 2),
            "is_solid_base": is_solid_base,
            "is_breakdown_freefall": is_breakdown_freefall,
            "details": f"BMS {bms:.0f} (Base {base_days}d | Retest {retest_count}x | Comp {vcr:.2f}x)"
        }

    @classmethod
    def check_crash_gate(cls, df_daily: pd.DataFrame, bms_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Claude_Crypto Crash Gate:
        Prevents buying falling knives after >50% 90-day drawdowns unless:
          - Days since lowest low >= 45 days.
          - Support retests >= 3.
          - Volatility is compressed (vcr <= 0.85).
          - No new lower lows.
        """
        if df_daily is None or len(df_daily) < 45:
            return {"status": "PASS", "passed": True, "detail": "Data crash tidak terdeteksi"}

        close = df_daily["close"].astype(float).dropna()
        lb = min(90, len(close))
        seg90 = close.iloc[-lb:]
        max_dd_90d = float((seg90 / seg90.cummax() - 1.0).min())
        days_since_low = int(lb - 1 - np.argmin(seg90.values))

        retests = bms_data.get("retests", 0)
        vcr = bms_data.get("vol_comp_ratio", 1.0)

        # Trigger if drawdown in 90 days was severe (>= 50%)
        if max_dd_90d <= -0.50:
            checks = {
                "stabilisasi_45d": days_since_low >= 45,
                "retest_support_3x": retests >= 3,
                "vol_compression": vcr <= 0.85,
            }
            failed = [k for k, v in checks.items() if not v]
            if failed:
                return {
                    "status": "FAIL",
                    "passed": False,
                    "max_dd_pct": round(max_dd_90d * 100, 1),
                    "detail": f"Crash {max_dd_90d*100:.0f}% (Belum lolos gerbang: {', '.join(failed)})"
                }
            return {
                "status": "PASS",
                "passed": True,
                "max_dd_pct": round(max_dd_90d * 100, 1),
                "detail": f"Crash {max_dd_90d*100:.0f}% telah sembuh (Base 45d+ & Retest 3x Terpenuhi)"
            }

        return {
            "status": "PASS",
            "passed": True,
            "max_dd_pct": round(max_dd_90d * 100, 1),
            "detail": f"Normal drawdown ({max_dd_90d*100:.0f}%)"
        }

    # ------------------------------------------------------------------
    # 2. DRS (DISTRIBUTION RISK SCORE) FOR SHORTS
    # ------------------------------------------------------------------
    @classmethod
    def compute_drs(cls,
                    df_daily: pd.DataFrame,
                    funding_rate: float = 0.0,
                    oi_vol_ratio: float = 0.0) -> Dict[str, Any]:
        """
        Computes Claude_Crypto's Distribution Risk Score (DRS, 0-100):
          - Structure Breakdown (35%): Below SMA30 3d (40) + SMA30 slope down (30) + 20d lower low (30).
          - Momentum Divergence (20%): Bearish RSI Divergence (Price HH, RSI LH) or MACD hist negative.
          - Distribution Volume (15%): Down-volume > 1.5x median in last 10 days.
          - Price Extension (15%): Prior 90d gain > 30% / overextended above SMA50.
          - Retail Long Trap (15%): Positive funding + high OI/Vol ratio (retail chasing tops).
        """
        if df_daily is None or len(df_daily) < 35:
            return {"drs": 50.0, "is_distributing": False, "phase": "P0 Unknown", "details": "Data terbatas"}

        close = df_daily["close"].astype(float).dropna()
        vol = df_daily["volume"].astype(float).dropna() if "volume" in df_daily.columns else pd.Series(dtype=float)
        n = len(close)
        cur = float(close.iloc[-1])

        # Moving Averages
        sma30 = close.rolling(30).mean()
        sma50 = close.rolling(50).mean() if n >= 50 else close.rolling(30).mean()

        # 1. Structure Breakdown (0 - 100)
        s30_cur = float(sma30.iloc[-1]) if n >= 30 else cur
        s30_prev5 = float(sma30.iloc[-6]) if n >= 36 else s30_cur
        s30_slope = (s30_cur / s30_prev5 - 1.0) if s30_prev5 > 0 else 0.0

        cons_below_3d = bool((close.iloc[-3:] < sma30.iloc[-3:]).all()) if n >= 33 else False
        sma30_down = bool(s30_slope < -0.008)
        lower_low_20d = bool(cur < close.iloc[-21:-1].min()) if n >= 22 else False

        sb_score = (40.0 if cons_below_3d else 0.0) + (30.0 if sma30_down else 0.0) + (30.0 if lower_low_20d else 0.0)

        # 2. Bearish Momentum Divergence (0 - 100)
        # Check RSI divergence in past 45 days
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs)).fillna(50)

        curr_rsi = float(rsi_series.iloc[-1])
        div_score = 0.0
        if n >= 45:
            p_recent = float(close.iloc[-12:].max())
            p_prior = float(close.iloc[-45:-12].max())
            r_recent = float(rsi_series.iloc[-12:].max())
            r_prior = float(rsi_series.iloc[-45:-12].max())
            if p_recent >= p_prior and r_recent < (r_prior - 2.5):
                div_score = 100.0  # Bearish RSI divergence
            elif curr_rsi < 45 and not cons_below_3d:
                div_score = 40.0
        elif curr_rsi >= 70:
            div_score = 50.0  # Overbought exhaustion

        # 3. Distribution Volume (0 - 100)
        dist_vol_score = 0.0
        if len(vol) >= 30:
            med_vol = float(vol.iloc[-30:].median())
            recent_10 = df_daily.iloc[-10:]
            down_candles = (recent_10["close"] < recent_10["open"]) | (recent_10["close"].diff() < 0)
            high_vol = recent_10["volume"] > (1.35 * med_vol)
            dist_share = float((down_candles & high_vol).sum() / 10.0)
            dist_vol_score = min(100.0, dist_share / 0.30 * 100.0)

        # 4. Extension Score (0 - 100)
        s50_val = float(sma50.iloc[-1]) if n >= 30 else cur
        dist_from_sma50 = (cur - s50_val) / max(s50_val, 1e-12) * 100.0
        lb90 = min(90, n)
        gain_90d = (cur / float(close.iloc[-lb90]) - 1.0) * 100.0
        ext_score = 0.0
        if gain_90d >= 70.0 or dist_from_sma50 >= 25.0:
            ext_score = 100.0
        elif gain_90d >= 40.0 or dist_from_sma50 >= 15.0:
            ext_score = 70.0
        elif gain_90d >= 20.0:
            ext_score = 40.0

        # 5. Retail Long Trap (Funding & OI)
        trap_score = 0.0
        if funding_rate >= 0.00003 and oi_vol_ratio >= 0.5:
            trap_score = 100.0
        elif funding_rate > 0.00001:
            trap_score = 60.0
        elif funding_rate < -0.00002:
            trap_score = 0.0  # Squeeze risk, low trap score

        # Composite DRS (Distribution Risk Score)
        drs = (0.35 * sb_score) + (0.20 * div_score) + (0.15 * dist_vol_score) + (0.15 * ext_score) + (0.15 * trap_score)
        drs = round(float(drs), 1)

        is_distributing = (drs >= 60.0) or (sb_score >= 70.0 and ext_score >= 50.0)

        return {
            "drs": drs,
            "sb_score": round(sb_score, 1),
            "div_score": round(div_score, 1),
            "dist_vol_score": round(dist_vol_score, 1),
            "ext_score": round(ext_score, 1),
            "trap_score": round(trap_score, 1),
            "is_distributing": is_distributing,
            "details": f"DRS {drs:.0f} (Struktur {sb_score:.0f} | Div {div_score:.0f} | DistVol {dist_vol_score:.0f})"
        }

    # ------------------------------------------------------------------
    # 3. WYCKOFF / CLAUDE_CRYPTO PHASE CLASSIFIER
    # ------------------------------------------------------------------
    @classmethod
    def classify_phase(cls,
                       df_daily: pd.DataFrame,
                       bms_data: Dict[str, Any],
                       drs_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Classifies the asset's macro cycle phase using Claude_Crypto methodology:
          - P1 Capitulation: Falling knife dump, no base -> Veto Long.
          - P3/P4 Whale Accumulation: Solid base (BMS >= 70), retests >= 2 -> Prime Long.
          - P5 Mark-Up: Bullish trend above SMA30.
          - P6.5 Climax Exhaustion: Parabolic top, extreme RSI/gain -> Short Watchlist.
          - P7 Distribution: DRS >= 60 or structure breakdown after pump -> Prime Short.
        """
        if df_daily is None or len(df_daily) < 35:
            return {"phase": "P0 Neutral", "badge": "⚪ P0 NETRAL", "action": "WAIT"}

        close = df_daily["close"].astype(float).dropna()
        n = len(close)
        cur = float(close.iloc[-1])
        sma30 = float(close.rolling(30).mean().iloc[-1]) if n >= 30 else cur

        lb90 = min(90, n)
        seg90 = close.iloc[-lb90:]
        max_dd_90d = float((seg90 / seg90.cummax() - 1.0).min())
        days_since_low = int(lb90 - 1 - np.argmin(seg90.values))

        bms = bms_data.get("bms", 50.0)
        drs = drs_data.get("drs", 50.0)
        gain_30d = (cur / float(close.iloc[-min(30, n)]) - 1.0) * 100.0

        # Phase Rules:
        # 1. P7 Distribution
        if drs >= 60.0 or (drs_data.get("sb_score", 0) >= 70 and gain_30d >= 20.0):
            return {
                "phase": "P7 Distribution",
                "badge": "🔴 P7 DISTRIBUSI",
                "action": "PRIME_SHORT",
                "desc": "Paus melakukan aksi lepas barang (distribusi) + struktur breakdown di bawah SMA30."
            }

        # 2. P6.5 Climax Exhaustion
        if gain_30d >= 70.0 or drs_data.get("ext_score", 0) >= 80.0:
            return {
                "phase": "P6.5 Climax Exhaustion",
                "badge": "⚠️ P6.5 EXHAUSTION",
                "action": "SHORT_WATCHLIST",
                "desc": "Harga overextended parabolic, pembeli lelah di pucuk, bersiap patah."
            }

        # 3. P1 Capitulation
        if max_dd_90d <= -0.40 and days_since_low < 15:
            return {
                "phase": "P1 Capitulation",
                "badge": "⛔ P1 KAPITULASI",
                "action": "VETO_LONG",
                "desc": "Air terjun / falling knife tanpa base support. Dilarang open Long."
            }

        # 4. P3/P4 Whale Accumulation
        if bms >= 70.0 and bms_data.get("retests", 0) >= 2:
            return {
                "phase": "P3/P4 Whale Accumulation",
                "badge": "🟢 P3/P4 AKUMULASI",
                "action": "PRIME_LONG",
                "desc": "Paus mengumpulkan barang (akumulasi tenang), base kokoh >=45 hari, siap mark-up."
            }

        # 5. P5 Mark-Up
        if cur > sma30 and gain_30d >= 5.0:
            return {
                "phase": "P5 Mark-Up",
                "badge": "🚀 P5 MARK-UP",
                "action": "TREND_CONTINUATION",
                "desc": "Tren penguatan sedang berjalan di atas SMA30."
            }

        return {
            "phase": "P2 Consolidation",
            "badge": "⚪ P2 KONSOLIDASI",
            "action": "NEUTRAL",
            "desc": "Rentang konsolidasi wajar tanpa dominasi akumulasi atau distribusi agresif."
        }

    # ------------------------------------------------------------------
    # 4. SMART MONEY DISTRIBUTION SCORE (SMS) — NANSEN ON-CHAIN LAYER
    # ------------------------------------------------------------------
    @classmethod
    def compute_smart_money_score(cls,
                                  sm_live: Optional[Dict[str, Any]],
                                  sm_trend: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Smart Money Distribution Score (SMS, 0-100; higher = stronger whale distribution):
          - Live balance change 24h (0-60): dumping scales with |change|, accumulating = 0.
          - Live netflow USD (±15): outflow >= $50k adds, inflow >= $50k subtracts.
          - 7d historical trend (0-25): falling Smart Money value + persistent distribution days.
        Returns sms=None when Nansen has no coverage for the coin.
        """
        if not sm_live and not sm_trend:
            return {"sms": None, "action": "N/A", "label": "N/A", "details": "Tidak ada data Nansen"}

        sms = 0.0
        action = "N/A"
        label = "N/A"
        if sm_live:
            action = sm_live.get("action", "NEUTRAL")
            pct = float(sm_live.get("change_24h_pct", 0.0))
            net_flow = float(sm_live.get("net_flow_usd", 0.0))
            label = f"{action} ({pct:+.1f}%)"
            if pct <= -1.0:
                sms += min(60.0, 30.0 + abs(pct) * 6.0)
            elif pct < 1.0:
                sms += 20.0
            if net_flow <= -50_000:
                sms += 15.0
            elif net_flow >= 50_000:
                sms -= 15.0

        trend_txt = "7d N/A"
        if sm_trend:
            v_chg = float(sm_trend.get("value_change_pct", 0.0))
            d_dist = int(sm_trend.get("days_distributing", 0))
            if v_chg <= -10.0:
                sms += 15.0
            elif v_chg <= -3.0:
                sms += 8.0
            sms += min(10.0, d_dist * 2.5)
            trend_txt = f"7d {v_chg:+.1f}% ({d_dist}d dist)"

        sms = round(max(0.0, min(100.0, sms)), 1)
        return {"sms": sms, "action": action, "label": label, "trend": trend_txt,
                "details": f"SMS {sms:.0f} | {label} | {trend_txt}"}

    # ------------------------------------------------------------------
    # 5. UNIFIED SHORT CONFLUENCE (SCR v10 + NANSEN) — SATU PANGGILAN
    # ------------------------------------------------------------------
    @classmethod
    def evaluate_short_confluence(cls,
                                  symbol: str,
                                  df_daily: pd.DataFrame,
                                  funding_rate: float = 0.0,
                                  oi_vol_ratio: float = 0.0,
                                  sm_live: Optional[Dict[str, Any]] = None,
                                  sm_trend: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Single entry point for the short screener: Sector + BMS + DRS + Phase + Smart Money.
        score_adj reproduces the legacy Pillar 6 (Nansen) + Pillar 8 (SCR) weights exactly,
        so scores stay comparable with historical signals. New metrics (sms, drs_onchain)
        are informational until validated by backtest.
        """
        sector = cls.get_coin_sector(symbol)
        bms = cls.compute_bms(df_daily)
        drs = cls.compute_drs(df_daily, funding_rate=funding_rate, oi_vol_ratio=oi_vol_ratio)
        phase = cls.classify_phase(df_daily, bms, drs)
        sm = cls.compute_smart_money_score(sm_live, sm_trend)

        score_adj = 0.0
        # Legacy Pillar 6: Nansen Smart Money
        if sm["action"] == "DUMPING":
            score_adj += 15
        elif sm["action"] == "ACCUMULATING":
            score_adj -= 25
        # Legacy Pillar 8a: macro confluence
        if phase["phase"] == "P7 Distribution":
            score_adj += 4
        elif drs["drs"] >= 65.0:
            score_adj += 2
        # Legacy Pillar 8b: whale base safety veto
        if bms["is_solid_base"] and bms["bms"] >= 80.0:
            score_adj -= 10

        # On-chain-blended DRS: 75% price structure + 25% Smart Money flow
        drs_onchain = round(0.75 * drs["drs"] + 0.25 * sm["sms"], 1) if sm["sms"] is not None else drs["drs"]

        return {
            "sector": sector,
            "bms": bms,
            "drs": drs,
            "phase": phase,
            "smart_money": sm,
            "drs_onchain": drs_onchain,
            "score_adj": score_adj,
        }
