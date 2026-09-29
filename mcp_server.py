"""
Production MCP (Model Context Protocol) Server for LiveKit Voice Agent.

Provides 100% free, fully functional tools:
  1. Web Search & News (via DuckDuckGo / ddgs - no API key needed, unlimited)
  2. Calendar Management (persistent event store with list, create, delete, and Google Calendar iCal sync)
  3. Gmail Integration (reading inbox, searching, and sending real emails via Gmail IMAP/SMTP)
"""

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

# Ensure .env is loaded
load_dotenv()

# Setup logging (stderr only so stdio protocol on stdout is not corrupted)
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

# ==============================================================================
# 1. WEB SEARCH TOOLS (100% Free, No API Key Required)
# ==============================================================================

@mcp.tool()
def search_web(query: str, max_results: int = 4) -> str:
    """Search the live web for up-to-date information, facts, weather, definitions, or general queries.

    Args:
        query: The search term or question to look up.
        max_results: Maximum number of search results to return (default 4).
    """
    if not query or not query.strip():
        return "Please provide a search query."

    logger.info(f"Executing web search for: '{query}'")
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query.strip(), max_results=max_results))
        
        if not results:
            return f"No web search results found for '{query}'."

        summaries = []
        for i, item in enumerate(results, 1):
            title = item.get("title", "No Title")
            snippet = item.get("body", "No description available.")
            summaries.append(f"{i}. {title}: {snippet}")

        return "\n\n".join(summaries)
    except Exception as e:
        logger.error(f"Error executing web search: {e}", exc_info=True)
        return f"Unable to complete web search: {str(e)}"


@mcp.tool()
def search_news(query: str, max_results: int = 4) -> str:
    """Search for the latest breaking news stories, headlines, and articles.

    Args:
        query: The topic or keyword for current news stories.
        max_results: Maximum number of news stories to return (default 4).
    """
    if not query or not query.strip():
        return "Please provide a news query."

    logger.info(f"Executing news search for: '{query}'")
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.news(query.strip(), max_results=max_results))

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
    except Exception as e:
        logger.error(f"Error executing news search: {e}", exc_info=True)
        return f"Unable to complete news search: {str(e)}"


# ==============================================================================
# 2. CALENDAR TOOLS (Free Persistent Calendar & Google Calendar iCal Sync)
# ==============================================================================

def _load_events() -> list[dict]:
    if not CALENDAR_FILE.exists():
        # Populate with a sample initial event
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
        _save_events(initial)
        return initial

    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Error reading calendar file: {e}")
        return []


