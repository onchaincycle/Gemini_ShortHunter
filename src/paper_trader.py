"""
Mode PAPER (2026-10-23): trade kertas untuk menguji logika intraday v3 tanpa order sungguhan.

Model eksekusi (sederhana & sengaja konservatif):
  - Entry di harga live saat sinyal + slippage 0,05% merugikan; biaya taker 0,045% per sisi.
  - Dipantau dari harga tengah (mid) tiap siklus daemon (±10–25 dtk) -> jarum singkat di antara polling bisa
    terlewat (keterbatasan; hasil PAPER bisa sedikit berbeda dari live).
  - TP1 (1R): tutup 50% & SL pindah ke impas+biaya. TP2 (2R): tutup sisa. SL: tutup sisa. Batas waktu 6 jam.
  - Hasil dicatat dalam R (kelipatan risiko) dan USD kertas (margin x leverage).
"""
import json
import os
import time
from typing import Dict, List, Optional

from src.paths import ROOT_DIR

PAPER_FILE = os.path.join(ROOT_DIR, "data", "paper_trades.json")
FEE_SIDE = 0.00045
SLIPPAGE = 0.0005


def _load(path: Optional[str] = None) -> List[Dict]:
    try:
        with open(path or PAPER_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(rows: List[Dict], path: Optional[str] = None):
    p = path or PAPER_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1, default=str)
    os.replace(tmp, p)


def open_trades(path: Optional[str] = None) -> List[Dict]:
    return [t for t in _load(path) if t.get("status") == "OPEN"]


def open_paper_trade(cand: Dict, audit: Optional[Dict] = None, margin_usd: float = 0.0, leverage: int = 2,
                     risk_pct: float = 0.0, equity: float = 0.0, now: Optional[float] = None,
                     path: Optional[str] = None) -> Dict:
    now = time.time() if now is None else now
    side = cand["side"].upper()
    sgn = 1 if side == "LONG" else -1
    px = float(cand["price"])
    entry = px * (1 + sgn * SLIPPAGE)
    sl_pct = float(cand["sl_pct"])
    sl_px = entry * (1 - sgn * sl_pct / 100.0)
    tp1_px = entry * (1 + sgn * float(cand["tp1_pct"]) / 100.0)
    tp2_px = entry * (1 + sgn * float(cand["tp2_pct"]) / 100.0)
    t = {
        "id": f"P_{cand['coin']}_{int(now)}", "status": "OPEN", "coin": cand["coin"], "side": side,
        "engine_version": cand.get("engine_version", ""), "score": cand.get("score"),
        "ai_grade": (audit or {}).get("conviction_grade"), "ai_conf": (audit or {}).get("confidence_score"),
        "opened_ts": now, "opened_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "entry_px": round(entry, 6), "sl_px": round(sl_px, 6), "init_sl_px": round(sl_px, 6),
        "tp1_px": round(tp1_px, 6), "tp2_px": round(tp2_px, 6), "sl_pct": sl_pct,
        "max_hold_hours": float(cand.get("max_hold_hours", 6)),
        "margin_usd": round(margin_usd, 2), "leverage": int(leverage), "notional_usd": round(margin_usd * leverage, 2),
        "risk_pct": round(risk_pct, 3), "equity_at_entry": round(equity, 2),
        "remaining": 1.0, "realized_r": 0.0, "tp1_hit": False, "reasons": cand.get("reasons", [])[:8],
    }
    rows = _load(path)
    rows.append(t)
    _save(rows, path)
    return t


def _leg_r(t: Dict, exit_px: float, frac: float) -> float:
    """R untuk sebagian posisi (frac) termasuk biaya keluar; biaya masuk dibebankan di leg pertama."""
    sgn = 1 if t["side"] == "LONG" else -1
    risk = abs(t["entry_px"] - t["init_sl_px"])
    if risk <= 0:
        return 0.0
    move = (exit_px - t["entry_px"]) * sgn
    fees = (exit_px + t["entry_px"]) * FEE_SIDE
    return frac * (move - fees) / risk


