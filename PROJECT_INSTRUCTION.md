# PROJECT SPECIFICATION & AI INSTRUCTION: SHORTHUNTER
## Crypto Short & Distribution Hunter (Reversal & Exhaustion Screener)

---

### 1. OVERVIEW & OBJECTIVE
**ShortHunter** adalah sistem analitik dan screener kripto otomatis yang dirancang untuk mendeteksi koin/token yang berada di fase **"Late-Stage Euphoria / Distribution"** dan memiliki probabilitas tinggi untuk mengalami koreksi tajam atau pembalikan tren (bearish reversal).

Tujuan utama sistem:
- Menghindari jebakan FOMO beli di pucuk (Peak Top).
- Mengidentifikasi peluang trading posisi **SHORT (Futures/Perpetuals)** atau **Take Profit Spot** secara objektif berbasis data (Data-Driven), bukan spekulasi emosional.

---

### 2. CORE DETECTION PILLARS (METRIC & KRITERIA FILTER)

Sistem bekerja dengan menyaring aset melalui 5 pilar kriteria berjenjang:

#### Pilar 1: Overextended Momentum (Penyaringan Awal / Universe Filter)
- **Timeframe Screening:** 30 hari s/d 90 hari (1Q).
- **Price Performance:** Kenaikan harga $\ge 100\%$ dalam rentang 30–90 hari terakhir.
- **Trend Distance:** Jarak harga saat ini $\ge 40\%$ di atas EMA 50 (Daily) atau $\ge 60\%$ di atas EMA 200 (Daily).
- **RSI Multi-Timeframe:** RSI(14) Daily $\ge 70$ (Overbought) atau Weekly $\ge 75$.

#### Pilar 2: Euphoria & Exhaustion Climax (Puncak Euforia Pembeli)
- **Blow-off Top / Volume Climax:** Terjadi lonjakan volume transaksi sangat masif pada candle harian/4H tetapi diiringi penutupan harga dengan *long upper shadow* (shooting star) atau candle *churning* (volume tinggi tanpa kenaikan harga berarti).
- **Bearish Divergence:**
  - Regular Bearish Divergence pada RSI (Daily & 4H): Harga mencetak *Higher High* (HH), namun RSI mencetak *Lower High* (LH).
  - MACD Histogram melemah saat harga mencoba retest pucuk.
- **Derivatives Euphoria (Pasar Futures):**
  - **Funding Rate Ekstrem:** Weighted Funding Rate bertengger pada level sangat tinggi positif (> +0.05% s/d +0.1% per 8 jam), menandakan retail *overleveraged long*.
  - **Open Interest (OI) Exhaustion:** OI mencapai rekor tertinggi baru (All-Time High / Local High) sementara laju kenaikan harga mulai melambat atau mendatar (stagnan).

#### Pilar 3: Trend Reversal & Breakdown Confirmation (Konfirmasi Mulai Turun)
- **Market Structure Break (CHoCH - Change of Character):**
  - Timeframe 4H atau 1D telah menembus *Higher Low* (HL) terakhir.
  - Pembentukan *Lower High* (LH) pertama setelah puncak.
- **Moving Average Breakdown:** Harga ditutup (*close candle*) di bawah EMA 20 (4H) atau EMA 9 (Daily).
- **Volume Imbalance:** Volume pada candle merah (sell volume) lebih dominan dibandingkan volume candle hijau saat retest/pullback.

#### Pilar 4: On-Chain & Smart Money Distribution (Whale Buang Barang)
- **Exchange Inflow Spike:** Terjadi lonjakan transfer koin dalam jumlah besar dari *cold wallet* / *private wallet* ke *Centralized Exchanges* (Binance, Bybit, OKX, dll.).
- **Top 100 Holder Balances:** Penurunan persentase kepemilikan koin pada 10-50 dompet non-exchange terbesar (Whale Dump).
- **Smart Money Tracking:** Aktivitas penjualan bersih (*Net Outflow*) oleh wallet kategori *Smart Money / VC / Insiders*.

