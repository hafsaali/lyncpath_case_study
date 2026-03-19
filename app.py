"""
LyncPath D&D Prevention Agent MVP
Streamlit UI — Groq Llama 3.3 + LangChain agents + pdfplumber PDF extraction
"""

import os
import streamlit as st
import json
import io
import threading
from datetime import datetime, timezone, timedelta
from queue import Queue
from dotenv import load_dotenv

load_dotenv()

# Read API key — Streamlit Cloud uses st.secrets, local uses .env

from pdf_utils import extract_text_from_pdf, get_pdf_metadata
from agents import build_agent1, build_agent2, run_agent1, run_agent2, _safe_json
from data import MOCK_TRACKING_PAYLOAD

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LyncPath D&D Prevention Agent",
    page_icon="🚢",
    layout="wide",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

.agent-header {
    font-size: 0.72rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: #64748b; margin-bottom: 6px;
}
.pill {
    display: inline-block; padding: 2px 10px;
    border-radius: 99px; font-size: 0.7rem; font-weight: 600;
}
.pill-idle    { background:#f1f5f9; color:#64748b; }
.pill-running { background:#dbeafe; color:#1e40af; }
.pill-done    { background:#dcfce7; color:#166534; }
.pill-error   { background:#fee2e2; color:#991b1b; }

.risk-HIGH   { color:#dc2626; font-weight:800; font-size:1.4rem; }
.risk-MEDIUM { color:#d97706; font-weight:800; font-size:1.4rem; }
.risk-LOW    { color:#16a34a; font-weight:800; font-size:1.4rem; }

.metric-box {
    background:#f8fafc; border:1px solid #e2e8f0;
    border-radius:8px; padding:0.7rem 0.9rem; text-align:center;
}
.metric-label { font-size:0.68rem; color:#64748b; text-transform:uppercase; letter-spacing:0.06em; }
.metric-value { font-size:1.3rem; font-weight:700; color:#0f172a; margin-top:2px; }

.log-box {
    background:#0f172a; color:#94a3b8;
    border-radius:8px; padding:0.9rem 1rem;
    font-family:monospace; font-size:0.76rem;
    max-height:220px; overflow-y:auto;
    white-space:pre-wrap; line-height:1.5;
}
.field-row {
    display:flex; justify-content:space-between; align-items:flex-start;
    padding:6px 0; border-bottom:1px solid #f1f5f9; font-size:0.85rem; gap:1rem;
}
.field-label { color:#64748b; white-space:nowrap; }
.field-value { font-weight:600; color:#0f172a; text-align:right; }

.alert-email {
    background:#fff7ed; border-left:4px solid #f97316;
    border-radius:0 8px 8px 0; padding:1rem 1.2rem;
    font-family:monospace; font-size:0.8rem; color:#1e293b;
    white-space:pre-wrap; max-height:380px; overflow-y:auto;
}
.alert-whatsapp {
    background:#f0fdf4; border-left:4px solid #22c55e;
    border-radius:0 8px 8px 0; padding:1rem 1.2rem;
    font-family:monospace; font-size:0.85rem; color:#1e293b;
    white-space:pre-wrap;
}
.ocr-preview {
    background:#f8fafc; border:1px solid #e2e8f0;
    border-radius:8px; padding:0.75rem 1rem;
    font-family:monospace; font-size:0.75rem; color:#334155;
    max-height:200px; overflow-y:auto; white-space:pre-wrap;
}
.ms-row {
    display:flex; align-items:center; gap:10px;
    padding:4px 0; font-size:0.83rem;
}
.ms-done { color:#16a34a; }
.ms-pend { color:#dc2626; }
.section-title {
    font-size:0.92rem; font-weight:700; color:#0f172a; margin-bottom:0.6rem;
}
</style>
""", unsafe_allow_html=True)


# ── helpers ────────────────────────────────────────────────────────────────────
def pill(label: str, kind: str = "idle") -> str:
    return f'<span class="pill pill-{kind}">{label}</span>'


def safe_str(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✅ Yes" if v else "❌ No"
    return str(v)


def render_fields(data: dict, field_map: list[tuple]) -> str:
    rows = ""
    for label, key in field_map:
        val = safe_str(data.get(key))
        rows += (
            f'<div class="field-row">'
            f'<span class="field-label">{label}</span>'
            f'<span class="field-value">{val}</span>'
            f'</div>'
        )
    return rows


# ── session state init ─────────────────────────────────────────────────────────
for k, default in {
    "a1_status": "idle",
    "a2_status": "idle",
    "a1_result": None,
    "a2_result": None,
    "ocr_text": None,
    "ocr_warnings": [],
    "pdf_meta": {},
    "run_log": [],
}.items():
    if k not in st.session_state:
        st.session_state[k] = default


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state["run_log"].append(f"[{ts}] {msg}")


# ── sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚢 LyncPath")
    st.markdown("**D&D Prevention Agent**")
    st.markdown("---")

    # API key loaded from .env only — not exposed in UI
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        st.session_state["gemini_key"] = groq_key
    elif "gemini_key" not in st.session_state:
        st.session_state["gemini_key"] = ""

    if not st.session_state.get("gemini_key"):
        st.error("⚠️ GROQ_API_KEY not found. Add it to your .env file and restart.")
        st.stop()

    st.markdown("---")
    st.markdown("**Upload Booking Document**")
    uploaded_pdf = st.file_uploader(
        "Drop a PDF here",
        type=["pdf"],
        label_visibility="collapsed",
    )

    if uploaded_pdf:
        pdf_bytes = uploaded_pdf.read()
        with st.spinner("Extracting text from PDF…"):
            ocr_text, ocr_warnings = extract_text_from_pdf(pdf_bytes)
            pdf_meta = get_pdf_metadata(pdf_bytes)
        st.session_state["ocr_text"] = ocr_text
        st.session_state["ocr_warnings"] = ocr_warnings
        st.session_state["pdf_meta"] = pdf_meta
        st.success(f"✅ Extracted {len(ocr_text):,} characters from {pdf_meta.get('page_count', '?')} page(s)")
        if ocr_warnings:
            for w in ocr_warnings:
                st.warning(w)

    st.markdown("---")
    st.markdown("**Mock tracking scenario**")
    scenario = st.selectbox(
        "Urgency scenario",
        [
            "🔴 LFD in 24 hours — customs pending",
            "🟡 LFD in 72 hours — customs pending",
            "🟢 LFD in 5 days — all clear",
        ],
        label_visibility="collapsed",
    )

    st.markdown("---")
    run_btn = st.button(
        "▶  Run Pipeline",
        use_container_width=True,
        type="primary",
        disabled=not st.session_state.get("ocr_text"),
    )

    st.markdown("---")
    st.caption("Groq llama-3.3-70b · LangChain agents · pdfplumber extraction")


# ── build tracking payload based on scenario ───────────────────────────────────
payload = json.loads(json.dumps(MOCK_TRACKING_PAYLOAD))
now = datetime(2026, 4, 29, 10, 0, 0, tzinfo=timezone.utc)

if "24 hours" in scenario:
    payload["lfd"] = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["milestones"][9]["status"] = "pending"   # customs
    payload["current_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
elif "72 hours" in scenario:
    payload["lfd"] = (now + timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["milestones"][9]["status"] = "pending"
    payload["current_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
else:
    payload["lfd"] = (now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["milestones"][9]["status"] = "complete"
    payload["milestones"][9]["timestamp"] = (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["current_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

lfd_dt = datetime.fromisoformat(payload["lfd"].replace("Z", "+00:00"))
cur_dt = datetime.fromisoformat(payload["current_time"].replace("Z", "+00:00"))
hours_left = (lfd_dt - cur_dt).total_seconds() / 3600


# ── main layout ────────────────────────────────────────────────────────────────
st.markdown("# 🚢 LyncPath D&D Prevention Agent")
st.markdown(
    "AI pipeline: **PDF upload → text extraction → Agent 1 (document intelligence) "
    "→ Agent 2 (risk reasoning + alerts)**"
)
st.markdown("---")

col_left, col_right = st.columns([1, 1], gap="large")

# ═══════════════════════════════════════════════════════
# LEFT COLUMN — inputs
# ═══════════════════════════════════════════════════════
with col_left:

    # PDF / OCR preview
    st.markdown('<div class="section-title">📄 Extracted Document Text</div>', unsafe_allow_html=True)
    if st.session_state["ocr_text"]:
        meta = st.session_state["pdf_meta"]
        st.caption(
            f"Pages: {meta.get('page_count','?')} · "
            f"Characters: {len(st.session_state['ocr_text']):,} · "
            f"Creator: {meta.get('creator','—')}"
        )
        with st.expander("Show extracted text", expanded=False):
            st.markdown(
                f'<div class="ocr-preview">{st.session_state["ocr_text"][:3000]}</div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("Upload a PDF in the sidebar to begin.")

    # Tracking payload
    st.markdown('<div class="section-title" style="margin-top:1.2rem;">📡 Mock Tracking Payload</div>', unsafe_allow_html=True)

    # Milestone display
    for ms in payload["milestones"]:
        done = ms["status"] == "complete"
        icon = "✅" if done else "❌"
        cls = "ms-done" if done else "ms-pend"
        ts_str = ms.get("timestamp") or "pending"
        st.markdown(
            f'<div class="ms-row"><span class="{cls}">{icon}</span>'
            f'<span style="flex:1"><b>M{ms["id"]}:</b> {ms["name"]}</span>'
            f'<span style="color:#94a3b8;font-size:0.74rem;">{ts_str}</span></div>',
            unsafe_allow_html=True,
        )

    # LFD summary bar
    color = "#dc2626" if hours_left < 30 else "#d97706" if hours_left < 80 else "#16a34a"
    st.markdown(
        f'<div style="margin-top:0.75rem;padding:0.6rem 0.9rem;'
        f'background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;font-size:0.85rem;">'
        f'LFD: <b>{payload["lfd"]}</b> &nbsp;·&nbsp; '
        f'Time remaining: <b style="color:{color}">{hours_left:.1f} hrs</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.expander("View full tracking JSON", expanded=False):
        st.json(payload)

    # Run log
    if st.session_state["run_log"]:
        st.markdown('<div class="section-title" style="margin-top:1.2rem;">🖥 Agent Log</div>', unsafe_allow_html=True)
        log_text = "\n".join(st.session_state["run_log"])
        st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════
# RIGHT COLUMN — agent outputs
# ═══════════════════════════════════════════════════════
with col_right:

    # ── Agent 1 panel ─────────────────────────────────
    st.markdown(
        f'<div class="agent-header">Agent 1 — Document Intelligence &nbsp;'
        + pill(st.session_state["a1_status"], st.session_state["a1_status"])
        + '</div>',
        unsafe_allow_html=True,
    )

    a1_spinner = st.empty()
    a1_fields  = st.empty()

    if st.session_state["a1_result"]:
        a1 = st.session_state["a1_result"]
        fields_html = render_fields(a1, [
            ("Document type",       "document_type"),
            ("Carrier",             "carrier"),
            ("Relevant document?",  "is_relevant"),
            ("Booking number",      "booking_number"),
            ("Shipper",             "shipper"),
            ("Port of Discharge",   "pod"),
            ("Vessel arrival (POD)","vessel_arrival_at_pod"),
            ("Last Free Date",      "lfd"),
            ("Free days used",      "free_days_used"),
            ("Container count",     "container_count"),
            ("Container type",      "container_type"),
            ("Commodity",           "commodity"),
        ])
        a1_fields.markdown(fields_html, unsafe_allow_html=True)

        if a1.get("lfd_reasoning"):
            st.markdown(
                f'<div style="margin-top:0.5rem;background:#f0f9ff;border-left:3px solid #38bdf8;'
                f'border-radius:0 6px 6px 0;padding:0.6rem 0.9rem;font-size:0.8rem;color:#0c4a6e;">'
                f'💡 <b>LFD Reasoning:</b> {a1["lfd_reasoning"]}</div>',
                unsafe_allow_html=True,
            )
        warnings = a1.get("warnings")
        if warnings:
            # LLM sometimes returns a plain string instead of a list — normalise it
            if isinstance(warnings, str):
                warnings = [warnings] if warnings.strip() else []
            for w in warnings:
                if isinstance(w, str) and len(w) > 2:   # skip stray single chars
                    st.warning(f"⚠️ {w}")

    st.markdown("---")

    # ── Agent 2 panel ─────────────────────────────────
    st.markdown(
        f'<div class="agent-header">Agent 2 — Agentic Reasoning &nbsp;'
        + pill(st.session_state["a2_status"], st.session_state["a2_status"])
        + '</div>',
        unsafe_allow_html=True,
    )

    a2_spinner = st.empty()
    a2_output  = st.empty()

    if st.session_state["a2_result"]:
        a2 = st.session_state["a2_result"]
        risk    = a2.get("risk_level", "UNKNOWN")
        ttf     = a2.get("time_to_fine_hours", "?")
        penalty = a2.get("projected_penalty_usd", 0)
        party   = a2.get("responsible_party", "?").replace("_", " ").title()

        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown(
                f'<div class="metric-box"><div class="metric-label">Risk level</div>'
                f'<div class="metric-value risk-{risk}">{risk}</div></div>',
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown(
                f'<div class="metric-box"><div class="metric-label">Time to fine</div>'
                f'<div class="metric-value">{ttf} hrs</div></div>',
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown(
                f'<div class="metric-box"><div class="metric-label">Projected penalty</div>'
                f'<div class="metric-value">USD {penalty:,}</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="margin-top:0.6rem;font-size:0.83rem;">'
            f'<b>Responsible:</b> {party} &nbsp;·&nbsp; '
            f'<b>Calc:</b> {a2.get("penalty_calculation","—")}</div>',
            unsafe_allow_html=True,
        )

        if a2.get("risk_justification"):
            st.markdown(
                f'<div style="margin-top:0.5rem;background:#fafafa;border:1px solid #e2e8f0;'
                f'border-radius:6px;padding:0.6rem 0.9rem;font-size:0.8rem;color:#334155;">'
                f'{a2["risk_justification"]}</div>',
                unsafe_allow_html=True,
            )

        if a2.get("recommended_actions"):
            st.markdown("**Recommended actions:**")
            for i, action in enumerate(a2["recommended_actions"], 1):
                st.markdown(f"{i}. {action}")

        # alerts
        st.markdown("---")
        tab_email, tab_wa = st.tabs(["📧 Email Alert", "💬 WhatsApp"])

        with tab_email:
            email = a2.get("email_alert", {})
            st.markdown(f"**To:** `{email.get('to', '—')}`")
            st.markdown(f"**Subject:** {email.get('subject', '—')}")
            st.markdown(
                f'<div class="alert-email">{email.get("body", "—")}</div>',
                unsafe_allow_html=True,
            )

        with tab_wa:
            wa = a2.get("whatsapp_alert", "—")
            st.markdown(
                f'<div class="alert-whatsapp">{wa}</div>',
                unsafe_allow_html=True,
            )


# ═══════════════════════════════════════════════════════
# STREAMING HELPERS
# ═══════════════════════════════════════════════════════

TOOL_LABELS = {
    "classify_document":     "🔍 Classifying document type…",
    "extract_booking_fields":"📋 Extracting booking fields…",
    "determine_lfd":         "📅 Calculating Last Free Date…",
    "calculate_risk":        "⚠️  Calculating risk level…",
    "estimate_penalty":      "💰 Estimating penalty exposure…",
    "draft_email_alert":     "📧 Drafting email alert…",
    "draft_whatsapp_alert":  "💬 Drafting WhatsApp message…",
}


def stream_agent(agent_executor, input_dict: dict, step_placeholder, label: str) -> str:
    """
    Stream an AgentExecutor using .stream(), updating step_placeholder
    after each tool call so the user sees progress in real time.
    Returns the final output string.
    """
    steps_done = []
    final_output = ""

    def render(extra_line: str = ""):
        lines = "".join(
            f'<div style="margin-bottom:4px;">✅ {s}</div>' for s in steps_done
        )
        if extra_line:
            lines += f'<div style="color:#93c5fd;">{extra_line}</div>'
        step_placeholder.markdown(
            f'<div class="log-box">{lines}</div>',
            unsafe_allow_html=True,
        )

    render(f"⏳ {label} starting…")

    for chunk in agent_executor.stream(input_dict):
        # Each chunk is a dict; keys tell us what just happened
        if "actions" in chunk:
            for action in chunk["actions"]:
                tool_name = getattr(action, "tool", "")
                friendly  = TOOL_LABELS.get(tool_name, f"🔧 {tool_name}…")
                render(f"⏳ {friendly}")

        elif "steps" in chunk:
            for step in chunk["steps"]:
                tool_name = getattr(step.action, "tool", "")
                friendly  = TOOL_LABELS.get(tool_name, f"🔧 {tool_name}")
                steps_done.append(friendly)
                render()

        elif "output" in chunk:
            final_output = chunk["output"]
            steps_done.append(f"✔ {label} complete")
            render()

    return final_output


# ═══════════════════════════════════════════════════════
# PIPELINE EXECUTION
# ═══════════════════════════════════════════════════════
if run_btn:
    api_key = st.session_state.get("gemini_key", "")
    ocr_text = st.session_state.get("ocr_text", "")

    if not api_key:
        st.error("Please enter your Groq API key in the sidebar.")
        st.stop()
    if not ocr_text:
        st.error("Please upload a PDF first.")
        st.stop()

    # reset state
    st.session_state.update({
        "a1_status": "running", "a2_status": "idle",
        "a1_result": None, "a2_result": None,
        "run_log": [],
    })

    # ── AGENT 1 — stream tool calls live ───────────────
    log("Building Agent 1 (Document Intelligence)…")
    try:
        from agents import build_agent1, run_agent1, _safe_json
        agent1 = build_agent1(api_key)

        a1_raw = stream_agent(
            agent1,
            {"input": f"""Analyse this shipping document text and extract all required fields.

--- DOCUMENT TEXT START ---
{ocr_text.strip()}
--- DOCUMENT TEXT END ---

Follow these steps in order:
1. Call classify_document to identify the document type and carrier
2. Call extract_booking_fields to pull the core fields
3. Call determine_lfd with the vessel arrival at final POD
4. Return your final JSON answer"""},
            a1_spinner,
            "Agent 1 — Document Intelligence",
        )

        a1_result = _safe_json(a1_raw)
        st.session_state["a1_result"] = a1_result
        st.session_state["a1_status"] = "done"
        log(f"Agent 1 done — booking: {a1_result.get('booking_number','?')}, LFD: {a1_result.get('lfd','?')}")

        # ── Render Agent 1 fields immediately ──────────
        fields_html = render_fields(a1_result, [
            ("Document type",        "document_type"),
            ("Carrier",              "carrier"),
            ("Relevant document?",   "is_relevant"),
            ("Booking number",       "booking_number"),
            ("Shipper",              "shipper"),
            ("Port of Discharge",    "pod"),
            ("Vessel arrival (POD)", "vessel_arrival_at_pod"),
            ("Last Free Date",       "lfd"),
            ("Free days used",       "free_days_used"),
            ("Container count",      "container_count"),
            ("Container type",       "container_type"),
            ("Commodity",            "commodity"),
        ])
        a1_fields.markdown(fields_html, unsafe_allow_html=True)

        if a1_result.get("lfd_reasoning"):
            st.markdown(
                f'<div style="margin-top:0.5rem;background:#f0f9ff;border-left:3px solid #38bdf8;'
                f'border-radius:0 6px 6px 0;padding:0.6rem 0.9rem;font-size:0.8rem;color:#0c4a6e;">'
                f'💡 <b>LFD Reasoning:</b> {a1_result["lfd_reasoning"]}</div>',
                unsafe_allow_html=True,
            )

        warnings = a1_result.get("warnings")
        if warnings:
            if isinstance(warnings, str):
                warnings = [warnings] if warnings.strip() else []
            for w in warnings:
                if isinstance(w, str) and len(w) > 2:
                    st.warning(f"⚠️ {w}")

    except Exception as e:
        st.session_state["a1_status"] = "error"
        log(f"Agent 1 ERROR: {e}")
        a1_spinner.error(f"Agent 1 failed: {e}")
        st.stop()

    # Check relevance before proceeding
    if not st.session_state["a1_result"].get("is_relevant", True):
        log("Agent 1: document not relevant. Skipping Agent 2.")
        a2_spinner.warning(
            "Document not identified as a shipping booking confirmation. Agent 2 skipped."
        )
        st.rerun()

    # ── AGENT 2 — stream tool calls live ───────────────
    st.session_state["a2_status"] = "running"
    log("Building Agent 2 (Agentic Reasoning)…")

    try:
        from agents import build_agent2, run_agent2, _safe_json
        agent2 = build_agent2(api_key)

        a2_raw = stream_agent(
            agent2,
            {"input": f"""Analyse this shipment for D&D risk and draft alerts.

=== BOOKING DOCUMENT DATA (from Agent 1) ===
{json.dumps(st.session_state["a1_result"], indent=2)}

=== LIVE TRACKING PAYLOAD ===
{json.dumps(payload, indent=2)}

Call all four tools in order, then return your final JSON answer.
Use the lfd and current_time from the tracking payload."""},
            a2_spinner,
            "Agent 2 — Agentic Reasoning",
        )

        a2_result = _safe_json(a2_raw)
        st.session_state["a2_result"] = a2_result
        st.session_state["a2_status"] = "done"
        log(f"Agent 2 done — risk: {a2_result.get('risk_level','?')}, penalty: USD {a2_result.get('projected_penalty_usd', 0):,}")

    except Exception as e:
        st.session_state["a2_status"] = "error"
        log(f"Agent 2 ERROR: {e}")
        a2_spinner.error(f"Agent 2 failed: {e}")
        st.stop()

    st.rerun()