def update_paper_trades(mids: Dict[str, float], now: Optional[float] = None, path: Optional[str] = None) -> List[Dict]:
    """Memeriksa SL/TP/batas waktu untuk semua trade kertas terbuka. Mengembalikan daftar event yang terjadi."""
    now = time.time() if now is None else now
    rows = _load(path)
    events, changed = [], False
    for t in rows:
        if t.get("status") != "OPEN":
            continue
        px = float(mids.get(t["coin"], 0.0) or 0.0)
        if px <= 0:
            continue
        is_long = t["side"] == "LONG"
        hit = (lambda lvl: px >= lvl) if is_long else (lambda lvl: px <= lvl)
        hit_sl = (lambda lvl: px <= lvl) if is_long else (lambda lvl: px >= lvl)
        reason = None
        if not t["tp1_hit"] and hit(t["tp1_px"]):
            t["realized_r"] += _leg_r(t, t["tp1_px"], 0.5)
            t["remaining"] = 0.5
            t["tp1_hit"] = True
            be = t["entry_px"] * (1 + (2 * FEE_SIDE if is_long else -2 * FEE_SIDE))
            t["sl_px"] = round(be, 6)
            events.append({"type": "PAPER_TP1", "coin": t["coin"], "msg": f"📝 PAPER TP1 {t['side']} {t['coin']} @ {t['tp1_px']} (50% ditutup, SL ke impas)"})
            changed = True
        if t["remaining"] > 0 and hit(t["tp2_px"]):
            reason, exit_px = "TP2", t["tp2_px"]
        elif t["remaining"] > 0 and hit_sl(t["sl_px"]):
            reason, exit_px = ("BE_STOP" if t["tp1_hit"] else "SL"), t["sl_px"]
        elif (now - t["opened_ts"]) >= t["max_hold_hours"] * 3600:
            reason, exit_px = "TIME_6H", px
        if reason:
            t["realized_r"] += _leg_r(t, exit_px, t["remaining"])
            t["remaining"] = 0.0
            t["status"] = "CLOSED"
            t["exit_reason"] = reason
            t["exit_px"] = round(exit_px, 6)
            t["closed_ts"] = now
            t["closed_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            t["realized_r"] = round(t["realized_r"], 3)
            risk_usd = t["notional_usd"] * abs(t["entry_px"] - t["init_sl_px"]) / t["entry_px"]
            t["pnl_usd"] = round(t["realized_r"] * risk_usd, 2)
            events.append({"type": "PAPER_CLOSE", "coin": t["coin"],
                           "msg": f"📝 PAPER CLOSE {t['side']} {t['coin']} [{reason}] {t['realized_r']:+.2f}R (${t['pnl_usd']:+.2f} kertas)"})
            changed = True
    if changed:
        _save(rows, path)
    return events


def summary(path: Optional[str] = None) -> Dict:
    rows = _load(path)
    closed = [t for t in rows if t.get("status") == "CLOSED"]
    n = len(closed)
    rs = [float(t.get("realized_r", 0.0)) for t in closed]
    wins = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r < 0)
    by_day: Dict[str, float] = {}
    for t in closed:
        day = str(t.get("closed_at", ""))[:10]
        by_day[day] = by_day.get(day, 0.0) + float(t.get("pnl_usd", 0.0))
    return {
        "open": len([t for t in rows if t.get("status") == "OPEN"]),
        "closed": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "avg_r": round(sum(rs) / n, 3) if n else 0.0,
        "total_r": round(sum(rs), 2),
        "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None,
        "pnl_usd": round(sum(float(t.get("pnl_usd", 0.0)) for t in closed), 2),
        "days": len(by_day),
        "green_days_pct": round(sum(1 for v in by_day.values() if v > 0) / len(by_day) * 100, 1) if by_day else 0.0,
        "by_side": {s: round(sum(float(t.get("realized_r", 0)) for t in closed if t.get("side") == s), 2) for s in ("SHORT", "LONG")},
    }
