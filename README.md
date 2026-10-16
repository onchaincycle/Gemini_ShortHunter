# ShortHunter: Crypto Reversal & Distribution Screener

Sistem analitik kuantitatif dan mesin *backtest* untuk mendeteksi koin kripto yang mengalami *late-stage euphoria* (overextended) dan berpotensi mengalami pembalikan tren turun (*bearish reversal*).

## Fitur Utama

1. **High-Performance Time-Series Database:** Menggunakan **DuckDB** lokal yang sangat cepat untuk memproses jutaan baris data candlestick dan metrik derivatif tanpa perlu setup server database berat.
2. **Direct Hyperliquid Integration:** Terkoneksi langsung ke API publik Hyperliquid (tidak terpengaruh pemblokiran DNS/Internet Positif di Indonesia).
3. **Multi-Pillar Composite Screener:**
   - **Pillar 1:** Momentum 90-Hari & 30-Hari ($\ge 100\%$).
   - **Pillar 2:** Deviasi ekstrem dari EMA 50/200 dan RSI Overbought.
   - **Pillar 3:** Konfirmasi pembalikan tren pada timeframe 4H (CHoCH / Breakdown EMA 20).
   - **Pillar 4:** Evaluasi *Funding Rate* (deteksi euforia retail dan proteksi terhadap *short squeeze*).
4. **Historical Backtest Engine:** Menguji performa strategi secara historis dengan parameter Stop Loss, Take Profit, dan Max Holding Period yang fleksibel.

---

## Cara Penggunaan

Gunakan CLI [**`main.py`**](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/main.py):

### 1. Sinkronisasi Data Pasar ke Database DuckDB
Mengunduh candlestick harian (Daily) dan 4-Jam (4H) serta snapshot funding rate:
```bash
./.venv/bin/python3 main.py --sync --min-vol 5000000
```

### 2. Menjalankan Screener
Menyaring koin di database berdasarkan kriteria *short*:
```bash
./.venv/bin/python3 main.py --screen --min-score 50 --min-gain 60
```

### 3. Menjalankan Simulasi Backtest Historis
Menguji performa sinyal short di masa lalu:
```bash
./.venv/bin/python3 main.py --backtest --min-gain 90 --sl 10 --tp 20
```

### 4. Melihat Status Database
Melihat jumlah baris data dan koin yang tersimpan di DuckDB:
```bash
./.venv/bin/python3 main.py --stats
```

---

## Struktur Direktori

```
Gemini_ShortHunter/
├── data/
│   └── shorthunter.duckdb       # Database DuckDB lokal
├── src/
│   ├── database.py              # Koneksi dan skema tabel DuckDB
│   ├── hyperliquid_client.py    # Klien API Hyperliquid
│   ├── downloader.py            # Sinkronisasi data ke DuckDB
│   ├── screener.py              # Algoritma penyaringan & skor komposit
│   └── backtester.py            # Simulasi backtest historis
├── main.py                      # CLI runner utama
├── PROJECT_INSTRUCTION.md       # Spesifikasi proyek lengkap
└── requirements.txt             # Dependensi Python
```
