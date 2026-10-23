#!/usr/bin/env python3
"""
INTRADAY — menu bot (2026-10-23; dulu "ShortHunter"). Istilah: Mesin (dulu daemon), Entry Otomatis (dulu saklar bot).
Swing multi-hari, Spot Vault, Gaji, AI War Room & Lab backtest lama dihapus dari menu atas keputusan user.

  [1] Screener intraday   [2] Order manual   [3] Posisi & tutup
  [4] Kontrol Mesin & Entry Otomatis (OFF/PAPER/LIVE)   [5] Jurnal & hasil PAPER
  [S] Sistem (akun, tes)  [0] Keluar
"""
import os
import sys
import time
import unicodedata
from typing import Dict, Optional

from src.trader import HyperliquidTrader
from src.position_manager import PositionManager
from src.profiles import get_profile
from src import position_tags, trading_mode, paper_trader

HOST_LABEL = os.getenv("INTRADAY_HOST_LABEL", "")   # mis. "VPS Sydney" (diisi di .env VPS)

# ---------------------------------------------------------------- util tampilan

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def display_width(text: str) -> int:
    w = 0
    for ch in str(text):
        if unicodedata.combining(ch) or ch in ("️", "‍"):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x1F300 else 1
    return w


def pad_str(text: str, target_width: int, align: str = "left") -> str:
    text = str(text)
    pad = max(0, target_width - display_width(text))
    return (" " * pad + text) if align == "right" else (text + " " * pad)


def fmt_price(p: float) -> str:
    p = float(p or 0)
    if p >= 1000:
        return f"{p:,.1f}"
    if p >= 1:
        return f"{p:,.4f}"
    return f"{p:.6f}"


def pause(msg: str = "\nTekan ENTER untuk kembali..."):
    try:
        input(msg)
    except EOFError:
        pass


# ---------------------------------------------------------------- header

def print_header(trader: Optional[HyperliquidTrader] = None):
    mode = trading_mode.get_mode()
    pid = PositionManager.is_daemon_running()
    print("═" * 72)
    print(" ⚡ INTRADAY — 15m (v3)" + (f"   🖥️  {HOST_LABEL}" if HOST_LABEL else ""))
    print("═" * 72)
    print(f" Mesin          : {'🟢 Berjalan (PID ' + str(pid) + ')' if pid else '⚪ Berhenti — tidak ada proses otomatis'}")
    print(f" Entry Otomatis : {trading_mode.LABELS[mode]}")
    try:
        t = trader or HyperliquidTrader()
        if t.can_read():
            acc = t.get_account_state()
            pos = [p for p in acc.get("positions", []) if float(p.get("size", 0)) > 0]
            ro = "  (baca-saja, tanpa kunci)" if t.is_read_only() else ""
            print(f" Ekuitas        : ${float(acc.get('equity', 0)):,.2f} | Margin bebas ${float(acc.get('available', 0)):,.2f} | Posisi {len(pos)}{ro}")
        else:
            print(" Akun           : ⚠️ alamat wallet Hyperliquid belum diatur (menu S)")
    except Exception as e:
        print(f" Akun           : ⚠️ gagal membaca akun ({e})")
    ps = paper_trader.summary()
    if ps["open"] or ps["closed"]:
        print(f" PAPER          : {ps['open']} terbuka | {ps['closed']} selesai | rata2 {ps['avg_r']:+.2f}R | total {ps['total_r']:+.2f}R")
    print("─" * 72)


MENU_ALIASES = {
    "1": "screener", "2": "manual", "3": "posisi", "4": "mesin", "5": "jurnal",
    "S": "sistem", "0": "exit", "Q": "exit", "EXIT": "exit",
}


def resolve_choice(choice: str) -> Optional[str]:
    return MENU_ALIASES.get((choice or "").strip().upper())


def print_main_menu():
    print("  [1] 🔍 Screener intraday (short & long)   [2] ⚡ Order manual")
    print("  [3] 📊 Posisi aktif & tutup posisi        [4] 🎚️  Kontrol Mesin & Entry")
    print("  [5] 📓 Jurnal & hasil PAPER               [S] ⚙️  Sistem")
    print("  [0] 🚪 Keluar")
    print("─" * 72)


# ---------------------------------------------------------------- [1] screener

