# ShortHunter Project Backlog

Daftar fitur dan peningkatan yang dijadwalkan untuk tahap berikutnya:

### 1. Telegram Alert Bot (Pemberitahuan Otomatis ke HP)
* **Status:** Backlog
* **Deskripsi:** Bot otomatis yang mengirim sinyal ke Telegram pengguna setiap kali ada koin yang mencapai skor ShortHunter $\ge 80$ atau memicu setup entry.
* **Format Pesan:**
  * Simbol koin & Harga saat ini
  * Skor Komposit (0-100) & Alasan (Momentum + Smart Money + Tokenomics)
  * Rekomendasi Titik Entry, Stop Loss (+10%), TP1 (+20%), dan TP2 (+35%)
  * Tombol aksi persetujuan eksekusi

### 2. Auto-Execution Connector (Hyperliquid Agent Wallet)
* **Status:** Backlog (Tahap 2)
* **Deskripsi:** Menghubungkan bot dengan Agent Wallet Hyperliquid untuk otomatis memasang order Limit/Market Short begitu tombol disetujui atau koin mencapai kriteria konfirmasi.
* **Fitur Pengaman:** Leverage terkunci maksimal 2x-3x, alokasi modal maksimal 1-2% per posisi.
