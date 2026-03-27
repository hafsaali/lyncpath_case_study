"""
gmail_client.py — Gmail API integration for LyncPath.

Polls Gmail inbox for unread emails with PDF attachments,
downloads them, and returns only those classified as booking
confirmations (by Agent 1 downstream).
"""

import os
import base64
import json
from typing import Optional
from datetime import datetime

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Only read-only scope needed — we never send or delete
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE  = "token.json"        # saved after first OAuth login
CREDS_FILE  = "credentials.json"  # downloaded from Google Cloud Console


def get_gmail_service():
    """
    Authenticate and return a Gmail API service object.
    On first run: opens browser for OAuth consent.
    On subsequent runs: uses saved token.json automatically.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_FILE):
                raise FileNotFoundError(
                    "credentials.json not found. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def fetch_emails_with_pdf(max_results: int = 10) -> list[dict]:
    """
    Fetch recent unread emails that have PDF attachments.

    Returns a list of dicts:
      {
        message_id, subject, sender, date,
        attachments: [{ filename, data_bytes }]
      }
    """
    try:
        service = get_gmail_service()
    except FileNotFoundError as e:
        return [{"error": str(e)}]
    except Exception as e:
        return [{"error": f"Gmail auth failed: {e}"}]

    try:
        # Search for emails with PDF attachments (both read and unread)
        # This way, opened emails don't disappear from the list
        query = "has:attachment filename:pdf"
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return []

        emails = []
        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me",
                id=msg_ref["id"],
                format="full",
            ).execute()

            # Extract headers
            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            subject = headers.get("Subject", "(no subject)")
            sender  = headers.get("From", "unknown")
            date    = headers.get("Date", "")

            # Check if email is unread
            labels = msg.get("labelIds", [])
            is_unread = "UNREAD" in labels

            # Extract PDF attachments
            attachments = []
            _extract_attachments(service, msg_ref["id"], msg.get("payload", {}), attachments)

            if attachments:
                emails.append({
                    "message_id": msg_ref["id"],
                    "subject":    subject,
                    "sender":     sender,
                    "date":       date,
                    "is_unread":  is_unread,
                    "attachments": attachments,
                })

        return emails

    except HttpError as e:
        return [{"error": f"Gmail API error: {e}"}]


def _extract_attachments(service, message_id: str, payload: dict, result: list):
    """Recursively walk MIME parts and collect PDF attachments."""
    mime_type = payload.get("mimeType", "")
    filename  = payload.get("filename", "")

    # Direct attachment
    if filename.lower().endswith(".pdf") and "body" in payload:
        body = payload["body"]
        attachment_id = body.get("attachmentId")
        if attachment_id:
            att = service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
            data = base64.urlsafe_b64decode(att["data"])
            result.append({"filename": filename, "data_bytes": data})
        elif body.get("data"):
            data = base64.urlsafe_b64decode(body["data"])
            result.append({"filename": filename, "data_bytes": data})

    # Walk sub-parts
    for part in payload.get("parts", []):
        _extract_attachments(service, message_id, part, result)


def get_connection_status() -> dict:
    """
    Quick check — returns dict with connected bool and account email.
    Used by the Streamlit UI to show connection status without fetching emails.
    """
    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        return {
            "connected": True,
            "email": profile.get("emailAddress", "unknown"),
            "messages_total": profile.get("messagesTotal", 0),
        }
    except FileNotFoundError:
        return {"connected": False, "reason": "credentials.json not found"}
    except Exception as e:
        return {"connected": False, "reason": str(e)}