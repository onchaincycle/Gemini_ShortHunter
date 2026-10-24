# ShortHunter — Bot Trading Otonom Hyperliquid

Bot trading kripto otonom di Hyperliquid (perpetual + spot). Empat stream: **Intraday Short, Intraday Long, Swing Short, Swing Long**, plus **Macro Spot Vault** yang membaca hasil riset SCR v10 / BottomScreenr. Semua Stop Loss dipasang langsung di bursa (on-chain), jadi posisi tetap terlindungi walau Mac mati.

> Dokumen ini diperbarui 2026-10-22. Sebelum mengubah kode live, baca **`ROADMAP_INTEGRASI.md`** dan **`CLAUDE.md`**.
> ShortHunter adalah **satu-satunya** sistem yang boleh mengirim order ke akun Hyperliquid ini (ImpulseHunter dibatalkan).
>
> **Serah terima 2026-10-24:** operasi bot LIVE di VPS, aturan wajib & insiden → **`HANDOFF_INTRADAY.md`**; riset v2, bank data VPS, sinkron, kotak tugas & SSD dirangkum di **`HANDOVER.md`** (keadaan, jadwal, tugas tertunda, aturan keamanan). Kotak tugas: `ops/README.md`. Kamus data: `KAMUS_DATA.md`.

## Menjalankan

```bash
./start.sh            # cek integritas kode (system_doctor) lalu buka menu
./start.sh --doctor   # diagnosis lengkap saja
```

Menu utama yang paling sering dipakai:
| Menu | Fungsi |
|---|---|
| `1` | Auto-Hunt sekali jalan (scan 4 kuadran → AI gatekeeper → eksekusi) |
| `4` | Pusat Komando daemon 24/7: `5` nyalakan/restart, `6` matikan (+ ditahan), `1` live monitor |
| `5` | Jurnal forward test & scorecard per stream |
| `M` | Macro Spot Vault (data SCR v10) |
| `11` | AI War Room (chat dengan agen) |

**Daemon.** Berjalan di latar belakang (`caffeinate` + `python -m src.position_manager --daemon-loop`), PID di `data/autopilot.pid`, log di `logs/autopilot.log` dan `logs/autonomous_ai.log`.
Menu otomatis menyalakan daemon bila mati, **kecuali** sedang ditahan: mematikan lewat menu `4 → 6` membuat `data/daemon_hold.flag`, menyalakan lewat `4 → 5` menghapusnya. Untuk pekerjaan yang butuh daemon mati (mis. backfill jurnal), matikan lewat `4 → 6`.

Cek proses: `ps aux | grep -v grep | grep daemon-loop` → normal 2 baris (caffeinate + python).

## Arsitektur ringkas

| Bagian | File utama |
|---|---|
| Koordinator autopilot, thermostat risiko, AI gatekeeper | `src/autonomous_engine.py` |
| Siklus daemon, lifecycle posisi, SL/TP on-chain, trailing | `src/position_manager.py`, `src/position_tags.py` |
| Eksekusi & data akun Hyperliquid | `src/trader.py`, `src/hyperliquid_client.py` |
| Scan 4 kuadran & batas slot per arah | `src/auto_hunter.py` |
| Screener swing short / swing long | `src/screener.py`, `src/long_screener.py` |
| Engine intraday 15 menit | `src/daytrade_engine.py` (short), `src/daytrade_long_engine.py` (long) |
| Engine evaluasi logika intraday & veto siklus | `src/intraday_core.py` (Market Cycle VETO, orderflow D1) |
| Engine siklus pasar mandiri & Wyckoff BMS | `src/cycle_engine.py` (Macro Halving BTC, Base Maturity P1-P7) |
| Buku bayangan forward paper testing | `src/shadow_book.py` (Early BE +0.50R lock, paper journal) |
| Gate propensity (heuristik bobot manual, **bukan ML**) | `src/propensity_gate.py` |
| Jembatan SCR v10 (sektor, BMS/DRS/fase) | `src/claude_crypto_engine.py`, `src/spot_macro_vault.py`, `src/spot_strategy_engine.py` |
| Smart money Nansen | `src/nansen_client.py` |
| Jurnal trade & rekonsiliasi fill bursa | `src/forward_test.py`, `src/trade_db.py` |
| AI (Gemini), War Room, Guardian, sync GitHub | `src/ai_engine.py`, `src/agent_chat.py`, `src/guardian_agent.py`, `src/github_sync.py` |

**Parameter risiko:** satu sumber di `src/risk_config.py` (override: `config/risk_overrides.json`); SL/TP per stream di `src/profiles.py`.

**Sizing yang berjalan:** jumlah slot dari ekuitas (5–10 posisi, ±65% swing / 35% intraday); cadangan margin dinamis 15% (normal) / 25% (2x rugi beruntun) / 30% + jeda entry 6 jam (3x rugi beruntun). Batas per arah swing tidak ikut naik saat slot dipinjam.

## Data

| File | Isi |
|---|---|
| `data/shorthunter_trade.db` | SQLite: jurnal trade (`trade_journal`), sinyal screener, posisi aktif, regime |
| `data/shorthunter.duckdb` | Candlestick & snapshot pasar Hyperliquid |
| `../Claude_Crypto/SCR_v10/data/scr.db` | Hasil SCR v10 (hanya dibaca) |
| `data/autonomous_state.json` | State thermostat & autopilot |

**Jurnal = kebenaran dari bursa.** Saat posisi tertutup, `forward_test.py` mencocokkan dengan fill Hyperliquid: `realized_pnl` = PnL bersih fee, `r_multiple` = PnL / risiko awal USD. Backfill manual (daemon harus mati): `scripts/backfill_journal.py` (tanpa argumen = uji coba, `--apply` = tulis + backup DB otomatis).

## Status kinerja

Lihat menu `5` atau jurnal. Per 2026-10-22 (35 trade, 16–22 Okt, bersih fee): total **-$4,61**; Swing Short +$5,70, Intraday Short +$0,16, Swing Long -$3,19, Intraday Long -$7,28. Sampel kecil — petunjuk, belum bukti.

## Riset lama (CLI)

`main.py` masih menyimpan alat riset awal: `--sync`, `--screen`, `--backtest`, `--stats`. Tidak dipakai autopilot.
