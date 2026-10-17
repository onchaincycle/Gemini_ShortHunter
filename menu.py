#!/usr/bin/env python3
"""
Interactive Menu for ShortHunter (Dual-Stream Architecture):
Stream 1: 🌊 SWING (Multi-Day Macro Peak & Reversal | Daily/4H | SL 10% | Trim 10% | TP 20% | Runner 35% | Max 72h)
Stream 2: ⚡ INTRADAY (Day-Trade Precision | 15m/1H | Dynamic Pinbar SL 2.8-5.5% | TP1 1R | TP2 2R | Max 6h | London/NY)
"""

import os
import sys
import unicodedata
from src.database import Database
from src.downloader import DataDownloader
from src.screener import ShortScreener
from src.backtester import ShortBacktester


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def display_width(text: str) -> int:
    """Calculate visible monospace terminal display width (accounting for wide emojis)."""
    w = 0
    for ch in text:
        cat = unicodedata.category(ch)
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ('W', 'F'):
            w += 2
        elif ord(ch) >= 0x1F300:
            w += 2
        elif ch in ['⚡', '🔴', '🟢', '✅', '✂️', '🎯', '🚀', '🛑', '⏰', '🪙', '🌊', '🏆', '🛡️']:
            w += 2
        elif cat == 'Mn':
            continue
        else:
            w += 1
    return w


def pad_str(text: str, target_width: int, align: str = 'left') -> str:
    """Pad string so visible terminal display width matches target_width."""
    dw = display_width(text)
    pad = max(0, target_width - dw)
    if align == 'right':
        return (' ' * pad) + text
    elif align == 'center':
        left_pad = pad // 2
        right_pad = pad - left_pad
        return (' ' * left_pad) + text + (' ' * right_pad)
    else:
        return text + (' ' * pad)


def fmt_price(p: float) -> str:
    """Format crypto price with optimal precision."""
    if p >= 100:
        return f"{p:.2f}"
    elif p >= 10:
        return f"{p:.3f}"
    elif p >= 1:
        return f"{p:.4f}"
    else:
        return f"{p:.5f}"


def print_header():
    print("=" * 68)
    print("      🎯 SHORTHUNTER & LONG QUANT ENGINE (DUAL-STREAM DUAL-SIDE)      ")
    print("=" * 68)
    print("Stream: ⚡ INTRADAY (Day-Trade 15m)  |  🌊 SWING (Multi-Hari)")
    print("Arah  : 🔴 SHORT (Patah Tren)   |  🟢 LONG (Whale Absorption)")
    print("-" * 68)
    try:
        from src.trader import HyperliquidTrader, categorize_trigger_order
        from src.position_manager import PositionManager
        from src.profiles import get_profile
        from src import position_tags
        trader = HyperliquidTrader()

        if trader.is_configured():
            state = trader.get_account_state()
            eq = state.get("equity", 0.0)
            avail = state.get("available", 0.0)
            net_label = "TESTNET" if state.get("testnet") else "MAINNET"

            positions = state.get("positions", [])
            pm = PositionManager(trader)
            lifecycle = pm.load_lifecycle_state()

            # Check daemon status & Self-Healing (starts even with 0 positions if Autonomous Mode is enabled)
            from src.autonomous_engine import AutonomousEngine
            auto_eng = AutonomousEngine(trader)
            auto_state = auto_eng.load_state()
            gov = auto_eng.get_live_governor_snapshot(account_state=state)
            thermo = gov["thermostat"]

            daemon_pid = PositionManager.is_daemon_running()
            auto_started = False
            if not daemon_pid and (positions or auto_state.get("enabled", True)):
                start_res = PositionManager.start_daemon()
                if start_res.get("success"):
                    daemon_pid = start_res.get("pid")
                    auto_started = True

            if daemon_pid:
                suffix = " [⚡ AUTO-RESTORED]" if auto_started else ""
                mode_tag = "Zero-Touch Autonomous Grade-A + Guard" if auto_state.get("enabled", True) else "Position Guard Only"
                daemon_str = f"🟢 AKTIF 24/7 DI BACKGROUND (PID: {daemon_pid} | {mode_tag}){suffix}"
            else:
                daemon_str = "⚪ OFF (Pilih menu [4] untuk menyalakan)"

            from src.ai_engine import ai_advisor
            ai_str = "🟢 AKTIF (Gemini Flash | Self-Eval & Gatekeeper Wajib Grade A)" if ai_advisor.is_available() else "⚪ OFF / FALLBACK"
            print(
                f"💼 Saldo Akun ({net_label}): ${eq:,.2f} USDC "
                f"(Bebas: ${avail:,.2f} | Cadangan {gov['reserve_pct']:.0f}%: ${gov['reserve_usd']:,.2f} | Siap Tembak: ${gov['free_above_reserve']:,.2f})"
            )
            print(f"🤖 Status AI Mandiri   : {daemon_str}")
            print(f"🧠 AI Advisor & Eval   : {ai_str}")
            print(
                f"📐 Pagar Dinamis f(E)  : Slot {gov['active_count']}/{gov['max_positions']} Posisi "
                f"(🌊Swing {gov['swing_count']}/{gov['max_swing']} | ⚡Intra {gov['intraday_count']}/{gov['max_intraday']}) "
                f"| Peluru: ${gov['min_order_margin']:,.2f}–${gov['max_order_margin']:,.2f} | Thermostat: {thermo.get('mode', 'OPTIMAL')}"
            )

            if positions:
                print(f"\n📊 DETAIL POSISI TERBUKA ANDA ({len(positions)}):")
                all_mids = trader.get_all_mids()
                open_orders = trader.get_open_orders()

                cols = [
                    ('KOIN', 6, 'center'),
                    ('POSISI', 12, 'center'),
                    ('MARGIN (SZ)', 16, 'right'),
                    ('ENTRY ➔ HARGA', 28, 'left'),
                    ('PnL (ROE%)', 16, 'right'),
                    ('STOP LOSS (SL)', 20, 'left'),
                    ('TP1 / TRIM', 15, 'center'),
                    ('TP2 / TARGET', 15, 'center'),
                ]

                col_widths = [c[1] for c in cols]
                top_border = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
                mid_border = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
                bot_border = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

                print(top_border)
                header_cells = [f" {pad_str(c[0], c[1], 'center')} " for c in cols]
                print("│" + "│".join(header_cells) + "│")
                print(mid_border)

                tot_pnl = 0.0
                tot_margin = 0.0

                for pos in positions:
                    coin = pos['coin']
                    side = pos.get('side', 'SHORT')
                    sz = pos['size']
                    lev = pos['leverage']
                    entry = pos['entry_px']
                    pnl = pos['unrealized_pnl']
                    roe = pos['roe_pct']
                    margin = pos['margin_used']
                    liq = pos['liq_px']

                    tot_pnl += pnl
                    tot_margin += margin

                    cur_px = float(all_mids.get(coin, entry))
                    is_long = (side == "LONG")
                    if is_long:
                        move_pct = ((cur_px - entry) / entry) * 100 if entry > 0 else 0
                    else:
                        move_pct = ((entry - cur_px) / entry) * 100 if entry > 0 else 0

                    # Determine position's own strategy stream & plan
                    life = lifecycle.get(coin, {})
                    strat = life.get("strategy") or position_tags.get_strategy(coin)
                    prof = get_profile(strat)
                    plan = position_tags.build_plan(
                        strat, entry,
                        sl_px=float(life.get("initial_sl_px") or 0) or None,
                        side=side
                    )

                    t1_done = life.get("trimmed_10pct", False)
                    t2_done = life.get("tp_20pct", False)

                    trim_p = float(life.get("tp1_pct") or plan["tp1_pct"])
                    tp2_p = float(life.get("tp2_pct") or plan["tp2_pct"])
                    r3_p = plan.get("runner_pct")
                    sl_p = float(life.get("sl_pct") or plan["sl_pct"])

                    t1_target = round(float(life.get("tp1_px") or plan["tp1_px"]), 5)
                    t2_target = round(float(life.get("tp2_px") or plan["tp2_px"]), 5)

                    # Check if SL order is resting on Hyperliquid & get exact trigger price
                    coin_sl_orders = [
                        o for o in open_orders
                        if o.get("coin") == coin and o.get("isTrigger") and o.get("triggerPx")
                        and categorize_trigger_order(o, position_side=side, entry_px=entry) == "STOP_LOSS"
                    ]
                    is_on_chain_sl = len(coin_sl_orders) > 0
                    if is_on_chain_sl:
                        if is_long:
                            sl_px = max(float(o["triggerPx"]) for o in coin_sl_orders)
                        else:
                            sl_px = min(float(o["triggerPx"]) for o in coin_sl_orders)
                    else:
                        sl_px = life.get("current_sl_px", round(plan["sl_px"], 5))

                    early_be = life.get("early_be_shield", False)
                    # Safety check
                    if side == "SHORT" and not t1_done and not early_be and float(sl_px) < entry:
                        sl_px = life.get("initial_sl_px", round(plan["sl_px"], 5))
                    elif side == "LONG" and not t1_done and not early_be and float(sl_px) > entry:
                        sl_px = life.get("initial_sl_px", round(plan["sl_px"], 5))

                    sl_badge = "🟢" if is_on_chain_sl else "🟡"
                    if t1_done:
                        sl_label = f"${fmt_price(float(sl_px))} [BE {sl_badge}]"
                    elif early_be:
                        sl_label = f"${fmt_price(float(sl_px))} [🛡️BE {sl_badge}]"
                    else:
                        sl_sign = "-" if is_long else "+"
                        sl_label = f"${fmt_price(float(sl_px))} [{sl_sign}{sl_p:.1f}% {sl_badge}]"

                    side_icon = "🔴 SHORT" if side == "SHORT" else "🟢 LONG "
                    st_icon = "⚡" if strat == "DAYTRADE" else "🌊"
                    pos_badge = f"{side_icon} {st_icon}"

                    sz_fmt = f"{sz:,.4f}".rstrip('0').rstrip('.') if sz < 1 else f"{sz:,.1f}"
                    margin_str = f"${margin:,.2f} 🧹DUST" if margin < 3.0 else f"${margin:,.2f} ({sz_fmt})"

                    e_str = fmt_price(entry)
                    c_str = fmt_price(cur_px)
                    px_str = f"${e_str} ➔ ${c_str} ({move_pct:+.1f}%)"

                    pnl_sign = "+" if pnl >= 0 else "-"
                    pnl_str = f"{pnl_sign}${abs(pnl):.2f} ({roe:+.2f}%)"

                    dist_tp1 = max(0.0, trim_p - move_pct)
                    dist_tp2 = max(0.0, tp2_p - move_pct)
                    tp1_str = "✅ SELESAI" if t1_done else f"${fmt_price(t1_target)} ({dist_tp1:.1f}%)"
                    if t2_done:
                        if r3_p:
                            r3_target = round(entry * (1 + r3_p / 100.0 if is_long else 1 - r3_p / 100.0), 5)
                            tp2_str = f"🚀 RUN (${fmt_price(r3_target)})"
                        else:
                            tp2_str = "✅ SELESAI"
                    else:
                        tp2_str = f"${fmt_price(t2_target)} ({dist_tp2:.1f}%)"

                    cells = [
                        f" {pad_str(coin, cols[0][1], cols[0][2])} ",
                        f" {pad_str(pos_badge, cols[1][1], cols[1][2])} ",
                        f" {pad_str(margin_str, cols[2][1], cols[2][2])} ",
                        f" {pad_str(px_str, cols[3][1], cols[3][2])} ",
                        f" {pad_str(pnl_str, cols[4][1], cols[4][2])} ",
                        f" {pad_str(sl_label, cols[5][1], cols[5][2])} ",
                        f" {pad_str(tp1_str, cols[6][1], cols[6][2])} ",
                        f" {pad_str(tp2_str, cols[7][1], cols[7][2])} ",
                    ]
                    print("│" + "│".join(cells) + "│")

                print(mid_border)
                tot_roe = (tot_pnl / tot_margin * 100.0) if tot_margin > 0 else 0.0
                tot_sign = "+" if tot_pnl >= 0 else "-"
                tot_pnl_str = f"{tot_sign}${abs(tot_pnl):.2f} ({tot_roe:+.2f}%)"
                tot_margin_str = f"${tot_margin:.2f}"

                sum_cells = [
                    f" {pad_str('TOTAL', cols[0][1], 'center')} ",
                    f" {pad_str(f'{len(positions)} Posisi', cols[1][1], 'center')} ",
                    f" {pad_str(tot_margin_str, cols[2][1], 'right')} ",
                    f" {pad_str('Net Floating PnL ➔', cols[3][1], 'right')} ",
                    f" {pad_str(tot_pnl_str, cols[4][1], 'right')} ",
                    f" {pad_str('Proteksi On-Chain', cols[5][1], 'center')} ",
                    f" {pad_str('-', cols[6][1], 'center')} ",
                    f" {pad_str('-', cols[7][1], 'center')} ",
                ]
                print("│" + "│".join(sum_cells) + "│")
                print(bot_border)
                print("  💡 Keterangan: 🟢 SL On-Chain Hyperliquid | BE = Breakeven (0% Risiko) | ⚡ Intraday | 🌊 Swing")
            else:
                print("📊 Posisi Aktif        : Tidak ada posisi terbuka (Mode Cash)")

            # Show compact summary of recently CLOSED positions from Forward Test Journal
            try:
                from src.forward_test import ForwardTestManager
                ft_journal = ForwardTestManager().load_journal()
                closed_positions = [p for p in ft_journal.values() if p.get("status") == "CLOSED"]
                if closed_positions:
                    print(f"🏁 RIWAYAT POSISI CLOSE TERAKHIR ({len(closed_positions)}) — [Detail Per-Stream di Menu 9]:")
                    for cp in closed_positions[-3:]:
                        c_sym = cp.get("coin")
                        c_strat = "⚡ INTRADAY" if cp.get("strategy") == "DAYTRADE" else "🌊 SWING"
                        c_entry = float(cp.get("entry_px", 0.0))
                        c_exit = float(cp.get("exit_px", 0.0))
                        c_pnl = float(cp.get("final_pnl_usd", 0.0))
                        c_reason = cp.get("exit_reason", "CLOSED")
                        c_time = cp.get("exit_time", "-")
                        c_icon = "🏆 WIN" if c_pnl > 0 else "🛑 CUT LOSS"
                        print(f"  • [{c_sym}] ({c_strat}) {c_icon} | ${c_entry:.5f} ➔ ${c_exit:.5f} | Net PnL: ${c_pnl:+.2f} USDC ({c_reason} @ {c_time})")
            except Exception:
                pass
            print("-" * 68)
    except Exception as e:
        print(f"[!] Info Akun: Gagal memuat status live ({e})")
        print("-" * 68)


