"""
LyncPath D&D Prevention Agent — v3
Gmail inbox → PDF extraction → Agent 1 (doc intelligence)
→ Agent 2 (milestone tracking) → Agent 3 (risk + alerts)
"""

import os, json, copy
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

import streamlit as st
from pdf_utils import extract_text_from_pdf, get_pdf_metadata
from agents import (build_agent1, build_agent2, build_agent3,
                    run_agent1, run_agent2, run_agent3, _safe_json)
from data import MOCK_TRACKING_PAYLOAD
from tracking_clients import build_tracking_payload_from_api

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="LyncPath D&D Agent", page_icon="🚢", layout="wide")

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""<style>
.block-container{padding-top:1.2rem;padding-bottom:2rem;}
.section-title{font-size:.92rem;font-weight:700;color:#0f172a;margin-bottom:.5rem;}
.agent-header{font-size:.7rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.08em;color:#64748b;margin-bottom:5px;}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.7rem;font-weight:600;}
.pill-idle   {background:#f1f5f9;color:#64748b;}
.pill-running{background:#dbeafe;color:#1e40af;}
.pill-done   {background:#dcfce7;color:#166534;}
.pill-error  {background:#fee2e2;color:#991b1b;}
.pill-skipped{background:#fef9c3;color:#854d0e;}
.risk-HIGH  {color:#dc2626;font-weight:800;font-size:1.35rem;}
.risk-MEDIUM{color:#d97706;font-weight:800;font-size:1.35rem;}
.risk-LOW   {color:#16a34a;font-weight:800;font-size:1.35rem;}
.metric-box{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
  padding:.65rem .9rem;text-align:center;}
.metric-label{font-size:.66rem;color:#64748b;text-transform:uppercase;letter-spacing:.06em;}
.metric-value{font-size:1.25rem;font-weight:700;color:#0f172a;margin-top:2px;}
.log-box{background:#0f172a;color:#94a3b8;border-radius:8px;padding:.8rem 1rem;
  font-family:monospace;font-size:.74rem;max-height:200px;overflow-y:auto;
  white-space:pre-wrap;line-height:1.5;}
.field-row{display:flex;justify-content:space-between;align-items:flex-start;
  padding:5px 0;border-bottom:1px solid #f1f5f9;font-size:.83rem;gap:1rem;}
.field-label{color:#64748b;white-space:nowrap;}
.field-value{font-weight:600;color:#0f172a;text-align:right;}
.ms-timeline{padding:.5rem 0;}
.ms-item{display:flex;align-items:flex-start;gap:12px;padding:6px 0;font-size:.83rem;}
.ms-dot-done{width:12px;height:12px;border-radius:50%;background:#16a34a;
  margin-top:3px;flex-shrink:0;}
.ms-dot-pend{width:12px;height:12px;border-radius:50%;background:#e2e8f0;
  border:2px solid #94a3b8;margin-top:3px;flex-shrink:0;}
.ms-dot-line{width:2px;background:#e2e8f0;margin:0 5px;flex-shrink:0;}
.ms-label{font-weight:600;color:#0f172a;}
.ms-ts{font-size:.72rem;color:#94a3b8;margin-top:1px;}
.ms-loc{font-size:.72rem;color:#64748b;}
.alert-email{background:#fff7ed;border-left:4px solid #f97316;
  border-radius:0 8px 8px 0;padding:.9rem 1.1rem;font-family:monospace;
  font-size:.78rem;color:#1e293b;white-space:pre-wrap;max-height:360px;overflow-y:auto;}
.alert-wa{background:#f0fdf4;border-left:4px solid #22c55e;
  border-radius:0 8px 8px 0;padding:.9rem 1.1rem;font-family:monospace;
  font-size:.82rem;color:#1e293b;white-space:pre-wrap;}
.gmail-card{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
  padding:.7rem 1rem;margin-bottom:.5rem;cursor:pointer;}
.gmail-card:hover{background:#f1f5f9;}
.gmail-subject{font-weight:600;font-size:.85rem;color:#0f172a;}
.gmail-meta{font-size:.73rem;color:#64748b;margin-top:2px;}
.source-badge{display:inline-block;padding:1px 8px;border-radius:4px;
  font-size:.68rem;font-weight:600;background:#dbeafe;color:#1e40af;margin-left:6px;}
.mock-badge{background:#fef9c3;color:#854d0e;}
.api-err{background:#fee2e2;border-left:3px solid #dc2626;border-radius:0 6px 6px 0;
  padding:.5rem .8rem;font-size:.78rem;color:#991b1b;margin-bottom:.5rem;}
</style>""", unsafe_allow_html=True)


# ── helpers ────────────────────────────────────────────────────────────────────
def pill(label, kind="idle"):
    return f'<span class="pill pill-{kind}">{label}</span>'

def safe_str(v):
    if v is None: return "—"
    if isinstance(v, bool): return "✅ Yes" if v else "❌ No"
    return str(v)

def render_fields(data, field_map):
    rows = ""
    for label, key in field_map:
        rows += (f'<div class="field-row">'
                 f'<span class="field-label">{label}</span>'
                 f'<span class="field-value">{safe_str(data.get(key))}</span>'
                 f'</div>')
    return rows

def render_milestone_timeline(milestones: list) -> str:
    if not milestones:
        return '<div style="color:#94a3b8;font-size:.82rem;">No milestone data available.</div>'
    html = '<div class="ms-timeline">'
    for ms in milestones:
        done    = ms.get("status") == "complete"
        dot_cls = "ms-dot-done" if done else "ms-dot-pend"
        icon    = "✅" if done else "⏳"
        ts      = ms.get("timestamp") or "—"
        loc     = ms.get("location") or ""
        html += (f'<div class="ms-item">'
                 f'<div class="{dot_cls}"></div>'
                 f'<div>'
                 f'<div class="ms-label">{icon} {ms.get("name","")}</div>'
                 f'<div class="ms-ts">{ts}'
                 + (f' &nbsp;·&nbsp; <span class="ms-loc">{loc}</span>' if loc else "")
                 + '</div></div></div>')
    html += '</div>'
    return html

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state["run_log"].append(f"[{ts}] {msg}")

TOOL_LABELS = {
    "classify_document":              "🔍 Classifying document…",
    "extract_booking_fields":         "📋 Extracting booking fields…",
    "determine_lfd":                  "📅 Calculating Last Free Date…",
    "fetch_carrier_tracking":         "📡 Fetching live milestones…",
    "merge_milestone_events":         "🔀 Merging tracking sources…",
    "should_trigger_risk_assessment": "⚡ Evaluating trigger conditions…",
    "calculate_risk":                 "⚠️  Calculating risk…",
    "estimate_penalty":               "💰 Estimating penalty…",
    "draft_email_alert":              "📧 Drafting email alert…",
    "draft_whatsapp_alert":           "💬 Drafting WhatsApp message…",
}

def stream_agent(executor, input_dict, placeholder, label) -> str:
    steps, final = [], ""
    def render(extra=""):
        lines = "".join(f'<div style="margin-bottom:3px;">✅ {s}</div>' for s in steps)
        if extra:
            lines += f'<div style="color:#93c5fd;">{extra}</div>'
        placeholder.markdown(f'<div class="log-box">{lines}</div>', unsafe_allow_html=True)

    render(f"⏳ {label} starting…")
    for chunk in executor.stream(input_dict):
        if "actions" in chunk:
            for a in chunk["actions"]:
                friendly = TOOL_LABELS.get(getattr(a, "tool", ""), f"🔧 {getattr(a, 'tool', '')}")
                render(f"⏳ {friendly}")
        elif "steps" in chunk:
            for s in chunk["steps"]:
                friendly = TOOL_LABELS.get(getattr(s.action, "tool", ""), f"🔧 {getattr(s.action, 'tool', '')}")
                steps.append(friendly)
                render()
        elif "output" in chunk:
            final = chunk["output"]
            steps.append(f"✔ {label} complete")
            render()
    return final


# ── session state ──────────────────────────────────────────────────────────────
for k, v in {
    "a1_status": "idle", "a2_status": "idle", "a3_status": "idle",
    "a1_result": None,   "a2_result": None,   "a3_result": None,
    "ocr_text": None, "pdf_meta": {}, "run_log": [],
    "gmail_emails": [], "selected_email_idx": None, "gmail_last_refresh": None,
    "tracking_payload": None, "source_mode": "upload",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── API key check ──────────────────────────────────────────────────────────────
groq_key = os.getenv("GROQ_API_KEY", "")
if not groq_key:
    st.error("⚠️ GROQ_API_KEY not found. Add it to your .env file and restart.")
    st.stop()
st.session_state["api_key"] = groq_key


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🚢 LyncPath")
    st.markdown("**D&D Prevention Agent v3**")
    st.markdown("---")

    st.markdown("**Input Source**")
    source_tab = st.radio("", ["📁 Upload PDF", "📧 Gmail Inbox"],
                          label_visibility="collapsed", horizontal=True)
    st.markdown("---")

    # ── Upload mode
    if source_tab == "📁 Upload PDF":
        st.session_state["source_mode"] = "upload"
        uploaded = st.file_uploader("Drop booking PDF", type=["pdf"],
                                    label_visibility="collapsed")
        if uploaded:
            pdf_bytes = uploaded.read()
            with st.spinner("Extracting text…"):
                ocr_text, warnings = extract_text_from_pdf(pdf_bytes)
                meta = get_pdf_metadata(pdf_bytes)
            st.session_state.update({"ocr_text": ocr_text, "pdf_meta": meta})
            st.success(f"✅ {len(ocr_text):,} chars · {meta.get('page_count','?')} page(s)")
            for w in warnings:
                st.warning(w)

    # ── Gmail mode
    else:
        st.session_state["source_mode"] = "gmail"
        gmail_configured = os.path.exists("credentials.json")

        if not gmail_configured:
            st.warning("credentials.json not found.\nSee README for Gmail setup steps.")
        else:
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button("🔄 Refresh Inbox", use_container_width=True):
                    with st.spinner("Connecting to Gmail…"):
                        from gmail_client import fetch_emails_with_pdf
                        emails = fetch_emails_with_pdf(max_results=15)
                    st.session_state["gmail_emails"] = emails
                    st.session_state["gmail_last_refresh"] = datetime.now()

            # Show last refresh time
            if st.session_state.get("gmail_last_refresh"):
                last_refresh = st.session_state["gmail_last_refresh"]
                st.caption(f"Last refreshed: {last_refresh.strftime('%I:%M:%S %p')}")

            emails = st.session_state.get("gmail_emails", [])
            if emails:
                if emails[0].get("error"):
                    st.error(emails[0]["error"])
                else:
                    st.markdown(f"**{len(emails)} email(s) with PDF attachments**")
                    for i, em in enumerate(emails):
                        # Add unread indicator
                        unread_marker = "🔵 " if em.get("is_unread", False) else ""
                        label = (f"{unread_marker}📧 {em['subject'][:35]}…"
                                 if len(em['subject']) > 35 else f"{unread_marker}📧 {em['subject']}")
                        if st.button(label, key=f"email_{i}", use_container_width=True):
                            att = em["attachments"][0]
                            pdf_bytes = att["data_bytes"]
                            ocr_text, _ = extract_text_from_pdf(pdf_bytes)
                            meta = get_pdf_metadata(pdf_bytes)
                            st.session_state.update({
                                "ocr_text": ocr_text,
                                "pdf_meta": meta,
                                "selected_email_idx": i,
                            })
                            st.success(f"✅ Loaded: {att['filename']}")

    st.markdown("---")

    st.markdown("**Tracking Data**")
    tracking_mode = st.radio(
        "",
        ["🔴 Live APIs (Terminal49 / ShipsGo)", "🟡 Mock data (demo)"],
        label_visibility="collapsed",
    )
    st.session_state["use_live_tracking"] = "Live" in tracking_mode

    if not st.session_state["use_live_tracking"]:
        scenario = st.selectbox("Scenario", [
            "🔴 LFD in 24 hours — customs pending",
            "🟡 LFD in 72 hours — customs pending",
            "🟢 LFD in 5 days — all clear",
        ], label_visibility="collapsed")
        st.session_state["mock_scenario"] = scenario

    st.markdown("---")
    run_btn = st.button("▶  Run Pipeline", use_container_width=True, type="primary",
                        disabled=not st.session_state.get("ocr_text"))

    # Show refresh button if we have async tracking in progress
    a2_result = st.session_state.get("a2_result")
    if a2_result and isinstance(a2_result, dict) and a2_result.get("api_warning"):
        st.markdown('<div style="margin-top:.5rem;"></div>', unsafe_allow_html=True)
        refresh_tracking_btn = st.button("🔄 Check for Updated Tracking",
                                         use_container_width=True,
                                         help="Check if ShipsGo has finished processing the shipment")
        if refresh_tracking_btn:
            st.info("Re-running Agent 2 to check for updated tracking data...")
            # Clear the warning so it re-runs fresh
            st.session_state["a2_status"] = "idle"
            st.session_state["a2_result"] = None
            st.rerun()

    st.markdown("---")
    st.caption("Groq Llama-3.3-70b · LangChain · pdfplumber · Gmail API\nTerminal49 · ShipsGo v2")


# ── Mock payload builder ───────────────────────────────────────────────────────
def build_mock_payload(scenario: str) -> dict:
    payload = copy.deepcopy(MOCK_TRACKING_PAYLOAD)
    now = datetime(2026, 4, 29, 10, 0, 0, tzinfo=timezone.utc)
    if "24 hours" in scenario:
        payload["lfd"]                         = (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload["milestones"][6]["status"]     = "pending"
    elif "72 hours" in scenario:
        payload["lfd"]                         = (now + timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload["milestones"][6]["status"]     = "pending"
    else:
        payload["lfd"]                         = (now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload["milestones"][6]["status"]     = "complete"
        payload["milestones"][6]["timestamp"]  = (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["current_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return payload


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LAYOUT
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("# 🚢 LyncPath D&D Prevention Agent")
st.markdown(
    "**Flow:** Gmail / PDF upload → Agent 1 (document intelligence) "
    "→ Agent 2 (live milestone tracking) → Agent 3 (risk + alerts)"
)
st.markdown("---")

col_left, col_right = st.columns([1, 1], gap="large")

# ══════════════════════════════════════════════════════════════════════════════
# LEFT COLUMN — document + tracking data
# ══════════════════════════════════════════════════════════════════════════════
with col_left:

    # ── Document preview
    st.markdown('<div class="section-title">📄 Document</div>', unsafe_allow_html=True)
    if st.session_state.get("ocr_text"):
        meta = st.session_state["pdf_meta"]
        st.caption(f"Pages: {meta.get('page_count','?')} · "
                   f"Chars: {len(st.session_state['ocr_text']):,} · "
                   f"Creator: {meta.get('creator','—')}")
        with st.expander("Show extracted text", expanded=False):
            st.code(st.session_state["ocr_text"][:3000], language=None)
    else:
        st.info("Upload a PDF or select an email in the sidebar.")

    # ── Milestone timeline
    st.markdown('<div class="section-title" style="margin-top:1.2rem;">📡 Milestone Tracking</div>',
                unsafe_allow_html=True)

    tracking_payload = st.session_state.get("tracking_payload")
    a2               = st.session_state.get("a2_result") or {}

    if a2.get("milestones"):
        source  = a2.get("sources_used", [])
        badge   = "mock-badge" if a2.get("_using_mock", False) else ""
        src_txt = ", ".join(s for s in source if s) or "unknown"
        st.markdown(
            f'Source: <span class="source-badge {badge}">{src_txt}</span>',
            unsafe_allow_html=True,
        )
        if a2.get("api_error"):
            st.markdown(
                f'<div class="api-err">⚠️ API note: {a2["api_error"]} — showing mock data</div>',
                unsafe_allow_html=True,
            )
        st.markdown(render_milestone_timeline(a2["milestones"]), unsafe_allow_html=True)

        lfd_str = a2.get("lfd", "")
        try:
            lfd_dt  = datetime.fromisoformat(lfd_str.replace("Z", "+00:00"))
            cur_dt  = datetime.fromisoformat(a2.get("current_time", "").replace("Z", "+00:00"))
            hrs     = (lfd_dt - cur_dt).total_seconds() / 3600
            color   = "#dc2626" if hrs < 30 else "#d97706" if hrs < 80 else "#16a34a"
            st.markdown(
                f'<div style="margin-top:.6rem;padding:.5rem .8rem;background:#f8fafc;'
                f'border:1px solid #e2e8f0;border-radius:6px;font-size:.83rem;">'
                f'LFD: <b>{lfd_str}</b> &nbsp;·&nbsp; '
                f'<b style="color:{color}">{hrs:.1f} hrs remaining</b></div>',
                unsafe_allow_html=True,
            )
        except Exception:
            pass

        if a2.get("trigger_agent3") is False:
            st.info(f"ℹ️ No urgent D&D risk detected: {a2.get('trigger_reason','')}")

    elif tracking_payload:
        st.markdown(render_milestone_timeline(tracking_payload.get("milestones", [])),
                    unsafe_allow_html=True)
    else:
        st.caption("Milestone data will appear after pipeline runs.")

    # ── Run log
    if st.session_state["run_log"]:
        st.markdown('<div class="section-title" style="margin-top:1rem;">🖥 Pipeline Log</div>',
                    unsafe_allow_html=True)
        log_text = "\n".join(st.session_state["run_log"])
        st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# RIGHT COLUMN — agent outputs
# ══════════════════════════════════════════════════════════════════════════════
with col_right:

    # ── Agent 1
    st.markdown(
        '<div class="agent-header">Agent 1 — Document Intelligence &nbsp;'
        + pill(st.session_state["a1_status"], st.session_state["a1_status"])
        + '</div>', unsafe_allow_html=True,
    )
    a1_spinner = st.empty()
    a1_fields  = st.empty()

    if st.session_state["a1_result"]:
        a1 = st.session_state["a1_result"]
        a1_fields.markdown(render_fields(a1, [
            ("Document type",        "document_type"),
            ("Carrier",              "carrier"),
            ("Relevant?",            "is_relevant"),
            ("Booking number",       "booking_number"),
            ("Container number",     "container_number"),
            ("Bill of lading",       "bill_of_lading"),
            ("Shipper",              "shipper"),
            ("Port of Discharge",    "pod"),
            ("Vessel arrival (POD)", "vessel_arrival_at_pod"),
            ("Last Free Date",       "lfd"),
            ("Free days used",       "free_days_used"),
            ("Containers",           "container_count"),
            ("Type",                 "container_type"),
            ("Commodity",            "commodity"),
        ]), unsafe_allow_html=True)

        if a1.get("lfd_reasoning"):
            st.markdown(
                f'<div style="margin:.4rem 0;background:#f0f9ff;border-left:3px solid #38bdf8;'
                f'border-radius:0 6px 6px 0;padding:.5rem .8rem;font-size:.78rem;color:#0c4a6e;">'
                f'💡 <b>LFD Reasoning:</b> {a1["lfd_reasoning"]}</div>',
                unsafe_allow_html=True,
            )
        warnings = a1.get("warnings", [])
        if isinstance(warnings, str):
            warnings = [warnings] if warnings.strip() else []
        for w in warnings:
            if isinstance(w, str) and len(w) > 2:
                st.warning(f"⚠️ {w}")

    st.markdown("---")

    # ── Agent 2 — Milestone Tracker
    st.markdown(
        '<div class="agent-header">Agent 2 — Milestone Tracker &nbsp;'
        + pill(st.session_state["a2_status"], st.session_state["a2_status"])
        + '</div>', unsafe_allow_html=True,
    )
    a2_spinner = st.empty()

    if st.session_state["a2_result"]:
        a2      = st.session_state["a2_result"]
        trigger = a2.get("trigger_agent3", False)
        st.markdown(
            f'<div style="font-size:.82rem;margin-bottom:.8rem;">'
            f'Risk Assessment Trigger: {"🔴 YES" if trigger else "🟢 NO"} &nbsp;·&nbsp; '
            f'{a2.get("trigger_reason","")}</div>',
            unsafe_allow_html=True,
        )

        # Show API warning with beautiful card for async processing
        if a2.get("api_warning"):
            sources = a2.get("sources_used", ["ShipsGo"])
            st.markdown(
                f'<div style="background:linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);'
                f'border:2px solid #fbbf24;border-radius:12px;padding:1.2rem;margin:1rem 0;">'
                f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:.8rem;">'
                f'<div style="font-size:2rem;">⏳</div>'
                f'<div>'
                f'<div style="font-weight:800;font-size:1.1rem;color:#78350f;">Tracking Request Submitted</div>'
                f'<div style="font-size:.82rem;color:#92400e;margin-top:2px;">'
                f'Processing asynchronously via {", ".join(sources)}</div>'
                f'</div></div>'
                f'<div style="background:rgba(255,255,255,0.6);border-radius:8px;padding:.8rem;'
                f'font-size:.85rem;color:#78350f;line-height:1.6;">'
                f'<div><b>📬 Email notification:</b> Complete tracking details will arrive in your inbox within 1-3 hours</div>'
                f'<div><b>🔄 Refresh option:</b> Return to this page later to check for updated milestone data</div>'
                f'<div><b>🌐 Direct check:</b> Visit <a href="https://shipsgo.com" target="_blank" '
                f'style="color:#0369a1;text-decoration:underline;">shipsgo.com</a> for real-time status</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

            # Show placeholder note
            st.markdown(
                '<div style="background:#fef9c3;border:1px solid #fde047;'
                'border-radius:6px;padding:.7rem 1rem;margin:.5rem 0;font-size:.8rem;color:#713f12;">'
                '<b>⏳ Milestone data pending</b> — Full tracking details are being processed by the carrier API. '
                'The system has created a tracking request and you will receive complete milestone information via email.'
                '</div>',
                unsafe_allow_html=True,
            )

        # Show fallback note if Terminal49 failed and ShipsGo was used
        elif a2.get("fallback_note"):
            st.info(f"ℹ️ {a2['fallback_note']}")

        # Show milestones if available
        milestones = a2.get("milestones", [])
        if milestones and len(milestones) > 0 and not a2.get("api_warning"):
            sources = a2.get("sources_used", [])
            st.markdown(
                f'<div style="font-size:.75rem;color:#64748b;text-transform:uppercase;'
                f'letter-spacing:.06em;margin:.8rem 0 .5rem 0;">'
                f'Milestone Timeline · Source: {", ".join(sources) if sources else "Unknown"}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(render_milestone_timeline(milestones), unsafe_allow_html=True)
        elif not a2.get("api_warning") and not milestones:
            # Only show "no milestones" if we're not waiting for async processing
            st.markdown(
                '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;'
                'padding:1rem;text-align:center;color:#64748b;font-size:.85rem;margin:.5rem 0;">'
                '<div style="font-size:1.5rem;margin-bottom:.5rem;">📦</div>'
                '<div><b>No tracking milestones available</b></div>'
                '<div style="margin-top:.3rem;font-size:.78rem;">'
                'This shipment may not have started tracking yet or the booking number is not recognized by the carrier'
                '</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── Agent 3 — Risk & Alerts
    st.markdown(
        '<div class="agent-header">Agent 3 — Risk & Alerts &nbsp;'
        + pill(st.session_state["a3_status"], st.session_state["a3_status"])
        + '</div>', unsafe_allow_html=True,
    )
    a3_spinner = st.empty()

    if st.session_state["a3_result"]:
        a3      = st.session_state["a3_result"]
        risk    = a3.get("risk_level", "UNKNOWN")
        ttf     = a3.get("time_to_fine_hours", "?")
        penalty = a3.get("projected_penalty_usd", 0)
        party   = a3.get("responsible_party", "?").replace("_", " ").title()

        c1, c2, c3 = st.columns(3)
        for col, label, val in [
            (c1, "Risk level",        f'<span class="risk-{risk}">{risk}</span>'),
            (c2, "Time to fine",      f"{ttf} hrs"),
            (c3, "Projected penalty", f"USD {penalty:,}"),
        ]:
            col.markdown(
                f'<div class="metric-box"><div class="metric-label">{label}</div>'
                f'<div class="metric-value">{val}</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="font-size:.82rem;margin:.5rem 0;">'
            f'<b>Responsible:</b> {party} &nbsp;·&nbsp; {a3.get("penalty_calculation","")}</div>',
            unsafe_allow_html=True,
        )
        if a3.get("risk_justification"):
            st.markdown(
                f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;'
                f'padding:.5rem .8rem;font-size:.78rem;color:#334155;margin-bottom:.5rem;">'
                f'{a3["risk_justification"]}</div>',
                unsafe_allow_html=True,
            )
        if a3.get("recommended_actions"):
            st.markdown("**Recommended actions:**")
            for i, action in enumerate(a3["recommended_actions"], 1):
                st.markdown(f"{i}. {action}")

        st.markdown("---")
        tab_email, tab_wa = st.tabs(["📧 Email Alert", "💬 WhatsApp"])
        with tab_email:
            email = a3.get("email_alert", {})
            st.markdown(f"**To:** `{email.get('to','—')}`")
            st.markdown(f"**Subject:** {email.get('subject','—')}")
            st.markdown(f'<div class="alert-email">{email.get("body","—")}</div>',
                        unsafe_allow_html=True)
        with tab_wa:
            st.markdown(f'<div class="alert-wa">{a3.get("whatsapp_alert","—")}</div>',
                        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE EXECUTION
# ══════════════════════════════════════════════════════════════════════════════
if run_btn:
    api_key  = st.session_state["api_key"]
    ocr_text = st.session_state.get("ocr_text", "")

    if not ocr_text:
        st.error("No document loaded.")
        st.stop()

    st.session_state.update({
        "a1_status": "running", "a2_status": "idle", "a3_status": "idle",
        "a1_result": None, "a2_result": None, "a3_result": None,
        "tracking_payload": None, "run_log": [],
    })

    # ── AGENT 1 — Document Intelligence ───────────────────────────────────────
    log("Agent 1 starting — document intelligence…")
    try:
        a1_raw = stream_agent(
            build_agent1(api_key),
            {"input": f"""Analyse this shipping document.

--- DOCUMENT START ---
{ocr_text.strip()}
--- DOCUMENT END ---

Steps: classify_document → extract_booking_fields → determine_lfd → return JSON"""},
            a1_spinner,
            "Agent 1 — Document Intelligence",
        )
        a1_result = _safe_json(a1_raw)
        st.session_state["a1_result"] = a1_result
        st.session_state["a1_status"] = "done"
        log(f"Agent 1 done — booking: {a1_result.get('booking_number','?')}, "
            f"container: {a1_result.get('container_number','none')}, "
            f"LFD: {a1_result.get('lfd','?')}")

        a1_fields.markdown(render_fields(a1_result, [
            ("Document type",        "document_type"),
            ("Carrier",              "carrier"),
            ("Relevant?",            "is_relevant"),
            ("Booking number",       "booking_number"),
            ("Container number",     "container_number"),
            ("Bill of lading",       "bill_of_lading"),
            ("Shipper",              "shipper"),
            ("Port of Discharge",    "pod"),
            ("Vessel arrival (POD)", "vessel_arrival_at_pod"),
            ("Last Free Date",       "lfd"),
            ("Free days used",       "free_days_used"),
            ("Containers",           "container_count"),
            ("Type",                 "container_type"),
            ("Commodity",            "commodity"),
        ]), unsafe_allow_html=True)

        if a1_result.get("lfd_reasoning"):
            st.markdown(
                f'<div style="margin:.4rem 0;background:#f0f9ff;border-left:3px solid #38bdf8;'
                f'border-radius:0 6px 6px 0;padding:.5rem .8rem;font-size:.78rem;color:#0c4a6e;">'
                f'💡 <b>LFD Reasoning:</b> {a1_result["lfd_reasoning"]}</div>',
                unsafe_allow_html=True,
            )

    except Exception as e:
        st.session_state["a1_status"] = "error"
        log(f"Agent 1 ERROR: {e}")
        a1_spinner.error(f"Agent 1 failed: {e}")
        st.stop()

    if not st.session_state["a1_result"].get("is_relevant", True):
        log("Agent 1: not relevant — pipeline stopped.")
        a2_spinner.warning("Document not a booking confirmation. Pipeline stopped.")
        st.session_state["a2_status"] = "skipped"
        st.session_state["a3_status"] = "skipped"
        st.rerun()

    # ── AGENT 2 — Milestone Tracker ────────────────────────────────────────────
    st.session_state["a2_status"] = "running"
    log("Agent 2 starting — milestone tracking…")

    try:
        if st.session_state.get("use_live_tracking"):
            a2_raw = stream_agent(
                build_agent2(api_key),
                {"input": f"""Fetch live milestones for this shipment.

Carrier:          {a1_result.get('carrier','UNKNOWN')}
Booking number:   {a1_result.get('booking_number') or 'not available'}
Container number: {a1_result.get('container_number') or 'not available'}
Bill of lading:   {a1_result.get('bill_of_lading') or 'not available'}

Call fetch_carrier_tracking → should_trigger_risk_assessment → return JSON."""},
                a2_spinner,
                "Agent 2 — Milestone Tracker",
            )
            a2_result = _safe_json(a2_raw)
        else:
            # Mock mode — build payload from scenario, skip live API
            mock_payload = build_mock_payload(st.session_state.get("mock_scenario", ""))
            st.session_state["tracking_payload"] = mock_payload
            ms_json  = json.dumps(mock_payload["milestones"])
            lfd      = mock_payload["lfd"]
            cur_time = mock_payload["current_time"]

            from agents import should_trigger_risk_assessment
            trigger_raw  = should_trigger_risk_assessment.invoke(
                {"milestones": ms_json, "lfd": lfd, "current_time": cur_time}
            )
            trigger_data = _safe_json(trigger_raw) if isinstance(trigger_raw, str) else trigger_raw
            a2_result = {
                "milestones":     mock_payload["milestones"],
                "lfd":            lfd,
                "current_time":   cur_time,
                "sources_used":   ["mock"],
                "trigger_agent3": trigger_data.get("should_trigger", True),
                "trigger_reason": trigger_data.get("reason", "mock scenario"),
                "hours_until_lfd":trigger_data.get("hours_until_lfd", 0),
                "api_error":      None,
                "_using_mock":    True,
            }
            a2_spinner.markdown(
                '<div class="log-box">✅ Mock tracking data loaded</div>',
                unsafe_allow_html=True,
            )

        st.session_state["a2_result"] = a2_result
        st.session_state["a2_status"] = "done"
        log(f"Agent 2 done — trigger Agent 3: {a2_result.get('trigger_agent3')}, "
            f"LFD: {a2_result.get('lfd','?')}")

    except Exception as e:
        st.session_state["a2_status"] = "error"
        log(f"Agent 2 ERROR: {e}")
        a2_spinner.error(f"Agent 2 failed: {e}")
        st.stop()

    # ── AGENT 3 — only if triggered ────────────────────────────────────────────
    a2 = st.session_state["a2_result"]
    if not a2.get("trigger_agent3", True):
        st.session_state["a3_status"] = "skipped"
        log("Agent 3 skipped — no urgent D&D risk detected.")
        st.rerun()

    st.session_state["a3_status"] = "running"
    log("Agent 3 starting — risk calculation and alerts…")

    try:
        tracking_payload = st.session_state.get("tracking_payload") or build_tracking_payload_from_api(
            api_result={
                "milestones": a2.get("milestones", []),
                "lfd":        a2.get("lfd", ""),
                "source":     ", ".join(a2.get("sources_used", [])),
            },
            booking_number  = a1_result.get("booking_number", ""),
            container_count = a1_result.get("container_count", 1) or 1,
            dnd_rate_card   = {
                "carrier":                              a1_result.get("carrier", ""),
                "currency":                             "USD",
                "destination_free_days":                a1_result.get("free_days_used", 5),
                "demurrage_rate_per_container_per_day": 200,
                "detention_rate_per_container_per_day": 150,
                "container_count":                      a1_result.get("container_count", 1) or 1,
                "container_type":                       a1_result.get("container_type", "40DRY"),
            },
        )
        # Override LFD and current_time from Agent 2
        tracking_payload["lfd"]          = a2.get("lfd") or tracking_payload.get("lfd", "")
        tracking_payload["current_time"] = a2.get("current_time") or tracking_payload.get("current_time", "")
        tracking_payload["milestones"]   = a2.get("milestones") or tracking_payload.get("milestones", [])
        st.session_state["tracking_payload"] = tracking_payload

        a3_raw = stream_agent(
            build_agent3(api_key),
            {"input": f"""Analyse this shipment for D&D risk.

=== BOOKING DATA (Agent 1) ===
{json.dumps(st.session_state["a1_result"], indent=2)}

=== TRACKING DATA (Agent 2) ===
{json.dumps(tracking_payload, indent=2)}

Call all four tools then return JSON."""},
            a3_spinner,
            "Agent 3 — Risk & Alerts",
        )
        a3_result = _safe_json(a3_raw)
        st.session_state["a3_result"] = a3_result
        st.session_state["a3_status"] = "done"
        log(f"Agent 3 done — risk: {a3_result.get('risk_level','?')}, "
            f"penalty: USD {a3_result.get('projected_penalty_usd',0):,}")

    except Exception as e:
        st.session_state["a3_status"] = "error"
        log(f"Agent 3 ERROR: {e}")
        a3_spinner.error(f"Agent 3 failed: {e}")
        st.stop()

    st.rerun()