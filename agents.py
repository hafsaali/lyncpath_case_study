"""
agents.py — LangChain agents for LyncPath D&D Prevention.

Agent 1 — Document Intelligence   (classify + extract + LFD calc)
Agent 2 — Milestone Tracker       (fetch + merge + trigger logic)
Agent 3 — Risk & Alerts           (risk + penalty + email/WA alerts)

Pipeline order: Agent 1 → Agent 2 → Agent 3
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain.agents import create_tool_calling_agent
from langchain.agents.agent import AgentExecutor
from pydantic import BaseModel, Field


# ── shared LLM ────────────────────────────────────────────────────────────────
def make_llm(api_key: str) -> ChatGroq:
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        groq_api_key=api_key,
        temperature=0.1,
    )


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 — Document Intelligence
# ══════════════════════════════════════════════════════════════════════════════

class ClassifyInput(BaseModel):
    ocr_text: str = Field(description="Full extracted text from the PDF")

class ExtractInput(BaseModel):
    ocr_text: str = Field(description="Full extracted text from the PDF")
    carrier: str  = Field(description="Carrier name identified during classification")

class LFDInput(BaseModel):
    vessel_arrival_at_pod: str      = Field(description="ISO8601 vessel ETA at final POD")
    carrier: str                    = Field(description="Carrier name")
    free_days_in_doc: Optional[int] = Field(default=None,
                                            description="Explicit free days in doc, or null")


@tool("classify_document", args_schema=ClassifyInput)
def classify_document(ocr_text: str) -> str:
    """Classify whether the text is a shipping booking confirmation or CRO."""
    keywords_booking = ["booking confirmation", "booking no", "booking number",
                        "cro", "container release"]
    keywords_carrier = {
        "maersk":      "Maersk",
        "swift flow":  "Swift Flow Shipping",
        "hapag":       "Hapag-Lloyd",
        "msc":         "MSC",
        "cma":         "CMA CGM",
        "evergreen":   "Evergreen",
        "cosco":       "COSCO",
        "dhl":         "DHL",
    }
    text_lower  = ocr_text.lower()
    is_relevant = any(kw in text_lower for kw in keywords_booking)
    carrier     = "UNKNOWN"
    for key, name in keywords_carrier.items():
        if key in text_lower:
            carrier = name
            break
    return json.dumps({
        "preliminary_is_relevant": is_relevant,
        "preliminary_carrier":     carrier,
        "instruction": (
            "Confirm or correct these values after reading the full text. "
            "Return JSON: document_type, carrier, is_relevant, confidence_note"
        )
    })


@tool("extract_booking_fields", args_schema=ExtractInput)
def extract_booking_fields(ocr_text: str, carrier: str) -> str:
    """Extract booking number, POD, vessel arrival, container number, and B/L from document text."""
    patterns = {
        "booking": [
            r"booking\s*no[.:]?\s*([A-Z0-9]{6,20})",
            r"confmn\s*#\s*[:\s]*([A-Z0-9]{6,30})",
            r"booking\s*number[:\s]+([A-Z0-9]{6,20})",
        ],
        "container": [
            r"container\s*(?:no|number|#)[.:\s]+([A-Z]{4}\d{7})",
            r"\b([A-Z]{4}\d{7})\b",
        ],
        "bol": [
            r"b(?:/|ill of )\s*l(?:ading)?[.:\s]+([A-Z0-9]{6,20})",
            r"bl\s*(?:no|number|#)?[.:\s]+([A-Z0-9]{6,20})",
        ],
        "pod": [
            r"port\s+of\s+discharge[:\s]+([^\n]+)",
            r"to[:\s]+([^\n,]+(?:italy|aden|spain|germany|usa|netherlands|china|india)[^\n]*)",
            r"pod[:\s]+([^\n]+)",
        ],
    }

    def first_match(pats):
        for p in pats:
            m = re.search(p, ocr_text, re.IGNORECASE)
            if m:
                return m.group(1).strip()[:80]
        return None

    free_days = None
    for p in [
        r"(\d+)\s*calendar\s*days?\s*(?:after|from)\s*(?:vessel\s*)?arrival",
        r"free\s*time[^\d]*(\d+)\s*days?",
        r"(\d+)\s*free\s*days?\s*at\s*destination",
    ]:
        m = re.search(p, ocr_text, re.IGNORECASE)
        if m:
            free_days = int(m.group(1))
            break

    return json.dumps({
        "regex_booking_number":   first_match(patterns["booking"]),
        "regex_container_number": first_match(patterns["container"]),
        "regex_bill_of_lading":   first_match(patterns["bol"]),
        "regex_pod_hint":         first_match(patterns["pod"]),
        "regex_free_days":        free_days,
        "carrier":                carrier,
        "instruction": (
            "Use these regex hints as starting points. Extract from the document: "
            "booking_number, container_number (if present), bill_of_lading (if present), "
            "shipper, pod (full name), pod_code (UN/LOCODE), "
            "vessel_arrival_at_pod (ISO8601 ETA at FINAL POD only), "
            "container_count (integer), container_type, commodity, "
            "free_days_explicitly_stated (integer or null). "
            "Default free days: Maersk SPOT = 5, others = 7 if not stated."
        )
    })


@tool("determine_lfd", args_schema=LFDInput)
def determine_lfd(vessel_arrival_at_pod: str, carrier: str,
                  free_days_in_doc: Optional[int] = None) -> str:
    """Calculate Last Free Date = vessel arrival at POD + free days."""
    defaults = {
        "Maersk": 5, "Swift Flow Shipping": 7, "Hapag-Lloyd": 6,
        "MSC": 7, "CMA CGM": 7, "DHL": 7, "UNKNOWN": 7,
    }
    free_days = free_days_in_doc if free_days_in_doc is not None else defaults.get(carrier, 7)
    source    = "explicitly stated in document" if free_days_in_doc else f"carrier default for {carrier}"
    try:
        arrival = datetime.fromisoformat(vessel_arrival_at_pod.replace("Z", "+00:00"))
        lfd     = arrival + timedelta(days=free_days)
        return json.dumps({
            "lfd":                    lfd.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "free_days_used":         free_days,
            "free_days_source":       source,
            "vessel_arrival_at_pod":  vessel_arrival_at_pod,
            "reasoning": (
                f"Vessel arrives at POD on {arrival.strftime('%Y-%m-%d')}. "
                f"Adding {free_days} free days ({source}) gives LFD = {lfd.strftime('%Y-%m-%d')}."
            )
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


AGENT1_SYSTEM = """You are an expert shipping document analyst for LyncPath.