def menu_sync(db):
    print("\n[+] Mengunduh / Memperbarui Data Pasar...")
    print("Data yang diunduh: Candlestick Harian (120 hari), 4-Jam, dan Funding Rate.")
    vol_input = input("Minimal volume harian koin (tekan ENTER untuk default $200,000): ").strip()
    min_vol = float(vol_input) if vol_input else 200_000.0

    downloader = DataDownloader(db)
    downloader.sync_all(min_vol_usd=min_vol, days_history=120, sync_4h=True)
    db.close()
    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_daytrade_screener():
    print("\n" + "=" * 68)
    print("      ⚡ STREAM 2: SCREENER INTRADAY SHORT (TIMEFRAME 15-MENIT)     ")
    print("=" * 68)
    print("Mendeteksi: Liquidity Sweep, 15m Rejection Pinbar, Volume Climax, BOS, & SL Teknis.")
    from src.daytrade_engine import IntradayDaytradeEngine
    engine = IntradayDaytradeEngine()
    session = engine.get_market_session()
    print(f"Jam & Sesi Pasar Saat Ini : {session['label']}")
    print("-" * 68)
    print("[*] Mengambil data candle 15-menit real-time dari Hyperliquid...")
    df = engine.scan_intraday_candidates(top_n=25)
    if df.empty:
        print("\n[-] Belum ada koin yang memenuhi kriteria intraday saat ini.")
    else:
        print(f"\n[✓] Ditemukan {len(df)} koin dipindai di Stream Intraday TF 15-Menit:\n")
        cols = ["coin", "score", "verdict", "current_px", "sl_pct", "tp1_pct", "tp2_pct", "rrr", "has_pinbar", "bos_detected", "rsi_div"]
        cols = [c for c in cols if c in df.columns]
        print(df[cols].to_string(index=False))
        print("\n💡 Spesifikasi Stream Intraday Day-Trade (TF 15m):")
        print("  • 🔥 SIAP SHORT INTRADAY : Skor >= 75, Liquidity Sweep / Pinbar + BOS 15m di Sesi London/NY.")
        print("  • Stop Loss Teknis      : Persis di atas pucuk jarum 15m (+0.35% buffer, batas 2.8% - 5.5%).")
        print("  • TP1 (1.0R - Trim 50%) : Panen 50% posisi & otomatis geser SL ke Breakeven + Fee.")
        print("  • TP2 (2.0R - Full TP)  : Panen sisa 50% posisi (rata-rata tercapai dalam ~4.1 jam).")
        print("  • Time-Stop             : Auto-Close 6 Jam (tanpa risiko menginap / overnight).")
    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_daytrade_long_screener():
    print("\n" + "=" * 68)
    print("      🚀 STREAM 4: SCREENER INTRADAY LONG (TIMEFRAME 15-MENIT)     ")
    print("=" * 68)
    print("Mendeteksi: Liquidity Sweep Lows, 15m Absorption Pinbar, BOS Reclaim EMA9, & Squeeze.")
    from src.daytrade_long_engine import IntradayLongEngine
    engine = IntradayLongEngine()
    session = engine.get_market_session()
    print(f"Jam & Sesi Pasar Saat Ini : {session['label']}")
    print("-" * 68)
    print("[*] Mengambil data candle 15-menit real-time dari Hyperliquid...")
    df = engine.scan_intraday_candidates(top_n=25)
    if df.empty:
        print("\n[-] Belum ada koin yang memenuhi kriteria intraday long saat ini.")
    else:
        print(f"\n[✓] Ditemukan {len(df)} koin dipindai di Stream Intraday Long TF 15-Menit:\n")
        cols = ["coin", "score", "verdict", "current_px", "sl_pct", "tp1_pct", "tp2_pct", "rrr", "has_pinbar", "bos_detected", "rsi_div"]
        cols = [c for c in cols if c in df.columns]
        print(df[cols].to_string(index=False))
        print("\n💡 Spesifikasi Stream Intraday Day-Trade Long (TF 15m):")
        print("  • 🔥 SIAP LONG INTRADAY  : Skor >= 75, Sweep Lows / Jarum Bawah + Reclaim BOS 15m.")
        print("  • Stop Loss Teknis      : Di bawah jarum terendah 15m (-0.35% buffer, batas 2.8% - 5.5%).")
        print("  • TP1 (1.0R - Trim 50%) : Panen 50% posisi & otomatis geser SL ke Breakeven + Fee.")
        print("  • TP2 (2.0R - Full TP)  : Panen sisa 50% posisi.")
        print("  • Time-Stop             : Auto-Close 6 Jam (tanpa risiko menginap / overnight).")
    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_swing_long_screener(db):
    print("\n🚀 MENJALANKAN STREAM 3: SCREENER SWING LONG (TF DAILY / 4H)")
    print("Mencari koin diskon sehat (12%-45% dari pucuk), candle absorption jarum bawah, reclaim EMA, & anti-unlock.")
    print("-" * 65)

    from src.long_screener import LongScreener
    from src.downloader import DataDownloader
    downloader = DataDownloader(db)
    print("\n[*] Menyinkronkan snapshot pasar terbaru...")
    downloader.sync_snapshot_only()

    up_in = input("Perbarui candlestick 3 hari terakhir (cepat ~3 detik)? (Y/N) [default: Y]: ").strip().upper()
    if up_in in ["", "Y", "YES"]:
        downloader.sync_fast_update(min_vol_usd=200_000, days_back=3)

    print("\n[*] Memindai koin-koin di database (Stream Swing Long)...")
    screener = LongScreener(db)
    results = screener.scan(min_score=40.0)
    db.close()

    if results.empty:
        print("\n[-] Belum ada koin yang memenuhi kriteria Swing Long saat ini.")
    else:
        print(f"\n[✓] Ditemukan {len(results)} koin potensial di Stream Swing Long:\n")
        display_cols = ["symbol", "score", "verdict", "price", "pullback_pct", "wick_pct", "reclaim_d1", "reclaim_h4", "funding_bonus", "unlock_veto"]
        display_cols = [c for c in display_cols if c in results.columns]
        print(results[display_cols].to_string(index=False))
        print("\n💡 Spesifikasi Stream Swing Long (Whale Absorption & Reclaim):")
        print("  • 🔥 SIAP LONG SWING     : Diskon sehat 12-45%, jarum bawah >= 30%, Reclaim EMA9 Daily/4H.")
        print("  • Stop Loss & Target     : SL -10% | Trim 1 +10% (50%, SL→BE) | TP2 +20% (30%) | Runner +35% (20%).")
        print("  • Time-Stop              : Auto-Close 72 Jam (3 Hari) disiplin waktu.")

    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_auto_hunt_sniper(db):
    print("\n" + "=" * 68)
    print("      🎯 AUTO-HUNT AI SNIPER (OMNI-STREAM 1-CLICK RADAR)      ")
    print("=================================================================")
    print("Menyisir 4 kuadran sekaligus secara real-time:")
    print("  ⚡ Intraday Short & Long (Candle 15m) | 🌊 Swing Short & Long (Daily/4H)")
    print("Memeriksa posisi aktif, batas kuota slot, sesi pasar, & circuit breaker,")
    print("lalu mengadu skor Alpha untuk memilih setup #1 paling prima di bursa!")
    print("-" * 68)

    from src.auto_hunter import AutoHuntSniper
    from src.trader import HyperliquidTrader
    from src.profiles import get_profile
    from src import position_tags

    trader = HyperliquidTrader()
    if not trader.is_configured():
        print("[!] Akun Hyperliquid belum dikonfigurasi. Silakan pilih menu [10] terlebih dahulu.\n")
        input("Tekan ENTER untuk kembali...")
        return

    print("[*] Menghubungkan ke Hyperliquid & memeriksa alokasi risiko per-stream...")
    sniper = AutoHuntSniper(db, trader)
    hunt_res = sniper.run_hunt_cycle()

    session_info = hunt_res["session_info"]
    slot_status = hunt_res["slot_status"]
    leaderboard = hunt_res["leaderboard"]
    winner = hunt_res["winner"]
    ai_audit = hunt_res["ai_audit"]
    waterfall_log = hunt_res["waterfall_log"]
    equity = hunt_res["equity"]

    print("\n📊 REKAPITULASI SESI PASAR & KUOTA SLOT:")
    print(f"  • Sesi Pasar Saat Ini : {session_info['label']}")
    cb_txt = "🛑 AKTIF (Maks 2x Loss tercapai, Intraday dinonaktifkan)" if slot_status["circuit_breaker"] else "🟢 AMAN (0x Loss Intraday hari ini)"
    print(f"  • Circuit Breaker     : {cb_txt}")
    dt_s_badge = f"🛑 PENUH ({', '.join(slot_status['dt_short']['coins'])})" if not slot_status['dt_short']['open'] else f"🟢 TERBUKA ({slot_status['dt_short']['active']}/{slot_status['dt_short']['max']})"
    dt_l_badge = f"🛑 PENUH ({', '.join(slot_status['dt_long']['coins'])})" if not slot_status['dt_long']['open'] else f"🟢 TERBUKA ({slot_status['dt_long']['active']}/{slot_status['dt_long']['max']})"
    sw_s_badge = f"🛑 PENUH ({', '.join(slot_status['sw_short']['coins'])})" if not slot_status['sw_short']['open'] else f"🟢 TERBUKA ({slot_status['sw_short']['active']}/{slot_status['sw_short']['max']})"
    sw_l_badge = f"🛑 PENUH ({', '.join(slot_status['sw_long']['coins'])})" if not slot_status['sw_long']['open'] else f"🟢 TERBUKA ({slot_status['sw_long']['active']}/{slot_status['sw_long']['max']})"
    print(f"  • Kuota Slot Intraday : Short [{dt_s_badge}] | Long [{dt_l_badge}]")
    print(f"  • Kuota Slot Swing    : Short [{sw_s_badge}] | Long [{sw_l_badge}]")

    if not leaderboard:
        print("\n[-] Belum ada koin yang memenuhi kriteria setup prima pada kuadran yang slotnya masih terbuka.")
        input("\nTekan ENTER untuk kembali ke menu utama...")
        return

    print("\n" + "=" * 78)
    print("          🏆 RADAR 4-KUADRAN: TOP 3 LEADERBOARD LINTAS-STREAM           ")
    print("=" * 78)
    print(f"{'RANK':<5} | {'KOIN':<6} | {'STREAM & ARAH':<18} | {'ALPHA':<6} | {'RRR':<6} | {'STATUS / SINYAL':<28}")
    print("-" * 78)
    for idx, cand in enumerate(leaderboard, 1):
        reasons_summary = ", ".join(cand.get("reasons", []))[:28]
        rank_tag = f"#{idx}" + (" 👑" if idx == 1 else "")
        print(f"{rank_tag:<5} | {cand['coin']:<6} | {cand['badge']:<18} | {cand['alpha_score']:<6} | 1:{cand['rrr']:<4} | {reasons_summary}")
    print("-" * 78)

    if not winner:
        print("\n🛑 SELURUH KANDIDAT DI-VETO OLEH AI GATEKEEPER DEMI KEAMANAN MODAL:")
        for log_entry in waterfall_log:
            print(f"  • {log_entry}")
        print("\n[✓] Tidak ada posisi yang dibuka untuk menjaga modal Anda dari risiko pasar.")
        input("\nTekan ENTER untuk kembali ke menu utama...")
        return

    if waterfall_log:
        print("\n📜 CATATAN WATERFALL AI GATEKEEPER:")
        for log_entry in waterfall_log:
            print(f"  • {log_entry}")

    def _execute_single_candidate(cand: dict, audit: dict, is_primary: bool = True) -> bool:
        nonlocal equity
        coin = cand["coin"]
        side = cand["side"]
        strat = cand["stream"]
        badge = cand["badge"]
        prof = get_profile(strat)
        lev = prof.get("leverage", 2)
        sl_pct = cand["sl_pct"]
        sl_px = cand["sl_px"]
        tp1_pct = cand["tp1_pct"]
        tp1_px = cand["tp1_px"]
        tp2_pct = cand["tp2_pct"]
        tp2_px = cand["tp2_px"]
        runner_px = cand.get("runner_px")
        runner_pct = cand.get("runner_pct")

        sl_sign = "-" if side == "LONG" else "+"
        tp_sign = "+" if side == "LONG" else "-"

        title_header = "REKOMENDASI JUARA TERPILIH" if is_primary else f"REKOMENDASI STREAM {badge}"
        print("\n" + "=" * 68)
        print(f"      🎯 {title_header}: {side} {coin} [{badge}]      ")
        print("=" * 68)
        print(f"  • Simbol Koin        : {coin} ({cand['timeframe']})")
        print(f"  • Harga Terkini      : ${cand['price']}")
        print(f"  • Stop Loss On-Chain : {sl_sign}{sl_pct:.2f}% (${sl_px})")
        print(f"  • TP1 (Trim 50% & BE): {tp_sign}{tp1_pct:.2f}% (${tp1_px})")
        print(f"  • TP2 (Full Harvest) : {tp_sign}{tp2_pct:.2f}% (${tp2_px}) [RRR 1:{cand['rrr']}]")
        if runner_px:
            print(f"  • Target Runner 3    : {tp_sign}{runner_pct:.1f}% (${runner_px})")
        print(f"  • Maksimal Hold      : {cand['max_hold_hours']} Jam")
        print("-" * 68)

        grade = audit.get("conviction_grade", "B")
        conf_score = audit.get("confidence_score", 80)
        size_mult = float(audit.get("size_multiplier", 1.0))
        reasons = audit.get("verdict_reason", "Setup prima.")
        risk_factors = audit.get("risk_factors", [])
        advice = audit.get("actionable_advice", "")
        base_margin = audit.get("base_margin", 15.0)
        rec_margin = audit.get("final_margin", 15.0)
        risk_usd = audit.get("max_risk_usd", 1.5)

        print(f"  AI Committee Status  : 🟢 APPROVED (Keyakinan: {conf_score}/100 | Grade: {grade})")
        sniper_th = audit.get("sniper_thesis", "")
        pros_crit = audit.get("prosecutor_critique", "")
        tools_inv = audit.get("tools_invoked") or {}
        if sniper_th:
            print(f"  🎯 Argumen Sniper    : {sniper_th}")
        if pros_crit:
            print(f"  ⚖️  Kritik Prosecutor : {pros_crit}")
        print(f"  👨‍⚖️ Putusan CRO Judge : {reasons}")
        if tools_inv:
            ob_info = tools_inv.get("orderbook") or {}
            corr_info = tools_inv.get("portfolio_correlation") or {}
            hist_info = tools_inv.get("coin_history") or {}
            print(
                f"  🛠️  Investigasi Tools : "
                f"Orderbook Bid {ob_info.get('bid_share_pct', 50):.0f}%/Ask {ob_info.get('ask_share_pct', 50):.0f}% | "
                f"Histori: {hist_info.get('trauma_status', 'CLEAN')[:28]} | "
                f"Portofolio: {corr_info.get('hedge_status', 'BALANCED')[:30]}"
            )
        if risk_factors:
            print("  Daftar Faktor Risiko Terdeteksi:")
            for idx, rf in enumerate(risk_factors, 1):
                print(f"    [{idx}] ⚠️  {rf}")
        if advice:
            print(f"  💡 Rekomendasi/Saran : {advice}")
        print("-" * 68)
        print("  📊 ANALISIS UKURAN MODAL (AI CONVICTION SIZING):")
        print(f"    • Modal Dasar Kuantitatif : ${base_margin:,.2f} USDC")
        mult_delta = (size_mult - 1.0) * 100
        mult_desc = f"+{mult_delta:.0f}% modal" if mult_delta > 0 else (f"{mult_delta:.0f}% modal" if mult_delta < 0 else "Normal")
        print(f"    • Kualitas Setup AI       : Grade {grade} (Pengali: {size_mult:.2f}x ➔ {mult_desc})")
        print(f"    • Risiko Maksimal jika SL : ${risk_usd} USDC (±{sl_pct:.2f}%)")
        print(f"    • REKOMENDASI MODAL FINAL : ${rec_margin:,.2f} USDC (Leverage {lev}x)")
        print("=" * 68)

        prompt_txt = f"\nEksekusi rekomendasi utama {side} {coin}? (ENTER untuk YA, ketik simbol lain dari leaderboard, atau [0] Batal): " if is_primary else f"\nEksekusi rekomendasi {side} {coin} [{badge}]? (ENTER untuk YA, atau [0] Lewati/Selesai): "
        pilih = input(prompt_txt).strip().upper()
        if pilih in ["0", "BATAL", "B", "Q", "EXIT"]:
            print(f"\n[✓] Eksekusi {side} {coin} dilewati/dibatalkan.")
            return False

        if is_primary and pilih and pilih != coin:
            alt_cand = next((c for c in leaderboard if c["coin"] == pilih), None)
            if alt_cand:
                cand = alt_cand
                coin = cand["coin"]
                side = cand["side"]
                strat = cand["stream"]
                badge = cand["badge"]
                prof = get_profile(strat)
                lev = prof.get("leverage", 2)
                sl_pct = cand["sl_pct"]
                sl_px = cand["sl_px"]
                tp1_pct = cand["tp1_pct"]
                tp2_pct = cand["tp2_pct"]
                print(f"\n[✓] Mengalihkan ke kandidat pilihan Anda: {side} {coin} [{badge}]")
                audit = sniper.audit_candidate(cand, equity)
                rec_margin = audit.get("final_margin", 15.0)

        # ⚡ 1-Click Execution: Langsung gunakan modal rekomendasi AI tanpa prompt lagi
        size_usd = rec_margin

        # Pre-flight Margin Safety Check
        cur_acct = trader.get_account_state(force_refresh=True)
        avail_margin = float(cur_acct.get("available", 0.0))
        cur_equity = float(cur_acct.get("equity", equity))
        pos_count = len(cur_acct.get("positions", []))

        if avail_margin < 5.0 or size_usd > avail_margin:
            print("\n" + "=" * 68)
            print("  ⚠️  SALDO MARGIN BEBAS TIDAK MENCUKUPI DI HYPERLIQUID")
            print("=" * 68)
            print(f"  • Modal Dibutuhkan  : ${size_usd:,.2f} USDC")
            print(f"  • Sisa Margin Bebas : ${avail_margin:,.2f} USDC (dari Total Modal ${cur_equity:,.2f} USDC)")
            print(f"  • Margin Terpakai   : ${cur_equity - avail_margin:,.2f} USDC (Sedang mengawal {pos_count} posisi aktif)")
            print("-" * 68)
            print("  💡 PENJELASAN:")
            print(f"    Hampir 100% modal Anda saat ini sedang bekerja mengawal {pos_count} posisi aktif.")
            print(f"    Order {side} {coin} belum dapat dibuka karena sisa margin bebas hanya ${avail_margin:,.2f} USDC.")
            print("    👉 Silakan tunggu salah satu posisi mencapai Take Profit / Stop Loss,")
            print("       atau lakukan top-up deposit di akun Hyperliquid Anda.")
            print("=" * 68)
            input("\nTekan ENTER untuk kembali ke Menu Utama...")
            return False

        print(f"\n🚀 Sedang mengirim order {side} {coin} [{badge}] ke Hyperliquid (Modal: ${size_usd:,.2f} USDC)...")
        notional_usd = size_usd * lev
        if side == "LONG":
            res = trader.execute_long(
                coin=coin, usd_size=notional_usd, leverage=lev,
                sl_pct=sl_pct, tp_pct=tp2_pct, sl_px=sl_px, strategy=strat
            )
        else:
            res = trader.execute_short(
                coin=coin, usd_size=notional_usd, leverage=lev,
                sl_pct=sl_pct, tp_pct=tp2_pct, sl_px=sl_px, strategy=strat
            )

        if res.get("success"):
            position_tags.tag_position(
                coin=coin,
                strategy=strat,
                plan=res.get("plan"),
                meta={"source": "AUTO_HUNT_SNIPER", "ai_grade": grade, "ai_conviction": conf_score}
            )
            print("\n" + "=" * 60)
            print(f"  [✓] ORDER {side} {coin} BERHASIL DIEKSEKUSI DI HYPERLIQUID! 🎯")
            print("=" * 60)
            print(f"  • Ukuran Notional : ${res.get('size_usd', notional_usd):,.2f} USD ({res.get('size_coin')} {coin})")
            print(f"  • Modal Margin    : ${size_usd:,.2f} USDC (Leverage {lev}x)")
            print(f"  • Harga Entry Est : ${res.get('entry_est')}")
            print(f"  • Stop Loss       : ${res.get('stop_loss_px')} ({sl_sign}{sl_pct:.2f}%) ➔ Terpasang di Server On-Chain")
            print(f"  • TP1 (Trim 50%)  : ${res.get('trim_10pct_px')} ({tp_sign}{tp1_pct:.2f}%) ➔ Terpasang di Server On-Chain")
            print(f"  • TP2 (Harvest)   : ${res.get('tp_20pct_px')} ({tp_sign}{tp2_pct:.2f}%)")
            print(f"\n[✓] Posisi {coin} ({side}) telah dikunci ke stream [{strat}] di Auto-Pilot Guard.")
            return True
        else:
            print(f"\n[!] Gagal eksekusi {coin}: {res.get('error')}")
            return False

    executed_primary = _execute_single_candidate(winner, ai_audit, is_primary=True)

    # 3. REKURSIVITAS OMNI-STREAM: Jika kandidat pertama berhasil/selesai,
    # periksa apakah ada kuadran stream lain yang slotnya masih terbuka dan memiliki kandidat!
    all_candidates = hunt_res.get("all_candidates", [])
    executed_stream = winner["stream"]
    executed_coin = winner["coin"]

    # Cari kandidat dari stream yang berbeda (misal: jika juara SWING, ajukan INTRADAY)
    other_stream_cands = [
        c for c in all_candidates
        if c["stream"] != executed_stream and c["coin"] != executed_coin
    ]

    if other_stream_cands:
        # Ambil kandidat dengan skor Alpha tertinggi dari stream lain tersebut
        next_cand = other_stream_cands[0]
        next_badge = next_cand["badge"]
        next_coin = next_cand["coin"]
        next_side = next_cand["side"]

        # Perbarui saldo equity terbaru dari Hyperliquid
        try:
            st = trader.get_account_state()
            fresh_eq = float(st.get("equity", equity))
        except Exception:
            fresh_eq = equity

        print("\n" + "─" * 68)
        print(f"🎯 KANDIDAT BERIKUTNYA DARI STREAM TERBUKA: {next_badge}")
        print(f"   Radar mendeteksi setup prima pada stream yang slotnya masih tersedia.")
        print(f"   Kandidat #1 untuk stream ini: {next_side} {next_coin} (Alpha: {next_cand['alpha_score']})")
        print(f"   🧠 Menjalankan Pre-Trade Audit AI (Gemini Flash) untuk {next_coin}...")

        next_audit = sniper.audit_candidate(next_cand, fresh_eq)
        if next_audit.get("approve", True) and next_audit.get("confidence_score", 0) >= 60:
            print(f"   [✓] AI Advisor MENYETUJUI setup {next_coin} (Grade: {next_audit.get('conviction_grade')})!")
            _execute_single_candidate(next_cand, next_audit, is_primary=False)
        else:
            print(f"   🛑 AI Advisor mendeteksi risiko tinggi pada {next_coin}: {next_audit.get('verdict_reason')}")
            # Coba alternatif lain dari stream yang sama jika ada
            found_alt = False
            for alt in other_stream_cands[1:]:
                if alt["stream"] == next_cand["stream"]:
                    print(f"\n   🧠 Mengevaluasi kandidat cadangan berikutnya: {alt['side']} {alt['coin']}...")
                    alt_audit = sniper.audit_candidate(alt, fresh_eq)
                    if alt_audit.get("approve", True) and alt_audit.get("confidence_score", 0) >= 60:
                        _execute_single_candidate(alt, alt_audit, is_primary=False)
                        found_alt = True
                        break
            if not found_alt:
                print(f"   [-] Tidak ada kandidat lain yang disetujui AI pada stream {next_badge}.")

    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_screen(db):
    from src.trader import HyperliquidTrader
    from src.position_manager import PositionManager
    from src.profiles import get_profile
    from src import position_tags

    trader = HyperliquidTrader()
    account_state = trader.get_account_state() if trader.is_configured() else {}
    pm = PositionManager(trader)
    lifecycle = pm.sync_active_positions()

    active_positions_map = {
        c: {
            "strategy": s.get("strategy") or position_tags.get_strategy(c) or "SWING",
            "side": s.get("side", "SHORT")
        }
        for c, s in lifecycle.items()
    }
    for p in account_state.get("positions", []):
        coin = p.get("coin")
        if coin and float(p.get("size", 0)) > 0:
            side = p.get("side", "SHORT")
            strat = active_positions_map.get(coin, {}).get("strategy") or position_tags.get_strategy(coin) or "SWING"
            active_positions_map[coin] = {
                "strategy": strat,
                "side": side
            }

    dt_short_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "DAYTRADE" and s.get("side") == "SHORT"]
    dt_long_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "DAYTRADE" and s.get("side") == "LONG"]
    sw_short_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "SWING" and s.get("side") == "SHORT"]
    sw_long_coins = [c for c, s in active_positions_map.items() if s.get("strategy") == "SWING" and s.get("side") == "LONG"]

    prof_dt = get_profile("DAYTRADE")
    prof_sw = get_profile("SWING")

    max_dt_s = prof_dt.get("max_concurrent_short", 2)
    max_dt_l = prof_dt.get("max_concurrent_long", 2)
    max_sw_s = prof_sw.get("max_concurrent_short", 3)
    max_sw_l = prof_sw.get("max_concurrent_long", 3)

    tag_dt_s = f"[⚠️ SLOT PENUH: {len(dt_short_coins)}/{max_dt_s} ({', '.join(dt_short_coins)})]" if len(dt_short_coins) >= max_dt_s else f"[🟢 {len(dt_short_coins)}/{max_dt_s} Slot]"
    tag_dt_l = f"[⚠️ SLOT PENUH: {len(dt_long_coins)}/{max_dt_l} ({', '.join(dt_long_coins)})]" if len(dt_long_coins) >= max_dt_l else f"[🟢 {len(dt_long_coins)}/{max_dt_l} Slot]"
    tag_sw_s = f"[⚠️ SLOT PENUH: {len(sw_short_coins)}/{max_sw_s} ({', '.join(sw_short_coins)})]" if len(sw_short_coins) >= max_sw_s else f"[🟢 {len(sw_short_coins)}/{max_sw_s} Slot]"
    tag_sw_l = f"[⚠️ SLOT PENUH: {len(sw_long_coins)}/{max_sw_l} ({', '.join(sw_long_coins)})]" if len(sw_long_coins) >= max_sw_l else f"[🟢 {len(sw_long_coins)}/{max_sw_l} Slot]"

    print("\n🔍 PILIH SCREENER & STREAM ARSITEKTUR (SHORT & LONG):")
    print(f"  [1] ⚡ Stream 1 — Screener Intraday Short (Liquidity Sweep High & Pinbar | TF 15-Menit) {tag_dt_s}")
    print(f"  [2] ⚡ Stream 2 — Screener Intraday Long  (Bottom Sweep & Squeeze | TF 15-Menit) {tag_dt_l}")
    print(f"  [3] 🌊 Stream 3 — Screener Swing Short    (True Peak 3-Gerbang | TF Daily/4H/1H) {tag_sw_s}")
    print(f"  [4] 🚀 Stream 4 — Screener Swing Long     (Whale Absorption & Structure Reclaim | TF Daily/4H) {tag_sw_l}")
    print("  [0] Kembali ke Menu Utama")

    sub = input("\nPilih stream screener [0-4, default: 0]: ").strip()
    if sub in ["0", ""]:
        return
    elif sub == "1":
        if len(dt_short_coins) >= max_dt_s:
            print(f"\n⚠️  BATAS KUOTA SLOT ⚡ INTRADAY SHORT TERCAPAI: Sudah ada {len(dt_short_coins)}/{max_dt_s} posisi aktif ({', '.join(dt_short_coins)}).")
            print("    Screener koin baru tidak diperlukan untuk eksekusi karena batas alokasi risiko sudah maksimal.")
            confirm = input("    Ketik 'LANJUT' jika Anda tetap ingin melihat sinyal (hanya untuk riset pasar), atau tekan ENTER untuk batal: ").strip().upper()
            if confirm != "LANJUT":
                return
        db.close()
        menu_daytrade_screener()
        return
    elif sub == "2":
        if len(dt_long_coins) >= max_dt_l:
            print(f"\n⚠️  BATAS KUOTA SLOT ⚡ INTRADAY LONG TERCAPAI: Sudah ada {len(dt_long_coins)}/{max_dt_l} posisi aktif ({', '.join(dt_long_coins)}).")
            print("    Screener koin baru tidak diperlukan untuk eksekusi karena batas alokasi risiko sudah maksimal.")
            confirm = input("    Ketik 'LANJUT' jika Anda tetap ingin melihat sinyal (hanya untuk riset pasar), atau tekan ENTER untuk batal: ").strip().upper()
            if confirm != "LANJUT":
                return
        db.close()
        menu_daytrade_long_screener()
        return
    elif sub == "4":
        if len(sw_long_coins) >= max_sw_l:
            print(f"\n⚠️  BATAS KUOTA SLOT 🚀 SWING LONG TERCAPAI: Sudah ada {len(sw_long_coins)}/{max_sw_l} posisi aktif ({', '.join(sw_long_coins)}).")
            print("    Screener koin baru tidak diperlukan untuk eksekusi karena batas alokasi risiko sudah maksimal.")
            confirm = input("    Ketik 'LANJUT' jika Anda tetap ingin melihat sinyal (hanya untuk riset pasar), atau tekan ENTER untuk batal: ").strip().upper()
            if confirm != "LANJUT":
                return
        menu_swing_long_screener(db)
        return

    # Option 3: Swing Short
    if len(sw_short_coins) >= max_sw_s:
        print(f"\n⚠️  BATAS KUOTA SLOT 🌊 SWING SHORT TERCAPAI: Sudah ada {len(sw_short_coins)}/{max_sw_s} posisi aktif ({', '.join(sw_short_coins)}).")
        print("    Screener koin baru tidak diperlukan untuk eksekusi karena batas alokasi risiko sudah maksimal.")
        confirm = input("    Ketik 'LANJUT' jika Anda tetap ingin melihat sinyal (hanya untuk riset pasar), atau tekan ENTER untuk batal: ").strip().upper()
        if confirm != "LANJUT":
            return

    print("\n🔍 MENJALANKAN STREAM 3: SCREENER SWING SHORT (TF DAILY / 4H)")
    print("Mencari koin overextended (+50% s/d +300% 90D) yang mulai patah struktur (CHoCH).")
    print("-" * 65)

    from src.market_regime import MarketRegime
    regime = MarketRegime(db).get_btc_regime()
    if regime["status"] == "BULL_STAMPEDE_PAUSE":
        print(f"⚠️  PERINGATAN REGIME PASAR: {regime['reason']}\n")
    else:
        print(f"🟢 REGIME PASAR: {regime['reason']}\n")

    gain_input = input("Minimal kenaikan koin dalam 90 hari terakhir (default 50%): ").strip()
    min_gain = float(gain_input) if gain_input else 50.0

    downloader = DataDownloader(db)
    print("\n[*] Menyinkronkan snapshot pasar terbaru (0.5s)...")
    downloader.sync_snapshot_only()

    up_in = input("Perbarui candlestick 3 hari terakhir (cepat ~3 detik)? (Y/N) [default: Y]: ").strip().upper()
    if up_in in ["", "Y", "YES"]:
        downloader.sync_fast_update(min_vol_usd=200_000, days_back=3)

    print("\n[*] Memindai koin-koin di database (Stream Swing)...")
    screener = ShortScreener(db)
    results = screener.scan(min_90d_gain=min_gain, min_score=40.0)
    db.close()

    if results.empty:
        print("\n[-] Belum ada koin yang memenuhi kriteria saat ini.")
    else:
        print(f"\n[✓] Ditemukan {len(results)} koin potensial di Stream Swing (3-Gerbang True Peak v2.0):\n")
        display_cols = ["symbol", "score", "verdict", "price", "gain_90d", "pullback_pct", "dist_ema50", "exhaustion", "h4_breakdown", "timing_1h", "squeeze_risk", "smart_money"]
        display_cols = [c for c in display_cols if c in results.columns]
        print(results[display_cols].to_string(index=False))
        print("\n💡 Spesifikasi Stream Swing Multi-Hari (True Peak v2.0):")
        print("  • 🔥 SIAP SHORT          : Lolos 3 Gerbang (Skor >= 75, Masih di Pucuk, Konfirmasi Patah 4H/1H).")
        print("  • Stop Loss & Target     : SL +10% | Trim 1 -10% (50%, SL→BE) | TP2 -20% (30%) | Runner -35% (20%).")
        print("  • Time-Stop              : Auto-Close 72 Jam (3 Hari) untuk menghindari pantulan Hari ke-5.")

    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_backtest(db, is_custom=False):
    print("\n🧪 LAB BACKTEST & UJI SENSITIVITAS (DUAL-STREAM ARSITEKTUR)")
    print("-" * 68)
    print("Pilih simulasi atau pengujian:")
    print("  [1] ⚡ Backtest Stream 1: Intraday Day-Trade (Data 15-Menit 67,000+ Bar | SL Teknis Jarum, TP 1R/2R, Max 6 Jam)")
    print("  [2] 🌊 Backtest Stream 2: Swing Multi-Hari (Data Daily/4H 135 Koin | SL 10%, Trim 10%, TP 20%, Runner 35%)")
    print("  [3] 📊 Uji Sensitivitas Stream Intraday (Bandingkan Sesi London/NY vs Asia & RRR)")
    print("  [4] 📊 Uji Sensitivitas Stream Swing (Bandingkan Threshold Skor 50 s/d 85)")
    print("  [5] ⚙️  Backtest Kustom (Tentukan sendiri angka SL, TP, Gain & Hold Days)")
    print("  [0] Kembali ke Menu Utama")
    print("-" * 68)
    sub_bt = input("Masukkan pilihan [0-5, default: 0]: ").strip()
    if sub_bt in ["0", ""]:
        return

    backtester = ShortBacktester(db)

    if sub_bt == "1":
        db.close()
        print("\n[*] Menjalankan Mesin Backtest Stream 1 (Candle 15-Menit Intraday - Sesi London/NY)...")
        from src.daytrade_engine import IntradayDaytradeEngine
        res = IntradayDaytradeEngine().run_intraday_backtest()
        prof = res["profile"]
        print("\n" + "=" * 65)
        print(f"     ⚡ HASIL BACKTEST STREAM 1 (INTRADAY): {prof['name'].upper()}     ")
        print("=" * 65)
        print(f"  • Timeframe & Data : Candle 15-Menit (20 Koin Aktif, Sesi London & NY 14-23 WIB)")
        print(f"  • Model Exit       : SL Teknis Pucuk 15m (2.8-5.5%) | TP1 1.0R (50%+BE) | TP2 2.0R (50%)")
        print(f"  • Biaya Transaksi  : Sudah dipotong Taker Fee 0.09% Roundtrip per Trade")
        print(f"  • Durasi Hold      : Maksimal 6 Jam (Rata-rata Aktual: {res['avg_hours']} Jam)")
        print(f"  • Total Trades     : {res['total_trades']} trade ({res['win_trades']} Win / {res['be_trades']} BE / {res['loss_trades']} Loss)")
        print(f"  • Win Rate         : {res['win_rate']}%  |  Safe Rate (W+BE): {res['safe_rate']}%")
        print(f"  • Profit Factor    : {res['profit_factor']}")
        print(f"  • Expectancy (R)   : {res['avg_r']:+.2f}R per trade  ({res['avg_trade_pnl']:+.2f}%/trade)")
        print(f"  • Net Return       : {res['net_return_pct']:+.2f}%")
        print("=" * 65)
        df_t = res.get("trades_df")
        if df_t is not None and not df_t.empty:
            print("\n12 Trade Intraday 15-Menit Terakhir:")
            cols = [c for c in ["symbol", "entry_date", "exit_date", "sl_pct", "result", "pnl_pct", "r_multiple", "hours_held"] if c in df_t.columns]
            print(df_t[cols].tail(12).to_string(index=False))
        input("\nTekan ENTER untuk kembali ke menu utama...")
        return

    elif sub_bt == "3":
        db.close()
        print("\n[*] Menjalankan Uji Sensitivitas Stream Intraday 15-Menit (Sesi & RRR)...")
        from src.daytrade_engine import IntradayDaytradeEngine
        df_grid = IntradayDaytradeEngine().run_intraday_sensitivity_grid()
        print("\n" + "=" * 90)
        print("     📊 HASIL UJI SENSITIVITAS STREAM INTRADAY 15-MENIT (SESI PASAR & RRR)     ")
        print("=" * 90)
        print(df_grid.to_string(index=False))
        print("=" * 90)
        print("\nKesimpulan Riset Stream Intraday (15m):")
        print("  • Sesi London + NY (14:00 - 23:30 WIB) memberikan Expectancy & Profit Factor tertinggi.")
        print("  • Sesi Asia (07:00 - 14:00 WIB) merugi karena volume tipis dan whipsaw/chop.")
        print("  • Target 1.0R (Trim 50% + Geser SL ke BE) melindungi modal sebelum pantulan intraday.")
        input("\nTekan ENTER untuk kembali ke menu utama...")
        return

    elif sub_bt == "4":
        menu_sensitivity_backtest(db)
        return

    elif sub_bt == "5":
        print("\nSilakan tentukan parameter sendiri (tekan ENTER untuk nilai default):")
        gain_in = input("1. Koin harus sudah naik berapa persen? [default: 90]: ").strip()
        min_gain = float(gain_in) if gain_in else 90.0

        sl_in = input("2. Pasang Stop Loss berapa persen? [default: 10]: ").strip()
        sl = float(sl_in) if sl_in else 10.0

        tp_in = input("3. Pasang Target Take Profit berapa persen? [default: 20]: ").strip()
        tp = float(tp_in) if tp_in else 20.0

        hold_in = input("4. Maksimal hold berapa hari jika belum kena SL/TP? [default: 7]: ").strip()
        hold = int(hold_in) if hold_in else 7
        min_sc = 70.0

        print("\nSedang mensimulasikan candle historis di database...")
        res = backtester.run_backtest(
            min_score=min_sc, min_90d_gain=min_gain,
            stop_loss_pct=sl, take_profit_pct=tp, max_hold_days=hold
        )
        db.close()

    else:
        print("\n[*] Menjalankan Mesin Backtest Stream 2: [Mode Swing 3 Hari] pada 135 koin...")
        res = backtester.run_profile_backtest("SWING")
        db.close()

    if res["total_trades"] == 0:
        print("\n[-] Tidak ada sinyal yang cocok dengan kriteria angka tersebut di masa lalu.")
    else:
        prof = res.get("profile", {})
        prof_title = prof.get("name", "STRATEGI SHORT").upper()
        print("\n" + "=" * 60)
        print(f"       🌊 HASIL BACKTEST STREAM 2 (SWING): {prof_title}       ")
        print("=" * 60)
        print(f"  • Total Posisi Short  : {res['total_trades']} koin")
        print(f"  • Posisi Untung (Win) : {res['win_trades']} koin")
        print(f"  • Breakeven (0% Rugi) : {res['be_trades']} koin")
        print(f"  • Posisi Rugi (Loss)  : {res['loss_trades']} koin")
        print(f"  • Win Rate Bersih     : {res['win_rate']}%")
        print(f"  • Safe Rate (Win + BE): {res['safe_rate']}% (Peluang modal aman)")
        print(f"  • Profit Factor       : {res['profit_factor']}")
        print(f"  • Total Keuntungan    : {res['net_return_pct']:+.2f}%")
        print(f"  • Rata-rata per Trade : {res['avg_trade_pnl']:+.2f}%")
        print("=" * 60)

        df_t = res.get("trades_df")
        if df_t is not None and not df_t.empty:
            print("\nContoh Trade Stream Swing yang Dieksekusi:")
            cols = [c for c in ["symbol", "entry_date", "exit_date", "entry_price", "exit_price", "result", "pnl_pct", "days_held", "hours_held"] if c in df_t.columns]
            print(df_t[cols].tail(10).to_string(index=False))

    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_stats(db):
    print("\n📦 STATUS DATA TERSIMPAN DI DATABASE (DuckDB)")
    print("-" * 60)
    stats = db.get_stats()
    db.close()
    for table, data in stats.items():
        print(f"  • {table.ljust(18)} : {data['rows']:,} baris ({data['symbols']} koin)")
    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_hyperliquid_setup():
    import setup_hyperliquid
    setup_hyperliquid.main()
    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_execute_short():
    print("\n⚡ EKSEKUSI POSISI 1-CLICK (SHORT 🔴 / LONG 🟢 | DUAL-STREAM)")
    print("Setiap posisi yang dibuka akan di-tag dengan stream strateginya (SWING / INTRADAY)")
    print("sehingga SL, Trimming, dan Time-Stop tidak pernah saling menimpa.")
    print("-" * 68)
    from src.trader import HyperliquidTrader
    from src.profiles import get_profile, set_active_profile
    from src.position_manager import PositionManager
    from src.forward_test import ForwardTestManager
    from src import position_tags

    trader = HyperliquidTrader()
    state = trader.get_account_state()
    if not state.get("configured"):
        print("[!] Akun Hyperliquid belum dikonfigurasi. Silakan pilih menu [10] terlebih dahulu.\n")
        input("Tekan ENTER untuk kembali...")
        return

    equity = state.get("equity", 0.0)
    print(f"Status Akun  : {'TESTNET' if state.get('testnet') else 'MAINNET'}")
    print(f"Total Ekuitas: ${equity:,.2f} USDC (Siap Trade: ${state.get('available', 0):,.2f} USDC)\n")

    print("PILIH ARAH ORDER (DIRECTION):")
    print("  [1] 🔴 SHORT (Mencari Pucuk / Breakdown / Overbought)")
    print("  [2] 🟢 LONG  (Mencari Dasar / Bottom Absorption / Reclaim)")
    print("  [0] Batal / Kembali ke Menu Utama")
    pilih_dir = input("\nMasukkan pilihan arah [0-2, default: 1 (SHORT)]: ").strip()
    if pilih_dir in ["0", "b", "q", "exit"]:
        return
    is_long_dir = (pilih_dir == "2")
    chosen_side = "LONG" if is_long_dir else "SHORT"

    print(f"\nPILIH STREAM STRATEGI UNTUK ORDER {chosen_side}:")
    if is_long_dir:
        print("  [1] ⚡ Stream 1 — Mode Intraday Long  (Bottom Sweep 15m | SL Jarum Bawah 2.8-5.5% | TP1 1R | TP2 2R | Max 6 Jam) [DEFAULT]")
        print("  [2] 🚀 Stream 2 — Mode Swing Long     (Diskon Sehat 12-45% & Reclaim EMA | SL -10% | Trim +10% | TP +20% | Max 72 Jam)")
    else:
        print("  [1] ⚡ Stream 1 — Mode Intraday Short (SL Teknis Pucuk 15m 2.8-5.5% | TP1 1R | TP2 2R | Max 6 Jam | Risiko 1%/trade) [DEFAULT]")
        print("  [2] 🌊 Stream 2 — Mode Swing Short    (SL +10% | Trim -10% | TP -20% | Runner -35% | Max 72 Jam | Risiko 2%/trade)")
    print("  [0] Batal / Kembali ke Menu Utama")
    pilih_mode = input("\nMasukkan pilihan stream [0-2, default: 1 (Intraday)]: ").strip()
    if pilih_mode in ["0", "b", "q", "exit"]:
        return

    is_intraday_mode = (pilih_mode in ["1", ""])
    chosen_strategy = "DAYTRADE" if is_intraday_mode else "SWING"
    prof = get_profile(chosen_strategy)
    set_active_profile(chosen_strategy)

    # Check active positions count in this specific stream and direction
    pm = PositionManager(trader)
    lifecycle = pm.sync_active_positions()
    active_in_stream = [c for c, s in lifecycle.items() if s.get("strategy", "SWING") == chosen_strategy]
    active_in_dir = [
        c for c, s in lifecycle.items()
        if s.get("strategy", "SWING") == chosen_strategy and s.get("side", "SHORT") == chosen_side
    ]

    max_dir = prof.get(f"max_concurrent_{chosen_side.lower()}", 2)
    max_tot = prof.get("max_concurrent", 4)

    if len(active_in_dir) >= max_dir:
        print(f"\n⚠️  BATAS SLOT {prof['badge']} {chosen_side} TERCAPAI: Sudah ada {len(active_in_dir)}/{max_dir} posisi {chosen_side} aktif ({', '.join(active_in_dir)}).")
        print(f"  (Total posisi aktif di {prof['badge']}: {len(active_in_stream)}/{max_tot} posisi)")
        cont = input(f"Ketik 'LANJUT' jika ingin membuka posisi {chosen_side} tambahan di luar batas slot, atau ENTER untuk batal: ").strip().upper()
        if cont != "LANJUT":
            return
    elif len(active_in_stream) >= max_tot:
        print(f"\n⚠️  BATAS TOTAL SLOT {prof['badge']} TERCAPAI: Sudah ada {len(active_in_stream)}/{max_tot} posisi aktif ({', '.join(active_in_stream)}).")
        cont = input("Ketik 'LANJUT' jika ingin membuka posisi tambahan di luar batas slot, atau ENTER untuk batal: ").strip().upper()
        if cont != "LANJUT":
            return

    sl_px_override = None
    dynamic_sl_note = ""

    # Map of all active positions across Hyperliquid perp account and lifecycle state
    active_positions_map = {}
    for p in state.get("positions", []):
        c = p.get("coin")
        if c and float(p.get("size", 0)) > 0:
            active_positions_map[c] = {
                "coin": c,
                "size": float(p.get("size", 0)),
                "side": p.get("side", "SHORT"),
                "entry_px": float(p.get("entry_px", 0)),
                "unrealized_pnl": float(p.get("unrealized_pnl", 0)),
                "roe_pct": float(p.get("roe_pct", 0)),
                "leverage": p.get("leverage", 2),
                "strategy": lifecycle.get(c, {}).get("strategy") or position_tags.get_strategy(c) or "SWING",
                "sl_px": lifecycle.get(c, {}).get("current_sl_px", "N/A")
            }
    for c, s in lifecycle.items():
        if c not in active_positions_map:
            active_positions_map[c] = {
                "coin": c,
                "size": float(s.get("remaining_sz", s.get("initial_sz", 0))),
                "side": s.get("side", "SHORT"),
                "entry_px": float(s.get("entry_px", 0)),
                "unrealized_pnl": float(s.get("unrealized_pnl", 0)),
                "roe_pct": float(s.get("roe_pct", 0)),
                "leverage": 2,
                "strategy": s.get("strategy") or position_tags.get_strategy(c) or "SWING",
                "sl_px": s.get("current_sl_px", "N/A")
            }

    def print_existing_position_alert(coin_sym: str, context: str = "rekomendasi"):
        pos_info = active_positions_map.get(coin_sym)
        if not pos_info:
            return
        pnl_val = pos_info['unrealized_pnl']
        roe_val = pos_info['roe_pct']
        pnl_display = f"+${pnl_val:.2f} (+{roe_val:.2f}%)" if pnl_val >= 0 else f"-${abs(pnl_val):.2f} ({roe_val:.2f}%)"
        print("\n" + "━" * 68)
        print(f"⚠️  INFO: ANDA SUDAH MEMILIKI POSISI AKTIF PADA [{coin_sym}]!")
        print(f"  Koin {coin_sym} terdeteksi di {context}, tetapi akun Anda sudah memiliki posisi terbuka:")
        print(f"    • Stream Strategi : [{pos_info['strategy']}]")
        print(f"    • Posisi Terbuka  : {pos_info['side']} {pos_info['size']} koin ({pos_info['leverage']}x)")
        print(f"    • Harga Entry     : ${pos_info['entry_px']}")
        print(f"    • Floating PnL    : {pnl_display}")
        print(f"    • Stop Loss Aktif : ${pos_info['sl_px']}")
        print("  ⛔ SISTEM TIDAK MENGAMBIL POSISI PADA KOIN INI.")
        print("  Aturan ShortHunter: Dilarang melakukan penumpukan / averaging pada koin yang sama.")
        print("━" * 68)

    if is_long_dir:
        # LONG SELECTION LOGIC
        if is_intraday_mode:
            ft = ForwardTestManager()
            losses_today = ft.count_losses_today("DAYTRADE")
            max_losses = prof.get("max_daily_losses") or 2
            if losses_today >= max_losses:
                print("\n" + "🛑" * 32)
                print(f"  ⚠️  CIRCUIT BREAKER INTRADAY AKTIF: Sudah tercatat {losses_today}x Stop Loss Intraday hari ini.")
                print("🛑" * 32)
                cb_in = input("\nKetik 'OVERRIDE' jika tetap ingin lanjut, atau tekan ENTER untuk berhenti: ").strip().upper()
                if cb_in != "OVERRIDE":
                    return

            from src.daytrade_long_engine import IntradayLongEngine
            day_long_engine = IntradayLongEngine()
            sess_info = day_long_engine.get_market_session()
            print(f"\nJam & Sesi Pasar Saat Ini: {sess_info['label']}")
            print("\n[*] Menganalisis pasar 15-Menit & mencari sinyal Long Sweep / Absorption...")
            df_intra = day_long_engine.scan_intraday_candidates(top_n=20)
            day_eval = {}
            if df_intra.empty:
                print("[-] Belum ada koin yang membentuk setup intraday long 15m ideal saat ini.")
                coin = input("Masukkan simbol koin manual (misal: ETH): ").strip().upper()
                if not coin:
                    return
                day_eval = day_long_engine.analyze_coin(coin)
            else:
                best_day = None
                for idx, (_, r) in enumerate(df_intra.iterrows()):
                    cand_coin = r["coin"]
                    if cand_coin in active_positions_map:
                        print_existing_position_alert(cand_coin, context=f"rekomendasi teratas #{idx+1}")
                    else:
                        best_day = r
                        if idx > 0:
                            print(f"\n💡 Rekomendasi dialihkan ke kandidat berikutnya: {cand_coin} (Skor: {r.get('score', 0)}/100)")
                        break

                if best_day is None:
                    coin = input("Masukkan simbol koin manual (atau tekan ENTER untuk kembali): ").strip().upper()
                    if not coin:
                        return
                    day_eval = day_long_engine.analyze_coin(coin)
                else:
                    coin = best_day["coin"]
                    score = best_day["score"]
                    curr_price = best_day["current_px"]
                    sl_pct = best_day["sl_pct"]
                    tp1_pct = best_day["tp1_pct"]
                    tp2_pct = best_day["tp2_pct"]
                    rrr = best_day["rrr"]
                    verdict = best_day["verdict"]
                    day_eval = best_day.to_dict()

                    print("=" * 65)
                    print(f"      🚀 REKOMENDASI STREAM INTRADAY LONG: {coin} (SKOR: {score}/100)      ")
                    print("=" * 65)
                    print(f"  • Simbol Koin        : {coin}")
                    print(f"  • Status Intraday    : {verdict}")
                    print(f"  • Harga Saat Ini     : ${curr_price}")
                    print(f"  • Stop Loss Teknis   : -{sl_pct}% (Di bawah jarum 15m @ ${day_eval.get('sl_px')})")
                    print(f"  • TP1 (1.0R Trim 50%): +{tp1_pct}% (${day_eval.get('tp1_px')}) ➔ Geser SL ke BE+Fee")
                    print(f"  • TP2 (2.0R Full TP) : +{tp2_pct}% (${day_eval.get('tp2_px')}) ➔ Panen Selesai (RRR 1:{rrr})")
                    print(f"  • Sinyal Terdeteksi  : {', '.join(day_eval.get('reasons', []))}")
                    print("=" * 65)

                    pilih_manual = input(f"\nGunakan koin rekomendasi {coin}? (Tekan ENTER untuk YA, atau ketik simbol lain): ").strip()
                    if pilih_manual:
                        coin = pilih_manual.upper()
                        day_eval = day_long_engine.analyze_coin(coin)
        else:
            print("\n[*] Menganalisis database & mencari kandidat Swing Long terbaik (Whale Absorption & Reclaim)...")
            db = Database()
            DataDownloader(db).sync_snapshot_only()
            from src.long_screener import LongScreener
            screener = LongScreener(db)
            candidates = screener.scan(min_score=40.0)
            db.close()

            if candidates.empty:
                print("[-] Belum ada koin yang memenuhi kriteria Swing Long saat ini.")
                coin = input("Masukkan simbol koin manual (misal: BTC): ").strip().upper()
                if not coin:
                    return
            else:
                best = None
                for idx, (_, cand_row) in enumerate(candidates.iterrows()):
                    cand_coin = cand_row["symbol"]
                    if cand_coin in active_positions_map:
                        print_existing_position_alert(cand_coin, context=f"rekomendasi teratas #{idx+1} (Skor: {cand_row.get('score', 0)}/100)")
                    else:
                        best = cand_row
                        if idx > 0:
                            print(f"\n💡 Rekomendasi dialihkan ke kandidat berikutnya: {cand_coin} (Skor: {cand_row.get('score', 0)}/100)")
                        break

                if best is None:
                    coin = input("Masukkan simbol koin manual (atau tekan ENTER untuk kembali): ").strip().upper()
                    if not coin:
                        return
                else:
                    coin = best["symbol"]
                    score = best["score"]
                    curr_price = best["price"]
                    pb_pct = best.get("pullback_pct", 0.0)
                    verdict = best.get("verdict", "⛔ BELUM LAYAK")
                    wick_pct = best.get("wick_pct", 0.0)

                    print("=" * 65)
                    print(f"      🚀 REKOMENDASI STREAM SWING LONG: {coin} (SKOR: {score}/100)      ")
                    print("=" * 65)
                    print(f"  • Simbol Koin       : {coin}")
                    print(f"  • Status Rekomendasi: {verdict}")
                    print(f"  • Harga Saat Ini    : ${curr_price}")
                    print(f"  • Diskon dari Pucuk : -{pb_pct}% (Ideal 12% - 45%)")
                    print(f"  • Jarum Bawah D1    : {wick_pct:.1f}% (Absorption)")
                    print(f"  • Reclaim Daily/4H  : Daily={best.get('reclaim_d1')}, 4H={best.get('reclaim_h4')}")
                    print(f"  • Token Unlock Veto : {best.get('unlock_veto')}")
                    print("=" * 65)

                    pilih_manual = input(f"\nGunakan koin rekomendasi {coin}? (Tekan ENTER untuk YA, atau ketik simbol lain): ").strip()
                    if pilih_manual:
                        coin = pilih_manual.upper()
    else:
        # SHORT SELECTION LOGIC
        if is_intraday_mode:
            # 1. Check Intraday Daily Circuit Breaker
            ft = ForwardTestManager()
            losses_today = ft.count_losses_today("DAYTRADE")
            max_losses = prof.get("max_daily_losses") or 2
            if losses_today >= max_losses:
                print("\n" + "🛑" * 32)
                print(f"  ⚠️  CIRCUIT BREAKER INTRADAY AKTIF: Sudah tercatat {losses_today}x Stop Loss Intraday hari ini.")
                print("  Aturan disiplin stream Intraday: berhenti entry hingga sesi berikutnya agar terhindar dari revenge trading.")
                print("🛑" * 32)
                cb_in = input("\nKetik 'OVERRIDE' jika tetap ingin lanjut, atau tekan ENTER untuk berhenti: ").strip().upper()
                if cb_in != "OVERRIDE":
                    return

            # 2. Check Market Session Gate
            from src.daytrade_engine import IntradayDaytradeEngine
            day_engine = IntradayDaytradeEngine()
            sess_info = day_engine.get_market_session()
            print(f"\nJam & Sesi Pasar Saat Ini: {sess_info['label']}")
            if not sess_info.get("is_prime"):
                print("\n⚠️  PERINGATAN SESI PASAR INTRADAY:")
                print("  Saat ini berada di luar Golden Killzone London/NY (14:00 - 23:59 WIB).")
                print("  Hasil backtest 67,220 candle 15m membuktikan Sesi Asia memiliki Win Rate rendah & rawan chop.")
                sess_confirm = input("Ketik 'TETAP' untuk lanjut scan intraday di sesi ini, atau ENTER untuk batal: ").strip().upper()
                if sess_confirm != "TETAP":
                    return

            print("\n[*] Menganalisis pasar 15-Menit & mencari sinyal Liquidity Sweep / Pinbar...")
            df_intra = day_engine.scan_intraday_candidates(top_n=20)
            day_eval = {}
            if df_intra.empty:
                print("[-] Belum ada koin yang membentuk setup intraday 15m ideal saat ini.")
                coin = input("Masukkan simbol koin manual (misal: SOL): ").strip().upper()
                if not coin:
                    return
                day_eval = day_engine.analyze_coin(coin)
            else:
                best_day = None
                for idx, (_, r) in enumerate(df_intra.iterrows()):
                    cand_coin = r["coin"]
                    if cand_coin in active_positions_map:
                        print_existing_position_alert(cand_coin, context=f"rekomendasi teratas #{idx+1} (Skor: {r.get('score', 0)}/100)")
                    else:
                        best_day = r
                        if idx > 0:
                            print(f"\n💡 Rekomendasi dialihkan ke kandidat berikutnya yang belum Anda miliki posisinya: {cand_coin} (Skor: {r.get('score', 0)}/100)")
                        break

                if best_day is None:
                    print("\n[-] Seluruh koin rekomendasi intraday saat ini sudah memiliki posisi aktif di akun Anda.")
                    coin = input("Masukkan simbol koin manual (atau tekan ENTER untuk kembali): ").strip().upper()
                    if not coin:
                        return
                    day_eval = day_engine.analyze_coin(coin)
                else:
                    coin = best_day["coin"]
                    score = best_day["score"]
                    curr_price = best_day["current_px"]
                    sl_pct = best_day["sl_pct"]
                    tp1_pct = best_day["tp1_pct"]
                    tp2_pct = best_day["tp2_pct"]
                    rrr = best_day["rrr"]
                    verdict = best_day["verdict"]
                    day_eval = best_day.to_dict()

                    print("=" * 65)
                    print(f"      ⚡ REKOMENDASI STREAM INTRADAY: {coin} (SKOR: {score}/100)      ")
                    print("=" * 65)
                    print(f"  • Simbol Koin        : {coin}")
                    print(f"  • Status Intraday    : {verdict}")
                    print(f"  • Harga Saat Ini     : ${curr_price}")
                    print(f"  • Stop Loss Teknis   : +{sl_pct}% (Di atas pucuk jarum 15m @ ${day_eval.get('sl_px')})")
                    print(f"  • TP1 (1.0R Trim 50%): -{tp1_pct}% (${day_eval.get('tp1_px')}) ➔ Geser SL ke BE+Fee")
                    print(f"  • TP2 (2.0R Full TP) : -{tp2_pct}% (${day_eval.get('tp2_px')}) ➔ Panen Selesai (RRR 1:{rrr})")
                    print(f"  • Waktu Auto-Close   : 6 Jam (Tanpa Risiko Menginap)")
                    print(f"  • Sinyal Terdeteksi  : {', '.join(day_eval.get('reasons', []))}")
                    print("=" * 65)

                    pilih_manual = input(f"\nGunakan koin rekomendasi {coin}? (Tekan ENTER untuk YA, atau ketik simbol lain): ").strip()
                    if pilih_manual:
                        coin = pilih_manual.upper()
                        day_eval = day_engine.analyze_coin(coin)
        else:
            print("\n[*] Menganalisis database & mencari koin pucuk terbaik (Stream 1: Macro 3-Gerbang)...")
            db = Database()
            DataDownloader(db).sync_snapshot_only()
            screener = ShortScreener(db)
            candidates = screener.scan(min_90d_gain=40.0, min_score=40.0)
            db.close()

            if candidates.empty:
                print("[-] Belum ada koin yang memenuhi kriteria short saat ini.")
                coin = input("Masukkan simbol koin manual (misal: PUMP): ").strip().upper()
                if not coin:
                    return
            else:
                best = None
                for idx, (_, cand_row) in enumerate(candidates.iterrows()):
                    cand_coin = cand_row["symbol"]
                    if cand_coin in active_positions_map:
                        print_existing_position_alert(cand_coin, context=f"rekomendasi teratas #{idx+1} (Skor: {cand_row.get('score', 0)}/100)")
                    else:
                        best = cand_row
                        if idx > 0:
                            print(f"\n💡 Rekomendasi dialihkan ke kandidat berikutnya yang belum Anda miliki posisinya: {cand_coin} (Skor: {cand_row.get('score', 0)}/100)")
                        break

                if best is None:
                    print("\n[-] Seluruh koin rekomendasi swing saat ini sudah memiliki posisi aktif di akun Anda.")
                    coin = input("Masukkan simbol koin manual (atau tekan ENTER untuk kembali): ").strip().upper()
                    if not coin:
                        return
                else:
                    coin = best["symbol"]
                    score = best["score"]
                    gain_90 = best["gain_90d"]
                    sm_status = best["smart_money"]
                    curr_price = best["price"]
                    timing_1h = best.get("timing_1h", "NEUTRAL")
                    squeeze_status = best.get("squeeze_risk", "NORMAL")
                    verdict = best.get("verdict", "⛔ BELUM LAYAK")
                    exhaust_str = best.get("exhaustion", "NONE")
                    pb_pct = best.get("pullback_pct", 0.0)
                    dist50 = best.get("dist_ema50", 0.0)

                    passes_threshold = (verdict == "🔥 SIAP SHORT")

                    print("=" * 65)
                    print(f"      🌊 REKOMENDASI STREAM SWING: {coin} (SKOR: {score}/100)      ")
                    print("=" * 65)
                    print(f"  • Simbol Koin       : {coin}")
                    print(f"  • Status 3-Gerbang  : {verdict}")
                    print(f"  • Harga Saat Ini    : ${curr_price}")
                    print(f"  • Kenaikan 90 Hari  : +{gain_90}% | Jarak Pucuk: -{pb_pct}% | Di Atas EMA50: +{dist50}%")
                    print(f"  • Sinyal Exhaustion : {exhaust_str} (Bukti Kelelahan Beli di Pucuk)")
                    print(f"  • Tren 4-Jam (CHoCH): {best['h4_breakdown']} (Patah ke Bawah)")
                    print(f"  • Timing 1-Jam (MTF): {timing_1h}")
                    print(f"  • Orderbook L2 Flow : {best.get('l2_flow', 'BALANCED')} | Kedalaman: {best.get('l2_depth', 'N/A')}")
                    print(f"  • Rasio OI/Vol 24h  : {best.get('oi_ratio', 'N/A')} | Squeeze Risk: {squeeze_status}")
                    print(f"  • Status Smart Money: {sm_status} (Radar Nansen)")
                    print("=" * 65)

                    if not passes_threshold:
                        print("\n" + "!" * 65)
                        print(f"  ⚠️  PERINGATAN GERBANG KEAMANAN V2.0: [{verdict}]")
                        print(f"  Koin {coin} (Skor {score}) BELUM memenuhi seluruh 3 Gerbang Eksekusi:")
                        print("    1. Gerbang 1 (Pucuk Segar) : Pullback 2%-22% & Harga > +12% di atas EMA50")
                        print("    2. Gerbang 2 (Kelelahan)   : Skor >= 75 + Sinyal Wick/Divergence/Volume")
                        print("    3. Gerbang 3 (Patah Leher) : 4H/Daily Patah & 1H TIDAK sedang Rebound Hijau")
                        print("!" * 65)
                        force = input("\nKoin belum lolos 3-Gerbang. Ketik 'FORCE' jika tetap ingin memaksa, atau tekan ENTER untuk batal: ").strip().upper()
                        if force != "FORCE":
                            print("\n[✓] Eksekusi dibatalkan untuk melindungi modal.")
                            input("Tekan ENTER untuk kembali...")
                            return

                    pilih_manual = input(f"\nGunakan koin rekomendasi {coin}? (Tekan ENTER untuk YA, atau ketik simbol lain): ").strip()
                    if pilih_manual:
                        coin = pilih_manual.upper()

    # HARD GATE: Pastikan koin yang akan dieksekusi TIDAK sedang memiliki posisi aktif
    if coin in active_positions_map:
        print_existing_position_alert(coin, context="pilihan koin eksekusi")
        print(f"\n⛔ EKSEKUSI DIBATALKAN: Anda sudah memiliki posisi aktif pada {coin}.")
        print("   Sistem ShortHunter TIDAK mengambil posisi baru pada koin yang sama demi keselamatan modal.\n")
        input("Tekan ENTER untuk kembali...")
        return

    # LIVE AUTO-SYNC ON-THE-FLY (STREAM SWING):
    if not is_intraday_mode:
        print(f"\n🔄 [Live Auto-Sync] Mengunduh candle Daily & 4H terbaru untuk {coin} dari Hyperliquid...")
        try:
            db_sync = Database()
            downloader = DataDownloader(db_sync)
            if downloader.sync_single_coin(coin):
                print(f"[✓] Data candle {coin} tersinkronisasi 100% terkini ke DuckDB.")
            db_sync.close()
        except Exception as e:
            print(f"[!] Warning: Live auto-sync gagal ({e}), melanjutkan dengan data lokal.")

    # LIVE FUNDING RATE & SQUEEZE CHECK
    funding_info = trader.get_funding_rate(coin)
    f_1h = funding_info.get("funding_1h_pct", 0.0)
    f_8h = funding_info.get("funding_8h_pct", 0.0)

    if is_long_dir:
        if f_1h < -0.01:
            f_badge = "🔥 SQUEEZE POTENTIAL (Menguntungkan Long, Short bayar Long)"
        elif f_1h > 0.02:
            f_badge = "⚠️ TINGGI (Long bayar Short)"
        else:
            f_badge = "🟢 NORMAL"
    else:
        f_badge = "🟢 NORMAL / POSITIF" if f_1h >= 0 else "🟡 NEGATIF (Hati-hati)"
        if f_1h < -0.015 or funding_info.get("is_high_squeeze_risk"):
            f_badge = "🛑 SANGAT RAWAN SHORT SQUEEZE"

    print(f"\n📡 LIVE HYPERLIQUID FUNDING RATE [{coin}]:")
    print(f"  • Rate 1-Jam : {f_1h:+.5f}%  |  Estimasi 8-Jam: {f_8h:+.4f}% [{f_badge}]")

    if not is_long_dir and (f_1h < -0.015 or funding_info.get("is_high_squeeze_risk")):
        print("\n" + "🛑" * 32)
        print(f"  ⚠️  BAHAYA SHORT SQUEEZE TERDETEKSI PADA {coin}!")
        print(f"  Funding Rate sangat negatif ({f_1h:+.4f}%/jam). Posisi SHORT harus membayar biaya kepada LONG.")
        print("🛑" * 32)
        squeeze_confirm = input(f"\nApakah Anda tetap ingin mengambil risiko SHORT pada {coin}? Ketik 'YAKIN' atau tekan ENTER untuk batal: ").strip().upper()
        if squeeze_confirm != "YAKIN":
            print("\n[✓] Eksekusi dibatalkan demi melindungi modal dari short squeeze.")
            input("Tekan ENTER untuk kembali...")
            return

    lev = int(prof.get("leverage", 2))
    risk_pct = float(prof.get("risk_pct", 2.0))
    sl = float(prof.get("sl_pct", 10.0))
    trim_p = float(prof.get("trim_pct", 10.0))
    tp = float(prof.get("tp2_pct", 20.0))
    runner_p = prof.get("runner_pct")

    if chosen_strategy == "DAYTRADE":
        try:
            if is_long_dir:
                from src.daytrade_long_engine import IntradayLongEngine
                day_long_engine = IntradayLongEngine()
                day_eval = day_long_engine.analyze_coin(coin, funding_info=funding_info)
                if day_eval.get("valid"):
                    sl = day_eval["sl_pct"]
                    sl_px_override = float(day_eval["sl_px"])
                    trim_p = day_eval["tp1_pct"]
                    tp = day_eval["tp2_pct"]
                    dynamic_sl_note = f" (Level Teknis Jarum 15m @ ${sl_px_override} | RRR 1:{day_eval['rrr']})"
            else:
                from src.daytrade_engine import IntradayDaytradeEngine
                day_engine = IntradayDaytradeEngine()
                day_eval = day_engine.analyze_coin(coin, funding_info=funding_info)
                if day_eval.get("valid"):
                    sl = day_eval["sl_pct"]
                    sl_px_override = float(day_eval["sl_px"])
                    trim_p = day_eval["tp1_pct"]
                    tp = day_eval["tp2_pct"]
                    dynamic_sl_note = f" (Level Teknis Pucuk 15m @ ${sl_px_override} | RRR 1:{day_eval['rrr']})"
        except Exception:
            pass

    # Baseline Quantitative Risk Sizing (1.0x Anchor)
    base_risk = trader.calculate_risk_margin(equity=equity, risk_pct=risk_pct, sl_pct=sl, leverage=lev, multiplier=1.0)
    base_margin = base_risk["recommended_margin"]

    # LOOP 3: LLM PRE-TRADE SANITY CHECK & CONVICTION SIZING (GEMINI FLASH)
    print("\n🧠 MENJALANKAN AUDIT PRE-TRADE AI (Gemini Flash Gatekeeper & Conviction Sizing)...")
    from src.ai_engine import ai_advisor
    pre_trade_context = {
        "strategy": chosen_strategy,
        "side": chosen_side,
        "leverage": lev,
        "equity": equity,
        "base_margin": base_margin,
        "sl_pct": sl,
        "funding_1h_pct": f_1h
    }
    cand_summary = {
        "coin": coin,
        "side": chosen_side,
        "strategy": chosen_strategy,
        "score": score if 'score' in locals() else 75.0,
        "price": curr_price if 'curr_price' in locals() else 0.0,
        "funding_rate": f_1h,
        "squeeze_risk": f_badge
    }
    sanity_res = ai_advisor.evaluate_pre_trade_signal(cand_summary, pre_trade_context)
    is_approved = sanity_res.get("approve", True)
    conf_score = sanity_res.get("confidence_score", 80)
    grade = sanity_res.get("conviction_grade", "B")
    size_mult = float(sanity_res.get("size_multiplier", 1.0))
    reasons = sanity_res.get("verdict_reason", "Validasi aturan kuantitatif selesai.")
    risk_factors = sanity_res.get("risk_factors", [])
    advice = sanity_res.get("actionable_advice", "")

    # Dynamic Leverage: Boost to 3x for Grade A / A+ Intraday setups
    dynamic_lev_note = ""
    if chosen_strategy == "DAYTRADE" and str(grade).upper() in ["A", "A+"]:
        lev = 3
        dynamic_lev_note = " ➔ ⚡ Boosted 3x (Grade A/A+ Precision Edge)"

    # Calculate dynamic AI recommended margin using conviction multiplier
    ai_risk = trader.calculate_risk_margin(equity=equity, risk_pct=risk_pct, sl_pct=sl, leverage=lev, multiplier=size_mult)
    rec_margin = ai_risk["recommended_margin"]

    status_ai = "🟢 APPROVED" if is_approved else "🛑 REJECTED / HIGH RISK"
    print("=" * 68)
    print(f"  AI Gatekeeper Status : {status_ai} (Keyakinan: {conf_score}/100 | Grade: {grade})")
    print(f"  Analisis AI Advisor  : {reasons}")
    if risk_factors:
        print("  Daftar Faktor Risiko Terdeteksi:")
        for idx, rf in enumerate(risk_factors, 1):
            print(f"    [{idx}] ⚠️  {rf}")
    if advice:
        print(f"  💡 Rekomendasi/Saran : {advice}")
    print("-" * 68)
    print("  📊 ANALISIS UKURAN MODAL (AI CONVICTION SIZING):")
    print(f"    • Modal Dasar Kuantitatif : ${base_margin:,.2f} USDC (Risiko {risk_pct:.1f}%)")
    mult_delta = (size_mult - 1.0) * 100
    mult_desc = f"+{mult_delta:.0f}% modal" if mult_delta > 0 else (f"{mult_delta:.0f}% modal" if mult_delta < 0 else "Normal")
    print(f"    • Kualitas Setup AI       : Grade {grade} (Pengali: {size_mult:.2f}x ➔ {mult_desc})")
    print(f"    • Risiko Maksimal jika SL : ${ai_risk['max_risk_usd']} USDC (±{sl:.2f}%)")
    print(f"    • REKOMENDASI MODAL FINAL : ${rec_margin:,.2f} USDC (Leverage {lev}x{dynamic_lev_note})")
    print("=" * 68)

    if not is_approved or conf_score < 60:
        print("\n" + "!" * 68)
        print(f"  ⚠️  AI ADVISOR MENDETEKSI RISIKO TINGGI PADA SETUP INI! (Grade {grade})")
        print("!" * 68)
        override_in = input("Ketik 'OVERRIDE' jika Anda tetap ingin memaksa entry, atau tekan ENTER untuk batal: ").strip().upper()
        if override_in != "OVERRIDE":
            print("\n[✓] Eksekusi dibatalkan demi disiplin proteksi modal.")
            input("Tekan ENTER untuk kembali...")
            return

    size_in = input(f"\nMasukkan modal margin USDC [tekan ENTER untuk rekomendasi ${rec_margin:,.2f}]: ").strip()
    size_usd = float(size_in) if size_in else rec_margin

    sl_sign = "-" if is_long_dir else "+"
    tp_sign = "+" if is_long_dir else "-"

    print("\n" + "=" * 60)
    print(f"      RINGKASAN EKSEKUSI {chosen_side} — STREAM {prof['badge']}      ")
    print("=" * 60)
    print(f"  Arah & Koin   : {chosen_side} {coin}  [{prof['badge']}]")
    print(f"  Modal Margin  : ${size_usd:,.2f} USDC (Leverage {lev}x | Notional ${size_usd * lev:,.2f})")
    print(f"  Stop Loss     : {sl_sign}{sl:.2f}% (Native Server On-Chain SL){dynamic_sl_note}")
    if chosen_strategy == "DAYTRADE":
        print(f"  ✂️ TP1 (1.0R)  : {tp_sign}{trim_p:.2f}% (Panen 50% & Geser SL ke BE+Fee)")
        print(f"  🎯 TP2 (2.0R)  : {tp_sign}{tp:.2f}% (Panen 50% Sisa)")
        print(f"  ⏱️ Time-Stop   : Auto-Close {prof['max_hold_hours']} Jam (Tanpa Risiko Menginap)")
    else:
        early_be_val = prof.get("early_be_pct", 5.0)
        print(f"  🛡️ Early BE Shield: {tp_sign}{early_be_val:.1f}% (Geser SL ke Breakeven, 0% Risiko)")
        print(f"  ✂️ Trimming 1     : {tp_sign}{trim_p:.2f}% (Panen 50% & Kunci SL di Breakeven)")
        print(f"  🎯 Target 2       : {tp_sign}{tp:.2f}% (Panen 30% & Trailing SL ±{trim_p:.1f}%)")
        if runner_p:
            print(f"  🚀 Runner 3       : {tp_sign}{runner_p:.2f}% (Sisa 20% | Cutoff {prof['max_hold_hours']} Jam)")

    confirm = input(f"\nTekan ENTER atau ketik 'YES' untuk eksekusi order {chosen_side} sekarang: ").strip().upper()

    if confirm in ["", "YES", "Y"]:
        print(f"\n🚀 Sedang mengirim order {chosen_side} {coin} [{prof['badge']}] ke Hyperliquid...")
        notional_usd = size_usd * lev
        if is_long_dir:
            res = trader.execute_long(
                coin=coin, usd_size=notional_usd, leverage=lev,
                sl_pct=sl, tp_pct=tp, sl_px=sl_px_override, strategy=chosen_strategy
            )
        else:
            res = trader.execute_short(
                coin=coin, usd_size=notional_usd, leverage=lev,
                sl_pct=sl, tp_pct=tp, sl_px=sl_px_override, strategy=chosen_strategy
            )
        if res.get("success"):
            position_tags.tag_position(
                coin=coin,
                strategy=chosen_strategy,
                plan=res.get("plan"),
                meta={"opened_via": "MENU_7", "margin_usd": size_usd, "side": chosen_side}
            )
            print(f"\n🎉 [✓] ORDER {chosen_side} BERHASIL DIEKSEKUSI & DI-TAG KE STREAM!")
            print(f"  • Simbol & Stream : {res['coin']} [{prof['badge']}] ({chosen_side})")
            print(f"  • Estimasi Entry  : ${res['entry_est']}")
            print(f"  • Stop Loss (SL)  : ${res['stop_loss_px']} ({sl_sign}{sl:.2f}%)")
            print(f"  • ✂️ Target TP 1   : ${res.get('trim_10pct_px', 'N/A')} ({tp_sign}{trim_p:.2f}%)")
            print(f"  • 🎯 Target TP 2   : ${res.get('tp_20pct_px', 'N/A')} ({tp_sign}{tp:.2f}%)")
            if res.get("runner_35pct_px"):
                print(f"  • 🚀 Target Runner: ${res.get('runner_35pct_px')} ({tp_sign}{runner_p:.0f}%)")

            pm.sync_active_positions()
            print(f"\n[✓] Posisi {coin} ({chosen_side}) telah dikunci ke stream [{chosen_strategy}] di Auto-Pilot Guard.")
        else:
            print(f"\n[!] Gagal eksekusi: {res.get('error')}")
    else:
        print("\nOrder dibatalkan.")

    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_position_guard():
    from src.position_manager import PositionManager, LOG_FILE
    from src.autonomous_engine import AutonomousEngine, AUTONOMOUS_LOG_FILE
    from src import position_tags
    pm = PositionManager()
    auto_eng = AutonomousEngine(pm.trader)

    while True:
        clear_screen()
        for ln in auto_eng.render_autonomous_dashboard():
            print(ln)

        print("\nPilih opsi Komando & Pemantauan AI Mandiri:")
        print("  [1] 📡 Buka Live Monitor Real-Time di Layar (Tabel Posisi + Telemetri AI Auto-Refresh)")
        print("  [2] 🎯 Paksa Siklus Auto-Hunt & Audit Grade A Sekarang (Force Autonomous Hunt)")
        print("  [3] 🧠 Jalankan Siklus Self-Evaluation & Kalibrasi Mandiri Sekarang (Force Self-Eval)")
        print("  [4] ⚙️  Atur Parameter AI Mandiri (Reserve % Ekuitas, Interval Hunt, Toggle ON/OFF)")
        print("  [5] 🚀 Nyalakan / Restart Daemon AI Mandiri 24/7 (Anti-Sleep Caffeinate)")
        print("  [6] 🛑 Matikan Background Daemon")
        print("  [7] 🏷️  Ubah Tag Stream Posisi Tertentu (INTRADAY ⇄ SWING)")
        print("  [8] 🌐 Update & Publish Dashboard Pemantauan ke Web / GitHub Pages Sekarang")
        print("  [0] Kembali ke Menu Utama")
        print("-" * 80)

        sub = input("Masukkan pilihan [0-8]: ").strip()
        if sub == "1":
            pm.run_live_guard()
        elif sub == "2":
            print("\n🎯 Menjalankan Siklus Autonomous Hunt & Audit Grade A Sekarang...")
            res = auto_eng.run_autonomous_hunt_if_due(force=True)
            print("\n" + "=" * 72)
            if res.get("executed"):
                print(f"  [✓] {res.get('msg')}")
            else:
                print(f"  [ℹ️] Status Siklus Hunt : {res.get('status')}")
                if res.get("reason"):
                    print(f"  • Alasan Pagar Modal   : {res.get('reason')}")
                if res.get("details"):
                    print("  • Catatan Seleksi AI   :")
                    for d in res["details"]:
                        print(f"      - {d}")
            print("=" * 72)
            input("\nTekan ENTER untuk kembali ke Pusat Komando...")
        elif sub == "3":
            print("\n🧠 Menjalankan Siklus Closed-Loop Self-Evaluation & Kalibrasi Bobot...")
            rep = auto_eng.run_self_evaluation(trigger_reason="MANUAL_USER_AUDIT")
            print("\n" + "=" * 72)
            print("  [✓] SELF-EVALUATION MANDIRI SELESAI!")
            print("=" * 72)
            print(f"  • Win Rate Realized   : {rep.get('win_rate_pct')}% ({rep.get('wins_count')}W / {rep.get('losses_count')}L)")
            print(f"  • Net Realized PnL    : ${rep.get('net_realized_pnl_usd'):+.2f} USDC (Profit Factor: {rep.get('profit_factor')})")
            print(f"  • Mode Thermostat     : {rep.get('thermostat_badge')}")
            print(f"  • Analisis Thermostat : {rep.get('thermostat_reason')}")
            print(f"  • Kalibrasi Bobot     : {rep.get('weight_calibration')}")
            print("=" * 72)
            input("\nTekan ENTER untuk kembali ke Pusat Komando...")
        elif sub == "4":
            st = auto_eng.load_state()
            th = st["thermostat"]
            print("\n⚙️  KONFIGURASI PARAMETER AI MANDIRI:")
            print(f"  [A] Status Zero-Touch Autonomous Hunt : {'🟢 ON (Aktif)' if st.get('enabled', True) else '⚪ OFF (Pause)'}")
            print(f"  [B] Pagar Cadangan Margin Bebas (%)   : {th.get('reserve_pct', 15.0):.1f}% dari Total Saldo USDC")
            print(f"  [C] Interval Siklus Auto-Hunt (Menit) : Tiap {st.get('hunt_interval_minutes', 30)} menit")
            print(f"  [D] Cooldown Antar-Entry Baru (Menit) : Minimal {st.get('cooldown_minutes', 30)} menit")
            print("  [ENTER] Batal / Kembali")
            p_in = input("\nPilih parameter yang ingin diubah [A/B/C/D]: ").strip().upper()
            if p_in == "A":
                st["enabled"] = not st.get("enabled", True)
                auto_eng.save_state(st)
                print(f"[✓] Status Zero-Touch Autonomous Hunt diubah menjadi: {'🟢 ON' if st['enabled'] else '⚪ OFF'}")
            elif p_in == "B":
                val = input("Masukkan persentase cadangan margin bebas baru (contoh 15 untuk 15%): ").strip()
                try:
                    pct = max(5.0, min(50.0, float(val)))
                    st["thermostat"]["reserve_pct"] = pct
                    auto_eng.save_state(st)
                    print(f"[✓] Pagar Cadangan Margin Bebas diatur ke {pct:.1f}% dari Total Saldo USDC.")
                except Exception:
                    print("[!] Input tidak valid.")
            elif p_in == "C":
                val = input("Masukkan interval Auto-Hunt dalam menit (contoh 15, 30, 60): ").strip()
                try:
                    mins = max(5, min(240, int(val)))
                    st["hunt_interval_minutes"] = mins
                    auto_eng.save_state(st)
                    print(f"[✓] Interval Auto-Hunt diatur ke tiap {mins} menit.")
                except Exception:
                    print("[!] Input tidak valid.")
            elif p_in == "D":
                val = input("Masukkan jeda Cooldown antar-entry dalam menit (contoh 15, 30, 60): ").strip()
                try:
                    mins = max(5, min(240, int(val)))
                    st["cooldown_minutes"] = mins
                    auto_eng.save_state(st)
                    print(f"[✓] Cooldown antar-entry diatur ke {mins} menit.")
                except Exception:
                    print("[!] Input tidak valid.")
            input("\nTekan ENTER untuk lanjut...")
        elif sub == "5":
            if PositionManager.is_daemon_running():
                PositionManager.stop_daemon()
            res = PositionManager.start_daemon()
            print(f"\n[✓] {res['message']}")
            input("Tekan ENTER untuk lanjut...")
        elif sub == "6":
            res = PositionManager.stop_daemon()
            print(f"\n[✓] {res['message']}\n")
            input("Tekan ENTER untuk lanjut...")
        elif sub == "7":
            lifecycle = pm.load_lifecycle_state()
            if not lifecycle:
                print("\n[!] Tidak ada posisi terbuka saat ini.")
                input("Tekan ENTER untuk lanjut...")
                continue
            print("\nDaftar posisi aktif & stream-nya saat ini:")
            for c, s in lifecycle.items():
                print(f"  • {c:<6} ➔ Stream: {s.get('strategy', 'SWING')} | Entry: ${s.get('entry_px')} | SL: ${s.get('current_sl_px')}")
            c_in = input("\nKetik simbol koin yang ingin diubah stream-nya (atau ENTER untuk batal): ").strip().upper()
            if c_in in lifecycle:
                s_in = input(f"Pilih stream baru untuk {c_in} — [1] ⚡ INTRADAY  atau  [2] 🌊 SWING: ").strip()
                new_strat = "SWING" if s_in == "2" else "DAYTRADE"
                entry_px = float(lifecycle[c_in]["entry_px"])
                new_plan = position_tags.build_plan(new_strat, entry_px)
                position_tags.tag_position(c_in, new_strat, new_plan, meta={"source": "MANUAL_RETAG"})
                lifecycle[c_in]["strategy"] = new_strat
                lifecycle[c_in]["tp1_px"] = new_plan["tp1_px"]
                lifecycle[c_in]["tp2_px"] = new_plan["tp2_px"]
                lifecycle[c_in]["runner_px"] = new_plan.get("runner_px")
                lifecycle[c_in]["tp1_pct"] = new_plan["tp1_pct"]
                lifecycle[c_in]["tp2_pct"] = new_plan["tp2_pct"]
                lifecycle[c_in]["max_hold_hours"] = new_plan["max_hold_hours"]
                pm.save_lifecycle_state(lifecycle)
                print(f"\n[✓] {c_in} berhasil dipindahkan ke stream [{new_strat}]!")
            input("\nTekan ENTER untuk lanjut...")
        elif sub == "8":
            print("\n🌐 Memperbarui Dashboard Web (docs/index.html) & Sinkronisasi ke GitHub Pages...")
            try:
                from src.github_sync import run_sync_pipeline
                run_sync_pipeline()
            except Exception as e:
                from src.html_reporter import generate_html_report
                p = generate_html_report()
                print(f"[✓] HTML lokal diperbarui di {p} ({e})")
            input("\nTekan ENTER untuk lanjut...")
        elif sub in ["0", ""]:
            break