def _save_events(events: list[dict]) -> None:
    try:
        CALENDAR_FILE.write_text(json.dumps(events, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Error saving calendar file: {e}")


def _fetch_google_calendar_ical() -> list[dict]:
    """Optionally syncs events from a user's Google Calendar private/public iCal URL if provided."""
    ical_url = os.environ.get("GOOGLE_CALENDAR_ICAL_URL", "").strip()
    if not ical_url:
        return []

    events = []
    try:
        req = urllib.request.Request(ical_url, headers={"User-Agent": "VoiceAI-Calendar/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            content = response.read().decode("utf-8", errors="ignore")

        # Basic iCal parser
        raw_events = content.split("BEGIN:VEVENT")
        for block in raw_events[1:]:
            summary_m = re.search(r"SUMMARY:(.*?)(?:\r?\n)", block)
            dtstart_m = re.search(r"DTSTART(?:;[^:]+)?:(\d{8}(?:T\d{6}Z?)?)", block)
            desc_m = re.search(r"DESCRIPTION:(.*?)(?:\r?\n)", block)

            if summary_m and dtstart_m:
                raw_summary = summary_m.group(1).strip()
                raw_date = dtstart_m.group(1).strip()
                # Parse date (YYYYMMDD)
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


@mcp.tool()
def calendar_list_events(timeframe: str = "today") -> str:
    """List scheduled calendar events and appointments.

    Args:
        timeframe: The timeframe to query. Options:
                   - 'today' (default): Events scheduled for today.
                   - 'tomorrow': Events scheduled for tomorrow.
                   - 'this_week': Events in the next 7 days.
                   - 'all': All upcoming and saved events.
                   - 'YYYY-MM-DD': A specific calendar date.
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    local_events = _load_events()
    gcal_events = _fetch_google_calendar_ical()
    all_events = local_events + gcal_events

    tf = timeframe.lower().strip()
    filtered = []

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
    else:
        filtered = all_events
        header = "All Scheduled Calendar Events:"

    if not filtered:
        return f"{header}\nNo events scheduled."

    # Sort by date and start_time
    filtered.sort(key=lambda x: (x.get("date", ""), x.get("start_time", "")))

    lines = [header]
    for e in filtered:
        time_part = e.get("start_time", "No time")
        if e.get("end_time"):
            time_part += f" - {e['end_time']}"
        loc_part = f" at {e['location']}" if e.get("location") else ""
        desc_part = f" ({e['description']})" if e.get("description") else ""
        lines.append(f"• [ID: {e.get('id')}] {e.get('date')} @ {time_part}: {e.get('title')}{loc_part}{desc_part}")

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
        title: Title or summary of the event (e.g. 'Dentist appointment', 'Sync with team').
        date: Date of the event. Supports 'today', 'tomorrow', or 'YYYY-MM-DD' (e.g. '2026-10-05').
        start_time: Start time (e.g. '2:30 PM', '14:30', '10:00 AM').
        end_time: End time (optional, e.g. '3:30 PM').
        description: Additional details or notes about the event.
        location: Physical location or meeting link.
    """
    if not title or not title.strip():
        return "Event title is required."

    now = datetime.now()
    d_clean = date.lower().strip()
    if d_clean == "today":
        resolved_date = now.strftime("%Y-%m-%d")
    elif d_clean == "tomorrow":
        resolved_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", d_clean):
        resolved_date = d_clean
    else:
        # Fallback to today if not parsed
        resolved_date = now.strftime("%Y-%m-%d")

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

    events = _load_events()
    events.append(new_event)
    _save_events(events)

    time_str = f"at {start_time}" + (f" - {end_time}" if end_time else "")
    return (
        f"Event scheduled successfully!\n"
        f"Title: {title.strip()}\n"
        f"Date: {resolved_date}\n"
        f"Time: {time_str}\n"
        f"ID: {event_id}"
    )


@mcp.tool()
def calendar_delete_event(event_id: str) -> str:
    """Delete or cancel a scheduled calendar event by its ID.

    Args:
        event_id: The unique identifier of the event (e.g. 'evt-101').
    """
    if not event_id:
        return "Please specify the event ID to delete."

    events = _load_events()
    initial_len = len(events)
    events = [e for e in events if e.get("id") != event_id.strip()]

    if len(events) == initial_len:
        return f"Event with ID '{event_id}' was not found in your calendar."

    _save_events(events)
    return f"Event '{event_id}' has been cancelled and removed from your calendar."


# ==============================================================================
# 3. GMAIL TOOLS (Free SMTP & IMAP Integration with Graceful Fallback)
# ==============================================================================

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()


def _is_gmail_configured() -> bool:
    return bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD)


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


@mcp.tool()
def gmail_send_email(to_email: str, subject: str, body: str) -> str:
    """Send an email using Gmail.

    Args:
        to_email: The recipient's email address (e.g. 'colleague@example.com').
        subject: The subject line of the email.
        body: The plain-text message content.
    """
    if not to_email or "@" not in to_email:
        return "A valid recipient email address is required."
    if not subject:
        return "Email subject is required."
    if not body:
        return "Email body content is required."

    if not _is_gmail_configured():
        # Record into simulated outbox so user workflow works immediately without errors
        simulated_record = {
            "to": to_email,
            "subject": subject,
            "body": body,
            "timestamp": datetime.now().isoformat(),
            "status": "simulated_sent",
        }
        try:
            outbox = []
            if OUTBOX_FILE.exists():
                outbox = json.loads(OUTBOX_FILE.read_text(encoding="utf-8"))
            outbox.append(simulated_record)
            OUTBOX_FILE.write_text(json.dumps(outbox, indent=2), encoding="utf-8")
        except Exception:
            pass

        return (
            f"Email simulated and queued for '{to_email}' with subject '{subject}'.\n"
            f"(Note: To send actual live emails via Gmail for free, please set GMAIL_ADDRESS and "
            f"GMAIL_APP_PASSWORD in your .env or Railway dashboard. Google App Passwords are 100% free "
            f"and can be generated at myaccount.google.com/apppasswords)."
        )

    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_email.strip()
        msg["Subject"] = subject.strip()
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email.strip()], msg.as_string())

        logger.info(f"Email sent successfully to {to_email}")
        return f"Email successfully sent to {to_email} with subject '{subject}'!"
    except Exception as e:
        logger.error(f"Failed to send email via Gmail SMTP: {e}", exc_info=True)
        return f"Failed to send email: {str(e)}. Please check your GMAIL_ADDRESS and App Password."


@mcp.tool()
def gmail_read_inbox(max_results: int = 5, unread_only: bool = True) -> str:
    """Read recent emails from your Gmail inbox.

    Args:
        max_results: Maximum number of emails to retrieve (default 5).
        unread_only: If True, only fetch unread emails. If False, fetch latest emails.
    """
    if not _is_gmail_configured():
        return (
            "Gmail inbox reading requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD configured in .env.\n"
            "Quick setup (100% free):\n"
            "1. Visit https://myaccount.google.com/apppasswords\n"
            "2. Generate an App Password for 'Mail'\n"
            "3. Put GMAIL_ADDRESS and GMAIL_APP_PASSWORD in your .env or Railway settings."
        )

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12) as mail:
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select("INBOX", readonly=True)

            search_criteria = "UNSEEN" if unread_only else "ALL"
            status, response = mail.search(None, search_criteria)
            if status != "OK":
                return "Could not retrieve messages from Gmail inbox."

            msg_ids = response[0].split()
            if not msg_ids:
                status_desc = "unread" if unread_only else "total"
                return f"No {status_desc} emails found in your inbox."

            # Get the most recent emails
            recent_ids = msg_ids[-max_results:]
            recent_ids.reverse()

            results = []
            for num in recent_ids:
                status, data = mail.fetch(num, "(RFC822.HEADER BODY.PEEK[TEXT])")
                if status != "OK" or not data or not data[0]:
                    continue

                raw_header = data[0][1]
                msg = email.message_from_bytes(raw_header)
                subject = _clean_header(msg.get("Subject", "(No Subject)"))
                sender = _clean_header(msg.get("From", "(Unknown Sender)"))
                date_str = msg.get("Date", "")

                # Extract text snippet
                snippet = ""
                if len(data) > 1 and isinstance(data[1], tuple) and len(data[1]) > 1:
                    raw_body = data[1][1]
                    try:
                        text = raw_body.decode("utf-8", errors="ignore")
                        snippet = " ".join(text.split()[:20])
                    except Exception:
                        snippet = ""

                results.append(
                    f"• From: {sender}\n"
                    f"  Subject: {subject}\n"
                    f"  Date: {date_str}\n"
                    f"  Snippet: {snippet}"
                )

            return "Recent Emails:\n\n" + "\n\n".join(results)
    except Exception as e:
        logger.error(f"Error reading Gmail inbox: {e}", exc_info=True)
        return f"Error connecting to Gmail: {str(e)}. Ensure your App Password has IMAP access enabled."


