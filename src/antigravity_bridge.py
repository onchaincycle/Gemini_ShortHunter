"""
ShortHunter <-> Antigravity Two-Way Communication Bridge & Q&A Logger:
Captures all Q&A conversations between the User and ShortHunter's AI Committee,
extracts actionable engineering/feature tickets for next-stage development, and
synchronizes a live two-way bridge (`data/antigravity_bridge.json`, `AGENTS.md`,
`BACKLOG.md`, and `logs/war_room_qa_transcript.jsonl`) so ShortHunter's Trading AI
and Antigravity (the AI Coding Assistant) communicate directly and seamlessly.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BRIDGE_JSON_PATH = os.path.join(ROOT_DIR, "data", "antigravity_bridge.json")
QA_JSONL_LOG_PATH = os.path.join(ROOT_DIR, "logs", "war_room_qa_transcript.jsonl")
AGENTS_MD_PATH = os.path.join(ROOT_DIR, "AGENTS.md")
BACKLOG_MD_PATH = os.path.join(ROOT_DIR, "BACKLOG.md")


class AntigravityBridge:
    """
    Manages the live two-way communication channel between:
      1. ShortHunter Trading AI (in-app Multi-Agent Committee & War Room)
      2. Antigravity Coding Agent (IDE Pair Programmer & System Architect)
    """

    def __init__(self):
        os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
        os.makedirs(os.path.join(ROOT_DIR, "logs"), exist_ok=True)
        self._ensure_initialized()

    def _default_bridge_state(self) -> Dict[str, Any]:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "bridge_version": "1.0",
            "last_synced_at": now_str,
            "total_qa_captured": 0,
            "antigravity_to_trading_ai": [
                {
                    "id": "AGY-MSG-001",
                    "timestamp": now_str,
                    "sender": "Antigravity (Lead Architect Agent)",
                    "status": "ACTIVE",
                    "summary": (
                        "Telah diaktifkan: (1) Multi-Agent Committee (Sniper vs Prosecutor vs CRO Judge), "
                        "(2) 3 Autonomous Live Tools (Coin History, L2 Orderbook, Portfolio Correlation), "
                        "(3) Episodic Memory Bank, (4) DevSecOps & Self-Healing Guardian Agent, dan "
                        "(5) AI War Room + Jembatan Komunikasi 2-Arah Antigravity."
                    )
                }
            ],
            "trading_ai_to_antigravity_tickets": [],
            "recent_qa_digest": []
        }

    def _ensure_initialized(self):
        if not os.path.exists(BRIDGE_JSON_PATH):
            self.save_bridge(self._default_bridge_state())
        if not os.path.exists(AGENTS_MD_PATH):
            self.sync_agents_md()

    def load_bridge(self) -> Dict[str, Any]:
        try:
            if os.path.exists(BRIDGE_JSON_PATH):
                with open(BRIDGE_JSON_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "recent_qa_digest" in data:
                    return data
        except Exception:
            pass
        return self._default_bridge_state()

    def save_bridge(self, data: Dict[str, Any]):
        try:
            tmp = BRIDGE_JSON_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, BRIDGE_JSON_PATH)
        except Exception:
            pass

    def record_qa_and_sync(
        self,
        user_message: str,
        consultation: Dict[str, Any],
        execution_result: str = "NONE"
    ) -> Dict[str, Any]:
        """
        1. Appends the full Q&A turn to `logs/war_room_qa_transcript.jsonl` (sanitized by Guardian DLP).
        2. Updates `data/antigravity_bridge.json` with the Q&A digest and any extracted Dev Ticket
           for Antigravity.
        3. Appends new feature/improvement tickets to `BACKLOG.md` if applicable.
        4. Re-generates `AGENTS.md` so Antigravity automatically sees the latest Q&A and tickets
           in its active workspace context.
        """
        from src.guardian_agent import guardian_agent

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_user = guardian_agent.sanitize_outbound_text(user_message)
        clean_review = guardian_agent.sanitize_outbound_text(consultation.get("opinion_review", ""))
        clean_response = guardian_agent.sanitize_outbound_text(consultation.get("ai_response", ""))
        verdict = consultation.get("agreement_verdict", "DISCUSSION_INSIGHT")
        follow_up = consultation.get("follow_up_action") or {}
        dev_ticket = consultation.get("antigravity_dev_ticket") or {}

        bridge = self.load_bridge()
        qa_id = f"QA-{int(bridge.get('total_qa_captured', 0)) + 1:04d}"

        qa_entry = {
            "qa_id": qa_id,
            "timestamp": now_str,
            "user_message": clean_user,
            "user_opinion_summary": guardian_agent.sanitize_outbound_text(
                consultation.get("user_opinion_summary", clean_user[:120])
            ),
            "agreement_verdict": verdict,
            "confidence": consultation.get("confidence_in_review", 80),
            "data_evidence_used": consultation.get("data_evidence_used", []),
            "opinion_review": clean_review,
            "ai_response": clean_response,
            "follow_up_action_type": follow_up.get("action_type", "NONE"),
            "follow_up_execution_result": execution_result,
            "antigravity_dev_ticket": dev_ticket
        }

        # 1. Append to JSONL transcript log
        try:
            with open(QA_JSONL_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(qa_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

        # 2. Update bridge state
        bridge["total_qa_captured"] = int(bridge.get("total_qa_captured", 0)) + 1
        bridge["last_synced_at"] = now_str

        digest = bridge.get("recent_qa_digest", [])
        digest.append({
            "qa_id": qa_id,
            "timestamp": now_str,
            "user": clean_user,
            "verdict": verdict,
            "ai_review_short": clean_review[:220],
            "ai_response_short": clean_response[:220],
            "follow_up": f"{follow_up.get('action_type', 'NONE')} -> {execution_result}"
        })
        bridge["recent_qa_digest"] = digest[-15:]  # Keep latest 15 Q&A turns in active bridge

        # 3. Check if there is a concrete development/feature insight for Antigravity
        has_dev_ticket = bool(dev_ticket.get("has_dev_request", False))
        ticket_record = None
        if has_dev_ticket and dev_ticket.get("ticket_title"):
            tickets = bridge.get("trading_ai_to_antigravity_tickets", [])
            t_id = f"DEV-{len(tickets) + 1:03d}"
            ticket_record = {
                "ticket_id": t_id,
                "created_at": now_str,
                "source_qa_id": qa_id,
                "status": "OPEN_FOR_ANTIGRAVITY",
                "category": dev_ticket.get("category", "FEATURE_ENHANCEMENT"),
                "priority": dev_ticket.get("priority", "MEDIUM"),
                "title": guardian_agent.sanitize_outbound_text(dev_ticket.get("ticket_title", "")),
                "technical_spec_for_antigravity": guardian_agent.sanitize_outbound_text(
                    dev_ticket.get("technical_spec_for_antigravity", "")
                ),
                "user_original_quote": clean_user[:200]
            }
            tickets.append(ticket_record)
            bridge["trading_ai_to_antigravity_tickets"] = tickets[-25:]
            self._append_ticket_to_backlog(ticket_record)

        self.save_bridge(bridge)

        # 4. Sync AGENTS.md so Antigravity automatically reads it in workspace context
        self.sync_agents_md()

        return {
            "qa_id": qa_id,
            "ticket_created": ticket_record
        }

    def post_message_from_antigravity(self, summary: str, status: str = "IMPLEMENTED") -> Dict[str, Any]:
        """
        Allows Antigravity (the coding agent) to post updates/messages directly to
        ShortHunter's Trading AI War Room and mark open tickets as resolved.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        bridge = self.load_bridge()
        outbox = bridge.get("antigravity_to_trading_ai", [])
        msg_id = f"AGY-MSG-{len(outbox) + 1:03d}"

        entry = {
            "id": msg_id,
            "timestamp": now_str,
            "sender": "Antigravity (Lead Architect Agent)",
            "status": status,
            "summary": summary
        }
        outbox.append(entry)
        bridge["antigravity_to_trading_ai"] = outbox[-10:]
        bridge["last_synced_at"] = now_str
        self.save_bridge(bridge)
        self.sync_agents_md()
        return entry

    def read_project_backlog_text(self) -> str:
        """Reads BACKLOG.md so the Trading AI inside the app can read the project backlog directly."""
        try:
            if os.path.exists(BACKLOG_MD_PATH):
                with open(BACKLOG_MD_PATH, "r", encoding="utf-8") as f:
                    return f.read().strip()[:8000]
        except Exception:
            pass
        return "BACKLOG.md belum tersedia."

    def get_context_for_trading_ai(self) -> Dict[str, Any]:
        """
        Direction 1 (Project -> Trading Tools):
        Provides the Trading AI in `agent_chat.py` with full visibility into:
        - `BACKLOG.md` (all project backlog items)
        - Active Python modules in `src/`
        - Latest messages/deployments from Antigravity
        - Open dev tickets sent to Antigravity
        """
        bridge = self.load_bridge()
        src_dir = os.path.join(ROOT_DIR, "src")
        src_modules = []
        if os.path.exists(src_dir):
            src_modules = sorted([f for f in os.listdir(src_dir) if f.endswith(".py") and not f.startswith("__")])

        return {
            "project_backlog_md": self.read_project_backlog_text(),
            "project_active_modules": src_modules,
            "messages_from_antigravity": bridge.get("antigravity_to_trading_ai", [])[-5:],
            "open_dev_tickets_sent_to_antigravity": [
                t for t in bridge.get("trading_ai_to_antigravity_tickets", [])
                if t.get("status") == "OPEN_FOR_ANTIGRAVITY"
            ][-6:]
        }

    def _append_ticket_to_backlog(self, ticket: Dict[str, Any]):
        """Appends a newly captured AI War Room development ticket to BACKLOG.md."""
        try:
            block = (
                f"\n### [{ticket['ticket_id']}] {ticket['title']} *(Captured via AI War Room {ticket['created_at']})*\n"
                f"* **Status:** `{ticket['status']}` | **Kategori:** `{ticket['category']}` | **Prioritas:** `{ticket['priority']}`\n"
                f"* **Kutipan User (`{ticket['source_qa_id']}`):** \"{ticket['user_original_quote']}\"\n"
                f"* **Spesifikasi Teknis untuk Antigravity:** {ticket['technical_spec_for_antigravity']}\n"
            )
            with open(BACKLOG_MD_PATH, "a", encoding="utf-8") as f:
                f.write(block)
        except Exception:
            pass

    def _read_trading_tools_live_summary(self) -> Dict[str, Any]:
        """
        Direction 2 Helper (Trading Tools -> Project/Antigravity):
        Reads live state from the trading tools (`autonomous_state.json`, `positions_lifecycle.json`,
        `episodic_memory.json`, `war_room_chat_history.json`) so Antigravity sees the exact runtime state.
        """
        summary: Dict[str, Any] = {}
        try:
            auto_path = os.path.join(ROOT_DIR, "data", "autonomous_state.json")
            if os.path.exists(auto_path):
                with open(auto_path, "r", encoding="utf-8") as f:
                    st = json.load(f)
                th = st.get("thermostat", {})
                summary["thermostat_mode"] = th.get("badge", th.get("mode", "OPTIMAL"))
                summary["reserve_pct"] = th.get("reserve_pct", 15.0)
                summary["last_hunt_time"] = st.get("last_hunt_time", "-")
                summary["last_hunt_status"] = st.get("last_hunt_status", "-")
        except Exception:
            pass

        try:
            pos_path = os.path.join(ROOT_DIR, "data", "positions_lifecycle.json")
            if os.path.exists(pos_path):
                with open(pos_path, "r", encoding="utf-8") as f:
                    pos = json.load(f)
                active_list = []
                for c, s in pos.items():
                    if float(s.get("margin_used", 0.0)) >= 3.0:
                        active_list.append(f"{s.get('side', 'SHORT')} {c} ({s.get('strategy', 'SWING')}, PnL: ${float(s.get('unrealized_pnl', 0.0)):+.2f})")
                summary["active_positions"] = active_list
        except Exception:
            pass

        try:
            mem_path = os.path.join(ROOT_DIR, "data", "episodic_memory.json")
            if os.path.exists(mem_path):
                with open(mem_path, "r", encoding="utf-8") as f:
                    mem = json.load(f)
                summary["episodic_rules"] = [
                    f"[{r.get('id')}] ({r.get('stream')}/{r.get('side')}): {r.get('rule')}"
                    for r in mem.get("rules", [])[-6:]
                ]
        except Exception:
            pass

        return summary

    def sync_agents_md(self):
        """
        Direction 2 (Trading Tools -> Project/Antigravity):
        Generates/Updates `AGENTS.md` in the workspace root.
        Antigravity automatically discovers and loads `AGENTS.md` on every turn,
        giving the Project/Antigravity live visibility into:
        1. Open Dev Tickets from Trading AI
        2. User <-> Trading AI Conversations (`war_room_chat_history.json` / `recent_qa_digest`)
        3. Live Trading Tool State (Active Positions, Thermostat, Episodic Memory Rules)
        4. Current Project `BACKLOG.md` Snapshot
        """
        bridge = self.load_bridge()
        tickets = bridge.get("trading_ai_to_antigravity_tickets", [])
        open_tickets = [t for t in tickets if t.get("status") == "OPEN_FOR_ANTIGRAVITY"]
        digest = list(bridge.get("recent_qa_digest", []))
        agy_msgs = bridge.get("antigravity_to_trading_ai", [])
        live_tools = self._read_trading_tools_live_summary()

        # Always merge with `war_room_chat_history.json` so any chat session is captured
        chat_path = os.path.join(ROOT_DIR, "data", "war_room_chat_history.json")
        if os.path.exists(chat_path):
            try:
                with open(chat_path, "r", encoding="utf-8") as f:
                    hist = json.load(f)
                existing_timestamps = {d.get("timestamp") for d in digest}
                for idx, h in enumerate(hist, 1):
                    ts = h.get("timestamp", "-")
                    if ts not in existing_timestamps:
                        digest.append({
                            "qa_id": f"CHAT-{idx:03d}",
                            "timestamp": ts,
                            "user": h.get("user", ""),
                            "verdict": h.get("verdict", "-"),
                            "ai_review_short": str(h.get("opinion_review", ""))[:220],
                            "ai_response_short": str(h.get("ai_response", ""))[:220],
                            "follow_up": f"{h.get('follow_up_type', 'NONE')} -> {h.get('execution_result', '-')}"
                        })
                digest.sort(key=lambda x: str(x.get("timestamp", "")))
            except Exception:
                pass

        now_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "# ShortHunter <-> Antigravity Live Two-Way Communication Bridge",
            "",
            "File ini disinkronkan secara otomatis oleh [`src/antigravity_bridge.py`](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/src/antigravity_bridge.py) setiap kali User berdiskusi dengan **AI War Room** di [`menu.py`](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/menu.py).",
            "- **Arah 1 (Tools Trading baca Project):** AI Trading membaca [`BACKLOG.md`](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/BACKLOG.md), daftar modul `src/`, dan pesan dari Antigravity via `get_context_for_trading_ai()`.",
            "- **Arah 2 (Project/Antigravity baca Tools Trading):** Antigravity otomatis membaca file `AGENTS.md` ini, [`logs/war_room_qa_transcript.jsonl`](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/logs/war_room_qa_transcript.jsonl), dan [`data/antigravity_bridge.json`](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/data/antigravity_bridge.json) di setiap giliran.",
            f"- **Terakhir Disinkronkan:** `{now_sync}` | **Total Q&A Tercatat:** `{len(digest)}`",
            "",
            "## 1. Tiket Pengembangan dari Trading AI untuk Antigravity (Open Dev Backlog)",
        ]

        if not open_tickets:
            lines.append("- *Belum ada tiket pengembangan baru yang menunggu eksekusi kode dari Antigravity (lihat `BACKLOG.md` untuk daftar backlog proyek).*")
        else:
            for t in reversed(open_tickets[-8:]):
                lines.append(
                    f"- **[{t.get('ticket_id')}] ({t.get('priority')} | {t.get('category')}) {t.get('title')}** — *[{t.get('created_at')}]*"
                )
                lines.append(f"  - **Input User (`{t.get('source_qa_id')}`):** \"{t.get('user_original_quote')}\"")
                lines.append(f"  - **Rekomendasi Arsitektur Trading AI ke Antigravity:** {t.get('technical_spec_for_antigravity')}")

        lines.append("")
        lines.append("## 2. Ringkasan Log Tanya-Jawab Terkini (User <-> Trading AI War Room)")
        if not digest:
            lines.append("- *Belum ada riwayat tanya-jawab baru.*")
        else:
            for q in reversed(digest[-8:]):
                lines.append(f"- **`[{q.get('qa_id')}]` ({q.get('timestamp')}) — Putusan AI: `{q.get('verdict')}`**")
                lines.append(f"  - **User:** \"{q.get('user')}\"")
                lines.append(f"  - **Review AI:** {q.get('ai_review_short')}")
                lines.append(f"  - **Respons AI:** \"{q.get('ai_response_short')}\"")
                lines.append(f"  - **Follow-Up:** `{q.get('follow_up')}`")

        lines.append("")
        lines.append("## 3. Snapshot Status Live Tools Trading Saat Ini (Dibaca Otomatis oleh Project)")
        lines.append(f"- **Thermostat & Cadangan Kas:** `{live_tools.get('thermostat_mode', '-')}` (Reserve: `{live_tools.get('reserve_pct', 15.0)}%`)")
        lines.append(f"- **Status Siklus Hunt Terakhir (`{live_tools.get('last_hunt_time', '-')}`):** {live_tools.get('last_hunt_status', '-')}")
        pos_list = live_tools.get("active_positions", [])
        lines.append(f"- **Posisi Aktif ({len(pos_list)}):** {', '.join(pos_list) if pos_list else 'Tidak ada posisi terbuka'}")
        ep_rules = live_tools.get("episodic_rules", [])
        if ep_rules:
            lines.append("- **Aturan Episodic Memory Aktif di Tools Trading:**")
            for er in ep_rules:
                lines.append(f"  - `{er}`")

        lines.append("")
        lines.append("## 4. Pesan & Update Terakhir dari Antigravity ke Trading AI")
        for m in reversed(agy_msgs[-4:]):
            lines.append(f"- **`[{m.get('id')}]` ({m.get('timestamp')} | `{m.get('status')}`):** {m.get('summary')}")

        lines.append("")
        lines.append("## 5. Instruksi Kerja untuk Antigravity")
        lines.append("- Saat User meminta pengembangan berikutnya atau bertanya tentang diskusi di aplikasi trading, selalu periksa bagian **Tiket Pengembangan**, **Log Tanya-Jawab**, dan **Snapshot Status Live Tools Trading** di atas (serta [`BACKLOG.md`](file:///Users/imansulaiman/Documents/Gemini_ShortHunter/BACKLOG.md)).")
        lines.append("- Setelah selesai mengimplementasikan fitur/perbaikan baru, panggil `antigravity_bridge.post_message_from_antigravity(...)` agar AI Trading di dalam aplikasi mengetahui pembaruan tersebut.")
        lines.append("")

        try:
            tmp = AGENTS_MD_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp, AGENTS_MD_PATH)
        except Exception:
            pass


antigravity_bridge = AntigravityBridge()

