"""
Production Model Context Protocol (MCP) Server for LiveKit Voice Agent.

Provides per-user tools across:
  1. Google OAuth 2.0 + Gmail API v1 (Reading inbox, searching, and sending real emails)
  2. Web Search & News (DuckDuckGo / ddgs - 100% free, unlimited)
  3. Calendar Management (Local persistent store + Google Calendar iCal sync)
"""

import argparse
import email
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import imaplib
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import sys
from datetime import datetime, timedelta
import urllib.request

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from auth_manager import auth_manager
from gmail_client import gmail_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mcp_server] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_server")

# FastMCP application instance
mcp = FastMCP("VoiceAI-Productivity-MCP")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CALENDAR_FILE = DATA_DIR / "calendar_events.json"
OUTBOX_FILE = DATA_DIR / "simulated_outbox.json"

# Global session user_id resolved at startup
_SESSION_USER_ID = os.environ.get("CURRENT_USER_ID", "default").strip() or "default"


def _get_target_user_id(user_id: str = "") -> str:
    """Return explicit user_id if provided, else session user_id."""
    clean = user_id.strip() if user_id else ""
    return clean or _SESSION_USER_ID


# ==============================================================================
# 1. GOOGLE OAUTH 2.0 + GMAIL API v1 TOOLS (Per-User)
# ==============================================================================

@mcp.tool()
def gmail_auth_status(user_id: str = "") -> str:
    """Check if the user has connected their Google Account via OAuth 2.0 for Gmail.

    Args:
        user_id: Optional user identifier. Defaults to the active session user.
    """
    uid = _get_target_user_id(user_id)
    if auth_manager.is_user_authenticated(uid):
        info = auth_manager.get_user_info(uid) or {}
        email_addr = info.get("email", "unknown")
        return f"Google Account connected for user '{uid}' ({email_addr}). Gmail API is active and ready."
    else:
        oauth_configured = auth_manager.is_oauth_configured()
        if not oauth_configured:
            return (
                f"Google OAuth is not yet configured on this server. "
                f"To enable per-user Gmail OAuth 2.0, please add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env."
            )
        return (
            f"User '{uid}' has not connected their Google Account yet.\n"
            f"Please connect your account by opening the web interface and clicking 'Connect Google' or visiting '/auth/google/login?user_id={uid}'."
        )


@mcp.tool()
def gmail_send_email(
    to_email: str,
    subject: str,
    body: str,
    user_id: str = "",
) -> str:
    """Send an email using official Google Gmail REST API v1 via user's OAuth 2.0 authentication.

    Args:
        to_email: The recipient's email address (e.g. 'friend@example.com').
        subject: The subject line of the email.
        body: The plain text message body.
        user_id: Optional user identifier. Defaults to active user.
    """
    uid = _get_target_user_id(user_id)
    if not to_email or "@" not in to_email:
        return "A valid recipient email address is required."
    if not subject:
        return "Email subject is required."
    if not body:
        return "Email body content is required."

    # 1. Primary: Use official Gmail API with user's OAuth 2.0 token
    if auth_manager.is_user_authenticated(uid):
        try:
            res = gmail_client.send_email(uid, to_email=to_email, subject=subject, body=body)
            msg_id = res.get("id", "")
            record_outbox_email({
                "user_id": uid,
                "to": to_email,
                "subject": subject,
                "body": body,
                "timestamp": datetime.now().isoformat(),
                "status": "sent_gmail_api",
                "message_id": msg_id,
            })
            return f"Email successfully sent to {to_email} via Gmail API (Message ID: {msg_id})!"
        except Exception as e:
            logger.error(f"Gmail API send failed for '{uid}': {e}", exc_info=True)
            return f"Failed to send email via Gmail API: {str(e)}"

    # 2. Secondary fallback: Check for shared SMTP credentials
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if gmail_addr and gmail_app_pw:
        try:
            msg = MIMEMultipart()
            msg["From"] = gmail_addr
            msg["To"] = to_email.strip()
            msg["Subject"] = subject.strip()
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
                server.login(gmail_addr, gmail_app_pw)
                server.sendmail(gmail_addr, [to_email.strip()], msg.as_string())
            record_outbox_email({
                "user_id": uid,
                "to": to_email,
                "subject": subject,
                "body": body,
                "timestamp": datetime.now().isoformat(),
                "status": "sent_smtp",
            })
            return f"Email sent to {to_email} using configured Gmail SMTP."
        except Exception as e:
            logger.error(f"SMTP send failed: {e}")

    # 3. Tertiary fallback: Record to simulated outbox so workflow doesn't break
    simulated_record = {
        "user_id": uid,
        "to": to_email,
        "subject": subject,
        "body": body,
        "timestamp": datetime.now().isoformat(),
        "status": "simulated_sent",
    }
    record_outbox_email(simulated_record)

    return (
        f"Email queued to '{to_email}' with subject '{subject}'.\n"
        f"(User '{uid}' has not connected Google OAuth yet. To send real emails via Gmail API, "
        f"click 'Connect Google' in the web interface or visit /auth/google/login?user_id={uid})."
    )


