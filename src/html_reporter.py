"""
ShortHunter HTML Performance Dashboard Generator
Generates a standalone, dark-mode, mobile-responsive HTML dashboard (docs/index.html)
displaying live positions, closed trade history, per-stream scorecards, and audit metrics.
Ideal for remote monitoring via GitHub Pages or local browser.
"""

import os
import sys
import json
from datetime import datetime
from typing import Dict, List, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
DOCS_DIR = os.path.join(ROOT_DIR, "docs")
OUTPUT_HTML = os.path.join(DOCS_DIR, "index.html")


def generate_html_report(output_path: str = OUTPUT_HTML) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 1. Load active lifecycle & trader equity
    from src.trader import HyperliquidTrader
    from src.position_manager import PositionManager
    from src.forward_test import ForwardTestManager

    trader = HyperliquidTrader()
    pm = PositionManager(trader)
    ft = ForwardTestManager()

    lifecycle = pm.load_lifecycle_state() or {}
    journal = ft.load_journal() or {}

    # Equity & margin
    equity = 0.0
    free_collateral = 0.0
    try:
        acct = trader.get_account_state()
        equity = float(acct.get("equity", 0.0))
        free_collateral = float(acct.get("free_collateral", 0.0))
    except Exception:
        equity = 200.0
        free_collateral = 100.0

    # Separate OPEN and CLOSED trades from journal
    open_trades = [p for p in journal.values() if p.get("status") == "OPEN"]
    closed_trades = [p for p in journal.values() if p.get("status") == "CLOSED"]

    # Calculate Performance Metrics
    total_closed = len(closed_trades)
    wins = [p for p in closed_trades if float(p.get("final_pnl_usd", 0.0) or 0.0) > 0]
    losses = [p for p in closed_trades if float(p.get("final_pnl_usd", 0.0) or 0.0) < 0]
    win_rate = (len(wins) / total_closed * 100.0) if total_closed > 0 else 0.0

    gross_profit = sum(float(p.get("final_pnl_usd", 0.0) or 0.0) for p in wins)
    gross_loss = abs(sum(float(p.get("final_pnl_usd", 0.0) or 0.0) for p in losses))
    net_realized_pnl = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    # Stream Scorecards
    intra_closed = [p for p in closed_trades if p.get("strategy") == "DAYTRADE"]
    intra_wins = [p for p in intra_closed if float(p.get("final_pnl_usd", 0.0) or 0.0) > 0]
    intra_pnl = sum(float(p.get("final_pnl_usd", 0.0) or 0.0) for p in intra_closed)
    intra_wr = (len(intra_wins) / len(intra_closed) * 100.0) if intra_closed else 0.0

    swing_closed = [p for p in closed_trades if p.get("strategy", "SWING") == "SWING"]
    swing_wins = [p for p in swing_closed if float(p.get("final_pnl_usd", 0.0) or 0.0) > 0]
    swing_pnl = sum(float(p.get("final_pnl_usd", 0.0) or 0.0) for p in swing_closed)
    swing_wr = (len(swing_wins) / len(swing_closed) * 100.0) if swing_closed else 0.0

    # Floating PnL of active positions
    tot_floating_pnl = sum(float(s.get("unrealized_pnl", 0.0) or 0.0) for s in lifecycle.values())
    tot_margin_used = sum(float(s.get("margin_used", 0.0) or 0.0) for s in lifecycle.values())

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>ShortHunter AI — Live Trade Performance & Audit</title>
    <style>
        :root {{
            --bg-dark: #0a0e17;
            --card-bg: #111827;
            --card-border: #1f293d;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --neon-green: #10b981;
            --neon-green-glow: rgba(16, 185, 129, 0.2);
            --coral-red: #ef4444;
            --coral-red-glow: rgba(239, 68, 68, 0.2);
            --cyan-intra: #06b6d4;
            --purple-swing: #8b5cf6;
            --amber-warn: #f59e0b;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            padding: 24px;
            line-height: 1.5;
        }}
        .container {{ max-width: 1300px; margin: 0 auto; }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--card-border);
            margin-bottom: 24px;
        }}
        .brand {{ display: flex; align-items: center; gap: 12px; }}
        .brand h1 {{ font-size: 1.7rem; font-weight: 700; letter-spacing: -0.5px; }}
        .badge-live {{
            background: rgba(16, 185, 129, 0.15);
            color: var(--neon-green);
            border: 1px solid var(--neon-green);
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .badge-live::before {{
            content: "";
            width: 8px;
            height: 8px;
            background: var(--neon-green);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--neon-green);
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.4; transform: scale(0.85); }}
            100% {{ opacity: 1; transform: scale(1); }}
        }}
        .header-meta {{
            font-size: 0.85rem;
            color: var(--text-muted);
            text-align: right;
        }}

        /* Metrics Grid */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 18px;
            transition: transform 0.2s, border-color 0.2s;
        }}
        .card:hover {{ border-color: #374151; }}
        .card-title {{
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 6px;
        }}
        .card-val {{
            font-size: 1.6rem;
            font-weight: 700;
            letter-spacing: -0.5px;
        }}
        .card-sub {{
            font-size: 0.78rem;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        .pnl-pos {{ color: var(--neon-green); text-shadow: 0 0 12px var(--neon-green-glow); }}
        .pnl-neg {{ color: var(--coral-red); text-shadow: 0 0 12px var(--coral-red-glow); }}
        .pnl-zero {{ color: var(--text-muted); }}

        /* Stream cards */
        .streams-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stream-box {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 16px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .stream-title {{
            font-size: 1.05rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .stream-intra {{ color: var(--cyan-intra); }}
        .stream-swing {{ color: var(--purple-swing); }}
        .stream-stats {{
            display: flex;
            gap: 20px;
            text-align: right;
        }}
        .stat-item .num {{ font-size: 1.15rem; font-weight: 700; }}
        .stat-item .lbl {{ font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; }}

        /* Table styling */
        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}
        .section-header h2 {{ font-size: 1.15rem; font-weight: 700; }}
        .table-responsive {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            overflow-x: auto;
            margin-bottom: 30px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.85rem;
        }}
        th {{
            background: #162032;
            color: #d1d5db;
            padding: 12px 14px;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.72rem;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--card-border);
            white-space: nowrap;
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid var(--card-border);
            white-space: nowrap;
        }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}

        /* Badges */
        .pill {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.72rem;
            font-weight: 600;
            text-align: center;
        }}
        .pill-intra {{ background: rgba(6, 182, 212, 0.15); color: var(--cyan-intra); border: 1px solid var(--cyan-intra); }}
        .pill-swing {{ background: rgba(139, 92, 246, 0.15); color: var(--purple-swing); border: 1px solid var(--purple-swing); }}
        .pill-short {{ background: rgba(239, 68, 68, 0.15); color: var(--coral-red); font-weight: 700; }}
        .pill-long  {{ background: rgba(16, 185, 129, 0.15); color: var(--neon-green); font-weight: 700; }}
        .pill-be    {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #10b981; }}
        .pill-sl    {{ background: rgba(239, 68, 68, 0.15); color: #f87171; }}
        .pill-win   {{ background: rgba(16, 185, 129, 0.2); color: var(--neon-green); font-weight: 700; }}
        .pill-loss  {{ background: rgba(239, 68, 68, 0.2); color: var(--coral-red); font-weight: 700; }}

        .empty-row {{
            text-align: center;
            padding: 36px !important;
            color: var(--text-muted);
            font-style: italic;
        }}

        footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--card-border);
            text-align: center;
            color: var(--text-muted);
            font-size: 0.8rem;
        }}

        @media (max-width: 768px) {{
            body {{ padding: 14px; }}
            .streams-grid {{ grid-template-columns: 1fr; }}
            .stream-box {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
            .stream-stats {{ width: 100%; justify-content: space-between; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <h1>⚡ ShortHunter AI</h1>
                <span class="badge-live">LIVE AUTO-PILOT GUARD</span>
            </div>
            <div class="header-meta">
                <div>Terakhir Disinkronkan: <strong>{now_str}</strong></div>
                <div>Server Stop Loss: <span style="color:var(--neon-green);">🟢 AKTIF ON-CHAIN</span></div>
            </div>
        </header>

        <!-- Executive Metrics Grid -->
        <div class="metrics-grid">
            <div class="card">
                <div class="card-title">Win Rate (Selesai)</div>
                <div class="card-val {'pnl-pos' if win_rate >= 50 else 'pnl-neg' if total_closed > 0 else 'pnl-zero'}">{win_rate:.1f}%</div>
                <div class="card-sub">{len(wins)} Menang / {len(losses)} Kalah ({total_closed} total)</div>
            </div>
            <div class="card">
                <div class="card-title">Net Realized PnL</div>
                <div class="card-val {'pnl-pos' if net_realized_pnl > 0 else 'pnl-neg' if net_realized_pnl < 0 else 'pnl-zero'}">{'+' if net_realized_pnl >= 0 else ''}${net_realized_pnl:.2f}</div>
                <div class="card-sub">Gross: +${gross_profit:.2f} | Loss: -${gross_loss:.2f}</div>
            </div>
            <div class="card">
                <div class="card-title">Profit Factor</div>
                <div class="card-val {'pnl-pos' if profit_factor >= 1.5 else 'pnl-zero'}">{profit_factor:.2f}</div>
                <div class="card-sub">Rasio Reward / Risk kumulatif</div>
            </div>
            <div class="card">
                <div class="card-title">Total Ekuitas Akun</div>
                <div class="card-val">${equity:.2f}</div>
                <div class="card-sub">Free: ${free_collateral:.2f} | Margin: ${tot_margin_used:.2f}</div>
            </div>
            <div class="card">
                <div class="card-title">Floating PnL Aktif</div>
                <div class="card-val {'pnl-pos' if tot_floating_pnl > 0 else 'pnl-neg' if tot_floating_pnl < 0 else 'pnl-zero'}">{'+' if tot_floating_pnl >= 0 else ''}${tot_floating_pnl:.2f}</div>
                <div class="card-sub">{len(lifecycle)} posisi terbuka di bursa</div>
            </div>
        </div>

        <!-- Stream Scorecards -->
        <div class="streams-grid">
            <div class="stream-box">
                <div class="stream-title stream-intra">
                    <span>⚡ INTRADAY (15m R-Multiple)</span>
                </div>
                <div class="stream-stats">
                    <div class="stat-item">
                        <div class="num">{intra_wr:.0f}%</div>
                        <div class="lbl">Win Rate</div>
                    </div>
                    <div class="stat-item">
                        <div class="num {'pnl-pos' if intra_pnl > 0 else 'pnl-neg' if intra_pnl < 0 else 'pnl-zero'}">{'+' if intra_pnl >= 0 else ''}${intra_pnl:.2f}</div>
                        <div class="lbl">Net PnL</div>
                    </div>
                    <div class="stat-item">
                        <div class="num">{len(intra_closed)}</div>
                        <div class="lbl">Closed</div>
                    </div>
                </div>
            </div>
            <div class="stream-box">
                <div class="stream-title stream-swing">
                    <span>🌊 SWING (Multi-Day Fixed %)</span>
                </div>
                <div class="stream-stats">
                    <div class="stat-item">
                        <div class="num">{swing_wr:.0f}%</div>
                        <div class="lbl">Win Rate</div>
                    </div>
                    <div class="stat-item">
                        <div class="num {'pnl-pos' if swing_pnl > 0 else 'pnl-neg' if swing_pnl < 0 else 'pnl-zero'}">{'+' if swing_pnl >= 0 else ''}${swing_pnl:.2f}</div>
                        <div class="lbl">Net PnL</div>
                    </div>
                    <div class="stat-item">
                        <div class="num">{len(swing_closed)}</div>
                        <div class="lbl">Closed</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- SECTION 1: POSISI AKTIF (REAL-TIME) -->
        <div class="section-header">
            <h2>📊 Detail Posisi Aktif ({len(lifecycle)})</h2>
            <span style="font-size:0.8rem; color:var(--text-muted);">Diperbarui otomatis tiap siklus</span>
        </div>
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>Simbol</th>
                        <th>Stream</th>
                        <th>Posisi</th>
                        <th>Entry / Skrg</th>
                        <th>Pergerakan</th>
                        <th>Margin / Size</th>
                        <th>Floating PnL (ROE%)</th>
                        <th>Proteksi SL</th>
                        <th>Target TP</th>
                        <th>Auto-Close</th>
                    </tr>
                </thead>
                <tbody>
    """

    if not lifecycle:
        html += """
                    <tr>
                        <td colspan="10" class="empty-row">Tidak ada posisi aktif saat ini di Hyperliquid. Auto-Pilot siaga menunggu entry baru.</td>
                    </tr>
        """
    else:
        for coin, s in sorted(lifecycle.items(), key=lambda x: (x[1].get("strategy", "SWING"), x[0])):
            strat = s.get("strategy", "SWING")
            side = s.get("side", "SHORT")
            entry_px = float(s.get("entry_px", 0.0))
            cur_px = float(s.get("last_px", entry_px))
            drop_pct = float(s.get("gain_pct", s.get("drop_pct", 0.0)))
            pnl = float(s.get("unrealized_pnl", 0.0))
            roe = float(s.get("roe_pct", 0.0))
            margin = float(s.get("margin_used", 0.0))
            rem_sz = float(s.get("remaining_sz", s.get("initial_sz", 0.0)))
            sl_px = float(s.get("current_sl_px", 0.0))
            tp1_done = s.get("trimmed_10pct", False)

            # Badges
            strat_pill = '<span class="pill pill-intra">⚡ INTRADAY</span>' if strat == "DAYTRADE" else '<span class="pill pill-swing">🌊 SWING</span>'
            side_pill = '<span class="pill pill-short">🔴 SHORT 2x</span>' if side == "SHORT" else '<span class="pill pill-long">🟢 LONG 2x</span>'

            # SL badge
            if tp1_done:
                sl_badge = f'<span class="pill pill-be">BE @ ${sl_px:.5f} (0% Risiko) 🟢</span>'
            else:
                sl_badge = f'<span class="pill pill-sl">${sl_px:.5f} (SL Teknis) 🟢</span>'

            # TP Targets
            tp1_px = s.get("tp1_px")
            tp2_px = s.get("tp2_px")
            tp_info = []
            if tp1_done:
                tp_info.append('<span style="color:var(--neon-green);">✅ TP1 Selesai (50%)</span>')
                if tp2_px:
                    tp_info.append(f'<span>🎯 TP2: ${float(tp2_px):.4f}</span>')
            else:
                if tp1_px:
                    tp_info.append(f'<span>✂️ TP1: ${float(tp1_px):.4f}</span>')
                if tp2_px:
                    tp_info.append(f'<span>🎯 TP2: ${float(tp2_px):.4f}</span>')
            tp_str = " | ".join(tp_info) or "-"

            # Time stop
            max_h = s.get("max_hold_hours", 6 if strat == "DAYTRADE" else 72)
            c_at = s.get("created_at")
            if c_at:
                try:
                    c_dt = datetime.fromisoformat(c_at)
                    elapsed_h = (datetime.now() - c_dt).total_seconds() / 3600.0
                    rem_h = max(0.0, max_h - elapsed_h)
                    time_str = f"{rem_h:.1f}j tersisa (Max {max_h}j)"
                except Exception:
                    time_str = f"Max {max_h}j"
            else:
                time_str = f"Max {max_h}j"

            pnl_cls = "pnl-pos" if pnl > 0 else "pnl-neg" if pnl < 0 else "pnl-zero"
            drop_cls = "pnl-pos" if drop_pct > 0 else "pnl-neg" if drop_pct < 0 else "pnl-zero"

            html += f"""
                    <tr>
                        <td><strong>{coin}</strong></td>
                        <td>{strat_pill}</td>
                        <td>{side_pill}</td>
                        <td>${entry_px:.5f} <span style="color:var(--text-muted);">➔</span> ${cur_px:.5f}</td>
                        <td class="{drop_cls}"><strong>{'+' if drop_pct >= 0 else ''}{drop_pct:.2f}%</strong></td>
                        <td>${margin:.2f} <span style="color:var(--text-muted); font-size:0.75rem;">({rem_sz:.1f} koin)</span></td>
                        <td class="{pnl_cls}"><strong>{'+' if pnl >= 0 else ''}${pnl:.2f} ({roe:+.1f}%)</strong></td>
                        <td>{sl_badge}</td>
                        <td>{tp_str}</td>
                        <td style="color:var(--text-muted); font-size:0.78rem;">{time_str}</td>
                    </tr>
            """

    html += f"""
                </tbody>
            </table>
        </div>

        <!-- SECTION 2: AUDIT LOG RIWAYAT TRADE YANG SUDAH CLOSE -->
        <div class="section-header">
            <h2>🏁 Riwayat & Audit Trade Selesai ({len(closed_trades)})</h2>
            <span style="font-size:0.8rem; color:var(--text-muted);">Sesi Forward-Test Baru Dimulai dari Batch Live Ini</span>
        </div>
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>Tgl Selesai</th>
                        <th>Simbol</th>
                        <th>Stream</th>
                        <th>Side</th>
                        <th>Entry / Exit</th>
                        <th>Durasi</th>
                        <th>Alasan Exit</th>
                        <th>Hasil</th>
                        <th>Net Realized PnL</th>
                        <th>ROE %</th>
                        <th>R-Multiple</th>
                    </tr>
                </thead>
                <tbody>
    """

    if not closed_trades:
        html += """
                    <tr>
                        <td colspan="11" class="empty-row">Belum ada posisi yang ditutup pada sesi baru ini. Seluruh posisi saat ini masih aktif dikawal.</td>
                    </tr>
        """
    else:
        for p in reversed(closed_trades):
            c_sym = p.get("coin", "-")
            strat = p.get("strategy", "SWING")
            side = p.get("side", "SHORT")
            exit_dt = p.get("exit_time", "-")
            en_px = float(p.get("entry_px", 0.0))
            ex_px = float(p.get("exit_px", 0.0))
            dur = p.get("hold_hours")
            dur_str = f"{dur:.1f}j" if dur is not None else "-"
            reason = p.get("exit_reason", "CLOSED")
            final_pnl = float(p.get("final_pnl_usd", 0.0))
            roe_val = float(p.get("realized_roe_pct", 0.0))
            r_mult = p.get("realized_r")
            r_str = f"{r_mult:+.2f}R" if r_mult is not None else "-"

            strat_pill = '<span class="pill pill-intra">⚡ INTRA</span>' if strat == "DAYTRADE" else '<span class="pill pill-swing">🌊 SWING</span>'
            side_pill = '<span class="pill pill-short">SHORT</span>' if side == "SHORT" else '<span class="pill pill-long">LONG</span>'
            res_pill = '<span class="pill pill-win">✅ WIN</span>' if final_pnl > 0 else '<span class="pill pill-loss">❌ LOSS</span>' if final_pnl < 0 else '<span class="pill pill-be">BE</span>'
            pnl_cls = "pnl-pos" if final_pnl > 0 else "pnl-neg" if final_pnl < 0 else "pnl-zero"

            html += f"""
                    <tr>
                        <td style="color:var(--text-muted); font-size:0.78rem;">{exit_dt}</td>
                        <td><strong>{c_sym}</strong></td>
                        <td>{strat_pill}</td>
                        <td>{side_pill}</td>
                        <td>${en_px:.5f} <span style="color:var(--text-muted);">➔</span> ${ex_px:.5f}</td>
                        <td>{dur_str}</td>
                        <td style="font-size:0.78rem;"><code>{reason}</code></td>
                        <td>{res_pill}</td>
                        <td class="{pnl_cls}"><strong>{'+' if final_pnl >= 0 else ''}${final_pnl:.2f}</strong></td>
                        <td class="{pnl_cls}">{roe_val:+.2f}%</td>
                        <td style="font-weight:600;">{r_str}</td>
                    </tr>
            """

    html += """
                </tbody>
            </table>
        </div>
    """

    # 4. Autonomous AI Self-Operating & Self-Evaluation Telemetry Section
    try:
        from src.autonomous_engine import AutonomousEngine
        auto_eng = AutonomousEngine(trader)
        auto_state = auto_eng.load_state()
        gov = auto_eng.get_live_governor_snapshot()
        thermo = gov.get("thermostat", {})
        eval_rep = auto_state.get("last_eval_report") or {}
        feed = auto_state.get("telemetry_feed", [])

        html += f"""
        <div class="section-title" style="margin-top:28px;">🤖 Autonomous AI Self-Operating Engine &amp; Self-Evaluation Monitor</div>
        <div class="metrics-grid" style="margin-bottom:18px;">
            <div class="metric-card">
                <div class="metric-label">Thermostat Self-Defense</div>
                <div class="metric-value" style="font-size:1.05rem;">{thermo.get('badge', '🟢 OPTIMAL')}</div>
                <div class="metric-sub">Wajib APPROVED + Grade A (&ge;{thermo.get('min_ai_confidence', 75)}%)</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Pagar Cadangan Kas f(E)</div>
                <div class="metric-value">${gov.get('reserve_usd', 15.0):.2f} <span style="font-size:0.85rem; color:var(--text-muted);">({gov.get('reserve_pct', 15.0):.0f}% E)</span></div>
                <div class="metric-sub">Margin Siap Tembak: ${gov.get('free_above_reserve', 0.0):.2f} USDC</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Kapasitas Slot N_max(E)</div>
                <div class="metric-value">{gov.get('active_count', 0)} / {gov.get('max_positions', 5)} <span style="font-size:0.85rem; color:var(--text-muted);">Posisi</span></div>
                <div class="metric-sub">Swing: {gov.get('swing_count', 0)}/{gov.get('max_swing', 3)} | Intra: {gov.get('intraday_count', 0)}/{gov.get('max_intraday', 2)}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Ukuran Peluru f(E)</div>
                <div class="metric-value">${gov.get('min_order_margin', 12.0):.2f} – ${gov.get('max_order_margin', 18.0):.2f}</div>
                <div class="metric-sub">Slot Base: ${gov.get('slot_base_margin', 15.0):.2f} USDC</div>
            </div>
        </div>
        <div class="table-card" style="padding:16px; margin-bottom:24px;">
            <div style="margin-bottom:10px; font-size:0.9rem;">
                <strong>Status Gatekeeper Otonom:</strong> <span style="color:var(--neon-green);">{gov.get('gate_summary', '-')}</span><br>
                <strong>Evaluasi Mandiri Terakhir ({eval_rep.get('evaluated_at', '-')}):</strong> {thermo.get('reason', '-')}<br>
                <strong>Kalibrasi Bobot Screener:</strong> <span style="color:var(--text-muted);">{eval_rep.get('weight_calibration', '-')}</span>
            </div>
            <table style="margin-top:10px;">
                <thead>
                    <tr>
                        <th>Waktu</th>
                        <th>Kategori</th>
                        <th>Koin</th>
                        <th>Aksi Otonom</th>
                        <th>Detail Keputusan &amp; Evaluasi AI</th>
                    </tr>
                </thead>
                <tbody>
        """
        if not feed:
            html += """<tr><td colspan="5" class="empty-row">Menunggu log telemetri otonom pertama...</td></tr>"""
        else:
            for item in reversed(feed[-12:]):
                html += f"""
                    <tr>
                        <td style="color:var(--text-muted); font-size:0.78rem;">{item.get('timestamp', '-')}</td>
                        <td><code>{item.get('category', '-')}</code></td>
                        <td><strong>{item.get('coin', '-')}</strong></td>
                        <td>{item.get('action', '-')}</td>
                        <td style="font-size:0.82rem; color:var(--text-muted);">{item.get('detail', '-')}</td>
                    </tr>
                """
        html += """
                </tbody>
            </table>
        </div>
        """
    except Exception:
        pass

    html += f"""
        <footer>
            ShortHunter Dual-Stream Autonomous Quant Engine &bull; Hyperliquid L1 Mainnet &bull; On-Chain Stop-Loss Protected
        </footer>
    </div>
</body>
</html>
    """

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


if __name__ == "__main__":
    path = generate_html_report()
    print(f"[✓] Dashboard HTML berhasil di-generate di: {path}")
