"""
agents.py — LangChain-based D&D Prevention Agents using Groq (free tier).

Agent 1: Document Intelligence
  Tools: classify_document, extract_booking_fields, determine_lfd

Agent 2: Agentic Reasoning
  Tools: calculate_risk, estimate_penalty, draft_email_alert, draft_whatsapp_alert
"""

import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain.agents import create_tool_calling_agent
from langchain.agents.agent import AgentExecutor
from pydantic import BaseModel, Field


# ── shared LLM factory ─────────────────────────────────────────────────────────
def make_llm(api_key: str) -> ChatGroq:
    return ChatGroq(
        model="llama-3.3-70b-versatile",
        groq_api_key=api_key,
        temperature=0.1,                    # low temp for consistent structured outputs
    )


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 — Document Intelligence
# ══════════════════════════════════════════════════════════════════════════════

# ── Tool schemas (pydantic) ────────────────────────────────────────────────────
class ClassifyInput(BaseModel):
    ocr_text: str = Field(description="Full OCR/extracted text from the PDF")


class ExtractInput(BaseModel):
    ocr_text: str = Field(description="Full OCR/extracted text from the PDF")
    carrier: str = Field(description="Carrier name identified during classification")


class LFDInput(BaseModel):
    vessel_arrival_at_pod: str = Field(description="ISO8601 vessel ETA at final POD")
    carrier: str = Field(description="Carrier name, used to look up standard free days")
    free_days_in_doc: Optional[int] = Field(
        default=None,
        description="Explicit free days mentioned in the document, or null"
    )


# ── Tools ──────────────────────────────────────────────────────────────────────
@tool("classify_document", args_schema=ClassifyInput)
def classify_document(ocr_text: str) -> str:
    """
    Classify whether the text is a shipping booking confirmation or CRO.
    Returns a JSON string with keys: document_type, carrier, is_relevant, confidence_note.
    """
    # This tool signals to the agent WHAT to look for — the LLM itself does the reasoning.
    keywords_booking = ["booking confirmation", "booking no", "booking number", "cro", "container release"]
    keywords_carrier = {
        "maersk": "Maersk",
        "swift flow": "Swift Flow Shipping",
        "hapag": "Hapag-Lloyd",
        "msc": "MSC",
        "cma": "CMA CGM",
        "evergreen": "Evergreen",
        "cosco": "COSCO",
    }
    text_lower = ocr_text.lower()
    is_relevant = any(kw in text_lower for kw in keywords_booking)
    carrier = "UNKNOWN"
    for key, name in keywords_carrier.items():
        if key in text_lower:
            carrier = name
            break
    return json.dumps({
        "preliminary_is_relevant": is_relevant,
        "preliminary_carrier": carrier,
        "instruction": (
            "Use this preliminary classification as a starting point. "
            "Read the full text carefully and confirm or correct these values. "
            "Return your final classification as JSON with keys: "
            "document_type, carrier, is_relevant, confidence_note"
        )
    })


@tool("extract_booking_fields", args_schema=ExtractInput)
def extract_booking_fields(ocr_text: str, carrier: str) -> str:
    """
    Extracts core booking fields from the document text.
    Returns a JSON hint with field locations to guide LLM extraction.
    """
    # Find booking number patterns
    booking_patterns = [
        r"booking\s*no[.:]?\s*([A-Z0-9]{6,20})",
        r"confmn\s*#\s*[:\s]*([A-Z0-9]{6,30})",
        r"booking\s*number[:\s]+([A-Z0-9]{6,20})",
    ]
    booking_number = None
    for pat in booking_patterns:
        m = re.search(pat, ocr_text, re.IGNORECASE)
        if m:
            booking_number = m.group(1).strip()
            break

    # Find POD patterns
    pod_patterns = [
        r"port\s+of\s+discharge[:\s]+([^\n]+)",
        r"to[:\s]+([^\n,]+(?:italy|aden|spain|germany|usa|netherlands)[^\n]*)",
        r"pod[:\s]+([^\n]+)",
    ]
    pod_hint = None
    for pat in pod_patterns:
        m = re.search(pat, ocr_text, re.IGNORECASE)
        if m:
            pod_hint = m.group(1).strip()[:80]
            break

    return json.dumps({
        "regex_booking_number": booking_number,
        "regex_pod_hint": pod_hint,
        "carrier": carrier,
        "instruction": (
            "Use the regex hints above as starting points. "
            "Extract ALL of the following from the document text and return as JSON: "
            "booking_number, shipper, pod (full name), pod_code (UN/LOCODE), "
            "vessel_arrival_at_pod (ISO8601 ETA at FINAL POD only — ignore transshipment ETAs), "
            "container_count (integer), container_type, commodity, "
            "free_days_explicitly_stated (integer or null). "
            "For Maersk SPOT: default free days = 5 if not stated. "
            "For Swift Flow / others: default = 7 if not stated."
        )
    })