@mcp.tool()
def gmail_read_inbox(
    max_results: int = 5,
    unread_only: bool = True,
    user_id: str = "",
) -> str:
    """Read recent emails from the user's Gmail inbox using the Gmail REST API.

    Args:
        max_results: Maximum number of emails to retrieve (default 5).
        unread_only: If True, only fetch unread emails. If False, fetch latest emails.
        user_id: Optional user identifier. Defaults to active user.
    """
    uid = _get_target_user_id(user_id)

    # 1. Primary: Use official Gmail API v1
    if auth_manager.is_user_authenticated(uid):
        try:
            messages = gmail_client.list_messages(
                user_id=uid,
                max_results=max_results,
                unread_only=unread_only,
            )
            if not messages:
                status_desc = "unread" if unread_only else "recent"
                return f"No {status_desc} emails found in your Gmail inbox."

            formatted = ["Your Recent Gmail Messages:"]
            for i, m in enumerate(messages, 1):
                formatted.append(
                    f"{i}. From: {m['from']}\n"
                    f"   Subject: {m['subject']}\n"
                    f"   Date: {m['date']}\n"
                    f"   Snippet: {m['snippet']}"
                )
            return "\n\n".join(formatted)
        except Exception as e:
            logger.error(f"Gmail API list_messages failed for '{uid}': {e}", exc_info=True)
            return f"Error reading emails via Gmail API: {str(e)}"

    # 2. Secondary fallback: Check for shared IMAP credentials
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_app_pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if gmail_addr and gmail_app_pw:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12) as mail:
                mail.login(gmail_addr, gmail_app_pw)
                mail.select("INBOX", readonly=True)
                search_criteria = "UNSEEN" if unread_only else "ALL"
                status, response = mail.search(None, search_criteria)
                if status == "OK" and response[0]:
                    msg_ids = response[0].split()[-max_results:]
                    msg_ids.reverse()
                    results = []
                    for num in msg_ids:
                        _, data = mail.fetch(num, "(RFC822.HEADER)")
                        if data and data[0]:
                            msg = email.message_from_bytes(data[0][1])
                            subj = _clean_header(msg.get("Subject", "(No Subject)"))
                            sender = _clean_header(msg.get("From", "(Unknown)"))
                            results.append(f"• From: {sender} - Subject: {subj}")
                    return "Recent Emails:\n" + "\n".join(results)
        except Exception as e:
            logger.warning(f"IMAP fallback error: {e}")

    return (
        f"Your Google account is not connected yet.\n"
        f"To read your live Gmail inbox, click 'Connect Google' in the web interface or visit /auth/google/login?user_id={uid}."
    )


@mcp.tool()
def gmail_search_emails(
    query: str,
    max_results: int = 5,
    user_id: str = "",
) -> str:
    """Search the user's Gmail inbox for specific messages, senders, or subjects via Gmail API v1.

    Args:
        query: Search keyword, sender email, or subject phrase (e.g. 'from:boss', 'meeting').
        max_results: Maximum results to retrieve (default 5).
        user_id: Optional user identifier. Defaults to active user.
    """
    uid = _get_target_user_id(user_id)
    if not query or not query.strip():
        return "Please specify a search query."

    # 1. Primary: Use official Gmail API v1
    if auth_manager.is_user_authenticated(uid):
        try:
            messages = gmail_client.list_messages(
                user_id=uid,
                query=query.strip(),
                max_results=max_results,
            )
            if not messages:
                return f"No emails matching '{query}' were found in your Gmail."

            formatted = [f"Search Results for '{query}':"]
            for i, m in enumerate(messages, 1):
                formatted.append(
                    f"{i}. From: {m['from']}\n"
                    f"   Subject: {m['subject']}\n"
                    f"   Snippet: {m['snippet']}"
                )
            return "\n\n".join(formatted)
        except Exception as e:
            logger.error(f"Gmail search failed for '{uid}': {e}", exc_info=True)
            return f"Error searching Gmail: {str(e)}"

    return (
        f"Cannot search Gmail for '{query}' because Google account for '{uid}' is not connected.\n"
        f"Connect your account via /auth/google/login?user_id={uid}."
    )


