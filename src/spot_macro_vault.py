"""
Spot Macro Vault (1-24 Bulan) — Powered by SCR v10 (Supply Concentration Reversal)
==================================================================================
Modul khusus pengelolaan portofolio SPOT dan strategi BELI & TAHAN (Buy & Hold) 
berjangka waktu 1-24 bulan tanpa leverage, bebas biaya funding perpetual, dan bebas 
risiko likuidasi (Zero Liquidation Risk).

Arsitektur & Pilar SCR v10:
1. Siklus Makro Bitcoin 4 Tahunan (Halving Cycle, Fase A-F, Mayer Multiple, 200W MA).
2. Evaluasi Portofolio Spot:
   - TAHAN (KEEP / CONVICTION HOLD): "Extended != Exit". Selama DRS < 55 dan struktur akumulasi 
     terjaga, biarkan keuntungan bertumbuh (*let winners run*).
   - PANEN SEBAGIAN (HARVEST-REDUCE): Jika keuntungan tercapai (+50%/+100%) dan DRS naik (>= 55).
   - KELUAR (EXIT SCR): DRS >= 70 (Distribusi Paus besar-besaran) atau kerusakan struktur makro.
3. Radar Akumulasi Baru (PRIME / CHALLENGER C1):
   - Setup teknikal dasar (SS >= 75) + Bukti Paus On-Chain (ES >= 70) + Crash Gate Lolos.
"""

import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
import numpy as np

# Path default ke project Claude_Crypto SCR v10
try:
    from src.paths import SCR_DIR as CLAUDE_CRYPTO_DIR  # Tahap 4A: tanpa jalur /Users
except ImportError:
    from paths import SCR_DIR as CLAUDE_CRYPTO_DIR
SCR_DB_PATH = os.path.join(CLAUDE_CRYPTO_DIR, "data", "scr.db")
PORTFOLIO_CSV_PATH = os.path.join(CLAUDE_CRYPTO_DIR, "inputs", "portfolio.csv")
LOCAL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOCAL_VAULT_CACHE = os.path.join(LOCAL_DATA_DIR, "spot_vault_cache.json")


