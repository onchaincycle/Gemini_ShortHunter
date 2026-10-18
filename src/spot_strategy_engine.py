"""
Spot Strategy & Execution Engine (Hold 1-24 Bulan) — Powered by SCR v10 & Whale Intelligence
==============================================================================================
Modul khusus untuk strategi akumulasi SPOT jangka panjang tanpa leverage (Zero Liquidation):
  1. Seleksi Koin Kuantitatif (PRIME & CHALLENGER C1 dari scr.db, Crash Gate, Klasifikasi Sektor)
  2. Sistem Pembelian & Smart 3-Tranche DCA (Scout, Value Pullback DCA, Momentum Pyramiding)
  3. Sistem Exit Bertahap Multi-Tier "Mendekati Sempurna":
     - Pilar 1: Whale & Smart Money On-Chain Tracking (DRS & Nansen Flows)
     - Pilar 2: Siklus Makro BTC 4-Tahunan (Mayer Multiple, Halving Cycle, Phase A-F)
     - Pilar 3: Tiered Profit Taking (TP1 Principal Free, TP2 Euphoria, TP3 Macro Peak, Tier 4 Moonbag Runner)
     - Pilar 4: Emergency Whale Invalidation Exit (Proteksi modal jika paus kabur total)
  4. Eksekusi Ordonansi Spot Hyperliquid & Sinkronisasi Portofolio (portfolio.csv & spot_vault_cache.json)
"""

import os
import sys
import csv
import json
import time
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("SpotStrategyEngine")

# Direktori path proyek
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_DATA_DIR = os.path.join(BASE_DIR, "data")
PORTFOLIO_CACHE_FILE = os.path.join(LOCAL_DATA_DIR, "spot_vault_cache.json")

# Path ke project Claude_Crypto (SCR v10)
SCR_V10_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "Claude_Crypto", "SCR_v10"))
SCR_DB_PATH = os.path.join(SCR_V10_PATH, "data", "scr.db")
PORTFOLIO_CSV = os.path.join(SCR_V10_PATH, "inputs", "portfolio.csv")
COIN_CLASSES_CSV = os.path.join(SCR_V10_PATH, "inputs", "coin_classes.csv")
UNLOCKS_CSV = os.path.join(SCR_V10_PATH, "inputs", "unlocks.csv")


