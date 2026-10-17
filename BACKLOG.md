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
* **Status:** Selesai / Aktif (Tahap 2)
* **Deskripsi:** Menghubungkan bot dengan Agent Wallet Hyperliquid untuk otomatis memasang order Limit/Market Short & Long begitu koin mencapai kriteria konfirmasi Grade A.
* **Fitur Pengaman:** Leverage terkunci maksimal 2x-3x, alokasi modal dinamis berbasis ekuitas & pagar cadangan 15%.

### 3. Weekly Profit Harvester — Program Tarik Rutin Saldo Mingguan ("Gajian Mingguan")
* **Status:** Backlog (Ditunda sesuai arahan User — `2026-10-17`)
* **Deskripsi:** Sistem pencairan dan penguncian profit mingguan otomatis berbasis rumus **50/50 High-Water Mark Skimming**:
  $$\text{Gajian Mingguan} = \max\left(0,\; (\text{Ekuitas Akhir Minggu} - \text{Modal Dasar}) \times 50\%\right)$$
  * **50% dari profit bersih mingguan** ditarik/dikunci menjadi **Gajian Mingguan**.
  * **50% sisanya** ditambahkan ke **Modal Dasar (*Baseline Capital*)** minggu berikutnya untuk *compounding* ukuran peluru secara sehat.
* **Opsi Arsitektur Eksekusi (Hybrid Opsi 1 + Opsi 2):**
  1. **Mode Auto-Lock Brankas Gajian (*Profit Vault Lock* — Tanpa Master Key):** Setiap akhir pekan daemon otomatis mengunci jatah gajian di dalam `Capital Governor` (`autonomous_engine.py`) sehingga saldo profit tersebut tidak disentuh oleh AI untuk buka posisi baru.
  2. **Mode 1-Click Weekly Withdraw (*Zero-Disk Master Key*):** Menyediakan menu pencairan 1-klik (`exchange.withdraw_from_bridge` ke Arbitrum One atau `usd_class_transfer` ke Spot) dengan input *Master Key* sekali pakai di RAM (`getpass`, tidak pernah disimpan ke `.env` atau disk).
* **Syarat Pengaman Mutlak:** Hanya menarik dari *Free Margin* di atas pagar cadangan 15% tanpa mengganggu margin posisi aktif yang sedang berjalan.



### [DEV-001] Implementasi Throttling & Jeda Antrian pada GitHub Sync Trade Close *(Captured via AI War Room 2026-10-17 11:43:11)*
* **Status:** `OPEN_FOR_ANTIGRAVITY` | **Kategori:** `STRATEGY_LOGIC` | **Prioritas:** `MEDIUM`
* **Kutipan User (`QA-0009`):** "gak perlu seketika, setiap ada close yg berdekatan, perlu diberi jeda"
* **Spesifikasi Teknis untuk Antigravity:** Tambahkan mekanisme antrian ber-jeda (throttling/debounce queue interval, misal minimal jeda 60 detik atau batching) di dalam github_sync.py untuk menangani log penutupan posisi (trade close) yang terjadi secara beruntun agar terhindar dari GitHub API rate-limit.

### [DEV-002] Penambahan Widget Tabel Log Perdagangan Interaktif di War Room *(Captured via AI War Room 2026-10-17 11:45:19)*
* **Status:** `OPEN_FOR_ANTIGRAVITY` | **Kategori:** `UI_UX_IMPROVEMENT` | **Prioritas:** `MEDIUM`
* **Kutipan User (`QA-0013`):** "gak ada tampilannya"
* **Spesifikasi Teknis untuk Antigravity:** Tambahkan komponen widget tabel interaktif atau format card Markdown yang rapi pada agent_chat.py / html_reporter.py untuk menampilkan riwayat log perdagangan secara langsung di layar chat War Room ketika Principal meminta data log.