Your job:
1. CLASSIFY — is this a booking confirmation or CRO? Which carrier?
2. EXTRACT  — booking number, container number (if present), B/L (if present),
   shipper, port of discharge, vessel arrival ETA at final POD
3. DETERMINE LFD — call determine_lfd tool last

CRITICAL:
- LFD = vessel arrival at FINAL POD + free days. Never use a transshipment ETA.
- Extract container_number and bill_of_lading if present — needed for live tracking.
- "warnings" MUST be a JSON array, never a plain string.

Final answer: JSON only, no prose. Keys:
document_type, carrier, is_relevant, booking_number, container_number,
bill_of_lading, shipper, pod, pod_code, vessel_arrival_at_pod,
lfd, free_days_used, lfd_reasoning, container_count, container_type, commodity, warnings"""

AGENT1_PROMPT = ChatPromptTemplate.from_messages([
    ("system", AGENT1_SYSTEM),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])
AGENT1_TOOLS = [classify_document, extract_booking_fields, determine_lfd]


def build_agent1(api_key: str) -> AgentExecutor:
    llm   = make_llm(api_key)
    agent = create_tool_calling_agent(llm, AGENT1_TOOLS, AGENT1_PROMPT)
    return AgentExecutor(agent=agent, tools=AGENT1_TOOLS, verbose=True,
                         max_iterations=6, handle_parsing_errors=True)


def run_agent1(executor: AgentExecutor, ocr_text: str) -> dict:
    result = executor.invoke({"input": f"""Analyse this shipping document.

