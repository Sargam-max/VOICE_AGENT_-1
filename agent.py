import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Set environment variables to prevent OpenBLAS memory allocation crashes on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

# Load environment variables FIRST before importing LiveKit
load_dotenv()

from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobExecutorType,
    WorkerOptions,
    cli,
    mcp,
    room_io,
)
from livekit.agents.inference import TurnDetector
from livekit.plugins import assemblyai, cartesia, google, noise_cancellation, silero

logger = logging.getLogger("voice-agent")

from auth_manager import auth_manager

MCP_SERVER_SCRIPT = str(Path(__file__).parent / "mcp_server.py")


class Assistant(Agent):
    def __init__(self, tools: list | None = None) -> None:
        super().__init__(
            instructions=(
                "You are voiceai, a friendly, highly capable, and intelligent real-time voice and text AI assistant. "
                "You were built by Sargam using LiveKit Agents, Google Gemini 2.5 Flash, Cartesia Sonic-3, and AssemblyAI Streaming STT.\n\n"
                "YOUR CORE TOOLSET (via Model Context Protocol / MCP):\n"
                "1. Web Search & Breaking News (`search_web`, `search_news`): Query current live web facts, news, documentation, weather, or real-time info.\n"
                "2. Calendar Management (`calendar_list_events`, `calendar_create_event`, `calendar_delete_event`): View appointments, create new events, or cancel existing events.\n"
                "3. Gmail Integration (`gmail_read_inbox`, `gmail_search_emails`, `gmail_send_email`, `gmail_auth_status`): Read inbox messages, search emails, and compose & send real emails via Google OAuth 2.0.\n\n"
                "CRITICAL EMAIL INSTRUCTIONS:\n"
                "- When the user asks to write, draft, or send an email to someone (e.g. 'write email to akash gupta 23mc3005@rgipt.ac.in greeting him happy journey'):\n"
                "  1. Extract the recipient email address (e.g. '23mc3005@rgipt.ac.in').\n"
                "  2. Autonomously craft an appropriate, concise subject (e.g. 'Wishing you a Safe and Happy Journey!').\n"
                "  3. Autonomously draft a friendly, professional message body fitting the user's intent.\n"
                "  4. Immediately execute the `gmail_send_email` tool with `to_email`, `subject`, and `body`.\n"
                "  5. In your spoken reply, announce clearly: 'I have sent the email to [recipient] with the subject [subject]!'\n"
                "- If the user asks to read, list, or check recent emails, call `gmail_read_inbox` or `gmail_search_emails` and summarize the results.\n\n"
                "CONVERSATIONAL STYLE:\n"
                "- Whenever the user asks a question requiring current facts, scheduling, calendar checks, or email actions, ALWAYS call your tools!\n"
                "- Keep your spoken replies concise, natural, and friendly. Avoid markdown asterisks, bulleted lists, and raw code in speech."
            ),
            tools=tools or [],
        )


