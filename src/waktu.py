"""Kunci zona waktu bot INTRADAY = WIB (2026-10-24).

Semua waktu jurnal ditulis sebagai teks jam lokal ("YYYY-MM-DD HH:MM:SS") dan dicocokkan sebagai teks. Bila satu proses
berjalan dengan jam lain (mis. sesi menu SSH lama ber-UTC di VPS), trade yang sama dianggap trade baru -> catatan dobel
(insiden 24 Okt: 32 baris dobel bergeser 7 jam). Modul ini dipanggil saat src.trade_db diimpor, sehingga SETIAP proses
yang menyentuh jurnal memakai WIB, apa pun pengaturan TZ peluncurnya. Perekam data riset (UTC) tidak mengimpor modul ini.
"""
import os
import time

ZONA = "Asia/Jakarta"


def kunci_wib() -> None:
    if os.environ.get("TZ") != ZONA:
        os.environ["TZ"] = ZONA
    if hasattr(time, "tzset"):
        time.tzset()


kunci_wib()