#### Pilar 5: Supply Dilution & Fundamental Catalyst (Lonjakan Pasokan)
- **Token Unlocks / Vesting Cliffs:** Jadwal *unlock* token $\ge 2\%$ dari circulating supply yang akan terjadi dalam 7–14 hari ke depan atau baru saja dibuka (< 48 jam).
- **Circulating Supply Growth:** Peningkatan pasokan beredar (inflasi koin/minting baru) secara mendadak.
- **Dev / Foundation Wallet Outflow:** Perpindahan token dari Treasury / Development pool ke liquidity pools atau exchange.

---

### 3. SCORING & ALERT ENGINE (SHORTHUNTER COMPOSITE SCORE)

Setiap koin yang lolos Pilar 1 akan dihitung skor risikonya (0–100):

| Kategori | Bobot | Metrik Evaluasi |
| :--- | :---: | :--- |
| **Momentum & Overextension** | 15% | Gain 90D, deviasi dari EMA 50/200, RSI Daily/Weekly |
| **Euphoria & Derivatives** | 25% | Funding rate level, OI divergence, liquidation density |
| **Technical Reversal (PA)** | 25% | 4H/1D CHoCH, bearish divergence RSI/MACD, volume seller |
| **On-Chain Whale Movement** | 20% | Exchange net inflow, whale wallet balance reduction |
| **Supply Inflation & Unlocks** | 15% | Upcoming vesting cliff, token unlock size vs daily volume |

**Trigger Level:**
- **Score $\ge 80$ (HIGH PROBABILITY SHORT / DUMP ALERT):** Semua pilar konvergen (Whale dumping + technical breakdown + supply unlock).
- **Score 65–79 (WATCHLIST / DISTRIBUTION WATCH):** Sedang fase distribusi puncak, menunggu konfirmasi breakdown teknikal.
- **Score < 65:** Abaikan / Momentum bull masih terlalu kuat.

---

### 4. DATA SOURCES & RECOMMANDED APIs

1. **Market Data & Technicals (Price, Volume, OHLCV):**
   - Binance API / Bybit API (Ccxt library)
   - CoinGecko API / CoinMarketCap API
2. **Derivatives Data (Funding Rate, Open Interest, Liquidations):**
   - CoinGlass API
   - Coinalyze API / Binance Futures WebSocket
3. **On-Chain & Whale Tracking:**
   - Arkham Intelligence API
   - Etherscan / Solscan / Blockchain Explorers
   - Whale Alert API / Nansen (jika ada akses)
4. **Tokenomics & Unlock Schedules:**
   - TokenUnlocks API
   - DefiLlama (Unlocks & Protocol Metrics)

---

### 5. SYSTEM ARCHITECTURE & TECH STACK

- **Backend / Core Engine:** Python 3.11+
  - Data ingestion: `ccxt`, `aiohttp`, `pandas`, `numpy`, `pandas-ta`
  - Scheduler / Worker: `APScheduler` / `Celery` / `cron`
- **Database:**
  - SQLite / PostgreSQL (Penyimpanan histori sinyal, token metadata, whitelist/blacklist)
- **Notification / Interface:**
  - **Phase 1:** Telegram Bot Alert (Kirim notifikasi instan saat koin mencapai skor $\ge 80$ beserta chart & breakdown metrik).
  - **Phase 2:** CLI Dashboard atau Web Dashboard (FastAPI + React / Streamlit) untuk screener table real-time.

---

### 6. SAFETY & RISK MANAGEMENT GUARDS (FILTERS)

1. **Minimum Liquidity & Volume Filter:**
   - Menghindari koin micin / illiquid token: Volume harian (24H Volume) minimal $\ge \$10,000,000$.
   - Market Cap minimal $\ge \$50,000,000$ (menghindari token manipulasi pump-and-dump satu pihak).
2. **Short Squeeze Protection:**
   - Peringatan jika Open Interest terus meningkat tajam dan Negative Funding Rate ekstrem (indikasi *crowded short* yang rawan *short squeeze*).
3. **Invalidation Level (Stop Loss Guideline):**
   - Setiap sinyal wajib menyertakan level invalidasi teknikal (misal: di atas retest high atau puncak jarum tertinggi).
