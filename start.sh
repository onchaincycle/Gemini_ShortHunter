#!/bin/bash
cd "$(dirname "$0")"

# Tes otomatis (ROADMAP 2.4): ./start.sh --test  -> wajib lulus sebelum restart daemon setelah ubah kode
if [ "$1" == "--test" ]; then
    ./.venv/bin/python3 -m unittest discover tests -v
    exit $?
fi

# Opsi diagnostik manual jika dijalankan dengan flag --doctor atau check
if [ "$1" == "--doctor" ] || [ "$1" == "check" ]; then
    ./.venv/bin/python3 scripts/system_doctor.py
    exit $?
fi

# Pre-flight Integrity & Error Guard (Mendeteksi eror sintaks & import sebelum crash)
./.venv/bin/python3 scripts/system_doctor.py --fast
DOCTOR_EXIT=$?

if [ $DOCTOR_EXIT -ne 0 ]; then
    echo ""
    echo "======================================================"
    echo "⚠️  INTEGRITY ERROR TERDETEKSI PADA SCRIPT BOT!"
    echo "======================================================"
    echo "Menjalankan diagnosis lengkap untuk melacak file bermasalah..."
    ./.venv/bin/python3 scripts/system_doctor.py
    echo ""
    echo "Silakan perbaiki error di atas atau laporkan ke Hermes AI."
    exit 1
fi

# Jalankan Menu Utama
./.venv/bin/python3 menu.py