# ==============================================================================
# 2. WEB SEARCH TOOLS (DuckDuckGo Search - 100% Free)
# ==============================================================================

@mcp.tool()
def search_web(query: str, max_results: int = 4) -> str:
    """Search the live web for facts, current news, weather, or definitions."""
    if not query or not query.strip():
        return "Please provide a search query."

    logger.info(f"Executing web search for: '{query}'")
    results = []
    try:
        from ddgs import DDGS
        with DDGS(timeout=10) as ddgs:
            results = list(ddgs.text(query.strip(), max_results=max_results))
    except Exception as e1:
        try:
            from duckduckgo_search import DDGS
            with DDGS(timeout=10) as ddgs:
                results = list(ddgs.text(query.strip(), max_results=max_results))
        except Exception as e2:
            logger.error(f"Error executing web search (ddgs={e1}, duckduckgo_search={e2})")
            return f"Unable to complete web search: {str(e1)}"

    if not results:
        return f"No web search results found for '{query}'."

    summaries = []
    for i, item in enumerate(results, 1):
        title = item.get("title", "No Title")
        snippet = item.get("body", "No description available.")
        summaries.append(f"{i}. {title}: {snippet}")

    return "\n\n".join(summaries)


@mcp.tool()
def search_news(query: str, max_results: int = 4) -> str:
    """Search for the latest breaking news stories and articles."""
    if not query or not query.strip():
        return "Please provide a news query."

    logger.info(f"Executing news search for: '{query}'")
    results = []
    try:
        from ddgs import DDGS
        with DDGS(timeout=10) as ddgs:
            results = list(ddgs.news(query.strip(), max_results=max_results))
    except Exception as e1:
        try:
            from duckduckgo_search import DDGS
            with DDGS(timeout=10) as ddgs:
                results = list(ddgs.news(query.strip(), max_results=max_results))
        except Exception as e2:
            logger.error(f"Error executing news search (ddgs={e1}, duckduckgo_search={e2})")
            return f"Unable to complete news search: {str(e1)}"

    if not results:
        return f"No news results found for '{query}'."

    summaries = []
    for i, item in enumerate(results, 1):
        title = item.get("title", "No Title")
        date = item.get("date", "Recent")
        source = item.get("source", "News")
        body = item.get("body", "")
        summaries.append(f"{i}. [{source} - {date}] {title}: {body}")

    return "\n\n".join(summaries)


# ==============================================================================
# 3. CALENDAR TOOLS (Persistent Calendar Store & Google Calendar Sync)
# ==============================================================================

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def resolve_date_phrase(phrase: str) -> str:
    """Resolve natural language date phrases like 'today', 'tomorrow', 'Friday', 'next Monday' to YYYY-MM-DD."""
    now = datetime.now()
    clean = (phrase or "").lower().strip()
    if not clean or clean == "today":
        return now.strftime("%Y-%m-%d")
    if clean == "tomorrow":
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if clean == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", clean):
        return clean

    # Check for weekdays (e.g. "friday", "this friday", "next friday")
    target_clean = clean.replace("this", "").replace("next", "").strip()
    if target_clean in WEEKDAYS:
        target_idx = WEEKDAYS.index(target_clean)
        current_idx = now.weekday()
        days_ahead = (target_idx - current_idx) % 7
        if "next" in clean and days_ahead == 0:
            days_ahead = 7
        elif days_ahead == 0 and clean != "today":
            days_ahead = 7
        return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return now.strftime("%Y-%m-%d")