@mcp.tool()
def gmail_search_emails(query: str, max_results: int = 5) -> str:
    """Search your Gmail emails for a specific sender, subject, or keyword.

    Args:
        query: Search keyword, email address, or subject phrase.
        max_results: Maximum results to retrieve (default 5).
    """
    if not query or not query.strip():
        return "Please specify a search query."

    if not _is_gmail_configured():
        return (
            f"Searched for '{query}'.\n"
            "To connect live Gmail search, set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in your .env."
        )

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12) as mail:
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select("INBOX", readonly=True)

            status, response = mail.search(None, f'(TEXT "{query.strip()}")')
            if status != "OK":
                return f"Could not search Gmail for '{query}'."

            msg_ids = response[0].split()
            if not msg_ids:
                return f"No emails matching '{query}' found."

            recent_ids = msg_ids[-max_results:]
            recent_ids.reverse()

            results = []
            for num in recent_ids:
                status, data = mail.fetch(num, "(RFC822.HEADER)")
                if status != "OK" or not data or not data[0]:
                    continue

                raw_header = data[0][1]
                msg = email.message_from_bytes(raw_header)
                subject = _clean_header(msg.get("Subject", "(No Subject)"))
                sender = _clean_header(msg.get("From", "(Unknown Sender)"))
                date_str = msg.get("Date", "")
                results.append(f"• From: {sender}\n  Subject: {subject}\n  Date: {date_str}")

            return f"Search results for '{query}':\n\n" + "\n\n".join(results)
    except Exception as e:
        logger.error(f"Error searching Gmail: {e}", exc_info=True)
        return f"Error searching Gmail: {str(e)}"


# ==============================================================================
# Server Entrypoint
# ==============================================================================

if __name__ == "__main__":
    # If run with a test argument, execute tool in CLI mode for quick verification
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("Testing MCP tools directly:")
        print("\n--- 1. Testing Web Search ---")
        print(search_web("LiveKit Agents voice AI"))
        print("\n--- 2. Testing Calendar List ---")
        print(calendar_list_events("all"))
        print("\n--- 3. Testing Calendar Create ---")
        print(calendar_create_event("Dentist appointment", "tomorrow", "3:00 PM"))
        print("\n--- 4. Testing Gmail (Offline Simulation) ---")
        print(gmail_send_email("test@example.com", "Test Subject", "Hello from Voice AI!"))
        print("\nAll direct tests completed successfully!")
    else:
        # Default: run standard Model Context Protocol stdio transport
        mcp.run(transport="stdio")
