"""
ShortHunter Interactive AI War Room (Conversational Co-Pilot & Follow-Up Executor):
Allows the user to speak freely in natural language with the Multi-Agent Trading Committee.

4-Stage Agentic Workflow:
1. LISTEN & INVESTIGATE:
   - Listens to the user's opinion, hypothesis, or directive.
   - Automatically pulls live portfolio state, Thermostat settings, Episodic Memory rules,
     and runs live L2 Orderbook & Coin History tools for any coin mentioned.
2. REVIEW OPINION OBJECTIVELY:
   - Evaluates the user's opinion against hard quantitative data (L2 orderbook walls,
     funding rate, active exposure, and risk guardrails).
   - Determines agreement verdict:
     * AGREE_AND_FOLLOW_UP (AI agrees and proposes/executes concrete follow-up action)
     * PARTIAL_AGREE_REFINED (AI agrees with core idea but refines parameters for capital safety)
     * RESPECTFUL_DISAGREE (AI disagrees based on live data/risk and explains why)
     * DISCUSSION_INSIGHT (General market/portfolio Q&A without state mutation)
3. RESPOND NATURALLY:
   - Replies clearly in Indonesian as an institutional quant partner.
4. EXECUTE FOLLOW-UP (IF AI AGREES):
   - Executes approved follow-up actions directly (saving new Episodic Memory rules,
     adjusting Thermostat reserve/score/sizing parameters, tightening SL on-chain,
     closing a stagnant position, evaluating/executing a coin trade, or triggering Auto-Hunt).
"""

import os
import re
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

CHAT_HISTORY_FILE = os.path.join(ROOT_DIR, "data", "war_room_chat_history.json")


# =========================================================================
# 1. PYDANTIC SCHEMAS FOR OPINION REVIEW & FOLLOW-UP ACTION
# =========================================================================

class FollowUpActionSpec(BaseModel):
    action_type: Literal[
        "NONE",
        "ADD_EPISODIC_RULE",
        "UPDATE_THERMOSTAT",
        "PAUSE_OR_RESUME_AUTONOMOUS",
        "TIGHTEN_SL_ON_CHAIN",
        "CLOSE_ACTIVE_POSITION",
        "EVALUATE_COIN_SETUP",
        "TRIGGER_AUTO_HUNT",
        "RUN_GUARDIAN_AUDIT",
        "REVISIT_AND_SCALE_POSITIONS",
        "RUN_QUANT_VALIDATION"
    ] = Field(
        default="NONE",
        description="Concrete system follow-up action to execute if AI agrees with the user's opinion"
    )
    action_summary: str = Field(
        default="",
        description="Human-readable summary in Indonesian of what follow-up action will be performed"
    )
    rule_to_save: Optional[str] = Field(
        default=None,
        description="If action_type is ADD_EPISODIC_RULE, the concise trading rule in Indonesian to store in Episodic Memory"
    )
    target_coin: Optional[str] = Field(
        default=None,
        description="Target coin symbol (e.g. 'SOL', 'SUI', 'BTC') if action targets a specific coin"
    )
    target_side: Optional[Literal["SHORT", "LONG"]] = Field(
        default=None,
        description="Target trade side ('SHORT' or 'LONG') if evaluating or opening a coin"
    )
    target_stream: Optional[Literal["SWING", "DAYTRADE"]] = Field(
        default=None,
        description="Target strategy stream ('SWING' or 'DAYTRADE')"
    )
    sl_mode: Optional[Literal["BREAKEVEN", "TIGHT_50PCT"]] = Field(
        default=None,
        description="If action_type is TIGHTEN_SL_ON_CHAIN, whether to move SL to BREAKEVEN or TIGHT_50PCT"
    )
    autonomous_enabled: Optional[bool] = Field(
        default=None,
        description="If action_type is PAUSE_OR_RESUME_AUTONOMOUS, True to resume/enable or False to pause"
    )
    reserve_pct: Optional[float] = Field(
        default=None,
        description="If action_type is UPDATE_THERMOSTAT, new reserve_pct (between 10.0 and 50.0)"
    )
    min_score_intraday: Optional[float] = Field(
        default=None,
        description="If action_type is UPDATE_THERMOSTAT, new min_score_intraday (between 65.0 and 90.0)"
    )
    min_score_swing: Optional[float] = Field(
        default=None,
        description="If action_type is UPDATE_THERMOSTAT, new min_score_swing (between 65.0 and 90.0)"
    )
    sizing_mult: Optional[float] = Field(
        default=None,
        description="If action_type is UPDATE_THERMOSTAT, new sizing_mult (between 0.50 and 1.35)"
    )


class AntigravityDevTicket(BaseModel):
    has_dev_request: bool = Field(
        default=False,
        description="True if the user's message or the AI review identifies a new code feature, UI improvement, strategy upgrade, or bug fix to forward to Antigravity (the Lead Architect & Coding Agent)"
    )
    category: Literal[
        "NONE",
        "NEW_FEATURE",
        "STRATEGY_LOGIC",
        "RISK_GUARDRAIL",
        "UI_UX_IMPROVEMENT",
        "BUG_OR_EDGE_CASE"
    ] = Field(
        default="NONE",
        description="Category of engineering ticket for Antigravity"
    )
    priority: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        default="MEDIUM",
        description="Priority level for Antigravity development"
    )
    ticket_title: str = Field(
        default="",
        description="Concise title in Indonesian for the development backlog ticket"
    )
    technical_spec_for_antigravity: str = Field(
        default="",
        description="Actionable technical specification in Indonesian from the Trading AI to Antigravity explaining what code/module to add or enhance"
    )


