# LyncPath — D&D Prevention Agent

An intelligent **Detention & Demurrage (D&D) prevention system** for ocean freight shipments.  
Automatically extracts booking details from PDFs or Gmail, tracks milestones via aggregator APIs (Terminal49 + ShipsGo), and alerts users before expensive demurrage or detention fees are incurred.

---

## ✨ Features

- **Smart Document Intelligence**: Extracts booking number, carrier, POD, container count, vessel ETA, and free days from booking confirmations or CROs using OCR + LLM.
- **Live Milestone Tracking**: Combines Terminal49 (primary) and ShipsGo v2 (fallback) for real-time container visibility.
- **Async-Friendly Processing**: Gracefully handles ShipsGo's delayed processing with clear user feedback and email notifications.
- **Risk Assessment & Alerts**: Calculates D&D risk and automatically drafts professional email + WhatsApp alerts.
- **Gmail Integration**: Pulls booking PDFs directly from inbox (with refresh support).
- **Streamlit UI**: Clean, responsive interface with real-time pipeline visualization.
- **Mock Mode**: Perfect for demos and testing without API credits.

---

## 🏗️ Architecture

```
Gmail / PDF Upload
        ↓
Agent 1 — Document Intelligence (Classification + Extraction + LFD Calculation)
        ↓
Agent 2 — Milestone Tracker (Terminal49 → ShipsGo + Risk Trigger Logic)
        ↓
Agent 3 — Risk & Alerts (Penalty Estimation + Email/WhatsApp Drafting)
```

### Key Components

- `tracking_clients.py` — Terminal49 & ShipsGo API clients with robust fallback & polling  
- `agents.py` — LangChain tool-calling agents (Agent 1, 2, 3)  
- `gmail_client.py` — Secure Gmail integration with Streamlit Secrets support  
- `app.py` — Streamlit frontend  
- `pdf_utils.py` — PDF text extraction using pdfplumber  

---

## 🚀 Quick Start (Local Development)

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd lyncpath-dd-prevention
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create `.env` file

```env
GROQ_API_KEY=your_groq_api_key_here
TERMINAL49_API_KEY=your_terminal49_key
SHIPSGO_API_KEY=your_shipsgo_key

# Optional Gmail broker details
BROKER_EMAIL=ops@yourcompany.com
BROKER_PHONE=+1234567890
BROKER_NAME="Your Broker Name"
```

### 4. Gmail Setup (Optional but recommended)

1. Go to Google Cloud Console  
2. Create OAuth 2.0 Client ID (Desktop app) → Download `credentials.json`  
3. Place `credentials.json` in the project root  
4. Run the app and click **"Refresh Inbox"** (first time will open browser for OAuth consent)

### 5. Run the app

```bash
streamlit run app.py
```

---

## 🌐 Deployment on Streamlit Cloud

### Required Secrets

```toml
GROQ_API_KEY = "gsk_..."
TERMINAL49_API_KEY = "..."
SHIPSGO_API_KEY = "..."

[gmail]
token_json = """{
  "token": "...",
  "refresh_token": "..."
}"""
```

> Note: For client Gmail accounts, have the client log in locally once, then securely share the resulting `token.json` content to paste into Secrets.

---

## 📋 How It Works

1. Upload a booking PDF or select from Gmail inbox  
2. Agent 1 analyzes the document and extracts key fields + calculates LFD  
3. Agent 2 fetches live milestones:  
   - Tries Terminal49 first  
   - Falls back to ShipsGo if needed  
   - Handles async processing gracefully  
4. Agent 3 evaluates D&D risk and generates alerts if action is needed  

---

## 🔧 Configuration & Customization

### Carrier SCAC Mapping
Edit `CARRIER_SCAC_MAP` in `tracking_clients.py` to add new carriers.

### Milestone Normalization
Extend `MILESTONE_MAP` to normalize carrier-specific event names.

### Risk & Penalty Logic
Modify thresholds and rates in `agents.py` (`calculate_risk`, `estimate_penalty`).

---

## 📧 Gmail Integration Notes

- Emails with PDF attachments are shown (read + unread)  
- Opening an email no longer removes it from the list  
- Use **"Refresh Inbox"** to fetch latest emails  
- On Streamlit Cloud: token stored securely via Secrets  

---

## 🛠️ Troubleshooting

| Issue | Solution |
|------|---------|
| Terminal49 "invalid_format" error | Common with some Maersk bookings — fallback to ShipsGo |
| ShipsGo returns empty milestones | Normal for "NEW" status; data arrives within 1–3 hours |
| Gmail authentication fails | Ensure credentials.json or token is configured |
| No milestones shown | Use "Check for Updated Tracking" or wait |

---

## 🧪 Testing

- Use **Mock Mode** for testing without API calls  
- Upload sample booking PDFs from `samples/` (if available)  

---

## 🔐 Security Notes

- Never commit `token.json`, `credentials.json`, or `.env`  
- API keys and Gmail tokens stored in Streamlit Secrets  
- Gmail scope is read-only  

---

## 📄 License

This project is for internal / client use.

---

## 💡 Future Enhancements

- Auto-forward parsed booking data to TMS/ERP  
- Multi-container support with better aggregation  
- Historical D&D data for carrier performance scoring  
- Webhook support from ShipsGo/Terminal49  
- Mobile-friendly push notifications  
