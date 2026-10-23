"""
Mode ENTRY OTOMATIS bot INTRADAY (keputusan user 2026-10-23 21:01; istilah "saklar bot" diganti 21:36).

  OFF   : bot tidak scan & tidak membuka posisi (hanya mengawal posisi yang ada).
  PAPER : bot scan + AI gatekeeper + mencatat "trade kertas" (data/paper_trades.json), TANPA order ke bursa.
  LIVE  : bot boleh membuka posisi intraday sendiri (order sungguhan).

Order manual dari menu selalu boleh, apa pun modenya.
Default bila file tidak ada / rusak = OFF (fail-safe).
Mode HANYA diubah oleh user lewat menu [4]. Agen/AI tidak boleh mengubah file ini.
"""
import json
import os
import time
from typing import Dict

from src.paths import ROOT_DIR

MODE_FILE = os.path.join(ROOT_DIR, "data", "trading_mode.json")
MODES = ("OFF", "PAPER", "LIVE")
LABELS = {
    "OFF": "⏸️  OFF — tidak buka posisi baru (posisi terbuka tetap dijaga)",
    "PAPER": "📝 PAPER — mencatat sinyal & trade kertas, tanpa order",
    "LIVE": "🟢 LIVE — boleh membuka posisi intraday sungguhan",
}


def get_mode(path: str = None) -> str:
    try:
        with open(path or MODE_FILE, "r", encoding="utf-8") as f:
            m = str(json.load(f).get("mode", "OFF")).upper()
        return m if m in MODES else "OFF"
    except Exception:
        return "OFF"


def set_mode(mode: str, path: str = None) -> Dict:
    mode = str(mode).upper()
    if mode not in MODES:
        return {"success": False, "error": f"Mode tidak valid: {mode} (pilih {', '.join(MODES)})"}
    p = path or MODE_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "changed_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
    os.replace(tmp, p)
    return {"success": True, "mode": mode}


def auto_entry_allowed(path: str = None) -> bool:
    """True hanya bila mode LIVE: satu-satunya kondisi bot boleh mengirim order pembuka sendiri."""
    return get_mode(path) == "LIVE"
