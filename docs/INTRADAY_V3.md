# Intraday v3 — bot Mac intraday saja (2026-10-23)

Permintaan user (21:01): hapus semua logika & menu swing multi-hari, menu intraday saja, saklar trading otomatis,
evaluasi logika intraday & langsung terapkan perbaikan (walau belum teruji). Keputusan user: menu intraday saja,
saklar 3 posisi OFF/PAPER/LIVE, Gemini tetap dipakai, kode swing dihapus total.

## Yang dihapus
`src/screener.py`, `src/long_screener.py`, `src/backtester.py`, `src/auto_optimizer.py`, `src/spot_macro_vault.py`,
`src/spot_strategy_engine.py`, `src/agent_chat.py`, `main.py`, profil SWING, `_manage_swing`, kuadran swing di auto_hunter,
backtest berkala + kalibrasi otomatis, top-up/scale-in otomatis, backtest v1 di `daytrade_engine.py`.
Menu lama (Spot Vault, Gaji, AI Hub/War Room, Lab) diganti menu 7 pilihan. Arsip: `_arsip/sebelum_intraday_only_20261023.tgz`.

## Evaluasi logika intraday v1 (temuan)
| # | Masalah v1 | Bukti |
|---|---|---|
| 1 | Sinyal memakai candle 15m yang belum tutup (`c0`) → beli/jual di jarum sesaat | audit Hermes 21 Okt |
| 2 | LONG memberi +25 poin untuk "dekat low 12 jam" → menangkap pisau jatuh; SHORT cermin "dekat pucuk" | audit; 53% trade LONG kena SL dengan MFE 0% |
| 3 | LONG tanpa filter volume | kode |
| 4 | Ekor 35% terlalu longgar | audit |
| 5 | SL > 4,5% dipaksa digeser ke 4,5% → SL masuk ke DALAM struktur (kena noise), bukan ditolak | kode |
| 6 | Tidak ada filter tren besar / rezim BTC — pembalikan melawan tren | riset: backtest LONG 22 Okt PF 0,69 (kalah dari 100% acak); H12 (sweep killzone) semua varian kalah dari acak |
| 7 | Bobot sesi besar (+25) padahal killzone bukan edge | H12 |
| 8 | Universe $300 rb volume/24j → slippage | kode |
| 9 | Ukuran posisi dari slot margin, bukan risiko | kerangka risiko berlapis (0,5–1%/trade) |
| 10 | Top-up otomatis menambah posisi berjalan | kode |

## Perbaikan v3 (diterapkan, BELUM teruji backtest)
Konsep: **pullback searah tren + penolakan + konfirmasi candle tutup** (simetris SHORT/LONG) — `src/intraday_core.py`.
Gerbang wajib untuk "SIAP": tren (EMA200 & EMA50 15m), penolakan (ekor ≥55% atau sweep 2 jam), BOS di candle tutup,
volume tidak rendah, tanpa veto (BTC ±1,5%/4j melawan, SL struktural > 4,5%, setup basi > 0,5R, funding ekstrem).
SL struktural + buffer max(0,35%, 0,25 ATR), min 2,5%. TP1 1R, TP2 2R (maks 9%). Universe ≥ $2 jt/24j;
LONG mencari koin kuat (gainer), SHORT koin lemah (loser). Ukuran posisi = ekuitas × 0,75–1% / SL%.

## Saklar & cara uji
Menu [4]: OFF (default) / PAPER / LIVE. PAPER mencatat trade kertas (`data/paper_trades.json`) dengan biaya & slippage,
dipantau tiap siklus daemon dari harga mid (jarum singkat bisa terlewat). Hasil di menu [5].
Saran sebelum LIVE: ≥30 trade PAPER, rata-rata R > 0 setelah biaya, hari hijau ≥ 50%, dan bandingkan dengan acak.
Catatan jujur: riset research_v2 sejauh ini menunjukkan setup intraday 15m sulit menutup biaya; v3 mengurangi
kesalahan yang jelas, tetapi edge-nya belum terbukti.
