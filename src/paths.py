"""Jalur file terpusat (ROADMAP Tahap 4A, 2026-10-22) — sama di Mac & Linux/VPS, tanpa jalur /Users/... di kode.

Default = struktur folder di Mac:  <induk>/Gemini_ShortHunter  dan  <induk>/Claude_Crypto/SCR_v10.
Override lewat variabel lingkungan (mis. di VPS, dari file .env / systemd EnvironmentFile):
  SH_CLAUDE_CRYPTO_DIR  folder Claude_Crypto           (default: ../Claude_Crypto)
  SH_SCR_CONTRACT       file kontrak scr_signals.json   (default: <Claude_Crypto>/SCR_v10/output/scr_signals.json;
                        di VPS Tipe A: data/scr_signals.json yang dikirim dari Mac)
  SH_HERMES_SCRIPTS     folder skrip Hermes             (default: ~/.hermes/scripts)
"""
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")


def _env_path(name, default):
    v = os.environ.get(name, "").strip()
    return os.path.abspath(os.path.expanduser(v)) if v else os.path.abspath(default)


CLAUDE_CRYPTO_DIR = _env_path("SH_CLAUDE_CRYPTO_DIR", os.path.join(ROOT_DIR, "..", "Claude_Crypto"))
SCR_DIR = os.path.join(CLAUDE_CRYPTO_DIR, "SCR_v10")
SCR_CONTRACT_PATH = _env_path("SH_SCR_CONTRACT", os.path.join(SCR_DIR, "output", "scr_signals.json"))
HERMES_SCRIPTS_DIR = _env_path("SH_HERMES_SCRIPTS", os.path.join(os.path.expanduser("~"), ".hermes", "scripts"))


def file_uri(rel):
    """URI file:// untuk tautan di dokumen yang dihasilkan (mengikuti lokasi repo saat ini)."""
    return "file://" + os.path.join(ROOT_DIR, rel)