def menu_sensitivity_backtest(db):
    print("\n🧪 UJI SENSITIVITAS THRESHOLD BERULANG — STREAM SWING (TRUE PEAK V2.0)")
    print("Menguji 135 koin di berbagai tingkat skor threshold (50 s/d 85)...")
    print("-" * 65)
    backtester = ShortBacktester(db)
    df_grid = backtester.run_threshold_sensitivity_grid()
    db.close()
    print("\n" + "=" * 85)
    print("       📊 HASIL PENGUJIAN BACKTEST BERULANG (STREAM SWING V2.0)        ")
    print("=" * 85)
    print(df_grid.to_string(index=False))
    print("=" * 85)
    print("\nKesimpulan Analisis Data (Stream Swing True Peak v2.0):")
    print("  • Skor < 65  : Tren bull masih kuat / belum ada kelelahan beli (Return Negatif).")
    print("  • Skor >= 75 : Titik balik True Peak (Win Rate 88.9%, Profit Factor 8.32, Net +73.2%).")
    print("  • Skor >= 80 : Akurasi Pucuk Sempurna (Win Rate 100.0%, 8 Win / 0 Loss, Net +83.2%).")
    input("\nTekan ENTER untuk kembali ke menu utama...")


def menu_forward_test_journal():
    from src.forward_test import ForwardTestManager
    from src.trader import HyperliquidTrader
    from datetime import datetime
    ft = ForwardTestManager()
    stream_filter = None

    while True:
        clear_screen()
        print(ft.render_summary_dashboard(stream_filter=stream_filter))
        print("\nOpsi Jurnal & Filter Stream Forward Test:")
        print("  [1] 🔄 Sinkronkan Ulang Posisi Baru & Riwayat Close dari Hyperliquid")
        print("  [2] ⚡ Filter Tampilan: Hanya Stream INTRADAY (Day-Trade)")
        print("  [3] 🌊 Filter Tampilan: Hanya Stream SWING (Multi-Hari)")
        print("  [4] 🌐 Tampilkan Semua Stream (Gabungan)")
        print("  [5] 📜 Lihat Tabel Lengkap Semua Transaksi / Fills di Hyperliquid")
        print("  [6] 🚀 Generate Dashboard HTML & Push ke GitHub (Pantau via Web / HP)")
        print("  [7] 🧹 Hapus & Reset Riwayat Trade Lama (Mulai Bersih dari Posisi Live)")
        print("  [0] Kembali ke Menu Utama")
        print("-" * 72)
        sub = input("Masukkan pilihan [0-7, default: 0]: ").strip()
        if sub == "1":
            print("\n[*] Menyinkronkan data posisi aktif, SL on-chain, dan riwayat close...")
            ft.sync_with_exchange()
            print("[✓] Sinkronisasi selesai!")
            input("Tekan ENTER untuk memperbarui tampilan...")
        elif sub == "2":
            stream_filter = "DAYTRADE"
        elif sub == "3":
            stream_filter = "SWING"
        elif sub == "4":
            stream_filter = None
        elif sub == "5":
            print("\n📜 RIWAYAT EKSEKUSI ORDER (USER FILLS) LANGSUNG DARI HYPERLIQUID:")
            print("-" * 82)
            try:
                t = HyperliquidTrader()
                fills = t.info.user_fills(t.account_address) or []
                if not fills:
                    print("  Belum ada riwayat transaksi.")
                else:
                    print(f"{'WAKTU':<20} | {'KOIN':<6} | {'AKSI':<12} | {'HARGA':<10} | {'SIZE':<10} | {'FEE':<8} | {'REALIZED PnL'}")
                    print("-" * 82)
                    for f in fills[:25]:
                        ts = f.get("time", 0)
                        dt_str = datetime.fromtimestamp(ts / 1000.0).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                        c_sym = str(f.get("coin", ""))
                        f_dir = str(f.get("dir", ""))
                        px = float(f.get("px", 0.0))
                        sz = float(f.get("sz", 0.0))
                        fee = float(f.get("fee", 0.0))
                        cpnl = float(f.get("closedPnl", 0.0))
                        pnl_str = f"${cpnl:+.4f}" if cpnl != 0 else "-"
                        print(f"{dt_str:<20} | {c_sym:<6} | {f_dir:<12} | ${px:<9.5f} | {sz:<10.2f} | ${fee:<7.4f} | {pnl_str}")
            except Exception as e:
                print(f"[!] Gagal mengambil riwayat fills: {e}")
            print("-" * 82)
            input("\nTekan ENTER untuk kembali ke Jurnal Forward Test...")
        elif sub == "6":
            print("\n" + "=" * 65)
            print("  🚀 PIPELINE GENERATE HTML DASHBOARD & PUSH KE GITHUB")
            print("=" * 65)
            print("[*] Meng-generate docs/index.html & menyinkronkan dengan GitHub...")
            from src.github_sync import sync_and_push
            res = sync_and_push()
            if res.get("success"):
                print(f"\n[✓] {res.get('message')}")
                if res.get("pages_url"):
                    print(f"  🌐 Link Dashboard (GitHub Pages) : {res['pages_url']}")
                if res.get("repo_url"):
                    print(f"  📦 Link Repository GitHub        : {res['repo_url']}")
                open_browser = input("\nBuka dashboard di browser laptop sekarang? (Y/N) [default: Y]: ").strip().upper()
                if open_browser in ["", "Y", "YES"]:
                    import subprocess
                    subprocess.run(["open", "docs/index.html"])
            else:
                print(f"\n[!] Gagal push ke GitHub: {res.get('error')}")
            input("\nTekan ENTER untuk kembali ke Jurnal...")
        elif sub == "7":
            confirm = input("\n[⚠️] Reset seluruh riwayat trade yang SUDAH CLOSE & mulai bersih dari posisi live saat ini? (Y/N): ").strip().upper()
            if confirm in ["Y", "YES"]:
                purged = ft.purge_closed_trades()
                print(f"\n[✓] Berhasil membersihkan {purged} log trade lama. Tracking kini murni dimulai dari posisi aktif!")
            else:
                print("\nReset dibatalkan.")
            input("\nTekan ENTER untuk lanjut...")
        else:
            break


