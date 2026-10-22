# Backtest offline modul LONG intraday — v1 (sekarang) vs v2 (refactor audit Hermes) — 2026-10-22

**Kesimpulan: refactor v2 mengurangi kerugian, tapi TIDAK menciptakan edge. Kedua versi kalah dari entry LONG acak. Jangan rilis v2 sebagai "perbaikan".** Bot live tidak disentuh.

## Data & metode
- 20 koin (`data/intraday_cache`), candle 15m 16 Sep – 21 Okt 2026 (±67 rb bar). Pasar naik: beli-tahan 6 jam rata-rata **+0,26%** per entry (menguntungkan LONG).
- Skor v1 = engine asli (diverifikasi: 200/200 sampel identik dengan `IntradayLongEngine.analyze_coin`). Entry bila skor ≥ 75 (ambang live), keputusan di penutupan candle, masuk di open candle berikutnya.
- v2 = 4 perbaikan audit Hermes: BOS dari candle tertutup, filter volume climax ≥1,35× MA10 (−15 bila tidak), Higher Low menggantikan hadiah "dekat lantai", ekor bawah ≥55%. Parameter dari audit (tidak di-tuning setelah melihat hasil).
- Exit sama untuk semua: SL 2,5–4,5%, TP1 1R trim 50% + SL→BE, TP2 2R (maks 9%), time-stop 6 jam; SL & TP di bar yang sama → SL. Biaya 0,19% PP.
- Placebo: entry acak pada koin & jumlah yang sama, exit sama.

## Hasil
| Varian | Trade | Win | Rata2 R | Total R | PF | Max DD (R) | Paruh 1 / 2 | Koin positif | vs acak |
|---|---|---|---|---|---|---|---|---|---|
| **v1 sekarang** | 965 | 42% | **−0,130** | −125,6 | 0,69 | 140,9 | −0,056 / −0,205 | 1/20 | lebih buruk dari 100% acak (acak median −0,015) |
| **v2 refactor** | 267 | 47% | **−0,033** | −8,7 | 0,93 | 26,0 | −0,041 / −0,024 | 8/20 | lebih buruk dari 95% acak (acak median +0,045) |
| hanya BOS tertutup | 927 | 42% | −0,129 | −119,2 | 0,70 | 132,2 | | 1/20 | |
| hanya volume | 746 | 43% | −0,108 | −80,4 | 0,75 | 93,3 | | 4/20 | |
| hanya Higher Low | 619 | 42% | −0,140 | −86,5 | 0,70 | 101,2 | | 2/20 | |
| hanya wick 55% | 842 | 43% | −0,121 | −101,8 | 0,72 | 115,6 | | 2/20 | |

- Selisih v2 − v1 = +0,098R/trade (z ≈ 1,5) → **petunjuk, belum terbukti**. Sebagian besar perbaikan datang dari **lebih sedikit trade** (filter gabungan), bukan dari pemilihan entry yang lebih baik.
- v1 kalah dari acak secara tegas (persentil 0) di pasar yang naik → logika "beli dekat dasar intraday" cenderung memilih pisau jatuh. Konsisten dengan jurnal live (DAYTRADE LONG n=11, −$7,28).
- Sesi: entry v1 terkonsentrasi di sesi London/NY (bonus sesi +20/+25) dan justru di sana paling rugi (−0,15R).

## Keterbatasan
- 20 koin (daftar cache, bukan universe penuh ±95 koin), 5 minggu, satu rezim (naik).
- Funding tidak tersedia historis → pilar funding = 0 di semua varian. AI gatekeeper tidak disimulasikan.
- Live v1 masuk di candle yang BELUM tutup; backtest memakai candle tertutup → hasil v1 di sini kemungkinan **terlalu optimis**.
- Exit disederhanakan (tanpa half-risk 0,45R, early BE 0,65R, trailing HWM).

## Rekomendasi (keputusan user)
1. **Opsi utama: nonaktifkan / mode paper DAYTRADE LONG** sampai ada logika yang mengalahkan acak (bukti: 965 trade backtest + jurnal live, keduanya negatif). Ini perubahan gerbang → butuh persetujuan; idealnya diterapkan sekaligus saat rilis v2 setelah 7 hari bebas bug.
2. Jangan rilis v2 sebagai pengganti — masih kalah dari acak.
3. Riset lanjut (offline): (a) data lebih luas — skrip ambil candle 15m HL ±95 koin × 90 hari dijalankan di Mac; (b) uji konsep LONG yang berbeda (ikut tren/breakout, bukan beli dasar) dengan placebo yang sama; (c) cek ulang sisi SHORT intraday dengan kerangka yang sama.

File: `scripts/research/bt_long_intraday.py` (mode `var`, `placebo`, `drift`), trade per varian `logs/research/tr_*.csv`.