--- DOCUMENT START ---
{ocr_text.strip()}
--- DOCUMENT END ---

Steps: classify_document → extract_booking_fields → determine_lfd → return JSON"""})
    return _safe_json(result.get("output", ""))


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 — Milestone Tracker
# (was Agent 3 — now renumbered to Agent 2 in pipeline order)
# ══════════════════════════════════════════════════════════════════════════════

class FetchTrackingInput(BaseModel):
    carrier: str
    booking_number: Optional[str]   = Field(default=None)
    container_number: Optional[str] = Field(default=None)
    bill_of_lading: Optional[str]   = Field(default=None)

class MergeInput(BaseModel):
    primary_result:  str = Field(description="JSON string from primary API call")
    fallback_result: str = Field(description="JSON string from fallback API call, or empty string")

class TriggerInput(BaseModel):
    milestones:   str = Field(description="JSON string of milestone list")
    lfd:          str = Field(description="Last Free Date ISO8601")
    current_time: str = Field(description="Current time ISO8601")


@tool("fetch_carrier_tracking", args_schema=FetchTrackingInput)
def fetch_carrier_tracking(
    carrier: str,
    booking_number: Optional[str]   = None,
    container_number: Optional[str] = None,
    bill_of_lading: Optional[str]   = None,
) -> str:
    """
    Fetch live milestone tracking using aggregator APIs.
    Terminal49 is tried first (all carriers); ShipsGo v2 is the fallback.
    Booking number is used as the primary identifier when available.
    """
    from tracking_clients import fetch_milestones
    result = fetch_milestones(
        carrier=carrier,
        container_number=container_number,
        booking_number=booking_number,
        bill_of_lading=bill_of_lading,
    )
    result.pop("raw", None)   # strip raw field to keep token count manageable
    return json.dumps(result)


@tool("merge_milestone_events", args_schema=MergeInput)
def merge_milestone_events(primary_result: str, fallback_result: str) -> str:
    """
    Merge milestones from two sources, deduplicating by name.
    Prefers 'complete' status over 'pending' for the same milestone.
    Returns a unified JSON list.
    """
    def _coerce(val) -> dict:
        """Accept a JSON string, a dict, or a list — always return a dict."""
        if not val:
            return {}
        if isinstance(val, dict):
            return val
        if isinstance(val, list):
            # LangChain sometimes passes the milestones list directly
            return {"milestones": val}
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return {"milestones": parsed}
                return parsed
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}

    try:
        primary  = _coerce(primary_result)
        fallback = _coerce(fallback_result)

        primary_ms  = primary.get("milestones", [])
        fallback_ms = fallback.get("milestones", [])
        lfd         = primary.get("lfd") or fallback.get("lfd")

        merged = {}
        for ms in primary_ms + fallback_ms:
            if not isinstance(ms, dict):
                continue
            name = ms.get("name", "")
            if name not in merged or ms.get("status") == "complete":
                merged[name] = ms

        return json.dumps({
            "milestones": list(merged.values()),
            "lfd":        lfd,
            "sources":    [primary.get("source"), fallback.get("source")],
        })
    except Exception as e:
        return json.dumps({"error": str(e), "milestones": []})


@tool("should_trigger_risk_assessment", args_schema=TriggerInput)
def should_trigger_risk_assessment(milestones: str, lfd: str, current_time: str) -> str:
    """
    Determine if D&D risk conditions are met and Agent 3 should be triggered.
    Returns JSON with trigger bool and reason.
    """
    try:
        ms_list    = json.loads(milestones)
        lfd_dt     = datetime.fromisoformat(lfd.replace("Z", "+00:00"))
        cur_dt     = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
        hours_left = (lfd_dt - cur_dt).total_seconds() / 3600

        vessel_arrived  = any(
            "arrived" in m.get("name", "").lower() and m.get("status") == "complete"
            for m in ms_list
        )
        customs_pending = any(
            "customs" in m.get("name", "").lower() and m.get("status") == "pending"
            for m in ms_list
        )
        overdue = hours_left < 0

        should_trigger = overdue or (vessel_arrived and customs_pending and hours_left < 96)

        reason = []
        if overdue:
            reason.append(f"LFD overdue by {abs(hours_left):.1f} hrs")
        if vessel_arrived and customs_pending:
            reason.append("vessel arrived but customs pending")
        if 0 <= hours_left < 96:
            reason.append(f"only {hours_left:.1f} hrs until LFD")

        return json.dumps({
            "should_trigger":  should_trigger,
            "reason":          ", ".join(reason) if reason else "no urgent conditions",
            "hours_until_lfd": round(hours_left, 1),
            "vessel_arrived":  vessel_arrived,
            "customs_pending": customs_pending,
        })
    except Exception as e:
        return json.dumps({"error": str(e), "should_trigger": True})


AGENT2_SYSTEM = """You are a milestone tracking agent for LyncPath.