@tool("determine_lfd", args_schema=LFDInput)
def determine_lfd(
    vessel_arrival_at_pod: str,
    carrier: str,
    free_days_in_doc: Optional[int] = None,
) -> str:
    """
    Calculates the Last Free Date (LFD) from vessel arrival + free days.
    Returns JSON with lfd, free_days_used, and reasoning.
    """
    # Standard free days by carrier
    carrier_free_days = {
        "Maersk": 5,
        "Swift Flow Shipping": 7,
        "Hapag-Lloyd": 6,
        "MSC": 7,
        "CMA CGM": 7,
        "UNKNOWN": 7,
    }

    free_days = free_days_in_doc
    if free_days is None:
        free_days = carrier_free_days.get(carrier, 7)
        source = f"carrier default for {carrier}"
    else:
        source = "explicitly stated in document"

    try:
        arrival = datetime.fromisoformat(vessel_arrival_at_pod.replace("Z", "+00:00"))
        lfd = arrival + timedelta(days=free_days)
        return json.dumps({
            "lfd": lfd.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "free_days_used": free_days,
            "free_days_source": source,
            "vessel_arrival_at_pod": vessel_arrival_at_pod,
            "reasoning": (
                f"Vessel arrives at POD on {arrival.strftime('%Y-%m-%d')}. "
                f"Adding {free_days} free days ({source}) gives LFD = {lfd.strftime('%Y-%m-%d')}. "
                f"This is the last day containers can remain at terminal without incurring demurrage."
            )
        })
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "instruction": "Could not parse vessel_arrival_at_pod. Please re-check the date format."
        })


# ── Agent 1 builder ────────────────────────────────────────────────────────────
AGENT1_SYSTEM = """You are an expert shipping document analyst for LyncPath, a container logistics platform.

Your job is to analyse a PDF-extracted text from a shipping document and:
1. CLASSIFY whether it is a booking confirmation or CRO
2. EXTRACT key fields: booking number, shipper, port of discharge, vessel arrival ETA at final POD
3. DETERMINE the Last Free Date (LFD) using the determine_lfd tool

CRITICAL rules:
- The LFD is NEVER the vessel ETA. It is: vessel arrival at final POD + free days.
- If the document has multiple ETAs (transshipments), use ONLY the final destination ETA.
- Always call determine_lfd as your last step after extracting the vessel arrival date.
- Return your FINAL answer as a JSON object with these exact keys:
  document_type, carrier, is_relevant, booking_number, shipper, pod, pod_code,
  vessel_arrival_at_pod, lfd, free_days_used, lfd_reasoning,
  container_count, container_type, commodity, warnings
- "warnings" MUST always be a JSON array e.g. [] or ["some warning"]. Never a plain string.

Do not include any text outside the final JSON object in your final answer."""