def _print_candidates(df, side: str, n: int = 8):
    if df is None or df.empty:
        print(f"  (tidak ada kandidat {side})")
        return
    print(f"  {'Koin':<9}{'Skor':>6}  {'Status':<26}{'SL%':>6}{'TP2%':>7}  Catatan")
    for _, r in df.head(n).iterrows():
        note = (r.get("vetoes") or [])
        note = note[0] if isinstance(note, list) and note else ("tren OK" if r.get("trend_ok") else "melawan tren")
        print(f"  {r['coin']:<9}{r['score']:>6.1f}  {pad_str(str(r['verdict'])[:24], 26)}{r['sl_pct']:>6.2f}{r['tp2_pct']:>7.2f}  {str(note)[:28]}")


def menu_screener():
    from src.daytrade_engine import IntradayDaytradeEngine
    from src.daytrade_long_engine import IntradayLongEngine
    print("\n⏳ Memindai candle 15m (hanya candle tertutup)...")
    try:
        df_s = IntradayDaytradeEngine().scan_intraday_candidates(top_n=30)
        df_l = IntradayLongEngine().scan_intraday_candidates(top_n=30)
    except Exception as e:
        print(f"[!] Gagal memindai: {e}")
        return pause()
    print("\n🔴 INTRADAY SHORT")
    _print_candidates(df_s, "SHORT")
    print("\n🟢 INTRADAY LONG")
    _print_candidates(df_l, "LONG")
    print("\nCatatan: logika v3 BELUM teruji backtest. Gunakan mode PAPER untuk membuktikannya.")
    pause()


# ---------------------------------------------------------------- [2] order manual

def menu_manual_order():
    from src.daytrade_engine import IntradayDaytradeEngine
    from src.daytrade_long_engine import IntradayLongEngine
    trader = HyperliquidTrader()
    if not trader.is_configured():
        print("[!] Kunci Hyperliquid belum diatur (menu S).")
        return pause()
    coin = input("\nKoin (mis. SOL): ").strip().upper()
    if not coin:
        return
    side = input("Arah [S]hort / [L]ong: ").strip().upper()
    side = "LONG" if side.startswith("L") else "SHORT" if side.startswith("S") else ""
    if not side:
        print("[!] Arah tidak valid.")
        return pause()
    eng = IntradayLongEngine() if side == "LONG" else IntradayDaytradeEngine()
    a = eng.analyze_coin(coin)
    if not a.get("valid"):
        print(f"[!] Analisis gagal: {a.get('error')}")
        return pause()

    acc = trader.get_account_state(force_refresh=True)
    equity = float(acc.get("equity", 0.0))
    if any(p.get("coin") == coin and float(p.get("size", 0)) > 0 for p in acc.get("positions", [])):
        print(f"[!] Sudah ada posisi {coin}. Tutup dulu lewat menu [3] (anti-pyramiding).")
        return pause()

    prof = get_profile("DAYTRADE")
    sl_pct = float(a["sl_pct"])
    print("\n" + "─" * 72)
    print(f" {side} {coin} | Skor {a['score']} | {a['verdict']}")
    for r in a.get("reasons", [])[:10]:
        print(f"   • {r}")
    print(f" Harga ${fmt_price(a['current_px'])} | SL ${fmt_price(a['sl_px'])} ({sl_pct:.2f}%) | "
          f"TP1 ${fmt_price(a['tp1_px'])} (1R) | TP2 ${fmt_price(a['tp2_px'])} | Time-stop {prof['max_hold_hours']} jam")
    if not a.get("ready"):
        print(" ⚠️  Setup BELUM memenuhi semua gerbang v3 — order manual tetap bisa, risiko keputusan Anda.")

    try:
        risk_pct = float(input("Risiko per trade % ekuitas [1.0]: ").strip() or 1.0)
        lev = int(input("Leverage [2] (maks 3): ").strip() or 2)
    except ValueError:
        print("[!] Angka tidak valid.")
        return pause()
    risk_pct = max(0.1, min(risk_pct, 2.0))
    lev = max(1, min(lev, 3))
    notional = equity * risk_pct / 100.0 / (sl_pct / 100.0)
    margin = notional / lev
    if margin > float(acc.get("available", 0.0)) * 0.85:
        print(f"[!] Margin ${margin:.2f} melebihi 85% margin bebas. Kecilkan risiko/naikkan leverage.")
        return pause()
    print(f"\n Notional ${notional:,.2f} | Margin ${margin:,.2f} ({lev}x) | Rugi bila SL ≈ ${equity * risk_pct / 100:.2f}")
    if input("Ketik YES untuk kirim order: ").strip().upper() != "YES":
        print("Dibatalkan.")
        return pause()
    fn = trader.execute_long if side == "LONG" else trader.execute_short
    res = fn(coin=coin, usd_size=round(notional, 2), leverage=lev, sl_pct=sl_pct, tp_pct=float(a["tp2_pct"]),
             sl_px=float(a["sl_px"]), strategy="DAYTRADE", manual=True)
    if res.get("success"):
        position_tags.tag_position(coin=coin, strategy="DAYTRADE", plan=res.get("plan"),
                                   meta={"opened_via": "MENU_MANUAL_V3", "margin_usd": round(margin, 2), "side": side,
                                         "engine_version": a.get("engine_version"), "score": a.get("score")})
        PositionManager(trader).sync_active_positions()
        print(f"\n✅ Order {side} {coin} terkirim | entry ±${res.get('entry_est')} | SL on-chain ${res.get('stop_loss_px')}")
    else:
        print(f"\n[!] Gagal: {res.get('error')}")
    pause()