Your job for each shipment:
1. Call fetch_carrier_tracking with the carrier, booking_number, container_number, and/or bill_of_lading
2. If a fallback source is available, call merge_milestone_events to combine both results
3. Call should_trigger_risk_assessment with the final milestones, lfd, and current_time
4. Return your final answer as JSON with these exact keys:
   milestones (list), lfd (string), current_time (string),
   sources_used (list), trigger_agent3 (bool), trigger_reason (string),
   hours_until_lfd (float), api_error (string or null)

If the API returns an error, still return the mock milestone data with api_error set.
Final answer: JSON only, no prose."""

AGENT2_PROMPT = ChatPromptTemplate.from_messages([
    ("system", AGENT2_SYSTEM),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])
AGENT2_TOOLS = [fetch_carrier_tracking, merge_milestone_events, should_trigger_risk_assessment]


def build_agent2(api_key: str) -> AgentExecutor:
    llm   = make_llm(api_key)
    agent = create_tool_calling_agent(llm, AGENT2_TOOLS, AGENT2_PROMPT)
    return AgentExecutor(agent=agent, tools=AGENT2_TOOLS, verbose=True,
                         max_iterations=6, handle_parsing_errors=True)


def run_agent2(executor: AgentExecutor, agent1_result: dict) -> dict:
    carrier          = agent1_result.get("carrier", "UNKNOWN")
    booking_number   = agent1_result.get("booking_number")
    container_number = agent1_result.get("container_number")
    bill_of_lading   = agent1_result.get("bill_of_lading")

    result = executor.invoke({"input": f"""Fetch live milestone tracking for this shipment.

Carrier:          {carrier}
Booking number:   {booking_number or 'not available'}
Container number: {container_number or 'not available — use booking number'}
Bill of lading:   {bill_of_lading or 'not available'}