AGENT1_PROMPT = ChatPromptTemplate.from_messages([
    ("system", AGENT1_SYSTEM),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

AGENT1_TOOLS = [classify_document, extract_booking_fields, determine_lfd]


def build_agent1(api_key: str) -> AgentExecutor:
    llm = make_llm(api_key)
    agent = create_tool_calling_agent(llm, AGENT1_TOOLS, AGENT1_PROMPT)
    return AgentExecutor(
        agent=agent,
        tools=AGENT1_TOOLS,
        verbose=True,
        max_iterations=6,
        handle_parsing_errors=True,
    )


def run_agent1(agent_executor: AgentExecutor, ocr_text: str) -> dict:
    """Run Agent 1 and return parsed result dict."""
    user_msg = f"""Analyse this shipping document text and extract all required fields.

--- DOCUMENT TEXT START ---
{ocr_text.strip()}
--- DOCUMENT TEXT END ---

Follow these steps in order:
1. Call classify_document to identify the document type and carrier
2. Call extract_booking_fields to pull the core fields
3. Call determine_lfd with the vessel arrival at final POD
4. Return your final JSON answer
"""
    result = agent_executor.invoke({"input": user_msg})
    output = result.get("output", "")
    return _safe_json(output)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 — Agentic Reasoning
# ══════════════════════════════════════════════════════════════════════════════

class RiskInput(BaseModel):
    lfd: str = Field(description="Last Free Date in ISO8601 format")
    current_time: str = Field(description="Current timestamp in ISO8601 format")
    milestones: str = Field(description="JSON string of milestone list")


class PenaltyInput(BaseModel):
    time_to_fine_hours: float = Field(description="Hours until LFD expires")
    rate_card: str = Field(description="JSON string of the D&D rate card")


class EmailInput(BaseModel):
    booking_number: str
    pod: str
    lfd: str
    time_to_fine_hours: float
    risk_level: str
    projected_penalty_usd: int
    penalty_calculation: str
    responsible_party: str
    broker_email: str
    broker_phone: str
    container_count: int
    recommended_actions: str = Field(description="JSON list of recommended actions")


class WhatsAppInput(BaseModel):
    booking_number: str
    lfd: str
    time_to_fine_hours: float
    risk_level: str
    projected_penalty_usd: int
    broker_name: str


@tool("calculate_risk", args_schema=RiskInput)
def calculate_risk(lfd: str, current_time: str, milestones: str) -> str:
    """
    Calculate time to fine and classify risk level based on LFD and milestone status.
    Returns JSON with time_to_fine_hours, risk_level, and risk_justification.
    """
    try:
        lfd_dt = datetime.fromisoformat(lfd.replace("Z", "+00:00"))
        cur_dt = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
        hours_left = (lfd_dt - cur_dt).total_seconds() / 3600

        ms_list = json.loads(milestones) if isinstance(milestones, str) else milestones
        customs_done = any(
            ms.get("name", "").lower().startswith("customs") and ms.get("status") == "complete"
            for ms in ms_list
        )
        vessel_arrived = any(
            "arrived" in ms.get("name", "").lower() and ms.get("status") == "complete"
            for ms in ms_list
        )

        if hours_left < 0:
            risk = "HIGH"
            justification = f"LFD has ALREADY PASSED by {abs(hours_left):.1f} hours. Demurrage is actively accruing."
        elif hours_left < 30:
            risk = "HIGH"
            justification = (
                f"Only {hours_left:.1f} hours remain until LFD. "
                f"Customs clearance is {'complete' if customs_done else 'PENDING'}. "
                "Immediate action required."
            )
        elif hours_left < 96:
            risk = "MEDIUM"
            justification = (
                f"{hours_left:.1f} hours until LFD. "
                f"Customs clearance is {'complete' if customs_done else 'pending — needs monitoring'}. "
                "Escalate if no progress within 24 hours."
            )
        else:
            risk = "LOW"
            justification = (
                f"{hours_left:.1f} hours until LFD. "
                f"Vessel {'has arrived' if vessel_arrived else 'en route'}. "
                "No immediate action needed but monitor customs clearance."
            )

        return json.dumps({
            "time_to_fine_hours": round(hours_left, 1),
            "risk_level": risk,
            "risk_justification": justification,
            "customs_clearance_complete": customs_done,
            "vessel_arrived": vessel_arrived,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("estimate_penalty", args_schema=PenaltyInput)
def estimate_penalty(time_to_fine_hours: float, rate_card: str) -> str:
    """
    Estimate projected D&D penalty based on time to fine and rate card.
    Returns JSON with projected_penalty_usd and penalty_calculation string.
    """
    try:
        rc = json.loads(rate_card) if isinstance(rate_card, str) else rate_card
        containers = rc.get("container_count", 1)
        dem_rate = rc.get("demurrage_rate_per_container_per_day", 200)
        currency = rc.get("currency", "USD")

        # Estimate days at risk = days remaining until fine + buffer
        if time_to_fine_hours < 0:
            # Already in demurrage — 3-day exposure estimate
            days_at_risk = 3
            note = "Already in demurrage, projecting 3 days exposure"
        elif time_to_fine_hours < 24:
            days_at_risk = 2
            note = "High risk — projecting 2 days demurrage if not cleared"
        elif time_to_fine_hours < 72:
            days_at_risk = 1
            note = "Medium risk — projecting 1 day demurrage if delayed"
        else:
            days_at_risk = 0
            note = "Low risk — no penalty projected if on schedule"

        total = dem_rate * containers * days_at_risk
        calc_str = f"{currency} {dem_rate}/day × {containers} containers × {days_at_risk} days = {currency} {total:,}"

        return json.dumps({
            "projected_penalty_usd": total,
            "penalty_calculation": calc_str,
            "days_at_risk": days_at_risk,
            "note": note,
            "currency": currency,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool("draft_email_alert", args_schema=EmailInput)
def draft_email_alert(
    booking_number: str,
    pod: str,
    lfd: str,
    time_to_fine_hours: float,
    risk_level: str,
    projected_penalty_usd: int,
    penalty_calculation: str,
    responsible_party: str,
    broker_email: str,
    broker_phone: str,
    container_count: int,
    recommended_actions: str,
) -> str:
    """
    Draft a professional urgent email alert to the customs broker.
    Returns JSON with to, subject, and body fields.
    """
    urgency = "URGENT: " if risk_level == "HIGH" else ""
    try:
        lfd_dt = datetime.fromisoformat(lfd.replace("Z", "+00:00"))
        lfd_display = lfd_dt.strftime("%d %B %Y at %H:%M UTC")
    except Exception:
        lfd_display = lfd

    try:
        actions = json.loads(recommended_actions) if isinstance(recommended_actions, str) else recommended_actions
        actions_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(actions))
    except Exception:
        actions_text = f"  1. {recommended_actions}"

    hours_str = (
        f"{abs(time_to_fine_hours):.1f} hours AGO (OVERDUE)"
        if time_to_fine_hours < 0
        else f"{time_to_fine_hours:.1f} hours"
    )

    subject = f"{urgency}D&D Alert — Booking {booking_number} — LFD in {hours_str} — {pod}"

    body = f"""Dear Team,

This is an automated alert from LyncPath regarding shipment booking {booking_number}.

RISK LEVEL: {risk_level}

─── Shipment Summary ───────────────────────────────────
Booking Number : {booking_number}
Port of Discharge : {pod}
Containers : {container_count}
Last Free Date (LFD) : {lfd_display}
Time Remaining : {hours_str}

─── Penalty Exposure ────────────────────────────────────
Projected Penalty : USD {projected_penalty_usd:,}
Calculation : {penalty_calculation}
Responsible Party : {responsible_party.replace('_', ' ').title()}

─── Required Actions ────────────────────────────────────
{actions_text}

─── Terminal & Contact ──────────────────────────────────
Please contact the terminal directly if customs documents are ready.
Terminal contact reference is available in the LyncPath portal.

This alert was generated automatically by the LyncPath D&D Prevention Agent.
If you have already actioned this, please update the milestone in the portal.

Regards,
LyncPath Operations Alert System"""

    return json.dumps({
        "to": broker_email,
        "subject": subject,
        "body": body,
    })


@tool("draft_whatsapp_alert", args_schema=WhatsAppInput)
def draft_whatsapp_alert(
    booking_number: str,
    lfd: str,
    time_to_fine_hours: float,
    risk_level: str,
    projected_penalty_usd: int,
    broker_name: str,
) -> str:
    """
    Draft a short WhatsApp/SMS-style message for the customs broker.
    Returns the message as a plain string (JSON-wrapped).
    """
    try:
        lfd_dt = datetime.fromisoformat(lfd.replace("Z", "+00:00"))
        lfd_display = lfd_dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        lfd_display = lfd

    emoji = "🔴" if risk_level == "HIGH" else "🟡" if risk_level == "MEDIUM" else "🟢"

    if time_to_fine_hours < 0:
        time_str = f"LFD OVERDUE by {abs(time_to_fine_hours):.0f}hrs"
    else:
        time_str = f"LFD in {time_to_fine_hours:.0f}hrs ({lfd_display})"

    msg = (
        f"{emoji} *D&D Alert — {risk_level} RISK*\n"
        f"Booking: *{booking_number}*\n"
        f"{time_str}\n"
        f"Penalty exposure: *USD {projected_penalty_usd:,}*\n"
        f"Action needed: Please confirm customs clearance status immediately."
    )

    return json.dumps({"whatsapp_message": msg})


# ── Agent 2 builder ────────────────────────────────────────────────────────────
AGENT2_SYSTEM = """You are a D&D (Detention & Demurrage) risk analyst for LyncPath.

You receive structured data from a booking document (Agent 1 output) and a live tracking payload.

Your workflow — call tools in this order:
1. calculate_risk — using lfd, current_time, and milestones from the tracking payload
2. estimate_penalty — using time_to_fine_hours and the dnd_rate_card
3. draft_email_alert — build the full email (all fields must be filled, no placeholders)
4. draft_whatsapp_alert — build the short message

After calling all four tools, return your FINAL answer as a JSON object with these exact keys:
  time_to_fine_hours, risk_level, risk_justification, responsible_party,
  responsible_party_reasoning, projected_penalty_usd, penalty_calculation,
  recommended_actions (list of 3 strings), email_alert (object with to/subject/body),
  whatsapp_alert (string)

Determine responsible_party from the responsibility_map in the tracking payload.
Write recommended_actions as 3 specific, actionable steps tailored to the risk level.
Do not include any text outside the final JSON in your final answer."""

AGENT2_PROMPT = ChatPromptTemplate.from_messages([
    ("system", AGENT2_SYSTEM),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

AGENT2_TOOLS = [calculate_risk, estimate_penalty, draft_email_alert, draft_whatsapp_alert]


def build_agent2(api_key: str) -> AgentExecutor:
    llm = make_llm(api_key)
    agent = create_tool_calling_agent(llm, AGENT2_TOOLS, AGENT2_PROMPT)
    return AgentExecutor(
        agent=agent,
        tools=AGENT2_TOOLS,
        verbose=True,
        max_iterations=8,
        handle_parsing_errors=True,
    )


def run_agent2(agent_executor: AgentExecutor, agent1_result: dict, tracking_payload: dict) -> dict:
    """Run Agent 2 and return parsed result dict."""
    user_msg = f"""Analyse this shipment for D&D risk and draft alerts.

=== BOOKING DOCUMENT DATA (from Agent 1) ===
{json.dumps(agent1_result, indent=2)}

=== LIVE TRACKING PAYLOAD ===
{json.dumps(tracking_payload, indent=2)}

Call all four tools in order, then return your final JSON answer.
Use the lfd and current_time from the tracking payload (they may differ from the document).
"""
    result = agent_executor.invoke({"input": user_msg})
    output = result.get("output", "")
    return _safe_json(output)


# ── shared utility ─────────────────────────────────────────────────────────────
def _safe_json(text: str) -> dict:
    """Extract the first JSON object from a string that may contain extra prose."""
    if not text:
        return {}
    # strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # find outermost { }
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])
        except Exception:
            return {"raw_output": text}