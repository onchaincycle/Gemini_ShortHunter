"""
ShortHunter Strategy Profiles:
Defines configuration profiles for:
1. SWING_3D   : Multi-day trend reversal (-10% Trim, -20% TP, -35% Runner, 10% SL, Hold 2-4 days)
2. DAYTRADE   : Intraday precision (TP1 1.0R / TP2 2.0R, SL teknis 2.8-5.5%, Auto-Close 6 jam)

PENTING (Dual-Stream Architecture):
- Profil "aktif" global HANYA dipakai sebagai default tampilan/menu.
- Manajemen posisi (SL, trimming, time-stop) SELALU memakai strategi yang
  di-tag pada masing-masing posisi (lihat src/position_tags.py), sehingga
  posisi Swing tidak pernah ikut dikelola dengan aturan Intraday (dan sebaliknya).
"""

import os
import json
from typing import Dict, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_CONFIG_FILE = os.path.join(ROOT_DIR, "data", "active_profile.json")

PROFILES = {
    "SWING": {
        "key": "SWING",
        "name": "Mode Swing 3 Hari (Macro Dump v2.0)",
        "badge": "🌊 SWING",
        "description": "Menangkap dump besar multi-hari (-20% s/d -35%) pada koin pucuk yang patah tren.",
        # --- Screening ---
        "timeframe": "1D / 4H / 1H",
        "min_score": 75.0,
        # --- Exit model: persentase tetap dari harga entry ---
        "exit_model": "FIXED_PCT",
        "sl_pct": 10.0,
        "half_risk_step_pct": 2.2,  # Step 1 (+2.2% move / +4.4% ROE): Compress on-chain SL from -10% to -5.0%
        "early_be_pct": 4.0,        # Step 2 (+4.0% move / +8.0% ROE): Early Trigger (+1.5R)
        "tier1_lock_profit_pct": 1.5, # Tier-1 Lock: Kunci minimal +1.5% net profit (+3.0% ROE) alih-alih BE polos
        "trim_pct": 7.0,            # Step 3 (+7.0% move / +14% ROE): Trim 50% + lock SL at +2.0% profit
        "continuous_trail_enabled": True, # Continuous Trailing Engine
        "continuous_trail_start_pct": 4.0, # Aktif sejak >= +4.0% move (+8.0% ROE / +1.5R)
        "continuous_trail_ratio": 0.55,    # Kunci 55% dari peak move (HWM)
        "continuous_trail_step_pct": 0.5,  # Ratchet on-chain per kenaikan peak >= +0.5% move
        "periodic_trail_start_pct": 8.5,  # Legacy fallback
        "hwm_lock_ratio": 0.55,
        "tp2_pct": 18.0,            # TP2 at +18% price move (36% ROE)
        "runner_pct": 30.0,         # Runner at +30% price move (60% ROE)
        "max_hold_hours": 72,
        "time_exit_enabled": True,  # 3-Day Cutoff to avoid Day-5 dead-cat bounce
        "stalemate_hours": 72,
        # --- Risk & portfolio ---
        "leverage": 2,
        "risk_pct": 2.5,            # % ekuitas yang dirisikokan per trade (Profil Agresif Terukur)
        "max_concurrent": 5,        # maksimal 5 slot swing terbuka bersamaan
        "max_concurrent_short": 3,  # batas slot swing short
        "max_concurrent_long": 2,   # batas slot swing long
        "max_daily_losses": None,   # swing tidak pakai circuit breaker harian
        # --- Eksekusi & monitoring ---
        "session_gate": False,
        "poll_seconds": 25,
        "fee_roundtrip_pct": 0.09,  # taker 0.045% x 2
        "target_desc": "Half-Risk @ ±2.2% ➔ Tier-1 Lock (+1.5% net) @ ±4% ➔ Continuous Trailing (55% HWM) ➔ Trim 50% @ ±7% ➔ TP @ ±18% ➔ Runner @ ±30%"
    },
    "DAYTRADE": {
        "key": "DAYTRADE",
        "name": "Mode Intraday Day-Trade (Precision v2.3)",
        "badge": "⚡ INTRADAY",
        "description": "Eksekusi intraday 15m: Dynamic 2x-3x leverage (Grade A), SL teknis 2.5-4.5%, TP1 1.0R (2.5-4.5%), TP2 2.0R (5.0-9.0%), auto-close 6 jam.",
        # --- Screening ---
        "timeframe": "15m / 1H",
        "min_score": 75.0,
        # --- Exit model: kelipatan risiko (R) dari SL dinamis per posisi ---
        "exit_model": "R_MULTIPLE",
        "sl_pct": 3.2,              # default bila SL dinamis tidak tersedia
        "sl_min_pct": 2.5,
        "sl_max_pct": 4.5,
        "half_risk_r": 0.45,        # Step 1 (0.45R / ~1.4% move): Perketat SL on-chain 50%
        "early_be_r": 0.65,         # Step 2 (0.65R / ~2.1% move): Geser SL on-chain ke Breakeven + Fee
        "tier1_trigger_r": 1.5,     # Step 2.5 (+1.5R move / >=+4.0%): Trigger Tier-1 Profit Lock
        "tier1_lock_r": 0.5,        # Kunci minimal +0.5R profit (~1.5% move)
        "tier1_lock_profit_pct": 1.5, # Minimum net profit lock %
        "continuous_trail_enabled": True, # Continuous Trailing Engine
        "continuous_trail_start_r": 1.2,   # Aktif sejak >= 1.2R (~3.0-3.8% move) atau >= 4.0%
        "continuous_trail_ratio": 0.50,     # Kunci 50% dari peak R / move
        "continuous_trail_step_r": 0.25,   # Ratchet on-chain per kenaikan peak >= 0.25R
        "tp1_r": 1.0,               # Step 3 (1.0R / 2.5-4.5% move): Trim 50% & kunci SL di profit +0.3R
        "tp2_r": 2.0,               # Step 4 (2.0R / 5.0-9.0% move): Tutup sisa (Hard Cap 9.0% gerak harga)
        "trim_pct": 3.2,            # = 1.0R dari default SL (fallback)
        "tp2_pct": 6.4,             # = 2.0R dari default SL (fallback)
        "tp2_max_pct": 9.0,         # Hard cap TP2 gerak harga spot (maks 9.0% / 18-27% ROE)
        "runner_pct": None,
        "max_hold_hours": 6,
        "time_exit_enabled": True,
        "stalemate_hours": 6,
        # --- Risk & portfolio ---
        "leverage": 2,              # Baseline 2x (auto-boost ke 3x untuk Grade A / A+)
        "dynamic_leverage_enabled": True,
        "risk_pct": 1.5,            # risiko per trade dinaikkan sedikit (1.0% -> 1.5%)
        "max_concurrent": 4,        # maksimal 4 slot intraday terbuka bersamaan (sebelumnya 3)
        "max_concurrent_short": 2,  # alokasi slot intraday short
        "max_concurrent_long": 2,   # alokasi slot intraday long
        "max_daily_losses": 3,      # circuit breaker: stop entry intraday setelah 3 loss/hari
        # --- Eksekusi & monitoring ---
        "session_gate": True,       # hanya London/NY (14:00-23:59 WIB)
        "poll_seconds": 10,
        "fee_roundtrip_pct": 0.09,
        "target_desc": "Half-Risk @ 0.45R ➔ Early BE @ 0.65R ➔ Continuous Trailing (50% HWM) ➔ TP1 1.0R (Trim 50%) ➔ TP2 2.0R | Auto-Close: 6 Jam"
    }
}

