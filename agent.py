import os
import logging
from dotenv import load_dotenv

# Set environment variables to prevent OpenBLAS memory allocation crashes on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

# Load environment variables FIRST before importing LiveKit
load_dotenv()

import sys
from pathlib import Path

from livekit import agents
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

MCP_SERVER_SCRIPT = str(Path(__file__).parent / "mcp_server.py")


class Assistant(Agent):
    def __init__(self, tools: list | None = None) -> None:
        super().__init__(
            instructions=(
                "You are a friendly, intelligent, and natural real-time voice AI assistant. "
                "You are equipped with powerful real-time tools via Model Context Protocol (MCP):\n"
                "1. Web Search & Breaking News (search_web, search_news): Query live web facts, weather, news, documentation.\n"
                "2. Calendar Scheduling & Events (calendar_list_events, calendar_create_event, calendar_delete_event): Check schedule, create appointments, delete events.\n"
                "3. Gmail Integration (gmail_read_inbox, gmail_search_emails, gmail_send_email): Read emails, search messages, or compose & send emails.\n"
                "Whenever the user asks a question requiring current information, scheduling, calendar checks, or email actions, ALWAYS call your tools! "
                "Keep your answers concise, direct, and conversational since you are speaking out loud. "
                "Avoid markdown formatting, bulleted lists, code blocks, or special symbols in your speech."
            ),
            tools=tools or [],
        )


async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")
    await ctx.connect()

    # Initialize MCP Toolset (Web Search, Calendar, Gmail)
    tools = []
    try:
        mcp_toolset = mcp.MCPToolset(
            id="productivity-mcp",
            mcp_server=mcp.MCPServerStdio(
                command=sys.executable,
                args=[MCP_SERVER_SCRIPT],
            ),
        )
        await mcp_toolset.setup()
        tools.append(mcp_toolset)
        ctx.add_shutdown_callback(mcp_toolset.aclose)
        logger.info(f"Connected to MCP Server with {len(mcp_toolset.tools)} active tools.")
    except Exception as e:
        logger.warning(f"Could not initialize MCP server tools: {e}")

    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm=google.LLM(model="gemini-2.5-flash"),
        tts="cartesia/sonic-3",
        vad=silero.VAD.load(),
        turn_handling={"turn_detection": TurnDetector()},
    )

    await session.start(
        agent=Assistant(tools=tools),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        ),
    )

    # Wait for the user participant to be ready
    participant = await ctx.wait_for_participant()
    logger.info(f"User joined: {participant.identity} ({participant.name or 'No name'})")

    display_name = participant.name or participant.identity or "there"
    if display_name.startswith("user-") or display_name.lower() in ("guest", "there"):
        greeting = "Hello! I am your voice AI assistant. I have web search, calendar, and email tools ready. How can I help you today?"
    else:
        greeting = f"Hello {display_name}! I am your voice AI assistant. I have web search, calendar, and email tools ready. How can I help you today?"

    try:
        await session.say(greeting)
    except Exception as e:
        logger.warning(f"Could not deliver initial greeting: {e}")


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
        )
    )