class SpotMacroVault:
    """
    Manager & Analyzer Portofolio Spot Jangka Panjang (1-24 Bulan)
    berbasis intelijen SCR v10.
    """

    @classmethod
    def get_db_path(cls) -> Optional[str]:
        # 2026-10-22 (ROADMAP 2.2): sumber = file kontrak SCR (scr_signals.json), bukan scr.db.
        # None bila kontrak tidak ada / basi (> MAX_AGE_DAYS) -> fitur Spot tampil "offline", bukan angka tebakan.
        from src import scr_contract
        return scr_contract.CONTRACT_PATH if scr_contract.is_fresh() else None

    @classmethod
    def get_portfolio_csv_path(cls) -> Optional[str]:
        if os.path.exists(PORTFOLIO_CSV_PATH):
            return PORTFOLIO_CSV_PATH
        return None

    @classmethod
    def load_portfolio_holdings(cls) -> List[Dict[str, Any]]:
        """
        Membaca daftar posisi koin spot dari inputs/portfolio.csv
        """
        csv_path = cls.get_portfolio_csv_path()
        if not csv_path:
            return []

        holdings = []
        try:
            df = pd.read_csv(csv_path, comment="#")
            for _, r in df.iterrows():
                sym = str(r.get("symbol") or "").strip().upper()
                cid = str(r.get("coin_id") or "").strip()
                if not sym and not cid:
                    continue
                try:
                    entry_px = float(r.get("entry_price")) if pd.notna(r.get("entry_price")) else 0.0
                except (ValueError, TypeError):
                    entry_px = 0.0
                try:
                    qty = float(r.get("qty")) if pd.notna(r.get("qty")) else 0.0
                except (ValueError, TypeError):
                    qty = 0.0
                
                entry_date = str(r.get("entry_date") or "").strip()
                note = str(r.get("note") or "").strip()

                holdings.append({
                    "symbol": sym,
                    "coin_id": cid,
                    "entry_price": entry_px,
                    "entry_date": entry_date,
                    "qty": qty,
                    "note": note
                })
        except Exception as e:
            print(f"[SpotMacroVault] Gagal membaca portfolio.csv: {e}")

        return holdings

    @classmethod
    def get_macro_cycle_status(cls) -> Dict[str, Any]:
        """
        Mengambil status siklus 4 tahunan Bitcoin & Sentimen Pasar dari scr.db.
        """
        db_path = cls.get_db_path()
        res = {
            "available": False,
            "btc_price": 0.0,
            "phase": "F",
            "phase_name": "Pemulihan Awal",
            "phase_text": "Harga sudah naik dari dasar dan berada di atas MA200.",
            "days_since_halving": 910,
            "cycle_pct": 62.3,
            "cycle_year": 3,
            "drawdown_from_peak": -34.6,
            "gain_from_bottom": +39.3,
            "mayer_multiple": 1.13,
            "sma200": 72256.0,
            "fng": 55.0,
            "fng_class": "Greed",
            "btc_dom": 59.3,
            "market_regime": "NETRAL"
        }

        if not db_path:
            return res

        try:
            from src import scr_contract
            conn = scr_contract.connect()
            cursor = conn.cursor()

            # 1. Ambil Fear & Greed & Market Info terakhir
            cursor.execute("SELECT fng, fng_class, btc_dom FROM market ORDER BY date DESC LIMIT 1;")
            m_row = cursor.fetchone()
            if m_row:
                res["fng"] = float(m_row[0]) if m_row[0] is not None else 55.0
                res["fng_class"] = str(m_row[1] or "Neutral")
                res["btc_dom"] = float(m_row[2]) if m_row[2] is not None else 59.3

            # 2. Cek metadata dari runs
            cursor.execute("SELECT meta FROM runs ORDER BY date DESC LIMIT 1;")
            r_row = cursor.fetchone()
            if r_row and r_row[0]:
                try:
                    # JSON metadata parsing
                    meta_str = r_row[0].replace("'", '"')
                    meta_json = json.loads(meta_str)
                    m_data = meta_json.get("market", {})
                    res["market_regime"] = m_data.get("label", "NETRAL")
                except Exception:
                    pass

            conn.close()

            # 3. Siklus BTC dari kontrak SCR (dulu: impor langsung scr.cycle dari Claude_Crypto)
            try:
                from src import scr_contract
                c = scr_contract.cycle() or {}
                if c.get("ok"):
                    res["available"] = True
                    res["btc_price"] = float(c.get("price", 0.0))
                    res["phase"] = str(c.get("phase", "F"))
                    res["phase_name"] = str(c.get("phase_name", "Pemulihan awal"))
                    res["phase_text"] = str(c.get("phase_text", ""))
                    res["days_since_halving"] = int(c.get("hari_halving", 910))
                    res["cycle_pct"] = round(float(c.get("cycle_pct", 0.62)) * 100, 1)
                    res["cycle_year"] = int(c.get("tahun_ke", 3))
                    res["drawdown_from_peak"] = round(float(c.get("turun_dari_puncak", -0.34)) * 100, 1)
                    res["gain_from_bottom"] = round(float(c.get("naik_dari_dasar", 0.39)) * 100, 1)
                    res["mayer_multiple"] = round(float(c.get("mayer", 1.13)), 2)
                    res["sma200"] = round(float(c.get("sma200", 72256.0)), 1)
            except Exception as e:
                # 2026-10-22: dulu available=True dengan angka default -> kini jujur "tidak tersedia"
                print(f"[SpotMacroVault] Siklus BTC dari kontrak SCR tidak tersedia: {e}")
        except Exception as e:
            print(f"[SpotMacroVault] Gagal mengambil siklus makro: {e}")

        return res

    @classmethod
    def get_spot_portfolio_audit(cls) -> Dict[str, Any]:
        """
        Melakukan audit lengkap terhadap posisi spot di portfolio.csv
        berdasarkan skor & sinyal harian terbaru dari scr.db.
        """
        holdings = cls.load_portfolio_holdings()
        db_path = cls.get_db_path()

        if not holdings:
            return {
                "holdings": [],
                "total_invested": 0.0,
                "total_current_value": 0.0,
                "total_unrealized_pnl": 0.0,
                "total_unrealized_pct": 0.0,
                "asof_date": "N/A",
                "counts": {"keep": 0, "harvest": 0, "exit": 0, "watch": 0}
            }

        scores_map = {}
        asof_date = "N/A"

        if db_path:
            try:
                from src import scr_contract
                conn = scr_contract.connect()
                cursor = conn.cursor()

                # Tanggal terbaru di database
                cursor.execute("SELECT MAX(date) FROM scores;")
                d_row = cursor.fetchone()
                if d_row and d_row[0]:
                    asof_date = d_row[0]

                # Ambil data koin & skor
                query = f"""
                SELECT c.symbol, c.coin_id, c.name, c.price, s.action, s.ss, s.es, s.drs, s.ph
                FROM coins c
                LEFT JOIN scores s ON c.coin_id = s.coin_id AND s.date = '{asof_date}'
                """
                cursor.execute(query)
                for r in cursor.fetchall():
                    sym = (r[0] or "").upper().strip()
                    cid = (r[1] or "").strip()
                    name = r[2] or sym
                    px = float(r[3]) if r[3] is not None else 0.0
                    act = str(r[4] or "WATCH").strip().upper()
                    ss = float(r[5]) if r[5] is not None else None
                    es = float(r[6]) if r[6] is not None else None
                    drs = float(r[7]) if r[7] is not None else None
                    ph = float(r[8]) if r[8] is not None else None

                    item = {
                        "symbol": sym,
                        "coin_id": cid,
                        "name": name,
                        "current_price": px,
                        "action": act,
                        "ss": ss,
                        "es": es,
                        "drs": drs,
                        "phase_num": ph
                    }
                    if sym:
                        scores_map[sym] = item
                    if cid:
                        scores_map[cid] = item

                conn.close()
            except Exception as e:
                print(f"[SpotMacroVault] Error query scores: {e}")

        # Gabungkan data portofolio dengan skor SCR
        audited = []
        total_invested = 0.0
        total_val = 0.0
        counts = {"keep": 0, "harvest": 0, "exit": 0, "watch": 0}

        for h in holdings:
            sym = h["symbol"]
            cid = h["coin_id"]
            entry_px = h["entry_price"]
            qty = h["qty"]

            scr_data = scores_map.get(sym) or scores_map.get(cid) or {}
            cur_px = scr_data.get("current_price", 0.0) or entry_px
            name = scr_data.get("name", sym)
            action = scr_data.get("action", "KEEP")
            ss = scr_data.get("ss")
            es = scr_data.get("es")
            drs = scr_data.get("drs")
            ph = scr_data.get("phase_num")

            # Kalkulasi PnL
            invested = entry_px * qty
            cur_val = cur_px * qty
            unrealized_pnl = cur_val - invested
            unrealized_pct = ((cur_px - entry_px) / entry_px * 100.0) if entry_px > 0 else 0.0

            total_invested += invested
            total_val += cur_val

            # Terjemahkan Fase Wyckoff
            phase_label = "P2 Konsolidasi"
            if ph is not None:
                if ph < 30:
                    phase_label = "P1 Kapitulasi"
                elif ph < 50:
                    phase_label = "P2 Base / Re-accum"
                elif ph < 65:
                    phase_label = "P3/P4 Akumulasi"
                elif ph < 80:
                    phase_label = "P5 Mark-Up"
                else:
                    phase_label = "P6/P7 Distribusi"

            # Logika Aksi SCR yang Disempurnakan untuk 1-24 Bulan:
            # 1. KEEP: DRS < 55, fundamental tenang.
            # 2. HARVEST-REDUCE: DRS 55-69 atau profit > +50% dengan tanda lelah.
            # 3. EXIT SCR: DRS >= 70 atau kerusakan struktur parah.
            if "HARVEST" in action:
                status_badge = "⚠️ PANEN SEBAGIAN (HARVEST)"
                status_desc = "Amankan modal (TP bertahap 25-50%). DRS atau RSI menunjukkan kejenuhan."
                counts["harvest"] += 1
            elif "EXIT" in action:
                status_badge = "🚨 KELUAR (EXIT SCR)"
                status_desc = "Tutup posisi. Terjadi distribusi paus besar-besaran (DRS tinggi)."
                counts["exit"] += 1
            elif action == "KEEP":
                status_badge = "💎 TAHAN (CONVICTION HOLD)"
                status_desc = "Extended != Exit. Biarkan pemenang bertumbuh. Tekanan distribusi rendah."
                counts["keep"] += 1
            else:
                status_badge = "👁️ PANTAU (WATCH)"
                status_desc = "Pergerakan netral. Pertahankan posisi sambil mengamati konfirmasi dasar."
                counts["watch"] += 1

            audited.append({
                "symbol": sym,
                "coin_id": cid,
                "name": name,
                "entry_price": entry_px,
                "current_price": cur_px,
                "qty": qty,
                "invested_usd": invested,
                "current_usd": cur_val,
                "unrealized_pnl_usd": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pct,
                "ss": ss,
                "es": es,
                "drs": drs,
                "phase_num": ph,
                "phase_label": phase_label,
                "action": action,
                "status_badge": status_badge,
                "status_desc": status_desc,
                "note": h.get("note", "")
            })

        net_pnl = total_val - total_invested
        net_pct = (net_pnl / total_invested * 100.0) if total_invested > 0 else 0.0

        # Simpan ke cache lokal
        summary = {
            "holdings": audited,
            "total_invested": round(total_invested, 2),
            "total_current_value": round(total_val, 2),
            "total_unrealized_pnl": round(net_pnl, 2),
            "total_unrealized_pct": round(net_pct, 2),
            "asof_date": asof_date,
            "counts": counts
        }

        try:
            os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
            with open(LOCAL_VAULT_CACHE, "w") as f:
                json.dump(summary, f, indent=2)
        except Exception:
            pass

        return summary

    @classmethod
    def get_top_accumulation_candidates(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Mengambil kandidat koin akumulasi SPOT terbaik dari scr.db (Sinyal E1/C1, PRIME, CHALLENGER).
        Koin yang sudah ada di portofolio dikecualikan atau ditandai sebagai 'TAMBAH/DCA'.
        """
        db_path = cls.get_db_path()
        if not db_path:
            return []

        candidates = []
        try:
            from src import scr_contract
            conn = scr_contract.connect()
            cursor = conn.cursor()

            # Tanggal terbaru di scores
            cursor.execute("SELECT MAX(date) FROM scores;")
            d_row = cursor.fetchone()
            asof_date = d_row[0] if d_row and d_row[0] else ""

            # Portofolio saat ini untuk filter
            existing = {h["symbol"] for h in cls.load_portfolio_holdings()}

            # Cari koin dengan aksi BELI, BELI KECIL, atau CHALLENGER dengan SS tinggi
            query = f"""
            SELECT c.symbol, c.name, c.price, s.action, s.ss, s.es, s.drs, s.ph, c.ath_chg
            FROM scores s
            JOIN coins c ON s.coin_id = c.coin_id
            WHERE s.date = '{asof_date}' 
              AND s.action IN ('BELI', 'BELI KECIL', 'CHALLENGER', 'RESEARCH')
              AND s.ss >= 65.0
            ORDER BY s.ss DESC, (CASE WHEN s.es IS NULL THEN 0 ELSE s.es END) DESC
            LIMIT {limit * 2};
            """
            cursor.execute(query)
            for r in cursor.fetchall():
                sym = (r[0] or "").upper().strip()
                name = r[1] or sym
                px = float(r[2]) if r[2] is not None else 0.0
                act = str(r[3] or "").strip().upper()
                ss = float(r[4]) if r[4] is not None else 0.0
                es = float(r[5]) if r[5] is not None else None
                drs = float(r[6]) if r[6] is not None else 0.0
                ph = float(r[7]) if r[7] is not None else 50.0
                ath_chg = float(r[8]) if r[8] is not None else 0.0

                is_in_portfolio = sym in existing

                # Terjemahkan Tier Beli
                if act == "BELI" or (ss >= 75 and es and es >= 70):
                    tier = "💎 PRIME (Akumulasi Penuh)"
                    detail = "Setup teknikal sempurna (SS>=75) + Paus On-Chain konfirmasi (ES>=70)."
                elif act == "BELI KECIL" or act == "CHALLENGER":
                    tier = "🌱 PROBE / CHALLENGER (Akumulasi Awal)"
                    detail = "Setup teknikal matang, menunggu konfirmasi lanjutan whale on-chain."
                else:
                    tier = "🔍 RESEARCH (Pantau Ketat)"
                    detail = "Kandidat potensial dengan base sehat, tunggu momentum trigger."

                candidates.append({
                    "symbol": sym,
                    "name": name,
                    "price": px,
                    "action": act,
                    "tier": tier,
                    "ss": round(ss, 1),
                    "es": round(es, 1) if es is not None else "N/A",
                    "drs": round(drs, 1),
                    "ath_chg_pct": round(ath_chg, 1),
                    "in_portfolio": is_in_portfolio,
                    "detail": detail
                })

                if len(candidates) >= limit:
                    break

            conn.close()
        except Exception as e:
            print(f"[SpotMacroVault] Error get accumulation candidates: {e}")

        return candidates