# ---------------------------------------------------------------- [3] posisi

def _close_coin(trader: HyperliquidTrader, coin: str):
    r = trader.close_position(coin)
    print(f"  {coin}: {'OK' if r.get('success') else 'GAGAL ' + str(r.get('error'))}")
    time.sleep(1.0)
    c = trader.cancel_coin_orders(coin)
    print(f"  {coin}: order sisa dibatalkan ({c.get('cancelled_count', 0)})")


def menu_positions():
    trader = HyperliquidTrader()
    if not trader.is_configured():
        print("[!] Kunci Hyperliquid belum diatur.")
        return pause()
    pm = PositionManager(trader)
    lc = pm.sync_active_positions() or {}
    acc = trader.get_account_state(force_refresh=True)
    pos = [p for p in acc.get("positions", []) if float(p.get("size", 0)) > 0]
    print("\n📊 POSISI AKTIF")
    if not pos:
        print("  (tidak ada posisi)")
        return pause()
    for p in pos:
        s = lc.get(p["coin"], {})
        strat = s.get("strategy", "?")
        lbl = "" if strat == "DAYTRADE" else "  ⚠️ LEGACY (tidak dikelola, tutup manual)"
        print(f"  {p['coin']:<8} {p.get('side'):<5} entry ${fmt_price(s.get('entry_px', p.get('entry_px', 0)))} | "
              f"SL ${fmt_price(s.get('current_sl_px', 0))} | uPnL ${float(p.get('unrealized_pnl', 0)):+.2f}{lbl}")
    ans = input("\nTutup: ketik KOIN (mis. SOL), SEMUA, atau ENTER untuk kembali: ").strip().upper()
    if not ans:
        return
    targets = [p["coin"] for p in pos] if ans == "SEMUA" else [ans]
    if ans == "SEMUA" and input(f"Yakin tutup {len(targets)} posisi (market)? ketik TUTUP: ").strip().upper() != "TUTUP":
        print("Dibatalkan.")
        return pause()
    for c in targets:
        _close_coin(trader, c)
    pause()


# ---------------------------------------------------------------- [4] Mesin & Entry Otomatis

def _sync_engine_state(mode: str):
    """Selaraskan state otonom lama dengan mode Entry Otomatis (dulu 'enabled'/'pause_new_positions')."""
    try:
        from src.autonomous_engine import AutonomousEngine
        eng = AutonomousEngine(HyperliquidTrader())
        st = eng.load_state()
        st["enabled"] = mode != "OFF"
        st["pause_new_positions"] = mode == "OFF"
        eng.save_state(st)
    except Exception as e:
        print(f"[!] Gagal menyelaraskan state otonom: {e}")