class WarRoomConsultation(BaseModel):
    user_opinion_summary: str = Field(
        description="1 sentence summary of what the user is proposing, thinking, or asking"
    )
    agreement_verdict: Literal[
        "AGREE_AND_FOLLOW_UP",
        "PARTIAL_AGREE_REFINED",
        "RESPECTFUL_DISAGREE",
        "DISCUSSION_INSIGHT"
    ] = Field(
        description="Whether the AI Committee agrees with the user's opinion after checking live data"
    )
    confidence_in_review: int = Field(
        ge=0, le=100,
        description="AI confidence score (0-100) in its assessment"
    )
    data_evidence_used: List[str] = Field(
        description="2-4 bullet points of real-time data/tools used to verify the user's opinion"
    )
    opinion_review: str = Field(
        description="Objective quantitative review of the user's opinion (strengths, blind spots, or risk considerations) in Indonesian"
    )
    ai_response: str = Field(
        description="Direct, natural conversational response to the user in Indonesian"
    )
    follow_up_action: FollowUpActionSpec = Field(
        description="Follow-up action to execute if agreement_verdict is AGREE_AND_FOLLOW_UP or PARTIAL_AGREE_REFINED"
    )
    antigravity_dev_ticket: AntigravityDevTicket = Field(
        default_factory=AntigravityDevTicket,
        description="Engineering ticket forwarded directly to Antigravity (the AI Coding Agent) if a new feature, code enhancement, or next-stage development is discussed"
    )


# =========================================================================
# 2. INTERACTIVE AI WAR ROOM ENGINE
# =========================================================================

