#!/usr/bin/env python3
"""
DAILY PAYOUT MANAGER — Gemini ShortHunter
Logika Pengelolaan Gaji Harian Berbasis USD Realized PnL Hari Terakhir

Aturan Utama:
1. Pengisian alamat gajian dituliskan di menu (tersimpan persisten di data/daily_payout_config.json).
2. Jika TIDAK ADA alamat gajian, maka TIDAK ADA transfer gaji di hari itu (strict safety bypass).
3. Nominal gaji harian dihitung sebagai fungsi dari USD PnL bersih hari terakhir (Realized PnL),
   BUKAN dari selisih saldo hari ini - saldo kemarin, guna melindungi dari bias deposit/penambahan modal luar.
4. Payout hanya diproses jika Realized PnL hari terakhir bernilai positif (profit).
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger("daily_payout")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "daily_payout_config.json")

DEFAULT_CONFIG = {
    "payout_address": "",
    "payout_pct": 50.0,
    "min_payout_usd": 5.0,
    "auto_payout": False,
    "last_payout_date": "",
    "last_payout_amount": 0.0,
    "last_tx_hash": "",
    "payout_history": []
}


class DailyPayoutManager:
    """
    Engine kalkulasi dan eksekusi gaji harian (daily profit payout).
    """

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        if not os.path.exists(CONFIG_PATH):
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            cls.save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                return cfg
        except Exception as e:
            logger.error(f"Gagal membaca {CONFIG_PATH}: {e}")
            return DEFAULT_CONFIG.copy()

    @classmethod
    def save_config(cls, config: Dict[str, Any]) -> bool:
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Gagal menyimpan {CONFIG_PATH}: {e}")
            return False

    @classmethod
    def set_payout_address(cls, address: str) -> Dict[str, Any]:
        address = address.strip()
        cfg = cls.load_config()
        cfg["payout_address"] = address
        cls.save_config(cfg)
        return {
            "success": True,
            "payout_address": address,
            "status": "ACTIVE" if address else "DISABLED_NO_ADDRESS"
        }

    @classmethod
    def set_payout_pct(cls, pct: float) -> Dict[str, Any]:
        pct = max(1.0, min(100.0, float(pct)))
        cfg = cls.load_config()
        cfg["payout_pct"] = pct
        cls.save_config(cfg)
        return {
            "success": True,
            "payout_pct": pct
        }

    @classmethod
    def get_last_day_realized_pnl(cls, target_date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Menghitung Realized PnL bersih dari posisi tertutup pada hari terakhir.
        Menggunakan kombinasi Hyperliquid user fills on-chain dan trade_db.
        """
        today_dt = datetime.now()
        target_date = target_date_str or (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        today_str = today_dt.strftime("%Y-%m-%d")

        trades_found: List[Dict[str, Any]] = []
        total_realized_pnl = 0.0
        total_fee = 0.0

        # 1. Sumber Primer: Hyperliquid On-Chain Fills
        try:
            from src.trader import HyperliquidTrader
            t = HyperliquidTrader()
            if t.is_configured():
                fills = t.info.user_fills(t.account_address) or []
                for f in fills:
                    closed_pnl = float(f.get("closedPnl", 0.0))
                    fee = float(f.get("fee", 0.0))
                    fill_time_ms = f.get("time", 0)
                    fill_date = datetime.fromtimestamp(fill_time_ms / 1000.0).strftime("%Y-%m-%d")

                    # Ambil trade hari terakhir atau hari ini jika belum ada target khusus
                    if fill_date in (target_date, today_str) and closed_pnl != 0.0:
                        trades_found.append({
                            "coin": f.get("coin"),
                            "side": f.get("side"),
                            "dir": f.get("dir"),
                            "closed_pnl": closed_pnl,
                            "fee": fee,
                            "net_pnl": closed_pnl - fee,
                            "date": fill_date,
                            "time": datetime.fromtimestamp(fill_time_ms / 1000.0).strftime("%H:%M:%S"),
                            "source": "hyperliquid_onchain"
                        })
                        total_realized_pnl += closed_pnl
                        total_fee += fee
        except Exception as e:
            logger.warning(f"Gagal query user_fills Hyperliquid: {e}")

        # 2. Sumber Sekunder (Trade Journal Forward Test) jika Hyperliquid fills kosong
        if not trades_found:
            try:
                from src.forward_test import ForwardTestManager
                ft = ForwardTestManager()
                journal = ft.load_journal()
                for j_key, tr in journal.items():
                    if tr.get("status") == "CLOSED":
                        ex_time = tr.get("exit_time", "")
                        tr_date = ex_time[:10] if ex_time else ""
                        if tr_date in (target_date, today_str):
                            pnl_val = float(tr.get("realized_pnl", tr.get("final_pnl_usd", 0.0)))
                            total_realized_pnl += pnl_val
                            trades_found.append({
                                "coin": tr.get("coin", "UNKNOWN"),
                                "closed_pnl": pnl_val,
                                "fee": 0.0,
                                "net_pnl": pnl_val,
                                "date": tr_date,
                                "source": "forward_test_journal"
                            })
            except Exception as e:
                logger.warning(f"Gagal query trade journal: {e}")

        net_profit = round(total_realized_pnl - total_fee, 2)
        win_trades = [t for t in trades_found if t.get("net_pnl", 0.0) > 0]
        loss_trades = [t for t in trades_found if t.get("net_pnl", 0.0) < 0]

        return {
            "target_date": target_date,
            "today_date": today_str,
            "total_trades": len(trades_found),
            "win_count": len(win_trades),
            "loss_count": len(loss_trades),
            "gross_realized_pnl_usd": round(total_realized_pnl, 2),
            "total_fee_usd": round(total_fee, 4),
            "net_realized_pnl_usd": net_profit,
            "is_profit": net_profit > 0,
            "trades": trades_found
        }

    @classmethod
    def calculate_daily_payout(cls) -> Dict[str, Any]:
        """
        Menghitung hak transfer gaji harian hari ini berdasarkan Realized PnL terakhir.
        """
        cfg = cls.load_config()
        address = cfg.get("payout_address", "").strip()
        payout_pct = float(cfg.get("payout_pct", 50.0))
        min_payout = float(cfg.get("min_payout_usd", 5.0))

        pnl_data = cls.get_last_day_realized_pnl()
        net_pnl = pnl_data.get("net_realized_pnl_usd", 0.0)

        # 1. Cek keberadaan alamat gajian
        if not address:
            return {
                "eligible": False,
                "reason": "NO_ADDRESS",
                "message": "Alamat gajian belum diisi di menu. Sesuai aturan, TIDAK ADA transfer gaji di hari ini.",
                "net_pnl_usd": net_pnl,
                "payout_amount_usd": 0.0,
                "payout_address": "",
                "pnl_data": pnl_data
            }

        # 2. Cek apakah ada profit di hari terakhir
        if net_pnl <= 0:
            return {
                "eligible": False,
                "reason": "ZERO_OR_NEGATIVE_PNL",
                "message": f"Realized PnL hari terakhir (${net_pnl:,.2f} USD) tidak profit. Gaji harian tidak dicairkan.",
                "net_pnl_usd": net_pnl,
                "payout_amount_usd": 0.0,
                "payout_address": address,
                "pnl_data": pnl_data
            }

        # 3. Hitung nominal gaji harian f(PNL)
        raw_payout = round(net_pnl * (payout_pct / 100.0), 2)

        # 4. Validasi kas bebas live di Hyperliquid
        try:
            from src.trader import HyperliquidTrader
            trader = HyperliquidTrader()
            acct = trader.get_account_state()
            eq = float(acct.get("equity", 0.0))
            avail = float(acct.get("available", 0.0))
            buffer_floor = eq * 0.10
            safe_cash = max(0.0, avail - buffer_floor)
        except Exception:
            safe_cash = raw_payout

        payout_amount = min(raw_payout, safe_cash)

        if payout_amount < min_payout:
            return {
                "eligible": False,
                "reason": "BELOW_MINIMUM",
                "message": f"Nominal gaji (${payout_amount:,.2f} USD) di bawah batas minimum transfer (${min_payout:,.2f} USD). Ditahan untuk akumulasi berikutnya.",
                "net_pnl_usd": net_pnl,
                "payout_amount_usd": payout_amount,
                "payout_address": address,
                "pnl_data": pnl_data
            }

        return {
            "eligible": True,
            "reason": "READY",
            "message": f"Gaji harian siap ditransfer: ${payout_amount:,.2f} USD ({payout_pct:.0f}% dari profit bersih ${net_pnl:,.2f} USD) ke {address[:6]}...{address[-4:]}",
            "net_pnl_usd": net_pnl,
            "payout_pct": payout_pct,
            "payout_amount_usd": payout_amount,
            "retained_profit_usd": round(net_pnl - payout_amount, 2),
            "payout_address": address,
            "pnl_data": pnl_data
        }

    @classmethod
    def execute_payout(cls, force: bool = False) -> Dict[str, Any]:
        """
        Mengeksekusi transfer gaji harian secara live ke alamat gajian via Hyperliquid L1 usd_transfer.
        """
        calc = cls.calculate_daily_payout()
        if not calc.get("eligible") and not force:
            return {
                "success": False,
                "error": calc.get("message"),
                "calc": calc
            }

        address = calc.get("payout_address")
        amount = calc.get("payout_amount_usd", 0.0)
        today_str = datetime.now().strftime("%Y-%m-%d")

        if amount <= 0:
            return {
                "success": False,
                "error": "Nominal transfer bernilai 0 USD.",
                "calc": calc
            }

        try:
            from src.trader import HyperliquidTrader
            trader = HyperliquidTrader()
            if not trader.is_configured():
                return {
                    "success": False,
                    "error": "HyperliquidTrader belum terkonfigurasi (Secret Key/Address hilang)",
                    "calc": calc
                }

            logger.info(f"Mengeksekusi transfer gaji harian ${amount:,.2f} USD ke {address}...")
            transfer_res = trader.exchange.usd_transfer(destination=address, amount=amount)

            # Catat ke history
            cfg = cls.load_config()
            cfg["last_payout_date"] = today_str
            cfg["last_payout_amount"] = amount
            tx_hash = transfer_res.get("response", {}).get("data", {}).get("hash", "") if isinstance(transfer_res, dict) else str(transfer_res)
            cfg["last_tx_hash"] = tx_hash
            cfg.setdefault("payout_history", []).append({
                "date": today_str,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "amount_usd": amount,
                "net_pnl_usd": calc.get("net_pnl_usd", 0.0),
                "destination": address,
                "tx_hash": tx_hash,
                "response": transfer_res
            })
            cls.save_config(cfg)

            return {
                "success": True,
                "amount_usd": amount,
                "destination": address,
                "tx_hash": tx_hash,
                "transfer_response": transfer_res
            }
        except Exception as e:
            logger.error(f"Eksekusi transfer gaji gagal: {e}")
            return {
                "success": False,
                "error": str(e),
                "calc": calc
            }

    @classmethod
    def format_daily_report(cls) -> str:
        """Membuat laporan teks evaluasi & potensi gaji harian yang rapi."""
        calc = cls.calculate_daily_payout()
        cfg = cls.load_config()
        addr = cfg.get("payout_address")
        pnl = calc.get("pnl_data", {})
        lines = [
            "============================================================",
            "   💰 LAPORAN EVALUASI GAJIAN HARIAN (DAILY PAYOUT)   ",
            "============================================================",
            f"📅 Tanggal Evaluasi     : {pnl.get('target_date', '-')}",
            f"📊 Total Trade Tertutup   : {pnl.get('total_trades', 0)} (Win: {pnl.get('win_count', 0)} | Loss: {pnl.get('loss_count', 0)})",
            f"💵 Gross Realized PnL     : ${pnl.get('gross_realized_pnl_usd', 0.0):,.2f} USD",
            f"⛽️ Total Fee              : ${pnl.get('total_fee_usd', 0.0):,.4f} USD",
            f"💵 Net Realized PnL (Net) : ${pnl.get('net_realized_pnl_usd', 0.0):,.2f} USD",
            "------------------------------------------------------------",
            f"⚙️  Porsi Gaji (Payout %) : {cfg.get('payout_pct', 50.0):.1f}% dari Profit Bersih",
            f"🛡️  Batas Min Transfer    : ${cfg.get('min_payout_usd', 5.0):.2f} USD",
            f"📫 Alamat Tujuan Gajian : {addr if addr else '(Belum diatur)'}",
            f"🏦 Mode Transfer        : {'OTOMATIS' if cfg.get('auto_payout') else 'MANUAL / KONFIRMASI'}",
            "------------------------------------------------------------"
        ]
        if calc.get("eligible"):
            lines.extend([
                "🎉 STATUS: MEMENUHI SYARAT TRANSFER HARI INI",
                f"👉 Estimasi Hak Gajian  : ${calc.get('payout_amount_usd', 0.0):,.2f} USD",
                f"💼 Profit Ditahan Modal : ${calc.get('retained_profit_usd', 0.0):,.2f} USD",
            ])
        else:
            lines.extend([
                "ℹ️  STATUS: BELUM MEMENUHI SYARAT HARI INI",
                f"👉 Keterangan           : {calc.get('message')}",
            ])
        lines.append("============================================================")
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1].lower() == "calc":
        print(json.dumps(DailyPayoutManager.calculate_daily_payout(), indent=2))
    else:
        print(DailyPayoutManager.format_daily_report())
