# Tahap 3.1 — Apakah skor screener ShortHunter memprediksi hasil? (2026-10-22)

**Jawaban singkat: BELUM terbukti. Untuk DAYTRADE tidak ada tanda prediktif sama sekali; untuk SWING ada petunjuk lemah dari skor, sedangkan P(Win)/p_grade tidak prediktif (cenderung terbalik).** Data baru 4–5 hari, satu rezim pasar (naik) — ini *petunjuk*, bukan bukti.

## Cara uji
- Data: 6.383 `screener_signals` (16–21 Okt; DAYTRADE 2.172 sinyal/109 scan/93 koin, SWING 4.211/81 scan/70 koin). Semua sinyal = sisi SHORT.
- Harga: snapshot mark price Hyperliquid tiap ±15 menit (`market_snapshots` + `ih_hl_market_snapshots`). Server cloud tidak bisa mengakses API Hyperliquid, jadi tidak pakai candle OHLC.
- Label triple-barrier, hasil dalam R, **bersih biaya** (fee 0,045%/sisi + slippage 0,05%/sisi + funding per jam):
  - DAYTRADE: SL = `sl_pct` sinyal (2,5–4,5%), TP 1R (dan varian 2R), batas waktu 6 jam.
  - SWING: SL 10%, TP 18%, batas 24 jam (varian 72 jam hanya 80 sinyal → tidak dipakai).
  - Seri SL/TP di bar yang sama dihitung SL. Sinyal hanya dilabeli bila horizon penuh ada di data (versi awal tanpa aturan ini bias ke SL — sudah dikoreksi).
- Uji: (1) IC = korelasi peringkat skor vs hasil **di dalam scan yang sama** (netral terhadap arah pasar), p-value dari permutasi skor 2.000×; (2) hasil per verdict & kuintil skor; (3) placebo: pilih koin acak sejumlah "SIAP SHORT" dari scan yang sama, 200×.
- Validasi label: pada 10 koin yang punya candle 15m, label snapshot = label candle di 86% kasus; selisihnya kira-kira simetris (wick terlewat ke dua arah).

## Hasil
| | DAYTRADE (TP1 1R / 6 jam) | SWING (24 jam) |
|---|---|---|
| Sinyal berlabel | 1.371 | 1.416 |
| Rata-rata semua sinyal (pembanding acak) | −0,20R, win 38% | −0,19R, win 34% |
| IC skor (per scan) | **+0,006, p=0,83** | +0,084, p=0,008* |
| IC per hari | −0,02 / +0,45 / +0,01 / +0,07 / −0,07 | +0,18 (1 scan) / +0,09 / +0,01 |
| "SIAP SHORT" | −0,42R, win 26% (n=35; 8 setelah dedup) | −0,10R, win 32% (n=31; 5 dedup) |
| SIAP SHORT vs placebo scan sama | **lebih buruk** dari 74% pilihan acak | lebih buruk dari 64% pilihan acak |
| Kuintil skor teratas vs terbawah | −0,19R vs −0,24R | −0,12R vs −0,20R |
| P(Win) | — | IC −0,035, p=0,50 |

\* p=0,008 SWING menyesatkan: 29 dari 35 scan berasal dari satu hari (19 Okt) dan scan per jam saling tumpang-tindih (horizon 24 jam). Sampel efektif ≈ 3 hari → **belum terbukti**.

Kalibrasi P(Win) SWING: kelompok P(Win) 0,6–0,7 menang 11%, 0,7+ menang 16%, sedangkan 0,5–0,6 menang 42%. GRADE_A −0,46R / GRADE_A+ −0,36R vs **VETO −0,24R** → grade yang lolos gate justru lebih buruk dari yang di-veto pada sampel ini.

## Kesimpulan & rekomendasi
1. **Jangan lanjut ke 3.2 (LightGBM)** — belum ada edge yang bisa dipelajari, dan datanya terlalu sedikit (risiko overfitting tinggi).
2. P(Win)/p_grade SWING tidak boleh diperlakukan sebagai probabilitas (sudah dijujurkan di 1.4); data ini menambah alasan untuk tidak menaikkan sizing berdasarkan GRADE_A/A+.
3. **Celah data:** sinyal LONG (`daytrade_long_engine.py`, modul paling rapuh — 18 dari 33 trade tertutup adalah LONG) tidak tercatat di `screener_signals`, jadi tidak bisa diuji. Perlu mencatat sinyal LONG (semua kandidat + skor, bukan hanya yang dieksekusi). *Mengubah kode live → butuh persetujuan user.*
4. Rekam candle OHLC 15m HL untuk koin yang ditandai screener (bukan hanya mark price) agar label akurat.
5. Ulangi uji ini setelah ≥4 minggu data (idealnya mencakup pasar turun & datar). Skrip siap: `python scripts/research/label_signals.py` lalu `python scripts/research/analyze_signal_labels.py`.

Bukan nasihat keuangan.

## File
- `scripts/research/label_signals.py` — label triple-barrier (menyalin DB ke folder sementara; tidak menulis ke data/).
- `scripts/research/analyze_signal_labels.py` — IC, permutasi, bucket, placebo.
- `logs/labeled_signals_20261022.csv` — semua sinyal + label. `logs/tahap3_1_output_20261022.txt` — keluaran lengkap.
