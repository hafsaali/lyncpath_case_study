# LyncPath D&D Prevention Agent MVP v2

AI-powered two-agent pipeline for Detention & Demurrage prevention.

## Stack
- **UI**: Streamlit
- **PDF extraction**: pdfplumber (for text-layer PDFs)
- **Agents**: LangChain `create_tool_calling_agent` + `AgentExecutor`
- **LLM**: Groq

## How it works

```
PDF Upload
    │
    ▼
pdfplumber extracts text
    │
    ▼
Agent 1 — Document Intelligence
    ├── Tool: classify_document     → is it a booking? which carrier?
    ├── Tool: extract_booking_fields → booking no., POD, vessel arrival
    └── Tool: determine_lfd         → vessel ETA + free days = LFD
    │
    ▼ structured JSON
Agent 2 — Agentic Reasoning  (+ mock tracking payload)
    ├── Tool: calculate_risk        → time to fine, HIGH/MEDIUM/LOW
    ├── Tool: estimate_penalty      → USD exposure calculation
    ├── Tool: draft_email_alert     → full broker email
    └── Tool: draft_whatsapp_alert  → short WA/SMS message
    │
    ▼
Streamlit UI — risk metrics + alert tabs
```

## Setup

### 1. Add Groq API key to .env file
Go to https://console.groq.com/keys → Create API key → copy it.

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run
```bash
streamlit run app.py
```

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI + pipeline orchestration |
| `agents.py` | LangChain Agent 1 + Agent 2 with all tools |
| `pdf_utils.py` | pdfplumber PDF → text extraction |
| `data.py` | Mock tracking payload (milestones, rate card, broker info) |
| `requirements.txt` | Python dependencies |

## Notes on LFD reasoning
Agent 1 explicitly reasons: vessel ETA at final POD → + carrier free days → LFD.
For Maersk SPOT: 5 free days. For Swift Flow: 7 days. Stated values in the doc take priority.
This is the core intelligence the spec calls out — not just date extraction.