STRATEGY_ALIASES = {
    "SWING": "SWING", "MULTIDAY": "SWING", "MULTI_DAY": "SWING",
    "DAYTRADE": "DAYTRADE", "INTRADAY": "DAYTRADE", "DAY": "DAYTRADE",
}


def normalize_strategy(key: Optional[str]) -> str:
    """Maps any alias (INTRADAY, DAYTRADE, MULTIDAY...) to a canonical profile key."""
    if not key:
        return "SWING"
    return STRATEGY_ALIASES.get(str(key).upper(), "SWING")


def get_profile(key: Optional[str]) -> Dict:
    """Returns the profile for a given strategy key (defaults to SWING)."""
    return PROFILES[normalize_strategy(key)]


def get_active_profile() -> Dict:
    """Returns the currently active (default UI) profile dictionary."""
    if os.path.exists(PROFILE_CONFIG_FILE):
        try:
            with open(PROFILE_CONFIG_FILE, "r") as f:
                data = json.load(f)
                key = data.get("active_key", "SWING")
                if key in PROFILES:
                    return PROFILES[key]
        except Exception:
            pass
    return PROFILES["SWING"]

def set_active_profile(profile_key: str) -> Dict:
    """Sets and persists the active (default UI) profile."""
    profile_key = normalize_strategy(profile_key)
    if profile_key not in PROFILES:
        return {"success": False, "error": f"Profil {profile_key} tidak valid. Pilih SWING atau DAYTRADE."}

    os.makedirs(os.path.dirname(PROFILE_CONFIG_FILE), exist_ok=True)
    with open(PROFILE_CONFIG_FILE, "w") as f:
        json.dump({"active_key": profile_key}, f, indent=2)

    return {"success": True, "profile": PROFILES[profile_key]}