async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")
    await ctx.connect()

    # Wait for the user participant to be ready to determine user identity
    participant = await ctx.wait_for_participant()
    user_id = participant.identity or "default"
    logger.info(f"User connected: {user_id} ({participant.name or 'No name'})")

    # Initialize MCP Toolset scoped specifically to this user
    tools = []
    try:
        mcp_toolset = mcp.MCPToolset(
            id=f"productivity-mcp-{user_id}",
            mcp_server=mcp.MCPServerStdio(
                command=sys.executable,
                args=[MCP_SERVER_SCRIPT, "--user-id", user_id],
                env={**os.environ, "CURRENT_USER_ID": user_id},
            ),
        )
        await mcp_toolset.setup()
        tools.append(mcp_toolset)
        ctx.add_shutdown_callback(mcp_toolset.aclose)
        logger.info(f"Connected to MCP Server for user '{user_id}' with {len(mcp_toolset.tools)} active tools.")
    except Exception as e:
        logger.warning(f"Could not initialize MCP server tools for '{user_id}': {e}")

    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm=google.LLM(model="gemini-2.5-flash"),
        tts="cartesia/sonic-3",
        vad=silero.VAD.load(),
        turn_handling={"turn_detection": TurnDetector()},
    )

    def broadcast_event(data_dict: dict):
        """Broadcast real-time structured telemetry to the room data channel."""
        try:
            payload = json.dumps(data_dict).encode("utf-8")
            asyncio.create_task(ctx.room.local_participant.publish_data(payload, reliable=True))
        except Exception as err:
            logger.debug(f"Could not broadcast telemetry event: {err}")

    # Forward tool execution updates to the UI in real time
    @session.on("tool_execution_updated")
    def on_tool_update(ev):
        try:
            update = ev.update
            if update.type == "tool_call_started":
                fn = update.function_call
                args = fn.arguments if isinstance(fn.arguments, (str, dict, list)) else str(fn.arguments)
                broadcast_event({
                    "type": "tool_started",
                    "tool": fn.name,
                    "call_id": fn.call_id,
                    "arguments": args,
                })
            elif update.type == "tool_call_ended":
                broadcast_event({
                    "type": "tool_ended",
                    "call_id": update.call_id,
                    "status": update.status,
                    "result": update.message or "",
                })
        except Exception as e:
            logger.warning(f"Error handling tool update: {e}")

    @session.on("agent_state_changed")
    def on_agent_state(ev):
        broadcast_event({"type": "agent_state", "state": str(ev.new_state)})

    @session.on("user_state_changed")
    def on_user_state(ev):
        broadcast_event({"type": "user_state", "state": str(ev.new_state)})

    @session.on("session_usage_updated")
    def on_usage_update(ev):
        try:
            usage = ev.usage
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            total_tokens = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
            broadcast_event({
                "type": "session_usage",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            })
        except Exception:
            pass

    await session.start(
        agent=Assistant(tools=tools),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )

    # Listen for typed chat messages sent from the web text input
    @ctx.room.on("data_received")
    def on_data_received(dp: rtc.DataPacket):
        try:
            # Prevent loop: ignore data packets emitted by local agent participant
            if dp.participant and dp.participant.identity == ctx.room.local_participant.identity:
                return

            raw_text = dp.data.decode("utf-8", errors="ignore").strip()
            if not raw_text:
                return
            text = ""
            try:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    # Filter out internal telemetry messages
                    if parsed.get("type") in ("tool_started", "tool_ended", "agent_state", "user_state", "session_usage", "ping", "pong"):
                        return
                    text = parsed.get("text") or parsed.get("message") or ""
                elif isinstance(parsed, str):
                    text = parsed
            except Exception:
                text = raw_text

            if text:
                logger.info(f"Received typed text message from user: '{text}'")
                asyncio.create_task(session.generate_reply(user_input=text))
        except Exception as e:
            logger.error(f"Error handling data_received: {e}", exc_info=True)

    display_name = participant.name or participant.identity or "there"
    is_google_connected = auth_manager.is_user_authenticated(user_id)
    user_info = auth_manager.get_user_info(user_id) if is_google_connected else None
    email_addr = user_info.get("email") if user_info else ""

    if is_google_connected and email_addr:
        greeting = f"Hello {display_name}! Your Gmail account ({email_addr}), calendar, and web search are connected. How can I help you today?"
    else:
        greeting = f"Hello {display_name}! I am your voice assistant with web search, calendar, and email tools ready. How can I help you today?"

    try:
        await session.say(greeting)
    except Exception as e:
        logger.warning(f"Could not deliver initial greeting: {e}")


def _always_available(*args, **kwargs) -> float:
    return 0.0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    # If LIVEKIT_AGENT_NAME is specified, worker registers with explicit dispatch
    agent_name = os.environ.get("LIVEKIT_AGENT_NAME", "my-voice-agent")
    worker_port = int(os.environ.get("LIVEKIT_WORKER_PORT", "0"))
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=agent_name,
            num_idle_processes=0,
            job_executor_type=JobExecutorType.THREAD,
            port=worker_port,
            load_threshold=100.0,
            load_fnc=_always_available,
        )
    )