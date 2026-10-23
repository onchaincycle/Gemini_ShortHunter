"""
SATU SUMBER parameter risiko autopilot ShortHunter (ROADMAP_INTEGRASI 2.3, 2026-10-22).

Semua angka di bawah = nilai yang SUDAH berjalan sebelum refactor (tidak ada perubahan perilaku).
Ubah angka tanpa menyentuh kode lewat config/risk_overrides.json (deep-merge, kunci sama), mis.:
    {"thermostat": {"modes": {"DEFENSIVE_SHIELD": {"reserve_pct": 20.0}}}}
Aturan proyek: ubah parameter hanya setelah ada bukti (backtest/forward test), catat di ROADMAP.

Yang TIDAK di sini (sudah terpusat di tempatnya):
- SL/TP/trailing/leverage/risk_pct/batas slot per arah per stream -> src/profiles.py (PROFILES)
- Alokasi Spot Vault -> data/portfolio_sizing_config.json (tidak dipakai sizing autopilot)
"""
import copy
import json
import os
from typing import Any, Dict

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERRIDES_FILE = os.path.join(ROOT_DIR, "config", "risk_overrides.json")

DEFAULTS: Dict[str, Any] = {
    # trader.get_equity_slot_plan
    "slot_plan": {
        "equity_tiers": [[120.0, 5], [260.0, 6], [420.0, 7]],   # ekuitas < batas -> jumlah slot
        "above_tiers_base": 5, "above_tiers_per_usd": 110.0, "max_positions_cap": 10,
        "swing_share": 0.0, "min_swing": 0, "min_intraday": 2,   # 2026-10-23: intraday saja (dulu 0.65 / 3)
        "min_slot_margin_floor": 12.0, "min_slot_margin_ratio": 0.80,
        "max_slot_margin_floor": 18.0, "max_slot_margin_ratio": 1.45,
        "dust_equity_pct": 0.025, "dust_min_usd": 3.0, "dust_max_usd": 8.0,
        "runner_exempt_ratio": 0.50,
        "empty_equity_plan": {"max_positions": 5, "max_swing": 3, "max_intraday": 2, "slot_base_margin": 14.0,
                              "min_slot_margin": 10.5, "max_slot_margin": 17.5, "dust_threshold_usd": 3.0,
                              "runner_exempt_margin_usd": 6.0},
    },
    # 2026-10-23 (intraday v3): ukuran posisi berbasis risiko (kerangka risiko berlapis 0,5–1% per trade)
    "intraday_risk": {
        "risk_per_trade_pct": 1.0,           # Grade A+
        "risk_per_trade_pct_grade_a": 0.75,  # Grade A
        "min_order_margin_usd": 5.0,
    },
        # autonomous_engine.get_live_governor_snapshot
    "governor": {
        "half_slot_ratio": 0.60,          # posisi < 60% slot_base_margin dihitung 0,5 slot
        "elastic_swing_borrow": 1,        # swing boleh pinjam 1 slot bila intraday kosong (total saja)
        "max_directional_ratio": 0.67, "max_directional_min": 3,
    },
    # auto_hunter: batas per kuadran = max(profil, (max_dasar + 1) // 2); swing TANPA pinjaman elastis
    "quadrant_caps": {"swing_elastic_raises_direction_cap": False},
    # autonomous_engine self-eval thermostat (dievaluasi berurutan; yang pertama cocok dipakai)
    "thermostat": {
        "stalemate_scratch_usd": 0.08,
        "circuit_breaker": {"loss_streak": 3, "daily_dd_pct": 5.0, "pause_hours": 6},
        "defensive": {"loss_streak": 2},
        "compound": {"win_streak": 3},
        "modes": {
            "CIRCUIT_BREAKER": {"badge": "🛑 CIRCUIT BREAKER (Auto-Pause Entry)", "reserve_pct": 30.0,
                                "dt_add": 7.0, "dt_floor": 70.0, "dt_cap": 88.0,
                                "sw_add": 6.0, "sw_floor": 72.0, "sw_cap": 88.0,
                                "min_ai_confidence": 85, "sizing_mult": 0.75},
            "DEFENSIVE_SHIELD": {"badge": "🟡 DEFENSIVE SHIELD (Mode Waspada)", "reserve_pct": 25.0,
                                 "dt_add": 5.0, "dt_floor": 70.0, "dt_cap": 85.0,
                                 "sw_add": 4.0, "sw_floor": 72.0, "sw_cap": 85.0,
                                 "min_ai_confidence": 80, "sizing_mult": 0.80},
            "COMPOUND_BOOST": {"badge": "🚀 COMPOUND BOOST (Win Streak Aktif)", "reserve_pct": 15.0,
                               "dt_add": 0.0, "dt_floor": 68.0, "dt_cap": 82.0,
                               "sw_add": -1.0, "sw_floor": 70.0, "sw_cap": 82.0,
                               "min_ai_confidence": 75, "sizing_mult": 1.08},
            "OPTIMAL": {"badge": "🟢 OPTIMAL (Standard Disiplin)", "reserve_pct": 15.0,
                        "dt_add": 0.0, "dt_floor": None, "dt_cap": None,
                        "sw_add": 0.0, "sw_floor": None, "sw_cap": None,
                        "min_ai_confidence": 75, "sizing_mult": 1.00},
        },
        "default_base_min_score_intraday": 75.0, "default_base_min_score_swing": 78.0,
    },
}


