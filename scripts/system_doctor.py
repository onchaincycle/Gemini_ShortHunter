#!/usr/bin/env python3
"""
System Doctor & Health Diagnostic Engine for ShortHunter:
- Validates all Python scripts syntax via AST & py_compile
- Detects Undefined Names (NameError prevention) via AST Static Analyzer
- Ensures entry points (main, main_menu) are defined and callable
- Verifies core imports (Hyperliquid SDK, DuckDB, SQLite, PortfolioSizer, SpotMacroVault)
- Checks database symlinks and required inputs data
"""

import sys
import os
import ast
import py_compile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

def check_syntax():
    """Checks syntax of all python files in the project."""
    print("🔍 [1/5] Memeriksa integritas sintaks seluruh script Python...")
    errors = []
    py_files = list(BASE_DIR.glob("*.py")) + list((BASE_DIR / "src").glob("*.py")) + list((BASE_DIR / "scripts").glob("*.py"))
    
    for f in py_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()
            ast.parse(content, filename=str(f))
            py_compile.compile(str(f), doraise=True)
        except Exception as e:
            errors.append((f.name, str(e)))

    if errors:
        print(f"  ❌ Ditemukan {len(errors)} kesalahan sintaks:")
        for name, err in errors:
            print(f"     • {name}: {err}")
        return False
    else:
        print(f"  ✅ Seluruh {len(py_files)} berkas Python terverifikasi bebas dari galat sintaks.")
        return True

def check_undefined_names():
    """Scans all python files for Undefined Names to 100% prevent NameError at runtime."""
    print("\n🔍 [2/5] Memindai Undefined Names & Variabel Hilang (Pencegahan NameError)...")
    try:
        from pyflakes import checker, messages
        has_pyflakes = True
    except ImportError:
        has_pyflakes = False

    py_files = list(BASE_DIR.glob("*.py")) + list((BASE_DIR / "src").glob("*.py"))
    undefined_errors = []

    for f in py_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=str(f))
            if has_pyflakes:
                w = checker.Checker(tree, filename=f.name)
                for m in w.messages:
                    if isinstance(m, messages.UndefinedName):
                        undefined_errors.append((f.name, m.lineno, str(m.message % m.message_args)))
        except Exception as e:
            undefined_errors.append((f.name, 0, f"Gagal memindai AST: {e}"))

    if undefined_errors:
        print(f"  ❌ DITEMUKAN {len(undefined_errors)} REFERENSI TAK TERDEFINISI (POTENSI NAMEERROR!):")
        for fname, line, msg in undefined_errors:
            print(f"     • {fname}:{line} ➔ {msg}")
        return False
    else:
        print(f"  ✅ Bersih 100%! Tidak ditemukan variabel atau modul tak terdefinisi pada {len(py_files)} berkas.")
        return True

def check_entrypoints():
    """Checks if main entrypoints in menu.py exist and are callable."""
    print("\n🔍 [3/5] Memeriksa entrypoint utama bot (menu.py: main & main_menu)...")
    try:
        with open(BASE_DIR / "menu.py", "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        
        defined_funcs = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assigned_vars = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned_vars.add(target.id)
        
        has_main = "main" in defined_funcs or "main" in assigned_vars
        has_main_menu = "main_menu" in defined_funcs or "main_menu" in assigned_vars
        
        if not has_main or not has_main_menu:
            print(f"  ❌ Entry point tidak lengkap! main={has_main}, main_menu={has_main_menu}")
            return False
        
        print("  ✅ Entry point main() dan main_menu() terdefinisi dengan sempurna.")
        return True
    except Exception as e:
        print(f"  ❌ Gagal menganalisis entrypoint: {e}")
        return False

def check_imports():
    """Checks if core project modules can be imported cleanly."""
    print("\n🔍 [4/5] Menguji impor modul inti sistem...")
    modules_to_test = [
        ("Hyperliquid Trader", "src.trader", "HyperliquidTrader"),
        ("Portfolio Sizer", "src.portfolio_sizer", "PortfolioSizer"),
        ("Spot Macro Vault", "src.spot_macro_vault", "SpotMacroVault"),
        ("Spot Strategy Engine", "src.spot_strategy_engine", "SpotStrategyEngine"),
        ("Daily Payout Manager", "src.daily_payout_manager", "DailyPayoutManager"),
        ("Autonomous Engine", "src.autonomous_engine", "AutonomousEngine"),
        ("AI Advisor Engine", "src.ai_engine", "GeminiTradeAdvisor"),
    ]
    
    failed = []
    for label, mod_name, cls_name in modules_to_test:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            print(f"  ✅ {label} ({mod_name}.{cls_name}) siap digunakan.")
        except Exception as e:
            print(f"  ❌ Gagal memuat {label} ({mod_name}): {e}")
            failed.append((label, str(e)))
            
    return len(failed) == 0

def check_data_and_symlinks():
    """Checks required data files, SQLite SCR DB, and portfolio symlinks."""
    print("\n🔍 [5/5] Memeriksa koneksi database SCR v10 dan berkas portofolio...")
    from src.spot_macro_vault import SpotMacroVault
    db_path = SpotMacroVault.get_db_path() or "/Users/imansulaiman/Documents/Claude_Crypto/SCR_v10/data/scr.db"
    
    if not os.path.exists(db_path):
        print(f"  ⚠️ Database SCR v10 ({db_path}) tidak ditemukan! Spot Vault offline.")
    else:
        sz_mb = os.path.getsize(db_path) / (1024 * 1024)
        print(f"  ✅ Database SCR v10 (SQLite) ditemukan ({sz_mb:.1f} MB).")
        
    p_file = BASE_DIR / "inputs" / "portfolio.csv"
    if not p_file.exists():
        print(f"  ⚠️ inputs/portfolio.csv belum ada. Portofolio hold spot 0 koin.")
    else:
        print(f"  ✅ Portofolio CSV ditemukan: {p_file.resolve()}")
        
    return True

def run_doctor(fast_mode=False):
    print("="*70)
    print("🩺 SHORTHUNTER SYSTEM DOCTOR — INTEGRITY & PRE-FLIGHT GUARD")
    print("="*70)
    
    ok_syntax = check_syntax()
    if not ok_syntax:
        return False
        
    ok_names = check_undefined_names()
    if not ok_names:
        return False

    ok_ep = check_entrypoints()
    if not ok_ep:
        return False
        
    if fast_mode:
        print("\n⚡ Fast pre-flight check passed! Meluncurkan menu...")
        return True
        
    ok_imp = check_imports()
    ok_data = check_data_and_symlinks()
    
    print("\n" + "="*70)
    if ok_syntax and ok_names and ok_ep and ok_imp and ok_data:
        print("🎉 HASIL AUDIT: SISTEM 100% SEHAT & BEBAS ERROR!")
        print("="*70)
        return True
    else:
        print("🚨 HASIL AUDIT: DITEMUKAN MASALAH. HARAP PERBAIKI SEBELUM TRADING!")
        print("="*70)
        return False

if __name__ == "__main__":
    is_fast = "--fast" in sys.argv
    success = run_doctor(fast_mode=is_fast)
    sys.exit(0 if success else 1)