class AgentWarRoom:
    """
    Conversational Co-Pilot & Follow-Up Execution Engine.
    """

    COMMON_TICKERS = {
        "BTC", "ETH", "SOL", "SUI", "XRP", "DOGE", "HYPE", "PEPE", "AVAX", "LINK",
        "ADA", "DOT", "NEAR", "APT", "ARB", "OP", "INJ", "TIA", "SEI", "WIF",
        "BONK", "FET", "RENDER", "TAO", "ONDO", "ENA", "PENDLE", "AAVE", "UNI", "BNB",
        "LTC", "BCH", "TRX", "TON", "ATOM", "FIL", "CRV", "LDO", "JUP", "PYTH"
    }

    def __init__(self):
        os.makedirs(os.path.join(ROOT_DIR, "data"), exist_ok=True)
        self.history_file = CHAT_HISTORY_FILE

    def load_chat_history(self) -> List[Dict]:
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data[-12:]
        except Exception:
            pass
        return []

    def append_chat_history(self, user_msg: str, consultation: Dict, exec_result: Optional[str] = None) -> Dict[str, Any]:
        history = self.load_chat_history()
        history.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user_msg,
            "verdict": consultation.get("agreement_verdict"),
            "opinion_review": consultation.get("opinion_review"),
            "ai_response": consultation.get("ai_response"),
            "follow_up_type": (consultation.get("follow_up_action") or {}).get("action_type", "NONE"),
            "execution_result": exec_result or "-",
            "antigravity_dev_ticket": consultation.get("antigravity_dev_ticket") or {}
        })
        try:
            tmp = self.history_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(history[-20:], f, indent=2)
            os.replace(tmp, self.history_file)
        except Exception:
            pass

        # Automatically capture Q&A into Antigravity Two-Way Bridge (JSONL + AGENTS.md + BACKLOG.md)
        try:
            from src.antigravity_bridge import antigravity_bridge
            return antigravity_bridge.record_qa_and_sync(user_msg, consultation, execution_result=exec_result or "NONE")
        except Exception:
            return {}

    def _extract_mentioned_coins(self, text: str, active_coins: List[str]) -> List[str]:
        """Detects any crypto ticker symbols mentioned in the user's message."""
        words = re.findall(r"\b[A-Za-z0-9]{2,10}\b", text.upper())
        stopwords = {
            "DAN", "YG", "YANG", "INI", "ITU", "ADA", "BISA", "KALAU", "KALO", "SAYA",
            "MAU", "BUAT", "UNTUK", "DARI", "KE", "DI", "JUGA", "LAGI", "SUDAH", "BELUM",
            "SHORT", "LONG", "SL", "TP", "TP1", "TP2", "BE", "AI", "BOT", "TOOLS", "TOOL",
            "MARKET", "PASAR", "KOIN", "COIN", "POSISI", "MODAL", "MARGIN", "PERSEN", "PCT",
            "JAM", "HARI", "MENIT", "SWING", "INTRADAY", "DAYTRADE", "SETUJU", "TIDAK", "JANGAN",
            "NAIK", "TURUN", "PUCUK", "BOTTOM", "UMPAN", "CEK", "TOLONG", "COBA", "GIMANA"
        }
        mentioned = []
        active_set = {c.upper() for c in active_coins}
        for w in words:
            if w in stopwords:
                continue
            if w in active_set or w in self.COMMON_TICKERS:
                if w not in mentioned:
                    mentioned.append(w)
        return mentioned[:3]

    def _gather_live_context_and_tools(self, user_text: str) -> Dict[str, Any]:
        """
        Pulls live portfolio state, Thermostat settings, Episodic Memory rules,
        Antigravity Bridge state, and invokes live L2 Orderbook & History tools for any mentioned coins.
        """
        from src.autonomous_engine import AutonomousEngine
        from src.antigravity_bridge import antigravity_bridge
        from src.ai_engine import (
            query_coin_history,
            inspect_orderbook_imbalance,
            check_portfolio_correlation,
            episodic_memory
        )

        auto_eng = AutonomousEngine()
        gov = auto_eng.get_live_governor_snapshot()
        state = auto_eng.load_state()

        # Load active positions details
        lifecycle_path = os.path.join(ROOT_DIR, "data", "positions_lifecycle.json")
        lifecycle = {}
        if os.path.exists(lifecycle_path):
            try:
                with open(lifecycle_path, "r") as f:
                    lifecycle = json.load(f)
            except Exception:
                pass

        active_positions_summary = []
        active_coin_names = []
        for c, s in lifecycle.items():
            m_used = float(s.get("margin_used", 0.0))
            if m_used > 0 and m_used < 3.0:
                continue
            active_coin_names.append(c)
            active_positions_summary.append({
                "coin": c,
                "side": s.get("side", "SHORT"),
                "strategy": s.get("strategy", "SWING"),
                "entry_px": s.get("entry_px"),
                "last_px": s.get("last_px"),
                "unrealized_pnl": s.get("unrealized_pnl", 0.0),
                "roe_pct": s.get("roe_pct", 0.0),
                "current_sl_px": s.get("current_sl_px"),
                "tp1_px": s.get("tp1_px"),
                "tp2_px": s.get("tp2_px"),
                "trimmed_10pct": s.get("trimmed_10pct", False)
            })

        # Check if user mentioned any specific coins
        mentioned_coins = self._extract_mentioned_coins(user_text, active_coin_names)
        coin_investigations = {}
        for mc in mentioned_coins:
            coin_investigations[mc] = {
                "history": query_coin_history(mc),
                "orderbook_l2": inspect_orderbook_imbalance(mc),
                "portfolio_correlation_short": check_portfolio_correlation(mc, "SHORT"),
                "portfolio_correlation_long": check_portfolio_correlation(mc, "LONG")
            }

        mem_rules = [
            f"[{r.get('id')}] ({r.get('stream')}/{r.get('side')}): {r.get('rule')}"
            for r in episodic_memory.load_memory().get("rules", [])[-8:]
        ]

        from src.profiles import PROFILES
        from src.portfolio_sizer import PortfolioSizer
        from src.spot_macro_vault import SpotMacroVault

        equity = float(gov.get("equity", 0.0))
        available = float(gov.get("available", 0.0))
        margin_used = float(gov.get("margin_used", 0.0))

        core_satellite = PortfolioSizer.calculate_allocation(equity)
        spot_audit = SpotMacroVault.get_spot_portfolio_audit()

        buffer_floor = equity * 0.10
        deployable_cash = max(0.0, available - buffer_floor)
        spot_invested = float(spot_audit.get("total_invested", 0.0))
        spot_budget = float(core_satellite["spot"]["budget_usd"])
        spot_remaining = max(0.0, spot_budget - spot_invested)

        capital_awareness = {
            "total_saldo_equity_usd": round(equity, 2),
            "total_saldo_available_usd": round(available, 2),
            "margin_used_usd": round(margin_used, 2),
            "free_margin_buffer_10pct_usd": round(buffer_floor, 2),
            "effective_deployable_cash_usd": round(deployable_cash, 2),
            "core_satellite_targets": {
                "spot_vault_55pct_usd": spot_budget,
                "spot_invested_usd": spot_invested,
                "spot_remaining_quota_usd": round(spot_remaining, 2),
                "spot_budget_per_coin_usd": core_satellite["spot"]["budget_per_coin"],
                "spot_3_tranches": {
                    "t1_scout_30pct": core_satellite["spot"]["tranche_1_scout_usd"],
                    "t2_pullback_40pct": core_satellite["spot"]["tranche_2_pullback_usd"],
                    "t3_breakout_30pct": core_satellite["spot"]["tranche_3_breakout_usd"]
                },
                "swing_25pct_usd": core_satellite["swing"]["budget_usd"],
                "swing_margin_per_slot_usd": core_satellite["swing"]["margin_per_slot"],
                "swing_slots_max": core_satellite["swing"]["max_slots"],
                "intraday_10pct_usd": core_satellite["intraday"]["budget_usd"],
                "intraday_margin_per_slot_usd": core_satellite["intraday"]["margin_per_slot"],
                "intraday_slots_max": core_satellite["intraday"]["max_slots"],
                "buffer_10pct_usd": core_satellite["buffer"]["budget_usd"]
            },
            "spot_holdings": spot_audit.get("holdings", []),
            "capital_constraint_status": (
                "ADEQUATE_LIQUIDITY" if deployable_cash >= 15.0
                else ("TIGHT_LIQUIDITY_USE_CAUTION" if deployable_cash > 0 else "ZERO_AVAILABLE_MARGIN")
            )
        }

        return {
            "live_capital_monitoring": capital_awareness,
            "portfolio_governor": {
                "equity_usdc": gov.get("equity"),
                "free_margin_usdc": gov.get("available"),
                "reserve_pct": gov.get("reserve_pct"),
                "reserve_usdc": gov.get("reserve_usd"),
                "free_above_reserve_usdc": gov.get("free_above_reserve"),
                "active_slots": f"{gov.get('active_count')}/{gov.get('max_positions')}",
                "long_count": gov.get("long_count"),
                "short_count": gov.get("short_count"),
                "can_open_new_position": gov.get("can_open_new_position"),
                "gate_summary": gov.get("gate_summary")
            },
            "strategy_profiles": {
                "SWING": {
                    "timeframe": PROFILES["SWING"]["timeframe"],
                    "sl_pct": PROFILES["SWING"]["sl_pct"],
                    "half_risk_step_pct": PROFILES["SWING"]["half_risk_step_pct"],
                    "early_be_pct": PROFILES["SWING"]["early_be_pct"],
                    "tp1_trim_pct": PROFILES["SWING"]["trim_pct"],
                    "tp2_pct": PROFILES["SWING"]["tp2_pct"],
                    "runner_pct": PROFILES["SWING"]["runner_pct"],
                    "max_hold_hours": PROFILES["SWING"]["max_hold_hours"],
                    "target_desc": PROFILES["SWING"]["target_desc"],
                },
                "DAYTRADE": {
                    "timeframe": PROFILES["DAYTRADE"]["timeframe"],
                    "sl_range_pct": f"{PROFILES['DAYTRADE']['sl_min_pct']}% - {PROFILES['DAYTRADE']['sl_max_pct']}% (default {PROFILES['DAYTRADE']['sl_pct']}%)",
                    "half_risk_step_r": PROFILES["DAYTRADE"]["half_risk_r"],
                    "early_be_r": PROFILES["DAYTRADE"]["early_be_r"],
                    "tp1_r": f"{PROFILES['DAYTRADE']['tp1_r']}R (~2.5% - 4.5% gerak harga / ~5% - 13.5% ROE)",
                    "tp2_r": f"{PROFILES['DAYTRADE']['tp2_r']}R (~5.0% - 9.0% gerak harga / ~10% - 27% ROE, hard cap 9.0%)",
                    "max_hold_hours": PROFILES["DAYTRADE"]["max_hold_hours"],
                    "target_desc": PROFILES["DAYTRADE"]["target_desc"],
                },
            },
            "thermostat": state.get("thermostat", {}),
            "autonomous_enabled": state.get("enabled", True),
            "quant_validation_schedule": {
                "forward_audit_interval_hours": state.get("forward_audit_interval_hours", 24),
                "backtest_interval_days": state.get("backtest_interval_days", 7),
                "last_validation_report": state.get("last_validation_report", {})
            },
            "active_positions": active_positions_summary,
            "mentioned_coins_tool_data": coin_investigations,
            "episodic_memory_rules": mem_rules,
            "antigravity_bridge_status": antigravity_bridge.get_context_for_trading_ai()
        }

    # ------------------------------------------------------------------
    # CONSULT & REVIEW USER OPINION
    # ------------------------------------------------------------------
    def consult_and_review(self, user_message: str) -> Dict[str, Any]:
        """
        Listens to the user's message, gathers live tools & portfolio context,
        reviews the user's opinion critically, and returns a structured response
        with an actionable follow-up if the AI Committee agrees.
        """
        from src.ai_engine import ai_advisor
        from src.guardian_agent import guardian_agent

        live_ctx = self._gather_live_context_and_tools(user_message)
        recent_chat = self.load_chat_history()[-5:]

        if not ai_advisor.is_available():
            return self._deterministic_consultation_fallback(user_message, live_ctx)

        from google.genai import types

        raw_prompt = f"""
You are the Interactive AI Quantitative Committee of ShortHunter (consisting of The Sniper, The Prosecutor, Chief Risk Officer, and System Guardian).
You are also directly linked via a 2-way bridge (`antigravity_bridge`) to **Antigravity** (the Lead AI Software Architect & Coding Agent that builds and upgrades ShortHunter's Python codebase).
The user (Fund Principal & Portfolio Owner) is speaking freely with you in the War Room.

=== CRITICAL CAPITAL & SIZING AWARENESS (TOTAL SALDO & SALDO AVAILABLE) ===
You MUST continuously monitor and strictly respect BOTH metrics provided in `live_capital_monitoring`:
1. TOTAL SALDO (Total Equity $E$): The master anchor for Core-Satellite target allocations:
   - Spot Vault: 55% of $E$ (Hold 1-24 Bulan, target 5 koin @ 11% $E$, Smart 3-Tranche DCA: 30% Scout, 40% Pullback, 30% Breakout).
   - Futures Swing 3D: 25% of $E$ (Max 5 slots @ 5% $E$ margin per trade, 2x leverage).
   - Futures Intraday: 10% of $E$ (Max 3 slots @ 3.33% $E$ margin per trade, 2x-3x leverage).
   - Free Margin Buffer: 10% of $E$ (Cadangan likuiditas kas aman).
2. SALDO AVAILABLE (Total Saldo Bebas / Free Margin $A$): The hard real-time execution constraint.
   - NEVER recommend or approve position sizing that exceeds `total_saldo_available_usd` or violates the 10% cash buffer floor (`free_margin_buffer_10pct_usd`).
   - If available margin is tight or partially deployed in floating positions, dynamically adjust and scale down position recommendations to fit within `effective_deployable_cash_usd`.
   - In your conversational response (`ai_response`) and opinion review (`opinion_review`), ALWAYS actively state and reference BOTH Total Saldo ($E$) and Saldo Available ($A$) so the user knows your position recommendations dynamically reflect their live wallet balance!

Your 5 core duties:
1. LISTEN carefully to the user's opinion, hypothesis, question, or instruction.
2. REVIEW their opinion objectively against the LIVE SYSTEM & MARKET DATA below:
   - Do NOT be a blind "yes-man". If the user's idea is supported by the orderbook, funding rate, and portfolio risk rules, AGREE (`AGREE_AND_FOLLOW_UP`).
   - If the user's idea has merit but needs safer parameters (e.g. user asks for 5% reserve or 80% sizing, refine it to safe bounds 15%-40% reserve), choose `PARTIAL_AGREE_REFINED`.
   - If the user's idea contradicts live L2 orderbook walls, creates dangerous directional over-exposure, or violates capital safety, choose `RESPECTFUL_DISAGREE` and explain the data clearly.
   - If the user is asking a question or discussing market conditions without proposing a rule/parameter/trade action, choose `DISCUSSION_INSIGHT`.
3. RESPOND naturally, warmly, and sharply in Indonesian (`ai_response`).
4. PROPOSE FOLLOW-UP ACTION (`follow_up_action`) if you agree (`AGREE_AND_FOLLOW_UP` or `PARTIAL_AGREE_REFINED`):
   - `ADD_EPISODIC_RULE`: When the user shares a strategic trading principle/rule and you agree it improves edge. Put the rule in `rule_to_save`.
   - `UPDATE_THERMOSTAT`: When the user suggests changing reserve cash %, minimum score thresholds, or sizing multiplier and you agree.
   - `PAUSE_OR_RESUME_AUTONOMOUS`: When the user wants to pause or resume autonomous hunting.
   - `TIGHTEN_SL_ON_CHAIN`: When the user suggests protecting/tightening SL on an open position (`target_coin`, `sl_mode` = "BREAKEVEN" or "TIGHT_50PCT").
   - `CLOSE_ACTIVE_POSITION`: When the user suggests closing a specific open position (`target_coin`) and data confirms it's best to exit.
   - `EVALUATE_COIN_SETUP`: When the user suggests shorting or longing a specific coin (`target_coin`, `target_side`, `target_stream`).
   - `TRIGGER_AUTO_HUNT`: When the user asks to scan/hunt the 4 quadrants right now.
   - `RUN_GUARDIAN_AUDIT`: When the user asks to check app health, security, or Git DLP.
   - `REVISIT_AND_SCALE_POSITIONS`: When the user asks to revisit/scale up active positions to current equity tier.
   - `RUN_QUANT_VALIDATION`: When the user asks to run a periodic walk-forward backtest or forward-test validation audit right now.
   - `NONE`: If `RESPECTFUL_DISAGREE` or `DISCUSSION_INSIGHT`.
5. FORWARD ENGINEERING SPEC TO ANTIGRAVITY (`antigravity_dev_ticket`):
   - If the user's message proposes a new code feature, UI/menu improvement, new indicator/API integration, or asks to tell Antigravity to build/fix something, set `has_dev_request = true`, choose `category` & `priority`, and write a clear `ticket_title` and `technical_spec_for_antigravity` so Antigravity can immediately code it in the next development cycle!

=== RECENT CONVERSATION HISTORY ===
{json.dumps(recent_chat, indent=2)}

=== LIVE PORTFOLIO, THERMOSTAT, ANTIGRAVITY BRIDGE & AUTONOMOUS TOOL DATA ===
{json.dumps(live_ctx, indent=2)}

=== USER'S MESSAGE / OPINION ===
"{user_message}"
"""
        sanitized_prompt = guardian_agent.sanitize_outbound_text(raw_prompt)

        try:
            response = ai_advisor._client.models.generate_content(
                model=ai_advisor.model_name,
                contents=sanitized_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=WarRoomConsultation,
                    temperature=0.2,
                )
            )
            res = json.loads(response.text)
            res["live_context_snapshot"] = live_ctx
            res["ai_generated"] = True
            return res
        except Exception as e:
            fallback = self._deterministic_consultation_fallback(user_message, live_ctx, err_note=str(e))
            return fallback

    def _deterministic_consultation_fallback(self, user_message: str, live_ctx: Dict, err_note: str = "") -> Dict:
        """
        Resilient deterministic reviewer if Gemini API is temporarily overloaded (503) or offline.
        Parses common user intents (reserve %, rules, coin checks, security audits) using live tools.
        """
        msg_lower = user_message.lower()
        gov = live_ctx.get("portfolio_governor", {})
        mentioned = list((live_ctx.get("mentioned_coins_tool_data") or {}).keys())

        cap_mon = live_ctx.get("live_capital_monitoring", {})
        eq_val = cap_mon.get("total_saldo_equity_usd", gov.get("equity_usdc", 0))
        avail_val = cap_mon.get("total_saldo_available_usd", gov.get("free_margin_usdc", 0))
        deploy_val = cap_mon.get("effective_deployable_cash_usd", 0)

        evidence = [
            f"Total Saldo (Equity): ${eq_val:,.2f} USDC | Saldo Available: ${avail_val:,.2f} USDC (Siap Tembak di atas Buffer: ${deploy_val:,.2f} USDC)",
            f"Posisi Aktif: {gov.get('active_slots')} ({gov.get('long_count', 0)} Long vs {gov.get('short_count', 0)} Short)"
        ]
        for mc in mentioned:
            ob = live_ctx["mentioned_coins_tool_data"][mc].get("orderbook_l2", {})
            evidence.append(f"Orderbook L2 {mc}: Bid {ob.get('bid_share_pct', 50)}% vs Ask {ob.get('ask_share_pct', 50)}% ({ob.get('orderbook_pressure', 'BALANCED')})")

        # Detect intent heuristically
        follow_up = {
            "action_type": "NONE",
            "action_summary": "Tidak ada perubahan parameter sistem.",
            "rule_to_save": None,
            "target_coin": mentioned[0] if mentioned else None,
            "target_side": "SHORT" if "short" in msg_lower else ("LONG" if "long" in msg_lower else None),
            "target_stream": "DAYTRADE" if ("intraday" in msg_lower or "daytrade" in msg_lower) else "SWING",
            "sl_mode": None,
            "autonomous_enabled": None,
            "reserve_pct": None,
            "min_score_intraday": None,
            "min_score_swing": None,
            "sizing_mult": None
        }
        verdict = "DISCUSSION_INSIGHT"
        review_txt = "Komite mengevaluasi pendapat Anda menggunakan data portofolio & orderbook L2 lokal."
        resp_txt = (
            f"Saat ini ekuitas Anda ${gov.get('equity_usdc', 0):.2f} USDC dengan slot aktif {gov.get('active_slots')} "
            f"dan cadangan wajib {gov.get('reserve_pct', 15)}%."
        )

        # Check if user proposes saving a rule / principle
        if any(w in msg_lower for w in ["aturan", "jangan", "prinsip", "kalau", "jika", "rule", "sebaiknya"]):
            verdict = "AGREE_AND_FOLLOW_UP"
            follow_up["action_type"] = "ADD_EPISODIC_RULE"
            follow_up["rule_to_save"] = user_message.strip()[:180]
            follow_up["action_summary"] = f"Menyimpan aturan baru ke Episodic Memory Bank: '{follow_up['rule_to_save']}'"
            review_txt = "Pendapat strategis Anda selaras dengan disiplin manajemen risiko kuantitatif dan layak dijadikan aturan permanen di Episodic Memory."
            resp_txt = "Saya setuju dengan prinsip tersebut. Aturan ini siap saya simpan ke Episodic Memory Bank agar dipatuhi oleh Komite AI di setiap analisis berikutnya."

        # Check if user mentions a coin to evaluate
        if mentioned and any(w in msg_lower for w in ["cek", "short", "long", "entry", "bagus", "analisa", "analisis"]):
            mc = mentioned[0]
            side = follow_up["target_side"] or "SHORT"
            ob = live_ctx["mentioned_coins_tool_data"][mc].get("orderbook_l2", {})
            bid_p = float(ob.get("bid_share_pct", 50.0))
            ask_p = float(ob.get("ask_share_pct", 50.0))
            if (side == "SHORT" and bid_p >= 65.0) or (side == "LONG" and ask_p >= 65.0):
                verdict = "RESPECTFUL_DISAGREE"
                follow_up["action_type"] = "NONE"
                review_txt = f"Data Orderbook L2 {mc} saat ini menunjukkan ketimpangan berlawanan (Bid {bid_p:.1f}% vs Ask {ask_p:.1f}%), sehingga berisiko tinggi bila dipaksakan entry {side} sekarang."
                resp_txt = f"Setelah saya cek orderbook L2 {mc} secara real-time, saya kurang menyarankan entry {side} saat ini karena likuiditas lawan cukup tebal (Bid {bid_p:.1f}% / Ask {ask_p:.1f}%)."
            else:
                verdict = "AGREE_AND_FOLLOW_UP"
                follow_up["action_type"] = "EVALUATE_COIN_SETUP"
                follow_up["target_coin"] = mc
                follow_up["target_side"] = side
                follow_up["action_summary"] = f"Menjalankan Audit Komite 3-Agent lengkap untuk {side} {mc}"
                review_txt = f"Likuiditas Orderbook L2 {mc} (Bid {bid_p:.1f}% / Ask {ask_p:.1f}%) mendukung untuk dilakukan audit komite mendalam."
                resp_txt = f"Pendapat Anda menarik untuk diuji. Mari kita jalankan audit Komite 3-Agent (Sniper vs Prosecutor vs CRO Judge) pada {side} {mc}."

        return {
            "user_opinion_summary": user_message[:100],
            "agreement_verdict": verdict,
            "confidence_in_review": 82,
            "data_evidence_used": evidence,
            "opinion_review": review_txt,
            "ai_response": resp_txt,
            "follow_up_action": follow_up,
            "live_context_snapshot": live_ctx,
            "ai_generated": False
        }

    # ------------------------------------------------------------------
    # EXECUTE FOLLOW-UP ACTION WHEN AI AGREES
    # ------------------------------------------------------------------
    def execute_follow_up(self, action_spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the concrete follow-up action proposed by the AI Committee.
        Enforces hard safety bounds on all parameters so the system can never crash.
        """
        act_type = str(action_spec.get("action_type", "NONE")).upper()

        if act_type == "NONE":
            return {"executed": False, "message": "Tidak ada aksi perubahan sistem yang diperlukan."}

        # 1. ADD_EPISODIC_RULE
        if act_type == "ADD_EPISODIC_RULE":
            from src.ai_engine import episodic_memory
            rule_txt = (action_spec.get("rule_to_save") or action_spec.get("action_summary") or "").strip()
            if not rule_txt:
                return {"executed": False, "message": "Teks aturan kosong."}
            coin = str(action_spec.get("target_coin") or "GLOBAL").upper()
            side = str(action_spec.get("target_side") or "ALL").upper()
            stream = str(action_spec.get("target_stream") or "ALL").upper()
            episodic_memory.record_lesson_from_trade(
                {"coin": coin, "strategy": stream, "side": side},
                {"root_cause": "WAR_ROOM_CONSENSUS", "episodic_rule_to_remember": rule_txt}
            )
            return {
                "executed": True,
                "message": f"✅ Aturan berhasil disimpan ke Episodic Memory Bank ({stream}/{side} - {coin}): \"{rule_txt}\""
            }

        # 2. UPDATE_THERMOSTAT
        if act_type == "UPDATE_THERMOSTAT":
            from src.autonomous_engine import AutonomousEngine
            eng = AutonomousEngine()
            st = eng.load_state()
            th = st.get("thermostat", {})
            changes = []

            if action_spec.get("reserve_pct") is not None:
                val = round(max(10.0, min(50.0, float(action_spec["reserve_pct"]))), 1)
                th["reserve_pct"] = val
                changes.append(f"Cadangan Kas = {val}%")
            if action_spec.get("min_score_intraday") is not None:
                val = round(max(65.0, min(90.0, float(action_spec["min_score_intraday"]))), 1)
                th["min_score_intraday"] = val
                changes.append(f"Min Skor Intraday = {val}")
            if action_spec.get("min_score_swing") is not None:
                val = round(max(65.0, min(90.0, float(action_spec["min_score_swing"]))), 1)
                th["min_score_swing"] = val
                changes.append(f"Min Skor Swing = {val}")
            if action_spec.get("sizing_mult") is not None:
                val = round(max(0.50, min(1.35, float(action_spec["sizing_mult"]))), 2)
                th["sizing_mult"] = val
                changes.append(f"Sizing Multiplier = {val}x")

            if changes:
                th["reason"] = f"Disesuaikan melalui konsensus AI War Room ({', '.join(changes)})."
                st["thermostat"] = th
                eng.save_state(st)
                eng.log_telemetry("WAR_ROOM", "Update Thermostat", ", ".join(changes))
                return {
                    "executed": True,
                    "message": f"✅ Thermostat berhasil diperbarui: {', '.join(changes)}"
                }
            return {"executed": False, "message": "Tidak ada parameter Thermostat yang berubah."}

        # 3. PAUSE_OR_RESUME_AUTONOMOUS
        if act_type == "PAUSE_OR_RESUME_AUTONOMOUS":
            from src.autonomous_engine import AutonomousEngine
            eng = AutonomousEngine()
            st = eng.load_state()
            new_enabled = bool(action_spec.get("autonomous_enabled", not st.get("enabled", True)))
            st["enabled"] = new_enabled
            eng.save_state(st)
            status_lbl = "DIAKTIFKAN (Zero-Touch Aktif)" if new_enabled else "DIJEDA (Paused - Hanya Mengawal Posisi Aktif)"
            eng.log_telemetry("WAR_ROOM", "Toggle Autonomous Mode", status_lbl)
            return {
                "executed": True,
                "message": f"✅ Mode Autonomous Hunter berhasil diubah menjadi: {status_lbl}"
            }

        # 4. TIGHTEN_SL_ON_CHAIN
        if act_type == "TIGHTEN_SL_ON_CHAIN":
            from src.position_manager import PositionManager
            pm = PositionManager()
            lifecycle = pm.sync_active_positions()
            coin = str(action_spec.get("target_coin") or "").upper()
            sl_mode = str(action_spec.get("sl_mode") or "BREAKEVEN").upper()

            targets = [coin] if (coin and coin in lifecycle) else list(lifecycle.keys())
            if not targets:
                return {"executed": False, "message": f"Posisi aktif {coin or ''} tidak ditemukan untuk diperketat SL-nya."}

            all_mids = pm.trader.get_all_mids()
            acc = pm.trader.get_account_state()
            live_pos = {p["coin"]: p for p in acc.get("positions", [])}
            updated_coins = []

            for c in targets:
                if c not in live_pos:
                    continue
                s = lifecycle[c]
                pos_side = s.get("side", "SHORT")
                cur_sz = float(live_pos[c].get("size", s.get("initial_sz", 0)))
                entry = float(s.get("entry_px", 0))
                cur_sl = float(s.get("current_sl_px", entry))
                cur_px = float(all_mids.get(c, entry))

                if sl_mode == "BREAKEVEN":
                    fee_buf = 0.0009
                    new_sl = pm.trader.round_price(c, entry * (1 + fee_buf if pos_side == "LONG" else 1 - fee_buf))
                else:
                    new_sl = pm.trader.round_price(c, (cur_px + cur_sl) / 2.0)

                ok = pm._move_sl_on_chain(c, s, new_sl, cur_sz)
                if ok:
                    s["current_sl_px"] = new_sl
                    lifecycle[c] = s
                    updated_coins.append(f"{c} -> ${new_sl}")

            pm.save_lifecycle_state(lifecycle)
            if updated_coins:
                return {"executed": True, "message": f"✅ Stop Loss On-Chain berhasil diperketat ({sl_mode}): {', '.join(updated_coins)}"}
            return {"executed": False, "message": "SL on-chain sudah berada pada level optimal atau gagal dipindahkan."}

        # 5. CLOSE_ACTIVE_POSITION
        if act_type == "CLOSE_ACTIVE_POSITION":
            from src.trader import HyperliquidTrader
            coin = str(action_spec.get("target_coin") or "").upper()
            if not coin:
                return {"executed": False, "message": "Simbol koin yang akan ditutup belum ditentukan."}
            trader = HyperliquidTrader()
            res = trader.close_position(coin)
            if res.get("success"):
                return {"executed": True, "message": f"✅ Posisi {coin} berhasil ditutup secara penuh di Hyperliquid!"}
            return {"executed": False, "message": f"Gagal menutup posisi {coin}: {res.get('error')}"}

        # 6. EVALUATE_COIN_SETUP
        if act_type == "EVALUATE_COIN_SETUP":
            from src.ai_engine import ai_advisor, inspect_orderbook_imbalance
            coin = str(action_spec.get("target_coin") or "BTC").upper()
            side = str(action_spec.get("target_side") or "SHORT").upper()
            stream = str(action_spec.get("target_stream") or "SWING").upper()
            ob = inspect_orderbook_imbalance(coin)
            mid_px = float(ob.get("mid_px") or 0.0)
            audit = ai_advisor.evaluate_pre_trade_signal(
                {
                    "coin": coin,
                    "side": side,
                    "strategy": stream,
                    "score": 78.0,
                    "price": mid_px,
                    "verdict": "WAR_ROOM_USER_PROPOSAL"
                },
                {"strategy": stream, "side": side, "source": "WAR_ROOM"}
            )
            grade = audit.get("conviction_grade", "B")
            conf = audit.get("confidence_score", 75)
            app_str = "🟢 APPROVED" if audit.get("approve") else "🛑 VETOED / REJECTED"
            msg = (
                f"📋 Hasil Audit Komite 3-Agent untuk {side} {coin} ({stream} @ ${mid_px}):\n"
                f"     • Putusan Komite : {app_str} | Grade {grade} (Keyakinan {conf}/100 | Sizing {audit.get('size_multiplier', 1.0)}x)\n"
                f"     • 🎯 Sniper      : {audit.get('sniper_thesis', '-')}\n"
                f"     • ⚖️  Prosecutor  : {audit.get('prosecutor_critique', '-')}\n"
                f"     • 👨‍⚖️ CRO Judge   : {audit.get('verdict_reason', '-')}"
            )
            return {"executed": True, "message": msg, "audit_result": audit}

        # 7. TRIGGER_AUTO_HUNT
        if act_type == "TRIGGER_AUTO_HUNT":
            from src.autonomous_engine import AutonomousEngine
            eng = AutonomousEngine()
            res = eng.run_autonomous_hunt_if_due(force=True)
            return {
                "executed": True,
                "message": f"✅ Siklus Auto-Hunt 4-Kuadran selesai dijalankan! Status: {res.get('status', 'DONE')} ({res.get('msg') or res.get('reason') or ''})"
            }

        # 8. RUN_GUARDIAN_AUDIT
        if act_type == "RUN_GUARDIAN_AUDIT":
            from src.guardian_agent import guardian_agent
            rep = guardian_agent.run_full_health_and_security_audit(force=True)
            return {
                "executed": True,
                "message": (
                    f"✅ Audit Guardian Agent Selesai: {rep.get('status_badge')} | "
                    f"AST: {rep['codebase_ast']['checked_files']} file OK | "
                    f"SQLite: {rep['sqlite_wal']['status']} | "
                    f"Git DLP: {rep['git_security']['action_taken']}"
                )
            }

        # 9. REVISIT_AND_SCALE_POSITIONS
        if act_type == "REVISIT_AND_SCALE_POSITIONS":
            from src.autonomous_engine import AutonomousEngine
            eng = AutonomousEngine()
            coin = action_spec.get("target_coin")
            targets = [str(coin).upper()] if coin else None
            res = eng.revisit_and_scale_active_positions(target_coins=targets, execute_topup=True)
            scaled = res.get("scaled_up", [])
            if scaled:
                items = [f"{s['side']} {s['coin']} (${s['old_margin']:.2f} ➔ ${s['new_margin']:.2f})" for s in scaled]
                return {
                    "executed": True,
                    "message": f"✅ Revisit & Scale-Up Tier ${res.get('equity', 200):.0f} Selesai: {', '.join(items)} (SL/TP1 On-Chain disinkronkan!)"
                }
            return {
                "executed": True,
                "message": f"✅ Audit Revisit Selesai: Semua posisi yang memenuhi syarat zona entry sudah optimal untuk Tier ${res.get('equity', 200):.0f}."
            }

        # 10. RUN_QUANT_VALIDATION
        if act_type == "RUN_QUANT_VALIDATION":
            from src.autonomous_engine import AutonomousEngine
            eng = AutonomousEngine()
            rep = eng.run_periodic_backtest_and_forward_validation(
                mode="FULL",
                trigger_reason="WAR_ROOM_REQUEST",
                auto_apply=True
            )
            sw_bt = rep.get("swing_backtest") or {}
            dt_bt = rep.get("intraday_backtest") or {}
            cal = rep.get("tuning_applied") or []
            return {
                "executed": True,
                "message": (
                    f"✅ Walk-Forward Backtest & Forward-Test Validation Selesai:\n"
                    f"     • Swing BT    : {sw_bt.get('total_trades', 0)} trades | WR {sw_bt.get('win_rate_pct', 0)}% | PF {sw_bt.get('profit_factor', 0)} | Net {sw_bt.get('net_return_pct', 0):+.2f}% | Rek. Skor >= {sw_bt.get('optimal_min_score', 75.0)}\n"
                    f"     • Intraday BT : {dt_bt.get('total_trades', 0)} trades | WR {dt_bt.get('win_rate_pct', 0)}% | PF {dt_bt.get('profit_factor', 0)} | Avg {dt_bt.get('avg_r', 0):+.2f}R | Rek. Skor >= {dt_bt.get('optimal_min_score', 75.0)}\n"
                    f"     • Status Drift: {rep.get('drift_badge', 'ON-TRACK')} | Kalibrasi: {', '.join(cal)}"
                )
            }

        return {"executed": False, "message": f"Tipe aksi {act_type} tidak dikenali."}


war_room = AgentWarRoom()