def menu_switch():
    while True:
        clear_screen()
        mode = trading_mode.get_mode()
        pid = PositionManager.is_daemon_running()
        print("═" * 72)
        print(" 🎚️  KONTROL MESIN & ENTRY OTOMATIS")
        print("═" * 72)
        print(f" Mesin          : {'🟢 Berjalan (PID ' + str(pid) + ')' if pid else '⚪ Berhenti'}")
        print(f" Entry Otomatis : {trading_mode.LABELS[mode]}")
        print(" Mesin = proses latar belakang tiap siklus. Wajib Berjalan untuk menjaga posisi (TP/time-stop) DAN untuk PAPER/LIVE.")
        print("─" * 72)
        print("  Entry Otomatis:  [1] ⏸️  OFF   [2] 📝 PAPER   [3] 🟢 LIVE")
        print("  Mesin:           [4] ▶️  Nyalakan / restart   [5] ⏹️  Hentikan")
        print("  [0] Kembali")
        sub = input("Pilih: ").strip()
        if sub in ("1", "2", "3"):
            new = {"1": "OFF", "2": "PAPER", "3": "LIVE"}[sub]
            if new == "LIVE":
                ps = paper_trader.summary()
                print(f"\n⚠️  LIVE = bot mengirim order SUNGGUHAN sendiri. Hasil PAPER sejauh ini: "
                      f"{ps['closed']} trade, rata2 {ps['avg_r']:+.2f}R, hari hijau {ps['green_days_pct']}%.")
                if ps["closed"] < 30:
                    print("    Saran: kumpulkan ≥30 trade PAPER dengan rata2 R > 0 sebelum LIVE.")
                if input("Ketik LIVE untuk konfirmasi: ").strip().upper() != "LIVE":
                    continue
            trading_mode.set_mode(new)
            _sync_engine_state(new)
            print(f"\n[✓] Mode = {new}")
            if new != "OFF" and not PositionManager.is_daemon_running():
                print("    Mesin berhenti — nyalakan dengan [4] agar mode ini berjalan.")
            pause("Tekan ENTER...")
        elif sub == "4":
            if sys.platform == "darwin" and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mesin_di_vps.flag")):
                print("\n⛔ Mesin INTRADAY berjalan di VPS. Menyalakan Mesin di Mac = DUA Mesin (order ganda).")
                print("   Kendalikan Mesin dari buka_intraday_vps.command. Hanya bila VPS sudah dihentikan:")
                if input("   ketik MAC untuk tetap menyalakan di Mac: ").strip().upper() != "MAC":
                    continue
            try:
                from src.position_manager import HOLD_FILE
                if os.path.exists(HOLD_FILE):
                    os.remove(HOLD_FILE)
            except Exception:
                pass
            if PositionManager.is_daemon_running():
                PositionManager.stop_daemon(hold=False)
            print(f"\n[✓] {PositionManager.start_daemon()['message']}")
            pause("Tekan ENTER...")
        elif sub == "5":
            print(f"\n[✓] {PositionManager.stop_daemon(hold=True)['message']}")
            print("    Catatan: tanpa Mesin, posisi hanya dilindungi SL on-chain (TP & time-stop tidak jalan).")
            pause("Tekan ENTER...")
        elif sub in ("0", ""):
            return


# ---------------------------------------------------------------- [5] jurnal

def menu_journal():
    ps = paper_trader.summary()
    print("\n📝 HASIL MODE PAPER (logika intraday v3, belum teruji backtest)")
    print(f"  Terbuka {ps['open']} | Selesai {ps['closed']} | Win {ps['win_rate']}% | Rata2 {ps['avg_r']:+.3f}R | "
          f"Total {ps['total_r']:+.2f}R | PF {ps['profit_factor']} | ${ps['pnl_usd']:+.2f} kertas")
    print(f"  Hari tercatat {ps['days']} | Hari hijau {ps['green_days_pct']}% | R per arah: {ps['by_side']}")
    rows = paper_trader._load()[-12:]
    for t in reversed(rows):
        res = f"{t.get('realized_r', 0):+.2f}R {t.get('exit_reason', '')}" if t.get("status") == "CLOSED" else "BERJALAN"
        print(f"   {t.get('opened_at', '')[5:16]} {t['side']:<5} {t['coin']:<8} skor {t.get('score')} | {res}")
    print("\n📓 JURNAL LIVE (forward-test)")
    try:
        from src.forward_test import ForwardTestManager
        print(ForwardTestManager().render_summary_dashboard(stream_filter="DAYTRADE"))
    except Exception as e:
        print(f"  (jurnal tidak bisa dibaca: {e})")
    pause()


# ---------------------------------------------------------------- [S] sistem

def menu_system():
    clear_screen()
    print(" ⚙️  SISTEM")
    print("  [1] 🔑 Setup & cek akun Hyperliquid")
    print("  [2] 🧪 Jalankan tes otomatis")
    print("  [0] Kembali")
    sub = input("Pilih: ").strip()
    if sub == "1":
        import setup_hyperliquid
        setup_hyperliquid.main()
        pause()
    elif sub == "2":
        os.system(f'"{sys.executable}" -m unittest discover tests')
        pause()


# ---------------------------------------------------------------- main

def main():
    while True:
        clear_screen()
        print_header()
        print_main_menu()
        action = resolve_choice(input("Pilih menu: "))
        if action == "screener":
            menu_screener()
        elif action == "manual":
            menu_manual_order()
        elif action == "posisi":
            menu_positions()
        elif action == "mesin":
            menu_switch()
        elif action == "jurnal":
            menu_journal()
        elif action == "sistem":
            menu_system()
        elif action == "exit":
            print("\nSampai jumpa. 🚀\n")
            sys.exit(0)
        else:
            pause("\nPilihan tidak valid. Tekan ENTER...")


main_menu = main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nKeluar dari program...")
        sys.exit(0)