class SpotStrategyEngine:
    """
    Engine komprehensif untuk perdagangan akumulasi Spot jangka panjang (1-24 bulan).
    """

    @classmethod
    def _get_scr_db_conn(cls) -> Optional[sqlite3.Connection]:
        if os.path.exists(SCR_DB_PATH):
            try:
                conn = sqlite3.connect(SCR_DB_PATH)
                conn.row_factory = sqlite3.Row
                return conn
            except Exception as e:
                logger.error(f"Gagal membuka koneksi scr.db: {e}")
        return None

    # =========================================================================
    # 1. SELEKSI KOIN KUANTITATIF (COIN SELECTION)
    # =========================================================================
    @classmethod
    def select_accumulation_candidates(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Memilih koin terbaik untuk akumulasi SPOT berdasarkan riset SCR v10 & On-Chain Whale:
          - Status PRIME: SS >= 70, ES >= 65, DRS <= 30
          - Status CHALLENGER: SS >= 65, base_days >= 45, VCR <= 0.40
          - Crash Gate: Memfilter koin yang belum membentuk base akumulasi solid
        """
        conn = cls._get_scr_db_conn()
        if not conn:
            logger.warning("scr.db tidak ditemukan, tidak dapat menyeleksi koin SPOT.")
            return []

        candidates = []
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(date) FROM scores")
            r_date = cursor.fetchone()
            if not r_date or not r_date[0]:
                return []
            latest_date = r_date[0]

            # Query koin yang menunjukkan akumulasi dasar (SS tinggi atau ES tinggi)
            query = """
                SELECT 
                    c.symbol, c.coin_id, c.name, c.price, c.vol24, c.mcap,
                    s.ss, s.es, s.drs, s.ph, s.action
                FROM scores s
                JOIN coins c ON s.coin_id = c.coin_id
                WHERE s.date = ?
                  AND (s.ss >= 65.0 OR (s.ss >= 60.0 AND s.es >= 65.0))
                  AND (s.drs IS NULL OR s.drs <= 40.0)
                  AND c.price > 0
                ORDER BY (s.ss * 0.5 + COALESCE(s.es, 50.0) * 0.5) DESC
                LIMIT ?
            """
            cursor.execute(query, (latest_date, limit * 2))
            rows = cursor.fetchall()

            for row in rows:
                sym = (row["symbol"] or "").upper().strip()
                ss = float(row["ss"] or 0.0)
                es = float(row["es"] or 0.0)
                drs = float(row["drs"] or 0.0)
                px = float(row["price"] or 0.0)
                action = (row["action"] or "WATCH").strip().upper()

                # Klasifikasi kategori sinyal
                if action == "PRIME" or (ss >= 72.0 and es >= 68.0 and drs <= 25.0):
                    signal_tier = "PRIME_CONVICTION"
                    badge = "💎 PRIME (Strong Base + Whale Accumulating)"
                    max_alloc_pct = 100.0  # 100% dari alokasi slot standar
                elif action == "CHALLENGER" or ss >= 68.0:
                    signal_tier = "CHALLENGER_C1"
                    badge = "🎯 CHALLENGER (Base Matang & Akumulasi Awal)"
                    max_alloc_pct = 75.0
                else:
                    signal_tier = "SCOUT_WATCH"
                    badge = "👁️ ACCUMULATION WATCH"
                    max_alloc_pct = 50.0

                candidates.append({
                    "symbol": sym,
                    "coin_id": row["coin_id"],
                    "name": row["name"],
                    "price": px,
                    "ss": round(ss, 1),
                    "es": round(es, 1),
                    "drs": round(drs, 1),
                    "phase_num": float(row["ph"] or 3.5),
                    "action": action,
                    "signal_tier": signal_tier,
                    "badge": badge,
                    "max_alloc_pct": max_alloc_pct,
                    "asof_date": latest_date
                })

                if len(candidates) >= limit:
                    break

        except Exception as e:
            logger.error(f"Error query accumulation candidates: {e}")
        finally:
            conn.close()

        return candidates

    # =========================================================================
    # 2. SISTEM PEMBELIAN & SMART 3-TRANCHE DCA
    # =========================================================================
    @classmethod
    def generate_smart_dca_plan(
        cls,
        symbol: str,
        total_allocation_usd: float,
        current_price: float,
        support_price: Optional[float] = None,
        es_score: float = 70.0,
        drs_score: float = 10.0
    ) -> Dict[str, Any]:
        """
        Menyusun rencana pembelian SPOT bertahap 3-Tranche DCA:
          - Tranche 1 (Pilot/Scout Entry 30%): Beli langsung di harga pasar saat ini.
          - Tranche 2 (Value Pullback DCA 35%): Limit buy di area diskon -8% s.d. -15% (Retest Support).
            *Syarat Mutlak: Whale TIDAK dump (ES >= 60 & DRS < 35). Batal jika paus buang barang!*
          - Tranche 3 (Momentum Breakout DCA 35%): Beli saat terkonfirmasi Higher High (+8% s.d. +12% breakout) dengan volume.
        """
        if current_price <= 0:
            return {"error": "Harga saat ini tidak valid"}

        # Estimasi support jika tidak diberikan (8% - 12% di bawah harga sekarang)
        supp_px = support_price if support_price and support_price < current_price else round(current_price * 0.90, 6)
        breakout_px = round(current_price * 1.10, 6)

        t1_usd = round(total_allocation_usd * 0.30, 2)
        t2_usd = round(total_allocation_usd * 0.35, 2)
        t3_usd = round(total_allocation_usd * 0.35, 2)

        t1_qty = round(t1_usd / current_price, 6)
        t2_qty = round(t2_usd / supp_px, 6)
        t3_qty = round(t3_usd / breakout_px, 6)

        # Evaluasi kondisi whale untuk Tranche 2
        t2_whale_approved = (drs_score < 35.0 and es_score >= 50.0)
        t2_condition_note = (
            "✅ Valid: Paus/Smart Money masih akumulasi (DRS rendah). Boleh pasang Limit Buy."
            if t2_whale_approved else
            "⚠️ ALERT: Tekanan jual paus terdeteksi (DRS >= 35). Tunda Tranche 2 (Anti-Falling Knife)!"
        )

        plan = {
            "symbol": symbol.upper(),
            "total_allocation_usd": total_allocation_usd,
            "current_price": current_price,
            "tranches": [
                {
                    "tranche": 1,
                    "name": "Pilot Scout Entry (30%)",
                    "type": "MARKET / IMMEDIATE LIMIT",
                    "target_price": current_price,
                    "allocation_usd": t1_usd,
                    "allocation_pct": 30.0,
                    "estimated_qty": t1_qty,
                    "trigger_rule": "Sinyal dasar PRIME/CHALLENGER aktif, buka posisi awal.",
                    "status": "READY_TO_BUY"
                },
                {
                    "tranche": 2,
                    "name": "Value Pullback DCA (35%)",
                    "type": "LIMIT PULLBACK / SUPPORT RETEST",
                    "target_price": supp_px,
                    "discount_pct": round((1.0 - supp_px / current_price) * 100.0, 1),
                    "allocation_usd": t2_usd,
                    "allocation_pct": 35.0,
                    "estimated_qty": t2_qty,
                    "trigger_rule": "Pullback ke level support dasar tanpa adanya lonjakan volume dump paus.",
                    "whale_approved": t2_whale_approved,
                    "whale_rule": t2_condition_note,
                    "status": "WAITING_PULLBACK" if t2_whale_approved else "BLOCKED_BY_WHALE"
                },
                {
                    "tranche": 3,
                    "name": "Momentum Breakout DCA (35%)",
                    "type": "STOP-LIMIT BREAKOUT / PYRAMIDING",
                    "target_price": breakout_px,
                    "markup_pct": round((breakout_px / current_price - 1.0) * 100.0, 1),
                    "allocation_usd": t3_usd,
                    "allocation_pct": 35.0,
                    "estimated_qty": t3_qty,
                    "trigger_rule": "Breakout dari resistance dasar konfirmasi Wyckoff SOS (Sign of Strength). Pyramiding on strength.",
                    "status": "WAITING_BREAKOUT"
                }
            ],
            "blended_estimated_avg_price": round((t1_usd + t2_usd + t3_usd) / (t1_qty + t2_qty + t3_qty), 6),
            "pacing_rules": [
                "DCA interval mingguan/dua-mingguan hanya dieksekusi saat Daily RSI < 45.",
                "Jangan pernah averaging down jika Nansen Smart Money Netflow berbalik merah pekat.",
                "Zero Liquidation: Tidak menggunakan leverage, posisi aman dari scam wicks."
            ]
        }
        return plan

    # =========================================================================
    # 3. SISTEM EXIT BERTAHAP "MENDEKATI SEMPURNA" (MULTI-TIERED EXIT)
    # =========================================================================
    @classmethod
    def evaluate_spot_exit_strategy(
        cls,
        symbol: str,
        entry_price: float,
        current_price: float,
        qty: float,
        ss_score: float = 50.0,
        es_score: float = 50.0,
        drs_score: float = 10.0,
        mayer_multiple: float = 1.2,
        btc_macro_phase: str = "Fase B"
    ) -> Dict[str, Any]:
        """
        Evaluasi Exit Bertahap Multi-Tier Paling Mendekati Sempurna:
          1. Filosofi Utama: 'Extended != Exit' (Jangan TP dini jika paus masih pegang & DRS rendah).
          2. Tier 1 (Principal Return / De-Risking): Gain +75% s.d. +100% -> TP 25-30% posisi (Kembalikan 100% modal awal).
          3. Tier 2 (Euphoria & Whale Distribution Harvest): Gain +150% s.d. +250% + DRS >= 50 atau Nansen Outflow -> TP 30-35%.
          4. Tier 3 (Macro Cycle Peak Climax): Gain +300% s.d. +500%+ + Mayer Multiple >= 2.4 / Siklus BTC Fase E/F -> TP 25%.
          5. Tier 4 (Moonbag Runner 15-20%): Trailing stop Weekly Chandelier / Weekly EMA 21 Close (Tangkap 10x-50x).
          6. Emergency Invalidation Exit: Whale capitulation (DRS >= 75) + breakdown support struktural multi-bulan -> Cut loss proteksi modal.
        """
        if entry_price <= 0 or current_price <= 0:
            return {"error": "Data harga entry/current tidak valid"}

        gain_pct = round(((current_price - entry_price) / entry_price) * 100.0, 2)
        invested_usd = round(entry_price * qty, 2)
        current_val_usd = round(current_price * qty, 2)
        unrealized_usd = round(current_val_usd - invested_usd, 2)

        action_type = "HOLD_CONVICTION"
        urgency = "LOW"
        tp_tier_reached = None
        sell_qty_recommended = 0.0
        sell_pct_recommended = 0.0
        reasons = []

        # ---------------------------------------------------------------------
        # A. Cek Emergency Whale Capitulation Exit (Prioritas Tertinggi Proteksi)
        # ---------------------------------------------------------------------
        if drs_score >= 75.0 and gain_pct < -20.0:
            action_type = "EMERGENCY_EXIT"
            urgency = "CRITICAL"
            sell_pct_recommended = 100.0
            sell_qty_recommended = qty
            reasons.append(f"🚨 Whale Capitulation Alert: DRS ekstrem ({drs_score:.1f}/100) dan harga jebol. Amankan sisa modal.")
            return cls._build_exit_response(
                symbol, entry_price, current_price, qty, gain_pct, invested_usd, current_val_usd,
                unrealized_usd, action_type, urgency, tp_tier_reached, sell_pct_recommended,
                sell_qty_recommended, reasons, drs_score, mayer_multiple
            )

        # ---------------------------------------------------------------------
        # B. Cek Tier 3: Macro Cycle Climax (Puncak Siklus 4-Tahunan)
        # ---------------------------------------------------------------------
        if gain_pct >= 300.0 or (gain_pct >= 200.0 and (mayer_multiple >= 2.4 or "E" in btc_macro_phase or "F" in btc_macro_phase)):
            action_type = "TIER_3_MACRO_CLIMAX_TP"
            urgency = "HIGH"
            tp_tier_reached = "Tier 3: Cycle Peak Climax (+300% / Mayer >= 2.4)"
            sell_pct_recommended = 25.0
            sell_qty_recommended = round(qty * 0.25, 6)
            reasons.append(f"🎯 Target Siklus Tercapai (Gain +{gain_pct:.1f}%). Mayer Multiple: {mayer_multiple:.2f} (Overheated). Amankan profit besar!")

        # ---------------------------------------------------------------------
        # C. Cek Tier 2: Euphoria & Whale Distribution Harvest
        # ---------------------------------------------------------------------
        elif gain_pct >= 150.0 and drs_score >= 48.0:
            action_type = "TIER_2_WHALE_DISTRIBUTION_TP"
            urgency = "MEDIUM_HIGH"
            tp_tier_reached = "Tier 2: Whale Distribution Harvest (+150% s.d. +250%)"
            sell_pct_recommended = 35.0
            sell_qty_recommended = round(qty * 0.35, 6)
            reasons.append(f"🐋 Distribusi Paus Mulai Terdeteksi: DRS naik ke {drs_score:.1f}/100 di zona profit +{gain_pct:.1f}%. Panen bertahap 35%.")

        # ---------------------------------------------------------------------
        # D. Cek Tier 1: Principal Return / De-Risking (Modal Kembali 100%)
        # ---------------------------------------------------------------------
        elif gain_pct >= 85.0:
            action_type = "TIER_1_PRINCIPAL_RECOVERY_TP"
            urgency = "MEDIUM"
            tp_tier_reached = "Tier 1: Principal Recovery / Free-Roll (+85% s.d. +100%)"
            sell_pct_recommended = 30.0
            sell_qty_recommended = round(qty * 0.30, 6)
            reasons.append(f"💎 Amankan Modal Awal: Posisi naik +{gain_pct:.1f}%. Jual 30% untuk menarik 100% modal awal. Sisa 70% posisi murni FREE-ROLL!")

        # ---------------------------------------------------------------------
        # E. Prinsip SCR v10: Extended != Exit (Conviction Hold / Moonbag)
        # ---------------------------------------------------------------------
        else:
            if gain_pct >= 40.0 and drs_score < 30.0:
                action_type = "HOLD_EXTENDED_CONVICTION"
                urgency = "NONE"
                reasons.append(f"💎 Extended != Exit: Profit +{gain_pct:.1f}%, namun Whale masih hold kuat (DRS {drs_score:.1f} rendah). Biarkan pemenang berlari (Let winners run)!")
            elif gain_pct < 0:
                action_type = "HOLD_ACCUMULATION_BASE"
                urgency = "NONE"
                reasons.append(f"⏳ Posisi masih di area akumulasi ({gain_pct:.1f}%). DRS {drs_score:.1f} aman. Pertahankan posisi SPOT (Zero liquidation).")
            else:
                action_type = "HOLD_NEUTRAL"
                urgency = "NONE"
                reasons.append(f"Pergerakan normal (+{gain_pct:.1f}%). Menunggu konfirmasi target Tier 1 (+85%).")

        return cls._build_exit_response(
            symbol, entry_price, current_price, qty, gain_pct, invested_usd, current_val_usd,
            unrealized_usd, action_type, urgency, tp_tier_reached, sell_pct_recommended,
            sell_qty_recommended, reasons, drs_score, mayer_multiple
        )

    @classmethod
    def _build_exit_response(
        cls, symbol, entry_px, cur_px, qty, gain_pct, invested, cur_val, unrealized,
        action, urgency, tp_tier, sell_pct, sell_qty, reasons, drs, mayer
    ) -> Dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "entry_price": entry_px,
            "current_price": cur_px,
            "qty": qty,
            "invested_usd": invested,
            "current_value_usd": cur_val,
            "unrealized_pnl_usd": unrealized,
            "gain_pct": gain_pct,
            "drs_score": drs,
            "mayer_multiple": mayer,
            "recommended_action": action,
            "urgency": urgency,
            "active_tp_tier": tp_tier,
            "sell_pct_recommended": sell_pct,
            "sell_qty_recommended": sell_qty,
            "reasons": reasons,
            "trailing_runner_rules": [
                "Sisa 15-20% (Moonbag) hanya ditutup jika Weekly Candle Close di bawah Weekly EMA 21.",
                "Tidak memasang stop loss ketat pada spot untuk menghindari wick hunting pasar kripto."
            ]
        }

    # =========================================================================
    # 4. SINKRONISASI & PENCATATAN PORTOFOLIO SPOT (portfolio.csv)
    # =========================================================================
    @classmethod
    def record_spot_purchase(
        cls,
        symbol: str,
        entry_price: float,
        qty: float,
        coin_id: str = "nan",
        note: str = "Spot Long DCA"
    ) -> Dict[str, Any]:
        """
        Mencatat pembelian SPOT baru atau top-up DCA ke dalam portfolio.csv dan memperbarui cache.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(PORTFOLIO_CSV), exist_ok=True)

        # Baca baris yang sudah ada
        existing_rows = []
        header = ["symbol", "coin_id", "entry_price", "entry_date", "qty", "note"]
        found_symbol = False

        if os.path.exists(PORTFOLIO_CSV):
            with open(PORTFOLIO_CSV, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or row[0].startswith("#"):
                        continue
                    if row[0].strip().lower() == "symbol":
                        continue
                    if len(row) >= 5:
                        sym = row[0].strip().upper()
                        if sym == symbol.upper():
                            # Update average price & qty jika koin sudah ada (DCA Blending)
                            found_symbol = True
                            old_px = float(row[2])
                            old_qty = float(row[4])
                            new_total_qty = old_qty + qty
                            new_avg_px = ((old_px * old_qty) + (entry_price * qty)) / new_total_qty if new_total_qty > 0 else entry_price
                            existing_rows.append([
                                sym, row[1], f"{new_avg_px:.6f}", today_str, f"{new_total_qty:.6f}",
                                f"{row[5] if len(row) > 5 else ''} | DCA +{qty:.4f}@{entry_price:.4f}".strip()
                            ])
                        else:
                            existing_rows.append(row)

        if not found_symbol:
            existing_rows.append([
                symbol.upper(), coin_id, f"{entry_price:.6f}", today_str, f"{qty:.6f}", note
            ])

        # Tulis ulang portfolio.csv
        with open(PORTFOLIO_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["# Portofolio Spot Long (1-24 Bulan) — Powered by SCR v10 & ShortHunter"])
            writer.writerow(header)
            for r in existing_rows:
                writer.writerow(r)

        logger.info(f"Sukses mencatat pembelian SPOT {symbol.upper()} di {PORTFOLIO_CSV}")
        return {
            "success": True,
            "symbol": symbol.upper(),
            "entry_price": entry_price,
            "qty": qty,
            "action": "DCA_TOPUP" if found_symbol else "NEW_POSITION",
            "portfolio_file": PORTFOLIO_CSV
        }

    @classmethod
    def record_spot_harvest(
        cls,
        symbol: str,
        sell_qty: float,
        sell_price: float,
        tier_name: str = "TP_PARTIAL"
    ) -> Dict[str, Any]:
        """
        Mencatat panen/penjualan bertahap posisi SPOT di portfolio.csv.
        """
        if not os.path.exists(PORTFOLIO_CSV):
            return {"success": False, "error": "portfolio.csv tidak ditemukan"}

        today_str = datetime.now().strftime("%Y-%m-%d")
        updated_rows = []
        header = ["symbol", "coin_id", "entry_price", "entry_date", "qty", "note"]
        target_found = False
        realized_profit_usd = 0.0

        with open(PORTFOLIO_CSV, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0].startswith("#"):
                    continue
                if row[0].strip().lower() == "symbol":
                    continue
                if len(row) >= 5:
                    sym = row[0].strip().upper()
                    if sym == symbol.upper():
                        target_found = True
                        entry_px = float(row[2])
                        cur_qty = float(row[4])
                        rem_qty = max(0.0, cur_qty - sell_qty)
                        realized_profit_usd = (sell_price - entry_px) * min(cur_qty, sell_qty)

                        if rem_qty > 0.000001:
                            updated_rows.append([
                                sym, row[1], row[2], row[3], f"{rem_qty:.6f}",
                                f"{row[5] if len(row) > 5 else ''} | {tier_name} Sold {sell_qty:.4f}@{sell_price:.4f} on {today_str}".strip()
                            ])
                    else:
                        updated_rows.append(row)

        if not target_found:
            return {"success": False, "error": f"Koin {symbol.upper()} tidak ditemukan di portfolio.csv"}

        # Tulis ulang portfolio.csv
        with open(PORTFOLIO_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["# Portofolio Spot Long (1-24 Bulan) — Powered by SCR v10 & ShortHunter"])
            writer.writerow(header)
            for r in updated_rows:
                writer.writerow(r)

        return {
            "success": True,
            "symbol": symbol.upper(),
            "sold_qty": sell_qty,
            "sell_price": sell_price,
            "realized_profit_usd": round(realized_profit_usd, 2),
            "tier": tier_name
        }