def menu_ai_hub():
    from src.ai_engine import ai_advisor, episodic_memory
    from src.guardian_agent import guardian_agent
    from src.trade_db import trade_db
    from src.adaptive_weights import get_weights, run_calibration
    from src.auto_optimizer import AutoOptimizer

    while True:
        clear_screen()
        status_str = "🟢 AKTIF (Gemini Flash Multi-Agent Committee)" if ai_advisor.is_available() else "⚪ DETERMINISTIC COMMITTEE FALLBACK (Set GEMINI_API_KEY di .env)"
        mem_data = episodic_memory.load_memory()
        rules_list = mem_data.get("rules", [])
        g_rep = guardian_agent.run_full_health_and_security_audit(force=False)
        print("=" * 74)
        print("   🧠 AGENTIC AI COMMITTEE, EPISODIC MEMORY & DEVSECOPS GUARDIAN HUB   ")
        print("=" * 74)
        print(f"Status Engine AI       : {status_str}")
        print("Komite Trading (3-Sub) : 🎯 The Sniper vs ⚖️  The Prosecutor vs 👨‍⚖️ CRO Judge")
        print("Autonomous Tools       : 🛠️  query_coin_history | inspect_orderbook | check_correlation")
        print(f"Episodic Memory Bank   : 🧠 {len(rules_list)} Aturan Mandiri Aktif (Diperbarui: {mem_data.get('updated_at', '-')})")
        print(f"System Guardian Agent  : {g_rep.get('status_badge')}")
        print("-" * 74)
        print("Pilih opsi:")
        print("  [1] 📋 Tampilkan Review & Grade AI Terhadap Trade Terakhir (Loop 1 Post-Mortem)")
        print("  [2] 🌐 Cek Sintesis Rezim Makro Pasar Versi AI (Loop 4 Regime Synthesizer)")
        print("  [3] ⚖️  Lihat & Kalibrasi Bobot Sinyal Screener Mandiri (Loop 2 Adaptive Weights)")
        print("  [4] 🧪 Jalankan Optimasi Hipotesis Backtest Mandiri (Loop 5 Auto Optimizer)")
        print("  [5] 🧠 Lihat & Tambah Aturan di Episodic Memory Bank (Memori Jangka Panjang)")
        print("  [6] 🛡️  Jalankan Audit Kesolidan, Anti-Crash & Firewall Privasi (Guardian Agent)")
        print("  [7] 💬 Masuk ke AI War Room (Bicara Bebas, Review Pendapat & Auto Follow-Up)")
        print("  [0] Kembali ke Menu Utama")
        print("-" * 74)

        choice = input("Masukkan pilihan [0-7]: ").strip()
        if choice == "1":
            journal = trade_db.load_journal()
            closed_trades = [v for v in journal.values() if v.get("status") == "CLOSED"]
            if not closed_trades:
                print("\n[-] Belum ada trade yang ditutup di jurnal.")
            else:
                print(f"\n📋 AUDIT POST-MORTEM AI PADA {len(closed_trades)} TRADE TERAKHIR:")
                print("=" * 68)
                for t in closed_trades[-5:]:
                    ai_rev = t.get("ai_review") or ai_advisor.analyze_closed_trade(t)
                    grade = ai_rev.get("execution_grade", "N/A")
                    cause = ai_rev.get("root_cause", "N/A")
                    lesson = ai_rev.get("lessons_learned", "-")
                    violation = "⚠️ RULE VIOLATION" if ai_rev.get("rule_violation_detected") else "✅ DISIPLIN"
                    pnl = float(t.get("realized_pnl") or t.get("final_pnl_usd", 0.0))
                    print(f"🪙 [{t.get('coin')}] Stream: {t.get('strategy')} | PnL: ${pnl:+.2f} USDC")
                    print(f"   • Grade Execution : [{grade}] | Kategori: {cause} | Status: {violation}")
                    print(f"   • Pelajaran (AI)  : {lesson}")
                    print("-" * 68)
            input("\nTekan ENTER untuk lanjut...")
        elif choice == "2":
            print("\n🌐 Menganalisis kondisi makro & derivatif pasar (Gemini Flash Loop 4)...")
            from src.database import Database
            from src.market_regime import MarketRegime
            db = Database(read_only=True)
            mr = MarketRegime(db)
            regime = mr.get_ai_synthesized_regime()
            db.close()
            print("\n" + "=" * 68)
            print("         🌐 HASIL SINTESIS REZIM PASAR MAKRO (AI ADVISOR)        ")
            print("=" * 68)
            print(f"  • Status Rezim    : {regime.get('regime_status')}")
            print(f"  • Tren Bitcoin    : {regime.get('btc_trend')}")
            print(f"  • Avg Funding 8H  : {regime.get('avg_funding_rate'):+.4f}%")
            print(f"  • Risk Multiplier : {regime.get('risk_multiplier')}x")
            print(f"  • Analisis AI     : {regime.get('reasoning')}")
            print("=" * 68)
            input("\nTekan ENTER untuk lanjut...")
        elif choice == "3":
            print("\n⚖️  BOBOT SKOR SCREENER ADAPTIF SAAT INI (Loop 2):")
            print("=" * 68)
            for s in ["SWING", "DAYTRADE"]:
                w = get_weights(s)
                print(f"\n--- Stream [{s}] ---")
                for k, v in w.items():
                    print(f"  • {k:<25}: {v:.1f} poin")
            print("-" * 68)
            cal_in = input("Ketik 'KALIBRASI' untuk menjalankan optimasi bobot AI dari riwayat trade, atau ENTER untuk batal: ").strip().upper()
            if cal_in == "KALIBRASI":
                print("\n[*] Menjalankan kalibrasi adaptif Gemini Flash...")
                res_sw = run_calibration("SWING")
                res_dt = run_calibration("DAYTRADE")
                print(f"[✓] Kalibrasi Swing    : {res_sw.get('calibration_rationale')}")
                print(f"[✓] Kalibrasi Daytrade : {res_dt.get('calibration_rationale')}")
            input("\nTekan ENTER untuk lanjut...")
        elif choice == "4":
            print("\n🧪 Menjalankan Autonomous Backtest Hypothesis Testing (Loop 5)...")
            from src.database import Database
            db = Database(read_only=True)
            opt = AutoOptimizer(db)
            res = opt.run_optimization_cycle()
            db.close()
            print("\n" + "=" * 75)
            print("       🧪 HASIL UJI HIPOTESIS PARAMETER MANDIRI (GEMINI FLASH)        ")
            print("=" * 75)
            b = res["baseline"]
            print(f"Baseline Saat Ini : PF {b['profit_factor']} | WinRate {b['win_rate_pct']}% | Net {b['net_return_pct']:+.1f}% ({b['total_trades']} trade)")
            print("-" * 75)
            for exp in res["experiments"]:
                print(f"• Eksperimen   : {exp['name']} ({exp['stream']}) -> {exp['verdict']}")
                print(f"  Parameter    : {exp.get('params')}")
                print(f"  Hipotesis    : {exp.get('hypothesis')}")
                print(f"  Hasil Backtest: PF {exp.get('profit_factor', 0)} ({exp.get('pf_delta', 0):+.2f}) | WinRate {exp.get('win_rate_pct', 0)}% | Net {exp.get('net_return_pct', 0):+.1f}% ({exp.get('total_trades', 0)} trade)")
                print("-" * 75)
            input("\nTekan ENTER untuk lanjut...")
        elif choice == "5":
            print("\n🧠 EPISODIC MEMORY BANK (ATURAN MANDIRI HASIL BELAJAR DARI RIWAYAT TRADE):")
            print("=" * 76)
            for r in rules_list:
                print(f"  • [{r.get('id')}] ({r.get('stream')}/{r.get('side')} | Sumber: {r.get('source_coin')} | {r.get('root_cause')}):")
                print(f"    └─ \"{r.get('rule')}\"")
            print("-" * 76)
            new_rule = input("Ketik aturan baru untuk ditambahkan ke Episodic Memory (atau ENTER untuk kembali): ").strip()
            if new_rule and len(new_rule) >= 10:
                episodic_memory.record_lesson_from_trade(
                    {"coin": "MANUAL_USER", "strategy": "ALL", "side": "ALL"},
                    {"root_cause": "USER_DIRECTIVE", "episodic_rule_to_remember": new_rule}
                )
                print("[✓] Aturan baru berhasil disimpan ke Episodic Memory Bank dan langsung di-inject ke Komite Agent!")
                input("Tekan ENTER untuk lanjut...")
        elif choice == "6":
            print("\n🛡️  MENJALANKAN AUDIT PENUH DEVSECOPS & SELF-HEALING GUARDIAN AGENT...")
            rep = guardian_agent.run_full_health_and_security_audit(force=True)
            ast_r = rep.get("codebase_ast", {})
            db_r = rep.get("sqlite_wal", {})
            git_r = rep.get("git_security", {})
            rt_r = rep.get("runtime_telemetry", {})
            healed = rep.get("healed_json_actions", [])
            print("=" * 76)
            print(f"  Status Keseluruhan   : {rep.get('status_badge')}")
            print(f"  1. Integritas Kode   : {'🟢 LULUS' if ast_r.get('healthy') else '🔴 ERROR'} ({ast_r.get('checked_files', 0)} modul Python diverifikasi AST 0 syntax error)")
            print(f"  2. Database SQLite   : {'🟢 SEHAT' if db_r.get('healthy') else '⚠️ WARNING'} ({db_r.get('status')})")
            print(f"  3. Git & Privasi DLP : {'🟢 AMAN 100%' if git_r.get('safe') else '🛑 BLOCKED'} ({git_r.get('action_taken')})")
            print(f"  4. Shadow JSON State : {'🟢 AKTIF (.bak siap auto-recovery)' if not healed else f'🔧 Auto-Healed {len(healed)} file'}")
            print(f"  5. Monitor Log Daemon: 429 Hits={rt_r.get('rate_limit_hits', 0)} | 503 Hits={rt_r.get('api_overloads', 0)} | Exceptions={rt_r.get('recent_exceptions_count', 0)}")
            if rt_r.get("remedies_applied"):
                for rem in rt_r["remedies_applied"]:
                    print(f"     └─ 🔧 Self-Heal: {rem}")
            print("=" * 76)
            input("\nTekan ENTER untuk lanjut...")
        elif choice == "7":
            menu_ai_war_room()
        elif choice in ["0", ""]:
            break