Call fetch_carrier_tracking, then should_trigger_risk_assessment, then return JSON."""})
    return _safe_json(result.get("output", ""))


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — Risk & Alerts
# (was Agent 2 — now renumbered to Agent 3 in pipeline order)
# ══════════════════════════════════════════════════════════════════════════════

class RiskInput(BaseModel):
    lfd:          str = Field(description="Last Free Date ISO8601")
    current_time: str = Field(description="Current time ISO8601")
    milestones:   str = Field(description="JSON string of milestone list")

class PenaltyInput(BaseModel):
    time_to_fine_hours: float = Field(description="Hours until LFD expires")
    rate_card:          str   = Field(description="JSON string of D&D rate card")

class EmailInput(BaseModel):
    booking_number:        str
    pod:                   str
    lfd:                   str
    time_to_fine_hours:    float
    risk_level:            str
    projected_penalty_usd: int
    penalty_calculation:   str
    responsible_party:     str
    broker_email:          str
    broker_phone:          str
    container_count:       int
    recommended_actions:   str

class WhatsAppInput(BaseModel):
    booking_number:        str
    lfd:                   str
    time_to_fine_hours:    float
    risk_level:            str
    projected_penalty_usd: int
    broker_name:           str


@tool("calculate_risk", args_schema=RiskInput)
def calculate_risk(lfd: str, current_time: str, milestones: str) -> str:
    """Calculate time to fine and risk level from LFD and milestone status."""
    try:
        lfd_dt     = datetime.fromisoformat(lfd.replace("Z", "+00:00"))
        cur_dt     = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
        hours_left = (lfd_dt - cur_dt).total_seconds() / 3600
        ms_list    = json.loads(milestones) if isinstance(milestones, str) else milestones

        customs_done   = any("customs" in m.get("name","").lower()
                             and m.get("status") == "complete" for m in ms_list)
        vessel_arrived = any("arrived" in m.get("name","").lower()
                             and m.get("status") == "complete" for m in ms_list)

        if hours_left < 0:
            risk = "HIGH"
            just = f"LFD passed {abs(hours_left):.1f} hrs ago. Demurrage actively accruing."
        elif hours_left < 30:
            risk = "HIGH"
            just = f"Only {hours_left:.1f} hrs until LFD. Customs {'complete' if customs_done else 'PENDING'}."
        elif hours_left < 96:
            risk = "MEDIUM"
            just = f"{hours_left:.1f} hrs until LFD. Customs {'complete' if customs_done else 'pending'}."
        else:
            risk = "LOW"
            just = f"{hours_left:.1f} hrs until LFD. {'Vessel arrived.' if vessel_arrived else 'Vessel en route.'}"

        return json.dumps({
            "time_to_fine_hours":         round(hours_left, 1),
            "risk_level":                 risk,
            "risk_justification":         just,
            "customs_clearance_complete": customs_done,
            "vessel_arrived":             vessel_arrived,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("estimate_penalty", args_schema=PenaltyInput)
def estimate_penalty(time_to_fine_hours: float, rate_card: str) -> str:
    """Estimate projected D&D penalty from time to fine and rate card."""
    try:
        rc         = json.loads(rate_card) if isinstance(rate_card, str) else rate_card
        containers = rc.get("container_count", 1)
        dem_rate   = rc.get("demurrage_rate_per_container_per_day", 200)
        currency   = rc.get("currency", "USD")

        if time_to_fine_hours < 0:
            days, note = 3, "Already in demurrage — projecting 3 days"
        elif time_to_fine_hours < 24:
            days, note = 2, "HIGH risk — projecting 2 days if not cleared"
        elif time_to_fine_hours < 72:
            days, note = 1, "MEDIUM risk — projecting 1 day if delayed"
        else:
            days, note = 0, "LOW risk — no penalty projected"

        total    = dem_rate * containers * days
        calc_str = (f"{currency} {dem_rate}/day × {containers} containers"
                    f" × {days} days = {currency} {total:,}")
        return json.dumps({
            "projected_penalty_usd": total,
            "penalty_calculation":   calc_str,
            "days_at_risk":          days,
            "note":                  note,
            "currency":              currency,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("draft_email_alert", args_schema=EmailInput)
def draft_email_alert(booking_number, pod, lfd, time_to_fine_hours, risk_level,
                      projected_penalty_usd, penalty_calculation, responsible_party,
                      broker_email, broker_phone, container_count,
                      recommended_actions) -> str:
    """Draft a professional urgent email alert to the customs broker."""
    urgency = "URGENT: " if risk_level == "HIGH" else ""
    try:
        lfd_display = datetime.fromisoformat(
            lfd.replace("Z", "+00:00")).strftime("%d %B %Y at %H:%M UTC")
    except Exception:
        lfd_display = lfd
    try:
        actions = (json.loads(recommended_actions)
                   if isinstance(recommended_actions, str) else recommended_actions)
        actions_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(actions))
    except Exception:
        actions_text = f"  1. {recommended_actions}"

    hours_str = (f"{abs(time_to_fine_hours):.1f} hours AGO (OVERDUE)"
                 if time_to_fine_hours < 0 else f"{time_to_fine_hours:.1f} hours")

    body = f"""Dear Team,

Automated D&D alert from LyncPath for booking {booking_number}.

RISK LEVEL: {risk_level}

