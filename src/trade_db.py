"""
Transactional SQLite WAL Engine for ShortHunter:
Handles ACID-compliant state management for:
- Active positions lifecycle (SL/TP, trailing stops, auto-close timers)
- Position strategy tags & trading plans
- Forward test trade journal & closed position history
- Equity snapshots & portfolio performance tracking
- Screener signal log history
- Dynamic adaptive weights & market regime logs

Features SQLite Write-Ahead Logging (WAL) for non-blocking concurrent multi-reader
and single-writer operations between background daemons and interactive CLI menus.
"""

import os
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "data", "shorthunter_trade.db")


class TradeDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging (WAL) and set busy timeout
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def init_db(self):
        """Creates the required tables if they do not exist."""
        with self.get_connection() as conn:
            # 1. Active Positions Lifecycle Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_positions (
                    coin TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    entry_px REAL NOT NULL,
                    size REAL NOT NULL,
                    leverage INTEGER DEFAULT 2,
                    margin_usd REAL DEFAULT 0.0,
                    initial_sl_px REAL,
                    current_sl_px REAL,
                    tp1_px REAL,
                    tp2_px REAL,
                    runner_px REAL,
                    trimmed_status INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    details_json TEXT
                )
            """)

            # 2. Position Tags & Strategy Plans
            conn.execute("""
                CREATE TABLE IF NOT EXISTS position_tags (
                    coin TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    plan_json TEXT,
                    meta_json TEXT,
                    tagged_at TEXT
                )
            """)

            # 3. Trade Journal (Forward Test History)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coin TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,  -- 'OPEN' or 'CLOSED'
                    entry_time TEXT,
                    exit_time TEXT,
                    entry_px REAL,
                    exit_px REAL,
                    size REAL,
                    leverage INTEGER DEFAULT 2,
                    margin_used REAL,
                    realized_pnl REAL,
                    fee_usd REAL DEFAULT 0.0,
                    exit_reason TEXT,
                    mfe_pct REAL DEFAULT 0.0,
                    mae_pct REAL DEFAULT 0.0,
                    r_multiple REAL,
                    hold_hours REAL,
                    snapshots_json TEXT,
                    event_logs_json TEXT,
                    ai_review_json TEXT,
                    milestones_json TEXT,
                    details_json TEXT
                )
            """)

            # Auto-migration for existing SQLite databases
            for col in ["milestones_json", "details_json"]:
                try:
                    conn.execute(f"ALTER TABLE trade_journal ADD COLUMN {col} TEXT")
                except Exception:
                    pass

            # 4. Portfolio Equity Snapshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    timestamp DATETIME PRIMARY KEY,
                    total_equity REAL,
                    available_margin REAL,
                    open_positions_count INTEGER,
                    unrealized_pnl REAL
                )
            """)

            # 5. Screener Historical Signals Log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS screener_signals (
                    signal_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    timestamp BIGINT NOT NULL,
                    datetime TEXT NOT NULL,
                    score REAL,
                    gain_90d REAL,
                    gain_30d REAL,
                    rsi_daily REAL,
                    funding_rate REAL,
                    verdict TEXT,
                    details_json TEXT
                )
            """)

            # 6. Adaptive Screener Weights
            conn.execute("""
                CREATE TABLE IF NOT EXISTS adaptive_weights (
                    stream TEXT PRIMARY KEY,
                    weights_json TEXT,
                    calibrated_at TEXT,
                    performance_summary_json TEXT
                )
            """)

            # 7. AI Market Regime State
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_regime (
                    timestamp DATETIME PRIMARY KEY,
                    regime_status TEXT NOT NULL,
                    btc_trend TEXT,
                    avg_funding_rate REAL,
                    reasoning TEXT,
                    details_json TEXT
                )
            """)

    # -------------------------------------------------------------------------
    # ACTIVE POSITIONS (LIFECYCLE) OPERATIONS
    # -------------------------------------------------------------------------
    def load_lifecycle_state(self) -> Dict[str, Dict]:
        """Loads all active positions from SQLite into a lifecycle dictionary."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM active_positions")
            rows = cursor.fetchall()
            lifecycle = {}
            for r in rows:
                coin = r["coin"]
                details = json.loads(r["details_json"]) if r["details_json"] else {}
                entry = dict(details)
                entry.update({
                    "coin": coin,
                    "strategy": r["strategy"],
                    "entry_px": float(r["entry_px"]),
                    "remaining_sz": float(r["size"]),
                    "leverage": int(r["leverage"]),
                    "margin_used": float(r["margin_usd"] or 0.0),
                    "initial_sl_px": float(r["initial_sl_px"]) if r["initial_sl_px"] else None,
                    "current_sl_px": float(r["current_sl_px"]) if r["current_sl_px"] else None,
                    "tp1_px": float(r["tp1_px"]) if r["tp1_px"] else None,
                    "tp2_px": float(r["tp2_px"]) if r["tp2_px"] else None,
                    "runner_px": float(r["runner_px"]) if r["runner_px"] else None,
                    "trimmed_10pct": bool(r["trimmed_status"]),
                    "created_at": r["created_at"],
                    "last_check": r["updated_at"]
                })
                lifecycle[coin] = entry
            return lifecycle

    def save_lifecycle_state(self, lifecycle: Dict[str, Dict]):
        """Persists entire lifecycle state atomically in SQLite."""
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            # Delete positions that are no longer in the active lifecycle
            active_coins = list(lifecycle.keys())
            if active_coins:
                placeholders = ",".join("?" for _ in active_coins)
                conn.execute(f"DELETE FROM active_positions WHERE coin NOT IN ({placeholders})", active_coins)
            else:
                conn.execute("DELETE FROM active_positions")

            for coin, s in lifecycle.items():
                details = {k: v for k, v in s.items() if k not in [
                    "coin", "strategy", "entry_px", "remaining_sz", "leverage", "margin_used",
                    "initial_sl_px", "current_sl_px", "tp1_px", "tp2_px", "runner_px", "trimmed_10pct"
                ]}
                conn.execute("""
                    INSERT OR REPLACE INTO active_positions (
                        coin, strategy, entry_px, size, leverage, margin_usd,
                        initial_sl_px, current_sl_px, tp1_px, tp2_px, runner_px,
                        trimmed_status, created_at, updated_at, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    coin,
                    s.get("strategy", "SWING"),
                    float(s.get("entry_px", 0.0)),
                    float(s.get("remaining_sz", s.get("initial_sz", 0.0))),
                    int(s.get("leverage", 2)),
                    float(s.get("margin_used", 0.0)),
                    float(s.get("initial_sl_px")) if s.get("initial_sl_px") else None,
                    float(s.get("current_sl_px")) if s.get("current_sl_px") else None,
                    float(s.get("tp1_px")) if s.get("tp1_px") else None,
                    float(s.get("tp2_px")) if s.get("tp2_px") else None,
                    float(s.get("runner_px")) if s.get("runner_px") else None,
                    1 if s.get("trimmed_10pct") else 0,
                    s.get("created_at", now_str),
                    now_str,
                    json.dumps(details)
                ))

    # -------------------------------------------------------------------------
    # POSITION TAGS OPERATIONS
    # -------------------------------------------------------------------------
    def load_position_tags(self) -> Dict[str, Dict]:
        """Loads all position strategy tags."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM position_tags")
            tags = {}
            for r in cursor.fetchall():
                tags[r["coin"]] = {
                    "coin": r["coin"],
                    "strategy": r["strategy"],
                    "plan": json.loads(r["plan_json"]) if r["plan_json"] else {},
                    "meta": json.loads(r["meta_json"]) if r["meta_json"] else {},
                    "tagged_at": r["tagged_at"]
                }
            return tags

    def save_position_tag(self, coin: str, strategy: str, plan: Optional[Dict] = None, meta: Optional[Dict] = None):
        """Saves or updates a strategy tag for a coin."""
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO position_tags (coin, strategy, plan_json, meta_json, tagged_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                coin,
                strategy,
                json.dumps(plan or {}),
                json.dumps(meta or {}),
                now_str
            ))

    def remove_position_tag(self, coin: str):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM position_tags WHERE coin = ?", (coin,))

    # -------------------------------------------------------------------------
    # FORWARD TEST TRADE JOURNAL OPERATIONS
    # -------------------------------------------------------------------------
    def load_journal(self) -> Dict[str, Dict]:
        """Loads all journal entries formatted as key -> dict map."""
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM trade_journal ORDER BY id ASC")
            journal = {}
            for r in cursor.fetchall():
                key = r["coin"] if r["status"] == "OPEN" else f"{r['coin']}@{r['exit_time'] or r['entry_time'] or r['id']}"
                details = {}
                if "details_json" in r.keys() and r["details_json"]:
                    try:
                        details = json.loads(r["details_json"])
                    except Exception:
                        details = {}
                item = dict(details)

                milestones = {
                    "trimmed_10pct": False,
                    "trimmed_time": None,
                    "tp_20pct": False,
                    "tp_time": None,
                    "runner_35pct": False,
                    "runner_time": None,
                    "sl_hit": False,
                    "sl_time": None
                }
                if "milestones_json" in r.keys() and r["milestones_json"]:
                    try:
                        m_db = json.loads(r["milestones_json"])
                        if isinstance(m_db, dict):
                            milestones.update(m_db)
                    except Exception:
                        pass
                elif "milestones" in item and isinstance(item["milestones"], dict):
                    milestones.update(item["milestones"])

                item.update({
                    "id": r["id"],
                    "coin": r["coin"],
                    "strategy": r["strategy"],
                    "status": r["status"],
                    "entry_time": r["entry_time"],
                    "exit_time": r["exit_time"],
                    "entry_px": r["entry_px"],
                    "exit_px": r["exit_px"],
                    "size": r["size"],
                    "leverage": r["leverage"],
                    "margin_used": r["margin_used"],
                    "realized_pnl": r["realized_pnl"],
                    "fee_usd": r["fee_usd"],
                    "exit_reason": r["exit_reason"],
                    "max_favorable_drop_pct": r["mfe_pct"],
                    "max_adverse_pump_pct": r["mae_pct"],
                    "r_multiple": r["r_multiple"],
                    "hold_hours": r["hold_hours"],
                    "snapshots": json.loads(r["snapshots_json"]) if r["snapshots_json"] else [],
                    "event_logs": json.loads(r["event_logs_json"]) if r["event_logs_json"] else [],
                    "ai_review": json.loads(r["ai_review_json"]) if r["ai_review_json"] else None,
                    "milestones": milestones
                })
                if r["status"] == "CLOSED" and "final_pnl_usd" not in item:
                    item["final_pnl_usd"] = r["realized_pnl"]
                journal[key] = item
            return journal

    def save_journal_position(self, pos: Dict):
        """Inserts or updates a position record in the trade journal."""
        coin = pos.get("coin")
        status = pos.get("status", "OPEN")
        entry_time = pos.get("entry_time")
        snapshots_json = json.dumps(pos.get("snapshots", []))
        event_logs_json = json.dumps(pos.get("event_logs", []))
        ai_review_json = json.dumps(pos.get("ai_review")) if pos.get("ai_review") else None
        milestones_json = json.dumps(pos.get("milestones", {}))

        standard_cols = {
            "coin", "strategy", "status", "entry_time", "exit_time", "entry_px", "exit_px",
            "size", "leverage", "margin_used", "realized_pnl", "fee_usd", "exit_reason",
            "mfe_pct", "mae_pct", "r_multiple", "hold_hours", "snapshots", "event_logs",
            "ai_review", "milestones", "id"
        }
        details = {k: v for k, v in pos.items() if k not in standard_cols}
        details_json = json.dumps(details)

        with self.get_connection() as conn:
            # Check existing record by ID, (coin, entry_time), or active OPEN coin
            pos_id = pos.get("id")
            row = None
            if pos_id:
                cursor = conn.execute("SELECT id FROM trade_journal WHERE id = ?", (pos_id,))
                row = cursor.fetchone()
            if not row and coin and entry_time:
                cursor = conn.execute(
                    "SELECT id FROM trade_journal WHERE coin = ? AND entry_time = ? LIMIT 1",
                    (coin, entry_time)
                )
                row = cursor.fetchone()
            if not row and status == "OPEN":
                cursor = conn.execute(
                    "SELECT id FROM trade_journal WHERE coin = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
                    (coin,)
                )
                row = cursor.fetchone()

            if row:
                conn.execute("""
                    UPDATE trade_journal SET
                        strategy = ?, status = ?, exit_time = ?, entry_px = ?, exit_px = ?,
                        size = ?, leverage = ?, margin_used = ?, realized_pnl = ?, fee_usd = ?,
                        exit_reason = ?, mfe_pct = ?, mae_pct = ?, r_multiple = ?, hold_hours = ?,
                        snapshots_json = ?, event_logs_json = ?, ai_review_json = ?,
                        milestones_json = ?, details_json = ?
                    WHERE id = ?
                """, (
                    pos.get("strategy", "SWING"), status, pos.get("exit_time"),
                    pos.get("entry_px"), pos.get("exit_px"), pos.get("size"),
                    pos.get("leverage", 2), pos.get("margin_used"),
                    pos.get("realized_pnl"), pos.get("fee_usd", 0.0),
                    pos.get("exit_reason"), pos.get("max_favorable_drop_pct", 0.0),
                    pos.get("max_adverse_pump_pct", 0.0), pos.get("r_multiple"),
                    pos.get("hold_hours"), snapshots_json, event_logs_json, ai_review_json,
                    milestones_json, details_json,
                    row["id"]
                ))
            else:
                conn.execute("""
                    INSERT INTO trade_journal (
                        coin, strategy, status, entry_time, exit_time, entry_px, exit_px,
                        size, leverage, margin_used, realized_pnl, fee_usd, exit_reason,
                        mfe_pct, mae_pct, r_multiple, hold_hours, snapshots_json, event_logs_json,
                        ai_review_json, milestones_json, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    coin, pos.get("strategy", "SWING"), status, entry_time, pos.get("exit_time"),
                    pos.get("entry_px"), pos.get("exit_px"), pos.get("size"),
                    pos.get("leverage", 2), pos.get("margin_used"),
                    pos.get("realized_pnl"), pos.get("fee_usd", 0.0), pos.get("exit_reason"),
                    pos.get("max_favorable_drop_pct", 0.0), pos.get("max_adverse_pump_pct", 0.0),
                    pos.get("r_multiple"), pos.get("hold_hours"), snapshots_json, event_logs_json,
                    ai_review_json, milestones_json, details_json
                ))

    def update_ai_review(self, trade_id: int, review_dict: Dict):
        """Attaches an AI post-mortem review to a closed trade."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE trade_journal SET ai_review_json = ? WHERE id = ?",
                (json.dumps(review_dict), trade_id)
            )

    # -------------------------------------------------------------------------
    # EQUITY & PORTFOLIO SNAPSHOTS
    # -------------------------------------------------------------------------
    def record_equity_snapshot(self, total_equity: float, available_margin: float, open_positions_count: int, unrealized_pnl: float):
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO equity_snapshots (timestamp, total_equity, available_margin, open_positions_count, unrealized_pnl)
                VALUES (?, ?, ?, ?, ?)
            """, (now_str, total_equity, available_margin, open_positions_count, unrealized_pnl))

    # -------------------------------------------------------------------------
    # SCREENER SIGNALS PERSISTENCE
    # -------------------------------------------------------------------------
    def log_signals(self, signals_list: List[Dict]):
        """Persists screener alert candidates into SQLite."""
        if not signals_list:
            return
        now_ms = int(datetime.now().timestamp() * 1000)
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            for s in signals_list:
                sig_id = f"{s.get('symbol')}_{s.get('timestamp', now_ms)}"
                conn.execute("""
                    INSERT OR REPLACE INTO screener_signals (
                        signal_id, symbol, strategy, timestamp, datetime, score,
                        gain_90d, gain_30d, rsi_daily, funding_rate, verdict, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sig_id,
                    s.get("symbol", s.get("coin")),
                    s.get("strategy", "SWING"),
                    s.get("timestamp", now_ms),
                    s.get("datetime", now_str),
                    float(s.get("score", 0.0)),
                    float(s.get("gain_90d", 0.0)),
                    float(s.get("gain_30d", 0.0)),
                    float(s.get("rsi_daily", 0.0)),
                    float(s.get("funding_rate", 0.0)),
                    s.get("verdict", "N/A"),
                    json.dumps(s)
                ))

    def get_recent_signals(self, limit: int = 50) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM screener_signals ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
            res = []
            for r in cursor.fetchall():
                d = dict(r)
                if d.get("details_json"):
                    d["details"] = json.loads(d["details_json"])
                res.append(d)
            return res

    # -------------------------------------------------------------------------
    # ADAPTIVE WEIGHTS & REGIME LOGS
    # -------------------------------------------------------------------------
    def save_adaptive_weights(self, stream: str, weights: Dict, summary: Optional[Dict] = None):
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO adaptive_weights (stream, weights_json, calibrated_at, performance_summary_json)
                VALUES (?, ?, ?, ?)
            """, (stream, json.dumps(weights), now_str, json.dumps(summary or {})))

    def load_adaptive_weights(self, stream: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT weights_json FROM adaptive_weights WHERE stream = ?", (stream,))
            row = cursor.fetchone()
            if row and row["weights_json"]:
                return json.loads(row["weights_json"])
        return None

    def record_market_regime(self, status: str, btc_trend: str, avg_funding: float, reasoning: str, details: Optional[Dict] = None):
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO market_regime (timestamp, regime_status, btc_trend, avg_funding_rate, reasoning, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now_str, status, btc_trend, avg_funding, reasoning, json.dumps(details or {})))

    def get_latest_market_regime(self) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM market_regime ORDER BY timestamp DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    # -------------------------------------------------------------------------
    # ONE-TIME AUTOMATIC MIGRATION FROM FLAT JSON
    # -------------------------------------------------------------------------
    def auto_migrate_from_json(self) -> Dict[str, int]:
        """
        Migrates all existing JSON data into SQLite tables without data loss.
        """
        migrated = {"lifecycle": 0, "tags": 0, "journal": 0}

        # 1. Migrate positions_lifecycle.json
        life_file = os.path.join(ROOT_DIR, "data", "positions_lifecycle.json")
        if os.path.exists(life_file):
            try:
                with open(life_file, "r") as f:
                    data = json.load(f)
                if data:
                    self.save_lifecycle_state(data)
                    migrated["lifecycle"] = len(data)
            except Exception as e:
                print(f"[!] Gagal migrasi positions_lifecycle.json: {e}")

        # 2. Migrate position_tags.json
        tags_file = os.path.join(ROOT_DIR, "data", "position_tags.json")
        if os.path.exists(tags_file):
            try:
                with open(tags_file, "r") as f:
                    data = json.load(f)
                for coin, val in data.items():
                    self.save_position_tag(
                        coin=coin,
                        strategy=val.get("strategy", "SWING"),
                        plan=val.get("plan"),
                        meta=val.get("meta")
                    )
                migrated["tags"] = len(data)
            except Exception as e:
                print(f"[!] Gagal migrasi position_tags.json: {e}")

        # 3. Migrate forward_test_journal.json
        journal_file = os.path.join(ROOT_DIR, "data", "forward_test_journal.json")
        if os.path.exists(journal_file):
            try:
                with open(journal_file, "r") as f:
                    data = json.load(f)
                for key, val in data.items():
                    self.save_journal_position(val)
                migrated["journal"] = len(data)
            except Exception as e:
                print(f"[!] Gagal migrasi forward_test_journal.json: {e}")

        return migrated


# Default singleton instance
trade_db = TradeDatabase()