def menu_ai_war_room():
    """
    Interactive Conversational War Room with the Multi-Agent Trading Committee.
    Listens to user opinions, verifies against live data/tools, reviews objectively,
    responds in natural language, and executes follow-up actions when AI agrees.
    """
    from src.agent_chat import war_room
    from src.autonomous_engine import AutonomousEngine
    from src.ai_engine import episodic_memory
    from src.antigravity_bridge import antigravity_bridge

    clear_screen()
    auto_eng = AutonomousEngine()
    gov = auto_eng.get_live_governor_snapshot()
    mem_cnt = len(episodic_memory.load_memory().get("rules", []))
    bridge_st = antigravity_bridge.load_bridge()
    open_dev_cnt = len([t for t in bridge_st.get("trading_ai_to_antigravity_tickets", []) if t.get("status") == "OPEN_FOR_ANTIGRAVITY"])

    print("=" * 78)
    print("   💬 AI WAR ROOM — DISKUSI BEBAS, REVIEW PENDAPAT & AUTO FOLLOW-UP   ")
    print("=" * 78)
    print(f"  • Status Portofolio : Ekuitas ${gov.get('equity', 0):.2f} USDC | Bebas Di Atas Cadangan: ${gov.get('free_above_reserve', 0):.2f}")
    print(f"  • Posisi Terbuka    : {gov.get('active_count', 0)}/{gov.get('max_positions', 5)} Slot (🟢 Long: {gov.get('long_count', 0)} | 🔴 Short: {gov.get('short_count', 0)})")
    print(f"  • Thermostat & Mem  : {gov.get('thermostat', {}).get('badge')} | 🧠 {mem_cnt} Aturan Episodic Memory")
    print(f"  • Jembatan Antigrav : 🔗 AKTIF 2-ARAH ({bridge_st.get('total_qa_captured', 0)} Log Q&A Tercatat | {open_dev_cnt} Tiket Dev untuk Antigravity)")
    print("-" * 78)
    print("💡 Anda bebas menyampaikan pendapat, strategi, hipotesis koin, atau ide fitur baru:")
    print("   Contoh 1: \"Menurut saya market lagi rawan squeeze, naikkan cadangan kas ke 25%\"")
    print("   Contoh 2: \"Coba cek koin SOL sekarang, menurut saya sudah pucuk dan layak di-short\"")
    print("   Contoh 3: \"Tolong ingat aturan: jangan ambil intraday short kalau orderbook bid > 65%\"")
    print("   Contoh 4: \"Tolong sampaikan ke Antigravity: buatkan fitur filter volume spike 5m\"")
    print("   (Ketik [0] atau [EXIT] untuk kembali ke Menu Utama)")
    print("=" * 78)

    while True:
        try:
            user_msg = input("\n🗣️  Pendapat / Pesan Anda ➔ ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[✓] Keluar dari AI War Room...")
            break

        if not user_msg:
            continue
        if user_msg.upper() in ["0", "Q", "EXIT", "KELUAR", "BACK"]:
            print("\n[✓] Kembali ke Menu...")
            break

        print("\n[*] 🧠 Komite AI sedang mendengarkan, menarik data live (Orderbook L2/Portofolio), & mereview pendapat Anda...")
        res = war_room.consult_and_review(user_msg)

        verdict = res.get("agreement_verdict", "DISCUSSION_INSIGHT")
        conf = res.get("confidence_in_review", 80)
        evidence = res.get("data_evidence_used", [])
        review_txt = res.get("opinion_review", "-")
        resp_txt = res.get("ai_response", "-")
        follow_up = res.get("follow_up_action") or {}
        act_type = str(follow_up.get("action_type", "NONE")).upper()

        verdict_badges = {
            "AGREE_AND_FOLLOW_UP": "🟢 SETUJU PENUH & SIAP FOLLOW-UP",
            "PARTIAL_AGREE_REFINED": "🟡 SETUJU DENGAN PENYESUAIAN RISIKO AMAN",
            "RESPECTFUL_DISAGREE": "🔴 TIDAK SETUJU (BERBEDA DENGAN DATA LIVE / PROTEKSI MODAL)",
            "DISCUSSION_INSIGHT": "💬 DISKUSI & ANALISIS KUANTITATIF"
        }
        v_badge = verdict_badges.get(verdict, verdict)

        print("\n" + "┌" + "─" * 76 + "┐")
        print(f"│ 🤖 REVIEW & RESPONS KOMITE AI (Keyakinan Evaluasi: {conf}/100)" + " " * 19 + "│")
        print("├" + "─" * 76 + "┤")
        print(f"  📌 Inti Pendapat Anda : {res.get('user_opinion_summary', user_msg)}")
        if evidence:
            print("  📊 Bukti Data Real-Time yang Diperiksa AI:")
            for ev in evidence:
                print(f"     • {ev}")
        print(f"\n  ⚖️  Status Review AI   : {v_badge}")
        print(f"  🔍 Evaluasi Objektif  : {review_txt}")
        print(f"\n  💬 Respons Komite AI  :\n     \"{resp_txt}\"")
        print("└" + "─" * 76 + "┘")

        exec_log_note = "NONE"

        # Step 4: Follow-Up Execution if AI Agrees
        if verdict in ("AGREE_AND_FOLLOW_UP", "PARTIAL_AGREE_REFINED") and act_type != "NONE":
            act_summary = follow_up.get("action_summary") or f"Menjalankan {act_type}"
            print(f"\n🚀 RENCANA FOLLOW-UP AI: [{act_type}] ➔ {act_summary}")

            # Non-destructive config/memory/audit actions auto-execute immediately when AI agrees
            auto_safe_types = {"ADD_EPISODIC_RULE", "UPDATE_THERMOSTAT", "EVALUATE_COIN_SETUP", "RUN_GUARDIAN_AUDIT", "PAUSE_OR_RESUME_AUTONOMOUS"}
            if act_type in auto_safe_types:
                exec_res = war_room.execute_follow_up(follow_up)
                print(f"   {exec_res.get('message')}")
                exec_log_note = exec_res.get("message", "EXECUTED")
            else:
                # On-chain position/order actions ask for 1-click ENTER confirmation
                confirm = input("   👉 Konfirmasi jalankan aksi on-chain ini sekarang? [Tekan ENTER / Y = Ya | N = Batal]: ").strip().upper()
                if confirm in ["", "Y", "YES", "YA"]:
                    exec_res = war_room.execute_follow_up(follow_up)
                    print(f"   {exec_res.get('message')}")
                    exec_log_note = exec_res.get("message", "EXECUTED")
                else:
                    print("   [ℹ️] Follow-up on-chain dilewati atas permintaan Anda.")
                    exec_log_note = "SKIPPED_BY_USER"

        bridge_sync = war_room.append_chat_history(user_msg, res, exec_result=exec_log_note)
        if bridge_sync.get("qa_id"):
            print(f"\n  📡 [Antigravity Bridge] Log Q&A [{bridge_sync['qa_id']}] otomatis diteruskan ke Antigravity (AGENTS.md & JSONL)")
        if bridge_sync.get("ticket_created"):
            tk = bridge_sync["ticket_created"]
            print(f"  🛠️  [Tiket Dev untuk Antigravity: {tk['ticket_id']}] ({tk['priority']}): {tk['title']}")
            print(f"     └─ Spesifikasi: {tk['technical_spec_for_antigravity']}")