─── Shipment Summary ────────────────────────────
Booking Number   : {booking_number}
Port of Discharge: {pod}
Containers       : {container_count}
Last Free Date   : {lfd_display}
Time Remaining   : {hours_str}

─── Penalty Exposure ────────────────────────────
Projected Penalty: USD {projected_penalty_usd:,}
Calculation      : {penalty_calculation}
Responsible Party: {responsible_party.replace('_', ' ').title()}

─── Required Actions ────────────────────────────
{actions_text}

─── Contact ─────────────────────────────────────
Broker Phone: {broker_phone}

Generated by LyncPath D&D Prevention Agent.
"""
    return json.dumps({
        "to":      broker_email,
        "subject": (f"{urgency}D&D Alert — Booking {booking_number} "
                    f"— LFD in {hours_str} — {pod}"),
        "body":    body,
    })


@tool("draft_whatsapp_alert", args_schema=WhatsAppInput)
def draft_whatsapp_alert(booking_number, lfd, time_to_fine_hours, risk_level,
                         projected_penalty_usd, broker_name) -> str:
    """Draft a short WhatsApp/SMS alert to the customs broker."""
    try:
        lfd_display = datetime.fromisoformat(
            lfd.replace("Z", "+00:00")).strftime("%d %b %Y %H:%M UTC")
    except Exception:
        lfd_display = lfd
    emoji    = "🔴" if risk_level == "HIGH" else "🟡" if risk_level == "MEDIUM" else "🟢"
    time_str = (f"LFD OVERDUE by {abs(time_to_fine_hours):.0f}hrs"
                if time_to_fine_hours < 0
                else f"LFD in {time_to_fine_hours:.0f}hrs ({lfd_display})")
    msg = (f"{emoji} *D&D Alert — {risk_level} RISK*\n"
           f"Booking: *{booking_number}*\n"
           f"{time_str}\n"
           f"Penalty exposure: *USD {projected_penalty_usd:,}*\n"
           f"Action: Confirm customs clearance status immediately.")
    return json.dumps({"whatsapp_message": msg})


AGENT3_SYSTEM = """You are a D&D risk analyst for LyncPath.

Workflow — call tools in this exact order:
1. calculate_risk      — lfd, current_time, milestones from tracking payload
2. estimate_penalty    — time_to_fine_hours + dnd_rate_card
3. draft_email_alert   — complete email, no placeholders
4. draft_whatsapp_alert — short message

Final JSON keys (no prose outside JSON):
time_to_fine_hours, risk_level, risk_justification, responsible_party,
responsible_party_reasoning, projected_penalty_usd, penalty_calculation,
recommended_actions (list of 3), email_alert (to/subject/body), whatsapp_alert (string)"""

AGENT3_PROMPT = ChatPromptTemplate.from_messages([
    ("system", AGENT3_SYSTEM),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])
AGENT3_TOOLS = [calculate_risk, estimate_penalty, draft_email_alert, draft_whatsapp_alert]


def build_agent3(api_key: str) -> AgentExecutor:
    llm   = make_llm(api_key)
    agent = create_tool_calling_agent(llm, AGENT3_TOOLS, AGENT3_PROMPT)
    return AgentExecutor(agent=agent, tools=AGENT3_TOOLS, verbose=True,
                         max_iterations=8, handle_parsing_errors=True)


def run_agent3(executor: AgentExecutor, agent1_result: dict, tracking_payload: dict) -> dict:
    result = executor.invoke({"input": f"""Analyse shipment for D&D risk.

=== BOOKING DATA (Agent 1) ===
{json.dumps(agent1_result, indent=2)}

=== TRACKING PAYLOAD (Agent 2) ===
{json.dumps(tracking_payload, indent=2)}

Call all four tools then return JSON."""})
    return _safe_json(result.get("output", ""))


# ── Shared utility ─────────────────────────────────────────────────────────────
def _safe_json(text: str) -> dict:
    if not text:
        return {}
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            start = text.index("{")
            end   = text.rindex("}") + 1
            return json.loads(text[start:end])
        except Exception:
            return {"raw_output": text}