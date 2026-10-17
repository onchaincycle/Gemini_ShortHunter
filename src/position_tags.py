"""
ShortHunter Position Tags (Dual-Stream Registry):
Menyimpan "rencana trade" per posisi, termasuk strategi asalnya (SWING / DAYTRADE)
dan level harga absolut (SL, TP1, TP2, Runner) yang dihitung saat eksekusi.

Mengapa perlu?
- Hyperliquid tidak menyimpan "strategi" sebuah posisi. Tanpa tag ini, daemon
  hanya bisa menebak dari profil global -> posisi Swing bisa ikut ter-time-stop
  6 jam, atau posisi Intraday dikelola dengan SL 10%.
- Posisi yang tidak memiliki tag otomatis dianggap SWING (default paling aman:
  SL lebih lebar, tidak ada auto-close cepat).
"""

import os
import json
from datetime import datetime
from typing import Dict, Optional

from src.profiles import get_profile, normalize_strategy
from src.trade_db import trade_db

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAGS_FILE = os.path.join(ROOT_DIR, "data", "position_tags.json")


def _load() -> Dict:
    try:
        # Primary: SQLite WAL TradeDatabase
        tags = trade_db.load_position_tags()
        if tags:
            return tags
    except Exception:
        pass
    # Fallback to JSON file
    try:
        if os.path.exists(TAGS_FILE):
            with open(TAGS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save(tags: Dict):
    # Primary: Persist each tag to SQLite
    try:
        for coin, val in tags.items():
            trade_db.save_position_tag(
                coin=coin,
                strategy=val.get("strategy", "SWING"),
                plan=val.get("plan"),
                meta=val.get("meta")
            )
    except Exception as e:
        print(f"[!] Warning: Gagal simpan tag ke SQLite: {e}")

    # Mirror: Non-blocking JSON file backup
    try:
        os.makedirs(os.path.dirname(TAGS_FILE), exist_ok=True)
        tmp = TAGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(tags, f, indent=2)
        os.replace(tmp, TAGS_FILE)
    except Exception:
        pass


def build_plan(strategy: str, entry_px: float, sl_px: Optional[float] = None,
               sl_pct: Optional[float] = None, side: str = "SHORT") -> Dict:
    """
    Builds absolute price levels for a SHORT or LONG position according to the strategy exit model.
    - SWING    (FIXED_PCT) :
        * SHORT: SL +10%, Trim -10%, TP2 -20%, Runner -35%.
        * LONG : SL -10%, Trim +10%, TP2 +20%, Runner +35%.
    - DAYTRADE (R_MULTIPLE):
        * SHORT: SL dinamis di atas pucuk, TP1 = entry - 1.0R, TP2 = entry - 2.0R.
        * LONG : SL dinamis di bawah jarum, TP1 = entry + 1.0R, TP2 = entry + 2.0R.
    """
    key = normalize_strategy(strategy)
    prof = get_profile(key)
    side = str(side or "SHORT").upper()

    if side == "LONG":
        if sl_px is None:
            pct = sl_pct if sl_pct else prof["sl_pct"]
            sl_px = entry_px * (1 - pct / 100.0)
        sl_pct_eff = (entry_px - sl_px) / entry_px * 100.0 if entry_px > 0 else prof["sl_pct"]

        if prof.get("exit_model") == "R_MULTIPLE":
            sl_min = float(prof.get("sl_min_pct", 2.5))
            sl_max = float(prof.get("sl_max_pct", 4.5))
            tp2_max = float(prof.get("tp2_max_pct", 9.0))
            if sl_pct_eff < sl_min or sl_pct_eff > sl_max:
                sl_pct_eff = max(sl_min, min(sl_max, sl_pct_eff if sl_pct_eff > 0 else float(prof["sl_pct"])))
                sl_px = entry_px * (1 - sl_pct_eff / 100.0)
            risk = entry_px - sl_px
            tp1_px = entry_px + prof.get("tp1_r", 1.0) * risk
            tp2_px = min(entry_px * (1 + tp2_max / 100.0), entry_px + prof.get("tp2_r", 2.0) * risk)
            runner_px = None
        else:
            tp1_px = entry_px * (1 + prof["trim_pct"] / 100.0)
            tp2_px = entry_px * (1 + prof["tp2_pct"] / 100.0)
            runner_px = entry_px * (1 + prof["runner_pct"] / 100.0) if prof.get("runner_pct") else None

        return {
            "strategy": key,
            "side": "LONG",
            "entry_px": entry_px,
            "sl_px": sl_px,
            "sl_pct": round(sl_pct_eff, 3),
            "tp1_px": tp1_px,
            "tp2_px": tp2_px,
            "runner_px": runner_px,
            "tp1_pct": round((tp1_px - entry_px) / entry_px * 100.0, 3) if entry_px > 0 else 0.0,
            "tp2_pct": round((tp2_px - entry_px) / entry_px * 100.0, 3) if entry_px > 0 else 0.0,
            "runner_pct": prof.get("runner_pct"),
            "max_hold_hours": prof["max_hold_hours"],
        }
    else:
        if sl_px is None:
            pct = sl_pct if sl_pct else prof["sl_pct"]
            sl_px = entry_px * (1 + pct / 100.0)
        sl_pct_eff = (sl_px - entry_px) / entry_px * 100.0 if entry_px > 0 else prof["sl_pct"]

        if prof.get("exit_model") == "R_MULTIPLE":
            sl_min = float(prof.get("sl_min_pct", 2.5))
            sl_max = float(prof.get("sl_max_pct", 4.5))
            tp2_max = float(prof.get("tp2_max_pct", 9.0))
            if sl_pct_eff < sl_min or sl_pct_eff > sl_max:
                sl_pct_eff = max(sl_min, min(sl_max, sl_pct_eff if sl_pct_eff > 0 else float(prof["sl_pct"])))
                sl_px = entry_px * (1 + sl_pct_eff / 100.0)
            risk = sl_px - entry_px
            tp1_px = entry_px - prof.get("tp1_r", 1.0) * risk
            tp2_px = max(entry_px * (1 - tp2_max / 100.0), entry_px - prof.get("tp2_r", 2.0) * risk)
            runner_px = None
        else:
            tp1_px = entry_px * (1 - prof["trim_pct"] / 100.0)
            tp2_px = entry_px * (1 - prof["tp2_pct"] / 100.0)
            runner_px = entry_px * (1 - prof["runner_pct"] / 100.0) if prof.get("runner_pct") else None

        return {
            "strategy": key,
            "side": "SHORT",
            "entry_px": entry_px,
            "sl_px": sl_px,
            "sl_pct": round(sl_pct_eff, 3),
            "tp1_px": tp1_px,
            "tp2_px": tp2_px,
            "runner_px": runner_px,
            "tp1_pct": round((entry_px - tp1_px) / entry_px * 100.0, 3) if entry_px > 0 else 0.0,
            "tp2_pct": round((entry_px - tp2_px) / entry_px * 100.0, 3) if entry_px > 0 else 0.0,
            "runner_pct": prof.get("runner_pct"),
            "max_hold_hours": prof["max_hold_hours"],
        }


def tag_position(coin: str, strategy: str, plan: Optional[Dict] = None, meta: Optional[Dict] = None) -> Dict:
    """Registers (or overwrites) the strategy tag & trade plan for a coin."""
    tags = _load()
    entry = {
        "coin": coin,
        "strategy": normalize_strategy(strategy),
        "tagged_at": datetime.now().isoformat(),
        "plan": plan or {},
        "meta": meta or {},
    }
    tags[coin] = entry
    _save(tags)
    return entry


def get_tag(coin: str) -> Optional[Dict]:
    return _load().get(coin)


def get_strategy(coin: str) -> str:
    """Returns the canonical strategy of a coin (untagged -> SWING)."""
    t = get_tag(coin)
    return normalize_strategy(t.get("strategy")) if t else "SWING"


def all_tags() -> Dict:
    return _load()


def remove_tag(coin: str) -> Optional[Dict]:
    """Removes a tag (called when the position is fully closed). Returns the removed tag."""
    tags = _load()
    removed = tags.pop(coin, None)
    if removed is not None:
        _save(tags)
    return removed
