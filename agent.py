import os
import logging
from dotenv import load_dotenv

# Set environment variables to prevent OpenBLAS memory allocation crashes on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

# Load environment variables FIRST before importing LiveKit
load_dotenv()

from livekit import agents
from livekit.agents import Agent, AgentSession, JobContext, JobExecutorType, WorkerOptions, cli, room_io
from livekit.agents.inference import TurnDetector
from livekit.plugins import assemblyai, cartesia, google, noise_cancellation, silero

logger = logging.getLogger("voice-agent")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly, helpful, and natural voice AI assistant. "
                "Keep your answers concise, direct, and conversational since you are speaking out loud. "
                "Avoid markdown formatting, bulleted lists, code blocks, or special symbols in your speech."
            ),
        )


async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")
    await ctx.connect()

    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm=google.LLM(model="gemini-2.5-flash"),
        tts="cartesia/sonic-3",
        vad=silero.VAD.load(),
        turn_handling={"turn_detection": TurnDetector()},
    )

    await session.start(
        agent=Assistant(),
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
        greeting = "Hello! I am your voice AI assistant. How can I help you today?"
    else:
        greeting = f"Hello {display_name}! I am your voice AI assistant. How can I help you today?"

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