def _merge(base: Dict, over: Dict) -> Dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


_cache = {"mtime": "unset", "cfg": None}


def get() -> Dict[str, Any]:
    """Konfigurasi efektif (DEFAULTS + overrides). Dibaca ulang bila file override berubah."""
    try:
        mt = os.path.getmtime(OVERRIDES_FILE)
    except OSError:
        mt = None
    if _cache["mtime"] != mt:
        over = {}
        if mt is not None:
            try:
                with open(OVERRIDES_FILE) as f:
                    over = json.load(f)
            except Exception as e:
                print(f"[risk_config] override diabaikan (rusak): {e}")
        _cache.update(mtime=mt, cfg=_merge(DEFAULTS, over))
    return _cache["cfg"]


def _clamp(v: float, floor, cap) -> float:
    if cap is not None:
        v = min(cap, v)
    if floor is not None:
        v = max(floor, v)
    return v


def apply_thermostat(thermo: Dict, loss_streak: int, win_streak: int, net_realized_pnl: float,
                     daily_dd_pct: float, win_rate, profit_factor, now_ts: float) -> Dict:
    """Pilih mode thermostat & isi parameternya ke dict `thermo` (in-place, juga dikembalikan)."""
    t = get()["thermostat"]
    base_sw = float(thermo.get("base_min_score_swing", thermo.get("min_score_swing", t["default_base_min_score_swing"])))
    base_dt = float(thermo.get("base_min_score_intraday", thermo.get("min_score_intraday", t["default_base_min_score_intraday"])))
    thermo["base_min_score_swing"] = base_sw
    thermo["base_min_score_intraday"] = base_dt

    cb = t["circuit_breaker"]
    if loss_streak >= cb["loss_streak"] or daily_dd_pct >= cb["daily_dd_pct"]:
        mode = "CIRCUIT_BREAKER"
        reason = (f"Terdeteksi {loss_streak}x loss beruntun / drawdown harian {daily_dd_pct:.1f}%. "
                  f"Entry baru di-pause {cb['pause_hours']:g} jam untuk proteksi modal.")
    elif loss_streak == t["defensive"]["loss_streak"]:
        mode = "DEFENSIVE_SHIELD"
        m = t["modes"][mode]
        reason = (f"Terdeteksi {loss_streak}x loss beruntun. Cadangan kas dinaikkan ke {m['reserve_pct']:g}% ekuitas, "
                  f"syarat skor diperketat, ukuran peluru dipangkas {round((1 - m['sizing_mult']) * 100):g}%.")
    elif win_streak >= t["compound"]["win_streak"] and net_realized_pnl > 0:
        mode = "COMPOUND_BOOST"
        reason = (f"Win streak {win_streak}x berturut-turut (Win Rate {win_rate}%, Net +${net_realized_pnl:.2f}). "
                  f"Mode Compounding Optimal aktif.")
    else:
        mode = "OPTIMAL"
        reason = (f"Portofolio stabil (Win Rate {win_rate}%, PF {profit_factor}). "
                  f"Cadangan kas {t['modes']['OPTIMAL']['reserve_pct']:g}% ekuitas, filter wajib Grade A.")
    m = t["modes"][mode]
    is_cb = mode == "CIRCUIT_BREAKER"
    thermo.update({
        "mode": mode,
        "badge": m["badge"],
        "reserve_pct": m["reserve_pct"],
        "min_score_intraday": _clamp(base_dt + m["dt_add"], m["dt_floor"], m["dt_cap"]),
        "min_score_swing": _clamp(base_sw + m["sw_add"], m["sw_floor"], m["sw_cap"]),
        "min_ai_confidence": m["min_ai_confidence"],
        "sizing_mult": m["sizing_mult"],
        "circuit_breaker_pause": is_cb,
        "pause_until_ts": now_ts + cb["pause_hours"] * 3600 if is_cb else 0.0,
        "reason": reason,
    })
    return thermo