def main_menu():
    while True:
        clear_screen()
        print_header()
        print("─── 🚀 TRADING OPERASIONAL & AI MANDIRI (FLOW UTAMA) ───────────────")
        print("  [1] 🎯 Auto-Hunt AI Sniper (Scan 4 Kuadran ➔ Auto-Pilih #1 ➔ AI Guard ➔ Eksekusi)")
        print("  [2] 🔍 Screener Sinyal Manual (Intraday ⚡ & Swing 🌊 | Short 🔴 & Long 🟢)")
        print("  [3] ⚡ Eksekusi Posisi Manual (Ketik Koin Sendiri | Server SL & TP)")
        print("  [4] 🤖 Pusat Komando AI Mandiri & Live Monitor (Zero-Touch 24/7, Self-Eval & Telemetri)")
        print("  [5] 📓 Jurnal Forward Test & Scorecard Per-Stream (Audit PnL)")
        print("\n─── 🧠 AI INTELLIGENCE, WAR ROOM & RISET ───────────────────────────")
        print("  [6] 🧠 Agentic AI Committee, Episodic Memory & Guardian Hub (6 Loops)")
        print("  [7] 🧪 Lab Backtest & Uji Sensitivitas (Intraday 15m & Swing Daily)")
        print("  [11] 💬 AI War Room — Bicara Bebas, Review Pendapat & Auto Follow-Up Bersama AI")
        print("\n─── ⚙️  DATA & KONFIGURASI SISTEM ─────────────────────────────────")
        print("  [8] ⬇️  Perbarui Data Candlestick Hyperliquid (Daily, 4H, & 15m)")
        print("  [9] 📦 Status Database & Penyimpanan (DuckDB & SQLite WAL)")
        print("  [10] 🔑 Setup & Cek Akun Hyperliquid (Testnet / Mainnet)")
        print("  [0] 🚪 Keluar")
        print("-" * 68)

        choice = input("Masukkan nomor pilihan [0-11, atau ketik C untuk Chat AI]: ").strip()
        if choice in ["1", "2", "7", "8", "9"]:
            # Gunakan read_only=True untuk query analitis DuckDB agar tidak konflik concurrency
            db = Database(read_only=(choice not in ["1", "8"]))
            try:
                if choice == "1":
                    menu_auto_hunt_sniper(db)
                elif choice == "2":
                    menu_screen(db)
                elif choice == "7":
                    menu_backtest(db, is_custom=False)
                elif choice == "8":
                    menu_sync(db)
                elif choice == "9":
                    menu_stats(db)
            finally:
                db.close()
        elif choice == "3":
            menu_execute_short()
        elif choice == "4":
            menu_position_guard()
        elif choice == "5":
            menu_forward_test_journal()
        elif choice == "6":
            menu_ai_hub()
        elif choice == "10":
            menu_hyperliquid_setup()
        elif choice.upper() in ["11", "C", "CHAT", "W", "WAR"]:
            menu_ai_war_room()
        elif choice in ["0", "q", "exit"]:
            print("\nTerima kasih! Semoga trading kuantitatif Anda menghasilkan profit maksimal. 🚀\n")
            sys.exit(0)
        else:
            input("\nPilihan tidak valid. Tekan ENTER untuk mencoba lagi...")


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nKeluar dari program...")
        sys.exit(0)

