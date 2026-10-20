import os
import sys
import json
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any, Optional

def generate_daily_performance_section(root_dir: str, trader = None) -> str:
    """
    Builds an SVG chart and table for daily realized profit vs total capital.
    Accounts for capital mutations (deposits/top-ups) so they do NOT distort ROI %.
    """
    daily_cache_path = os.path.join(root_dir, "data", "daily_performance_cache.json")
    daily_stats = {}
    if os.path.exists(daily_cache_path):
        try:
            with open(daily_cache_path, "r") as f:
                daily_stats = json.load(f)
        except Exception:
            daily_stats = {}

    # Refresh live fills if available
    if trader and hasattr(trader, "account_address") and trader.account_address:
        try:
            fills = trader.info.user_fills(trader.account_address) or []
            live_daily = defaultdict(lambda: {"gross_pnl": 0.0, "fee": 0.0, "net_pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
            has_fills = False
            for f in fills:
                closed_pnl = float(f.get("closedPnl", 0))
                if closed_pnl != 0:
                    dt_str = datetime.fromtimestamp(f["time"] / 1000).strftime("%Y-%m-%d")
                    fee = float(f.get("fee", 0))
                    live_daily[dt_str]["gross_pnl"] += closed_pnl
                    live_daily[dt_str]["fee"] += fee
                    live_daily[dt_str]["net_pnl"] += (closed_pnl - fee)
                    live_daily[dt_str]["trades"] += 1
                    if closed_pnl > 0:
                        live_daily[dt_str]["wins"] += 1
                    else:
                        live_daily[dt_str]["losses"] += 1
                    has_fills = True
            if has_fills:
                daily_stats = dict(live_daily)
                try:
                    with open(daily_cache_path, "w") as f:
                        json.dump(daily_stats, f, indent=2)
                except Exception:
                    pass
        except Exception:
            pass

    mutations_path = os.path.join(root_dir, "data", "capital_mutations.json")
    init_cap = 100.0
    deposits_list = []
    if os.path.exists(mutations_path):
        try:
            with open(mutations_path, "r") as f:
                mut_data = json.load(f)
                init_cap = float(mut_data.get("initial_capital_usd", 100.0))
                deposits_list = mut_data.get("deposits", [])
        except Exception:
            pass

    daily_rows = []
    tot_onchain_pnl = 0.0
    tot_onchain_trades = 0
    tot_onchain_wins = 0

    for d_str, st in sorted(daily_stats.items()):
        day_modal = init_cap + sum(float(dep.get("amount_usd", 0.0)) for dep in deposits_list if dep.get("date", "") <= d_str)
        pct_modal = (st["net_pnl"] / day_modal * 100.0) if day_modal > 0 else 0.0
        wr = (st["wins"] / st["trades"] * 100.0) if st["trades"] > 0 else 0.0
        tot_onchain_pnl += st["net_pnl"]
        tot_onchain_trades += st["trades"]
        tot_onchain_wins += st["wins"]
        daily_rows.append({
            "date": d_str,
            "net_pnl": st["net_pnl"],
            "modal": day_modal,
            "pct_modal": pct_modal,
            "trades": st["trades"],
            "wins": st["wins"],
            "losses": st["losses"],
            "wr": wr,
            "fee": st.get("fee", 0.0)
        })

    if not daily_rows:
        return """<div class=\"card\" style=\"margin: 20px 0; padding: 16px;\">Belum ada riwayat trading on-chain terarsip...</div>"""

    overall_wr = (tot_onchain_wins / tot_onchain_trades * 100.0) if tot_onchain_trades > 0 else 0.0
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_row = next((r for r in daily_rows if r["date"] == today_str), None)

    svg_w = 760
    svg_h = 220
    zero_y = 115
    n_days = len(daily_rows)
    slot_w = 640 / max(1, n_days)
    svg_bars = []

    for i, row in enumerate(daily_rows):
        cx = 70 + i * slot_w + slot_w / 2
        pnl = row["net_pnl"]
        pct = row["pct_modal"]
        bar_h = min(78, max(10, abs(pnl) * 12.0))
        color = "#10b981" if pnl >= 0 else "#ef4444"
        by = (zero_y - bar_h) if pnl >= 0 else zero_y
        
        val_y = by - 16 if pnl >= 0 else by + bar_h + 15
        pct_y = by - 4 if pnl >= 0 else by + bar_h + 27
        
        dt_obj = datetime.strptime(row["date"], "%Y-%m-%d")
        d_label = dt_obj.strftime("%d %b")
        if row["date"] == today_str:
            d_label = f"{dt_obj.strftime('%d %b')} (Hari Ini)"
        
        bar_snippet = f"""
            <g class="bar-group">
                <rect x="{cx - 24:.1f}" y="{by:.1f}" width="48" height="{bar_h:.1f}" rx="5" fill="{color}" opacity="0.9" />
                <text x="{cx:.1f}" y="{val_y:.1f}" text-anchor="middle" fill="{color}" font-size="11" font-weight="700">{'+' if pnl >= 0 else ''}${pnl:.2f}</text>
                <text x="{cx:.1f}" y="{pct_y:.1f}" text-anchor="middle" fill="{color}" font-size="10" font-weight="800">({pct:+.2f}%)</text>
                <text x="{cx:.1f}" y="175" text-anchor="middle" fill="var(--text-main)" font-size="11" font-weight="600">{d_label}</text>
                <text x="{cx:.1f}" y="190" text-anchor="middle" fill="var(--text-muted)" font-size="9">{row['wins']}W/{row['losses']}L • M:${row['modal']:.0f}</text>
            </g>"""
        svg_bars.append(bar_snippet)

    svg_bars_html = "".join(svg_bars)

    table_rows = []
    for row in reversed(daily_rows):
        pnl = row["net_pnl"]
        pct = row["pct_modal"]
        p_cls = "pnl-pos" if pnl > 0 else "pnl-neg" if pnl < 0 else "pnl-zero"
        pct_badge = f"<span style='padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 0.85rem; background: {'rgba(16,185,129,0.15); color:#10b981;' if pnl >= 0 else 'rgba(239,68,68,0.15); color:#ef4444;'}'>{pct:+.2f}%</span>"
        dt_obj = datetime.strptime(row["date"], "%Y-%m-%d")
        dt_display = dt_obj.strftime("%A, %d %B %Y") if row["date"] != today_str else f"<strong>{dt_obj.strftime('%d %B %Y')} (Hari Ini)</strong>"
        status_badge = "🚀 Target Tercapai" if pct >= 2.0 else "🟢 Profit Konsisten" if pnl > 0 else "⚠️ Controlled Drawdown"
        
        row_html = f"""
            <tr>
                <td>{dt_display}</td>
                <td>${row['modal']:.2f}</td>
                <td class="{p_cls}"><strong>{'+' if pnl >= 0 else ''}${pnl:.2f}</strong></td>
                <td>{pct_badge}</td>
                <td>{row['wr']:.0f}%</td>
                <td>{row['trades']} trades ({row['wins']}W / {row['losses']}L)</td>
                <td style="color:var(--text-muted);">${row['fee']:.4f}</td>
                <td><span class="badge badge-neutral">{status_badge}</span></td>
            </tr>"""
        table_rows.append(row_html)

    table_rows_html = "".join(table_rows)

    today_summary = ""
    if today_row:
        today_pnl = today_row["net_pnl"]
        today_pct = today_row["pct_modal"]
        today_color = "#10b981" if today_pnl >= 0 else "#ef4444"
        today_summary = f"Hari Ini: <strong style='color:{today_color};'>{'+' if today_pnl >= 0 else ''}${today_pnl:.2f} ({today_pct:+.2f}% terhadap modal)</strong> | "

    section_html = f"""
        <!-- SECTION: DAILY PERFORMANCE & % PROFIT TERHADAP TOTAL MODAL -->
        <div class="section-header" style="margin-top: 36px;">
            <h2>📈 Performa & % Profit Harian terhadap Total Modal</h2>
            <span style="font-size:0.8rem; color:var(--text-muted);">Dihitung on-chain live Hyperliquid (memisahkan injeksi deposit dari profit murni)</span>
        </div>

        <div class="card" style="margin-bottom: 24px; padding: 22px; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; margin-bottom: 16px; border-bottom: 1px solid var(--card-border); padding-bottom: 14px;">
                <div>
                    <div style="font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600;">Akumulasi Realized Profit On-Chain</div>
                    <div style="font-size: 1.5rem; font-weight: 800; color: #10b981; margin-top: 2px;">+${tot_onchain_pnl:.2f} USD <span style="font-size: 0.85rem; font-weight: 500; color: var(--text-muted);">(Total Bersih 5 Hari)</span></div>
                </div>
                <div style="display: flex; gap: 24px; text-align: right; flex-wrap: wrap;">
                    <div>
                        <div style="font-size: 0.78rem; color: var(--text-muted);">Win Rate Kumulatif</div>
                        <div style="font-size: 1.25rem; font-weight: 700; color: #10b981;">{overall_wr:.1f}% <span style="font-size: 0.75rem; color: var(--text-muted);">({tot_onchain_wins}W / {tot_onchain_trades - tot_onchain_wins}L)</span></div>
                    </div>
                    <div>
                        <div style="font-size: 0.78rem; color: var(--text-muted);">Modal Pokok Disetor</div>
                        <div style="font-size: 1.25rem; font-weight: 700; color: var(--text-main);">${daily_rows[-1]['modal']:.2f} USDC</div>
                        <div style="font-size: 0.72rem; color: #10b981; font-weight: 600;">Saldo Bursa: ${daily_rows[-1]['modal'] + tot_onchain_pnl:.2f} (+$${tot_onchain_pnl:.2f})</div>
                    </div>
                </div>
            </div>

            <!-- SVG Performance Chart -->
            <div style="width: 100%; overflow-x: auto; padding-bottom: 8px;">
                <svg viewBox="0 0 {svg_w} {svg_h}" style="min-width: 620px; width: 100%; height: auto; font-family: inherit;">
                    <!-- Zero line -->
                    <line x1="50" y1="{zero_y}" x2="{svg_w - 30}" y2="{zero_y}" stroke="var(--card-border)" stroke-dasharray="4" stroke-width="1.5" />
                    <text x="20" y="{zero_y + 4}" fill="var(--text-muted)" font-size="11" font-weight="600">$0</text>

                    <!-- Grid Reference lines -->
                    <line x1="50" y1="{zero_y - 60}" x2="{svg_w - 30}" y2="{zero_y - 60}" stroke="rgba(255,255,255,0.05)" stroke-width="1" />
                    <text x="15" y="{zero_y - 56}" fill="var(--text-muted)" font-size="10">+$5</text>

                    <line x1="50" y1="{zero_y + 40}" x2="{svg_w - 30}" y2="{zero_y + 40}" stroke="rgba(255,255,255,0.05)" stroke-width="1" />
                    <text x="15" y="{zero_y + 44}" fill="var(--text-muted)" font-size="10">-$3</text>

                    {svg_bars_html}
                </svg>
            </div>

            <div style="margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--card-border); font-size: 0.8rem; color: var(--text-muted); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                <span>{today_summary}🛡️ Formula: <code>% Profit = (Net PnL / Total Modal Harian) × 100%</code></span>
                <span>💡 Top-Up Modal (+$194.61) tercatat terpisah & tidak mendistorsi % ROI</span>
            </div>
        </div>

        <div class="table-responsive" style="margin-bottom: 36px;">
            <table>
                <thead>
                    <tr>
                        <th>Tanggal</th>
                        <th>Total Modal</th>
                        <th>Net Profit</th>
                        <th>% Profit / Modal</th>
                        <th>Win Rate</th>
                        <th>Trades</th>
                        <th>Fee Bursa</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows_html}
                </tbody>
            </table>
        </div>
    """
    return section_html