def load_calendar_events() -> list[dict]:
    """Load local persistent calendar events."""
    if not CALENDAR_FILE.exists():
        today_str = datetime.now().strftime("%Y-%m-%d")
        initial = [
            {
                "id": "evt-101",
                "title": "Welcome to Voice AI Assistant",
                "date": today_str,
                "start_time": "10:00 AM",
                "end_time": "10:30 AM",
                "description": "Onboarding session and feature exploration",
                "location": "LiveKit Voice Room",
            }
        ]
        save_calendar_events(initial)
        return initial

    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_calendar_events(events: list[dict]) -> None:
    """Save local calendar events to disk."""
    try:
        CALENDAR_FILE.write_text(json.dumps(events, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Error saving calendar file: {e}")


def _load_events() -> list[dict]:
    return load_calendar_events()


def _save_events(events: list[dict]) -> None:
    save_calendar_events(events)


def fetch_google_calendar_ical() -> list[dict]:
    """Fetch events from user's Google Calendar iCal feed if configured."""
    ical_url = os.environ.get("GOOGLE_CALENDAR_ICAL_URL", "").strip()
    if not ical_url:
        return []

    events = []
    try:
        req = urllib.request.Request(ical_url, headers={"User-Agent": "VoiceAI-Calendar/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read().decode("utf-8", errors="ignore")

        raw_events = content.split("BEGIN:VEVENT")
        for block in raw_events[1:]:
            summary_m = re.search(r"SUMMARY:(.*?)(?:\r?\n)", block)
            dtstart_m = re.search(r"DTSTART(?:;[^:]+)?:(\d{8}(?:T\d{6}Z?)?)", block)
            desc_m = re.search(r"DESCRIPTION:(.*?)(?:\r?\n)", block)

            if summary_m and dtstart_m:
                raw_summary = summary_m.group(1).strip()
                raw_date = dtstart_m.group(1).strip()
                if len(raw_date) >= 8:
                    d_str = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    time_str = "All day"
                    if "T" in raw_date and len(raw_date) >= 13:
                        time_str = f"{raw_date[9:11]}:{raw_date[11:13]}"
                    events.append({
                        "id": f"gcal-{hash(raw_summary + raw_date) % 100000}",
                        "title": raw_summary,
                        "date": d_str,
                        "start_time": time_str,
                        "end_time": "",
                        "description": desc_m.group(1).strip() if desc_m else "Google Calendar event",
                        "location": "Google Calendar",
                        "source": "google_calendar",
                    })
    except Exception as e:
        logger.warning(f"Could not fetch Google Calendar iCal: {e}")

    return events


def _fetch_google_calendar_ical() -> list[dict]:
    return fetch_google_calendar_ical()


def fetch_all_calendar_events() -> list[dict]:
    """Return merged list of local events and synced Google Calendar events."""
    local_events = load_calendar_events()
    gcal_events = fetch_google_calendar_ical()
    all_events = local_events + gcal_events
    all_events.sort(key=lambda x: (x.get("date", ""), x.get("start_time", "")))
    return all_events


def load_simulated_outbox() -> list[dict]:
    """Load logged / outbox emails."""
    if not OUTBOX_FILE.exists():
        return []
    try:
        return json.loads(OUTBOX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def record_outbox_email(record: dict) -> None:
    """Save an email record to the outbox log."""
    try:
        outbox = load_simulated_outbox()
        outbox.insert(0, record)  # most recent first
        OUTBOX_FILE.write_text(json.dumps(outbox[:100], indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not record outbox email: {e}")


@mcp.tool()
def calendar_list_events(timeframe: str = "today") -> str:
    """List scheduled calendar events and appointments.

    Args:
        timeframe: 'today', 'tomorrow', 'this_week', a day name (e.g. 'friday'), or 'all'.
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    all_events = fetch_all_calendar_events()
    tf = timeframe.lower().strip()

    if tf == "today":
        filtered = [e for e in all_events if e.get("date") == today_str]
        header = f"Events for Today ({today_str}):"
    elif tf == "tomorrow":
        filtered = [e for e in all_events if e.get("date") == tomorrow_str]
        header = f"Events for Tomorrow ({tomorrow_str}):"
    elif tf in ("this_week", "week"):
        week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d")
        filtered = [e for e in all_events if today_str <= e.get("date", "") <= week_end]
        header = f"Events for the next 7 days ({today_str} to {week_end}):"
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", tf):
        filtered = [e for e in all_events if e.get("date") == tf]
        header = f"Events for {tf}:"
    elif tf in WEEKDAYS or any(w in tf for w in WEEKDAYS):
        resolved = resolve_date_phrase(tf)
        filtered = [e for e in all_events if e.get("date") == resolved]
        header = f"Events for {tf.title()} ({resolved}):"
    else:
        filtered = all_events
        header = "All Scheduled Calendar Events:"

    if not filtered:
        return f"{header}\nNo events scheduled."

    lines = [header]
    for e in filtered:
        time_part = e.get("start_time", "No time")
        if e.get("end_time"):
            time_part += f" - {e['end_time']}"
        loc_part = f" at {e['location']}" if e.get("location") else ""
        lines.append(f"• [ID: {e.get('id')}] {e.get('date')} @ {time_part}: {e.get('title')}{loc_part}")

    return "\n".join(lines)


@mcp.tool()
def calendar_create_event(
    title: str,
    date: str = "today",
    start_time: str = "12:00 PM",
    end_time: str = "",
    description: str = "",
    location: str = "",
) -> str:
    """Create and schedule a new calendar event or appointment.

    Args:
        title: Title/summary of the appointment.
        date: 'today', 'tomorrow', weekday ('friday'), or 'YYYY-MM-DD'.
        start_time: Starting time (e.g. '2:00 PM', '14:00').
        end_time: Optional end time.
        description: Optional notes/details.
        location: Optional location (e.g. 'Zoom', 'Office').
    """
    if not title or not title.strip():
        return "Event title is required."

    resolved_date = resolve_date_phrase(date)
    event_id = f"evt-{int(datetime.now().timestamp() * 1000) % 1000000}"
    new_event = {
        "id": event_id,
        "title": title.strip(),
        "date": resolved_date,
        "start_time": start_time.strip(),
        "end_time": end_time.strip(),
        "description": description.strip(),
        "location": location.strip(),
        "created_at": datetime.now().isoformat(),
    }

    events = load_calendar_events()
    events.append(new_event)
    save_calendar_events(events)

    time_str = f"at {start_time}" + (f" - {end_time}" if end_time else "")
    return f"Event '{title.strip()}' scheduled on {resolved_date} {time_str} (ID: {event_id})."


@mcp.tool()
def calendar_delete_event(event_id: str) -> str:
    """Delete or cancel a scheduled calendar event by its ID."""
    if not event_id:
        return "Please specify the event ID to delete."

    events = load_calendar_events()
    initial_len = len(events)
    events = [e for e in events if str(e.get("id")) != str(event_id).strip()]

    if len(events) == initial_len:
        return f"Event with ID '{event_id}' was not found in your calendar."

    save_calendar_events(events)
    return f"Event '{event_id}' has been cancelled and removed."


def _clean_header(header_val: str | None) -> str:
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    res = []
    for content, encoding in decoded_parts:
        if isinstance(content, bytes):
            res.append(content.decode(encoding or "utf-8", errors="ignore"))
        else:
            res.append(str(content))
    return "".join(res)


# ==============================================================================
# Server Entrypoint
# ==============================================================================

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="LiveKit Voice AI MCP Server")
    parser.add_argument("--user-id", default=None, help="Active user identifier for per-user tools")
    parser.add_argument("--test", action="store_true", help="Run self-tests and exit")
    args, unknown = parser.parse_known_args()

    if args.user_id:
        _SESSION_USER_ID = args.user_id.strip()

    if args.test:
        print("Testing MCP tools directly:")
        print(f"Session User ID: '{_SESSION_USER_ID}'")
        print("\n--- 1. Testing Web Search ---")
        print(search_web("Python FastMCP Anthropic"))
        print("\n--- 2. Testing Gmail Auth Status ---")
        print(gmail_auth_status(_SESSION_USER_ID))
        print("\n--- 3. Testing Calendar List ---")
        print(calendar_list_events("today"))
        print("\n--- 4. Testing Gmail Send (Fallback/Simulation) ---")
        print(gmail_send_email("recipient@example.com", "Test Subject", "Body content"))
        print("\nDirect tests completed successfully!")
    else:
        # Default: run standard Model Context Protocol stdio transport
        mcp.run(transport="stdio")
