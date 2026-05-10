"""Wyoming TCP server and session handler for Wyoming WS Bridge."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from wyoming.event import Event
from wyoming.info import (
    Artifact,
    AsrModel,
    AsrProgram,
    Attribution,
    Info,
    TtsProgram,
    TtsVoice,
)
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.tts import Synthesize, SynthesizeStart, SynthesizeChunk, SynthesizeStop, SynthesizeStopped
from wyoming.asr import Transcribe, Transcript, TranscriptStart, TranscriptChunk, TranscriptStop
from wyoming.error import Error

from .const import (
    CONF_AUDIO_BUFFER_SIZE,
    CONF_CONNECT_TIMEOUT,
    CONF_ENABLE_PARTIAL,
    CONF_JSON_KEY_MAP,
    CONF_MAX_CONCURRENT,
    CONF_OUTPUT_SAMPLE_RATE,
    CONF_SERVICE_TYPE,
    CONF_STT_MODEL,
    CONF_TTS_MODEL,
    CONF_WS_URL,
    DEFAULT_AUDIO_BUFFER_SIZE,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_ENABLE_PARTIAL,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_OUTPUT_SAMPLE_RATE,
    DEFAULT_STT_MODEL,
    DEFAULT_TTS_MODEL,
    WYOMING_NAME,
    WYOMING_PROTOCOL_VERSION,
    WYOMING_VERSION,
)
from .vllm_client import VLLMClient

_LOGGER = logging.getLogger(__name__)

# Track active sessions for concurrency limiting
_active_sessions: dict[str, "VLLMHandler"] = {}


def build_info_response(config: dict[str, Any]) -> Info:
    """Build the standard Wyoming InfoResponse based on service type."""
    service_type = config.get(CONF_SERVICE_TYPE, "tts")
    attribution = Attribution(name="vLLM", url="")
    kwargs = dict(name=WYOMING_NAME, attribution=attribution,
                  installed=True, description=None, version=WYOMING_VERSION)

    if service_type == "tts":
        return Info(tts=[
            TtsProgram(
                **kwargs,
                voices=[TtsVoice(
                    **kwargs,
                    languages=["zh", "en"],
                    speakers=None,
                )],
                supports_synthesize_streaming=True,
            )
        ])
    else:
        model_name = config.get(CONF_STT_MODEL, DEFAULT_STT_MODEL)
        return Info(asr=[
            AsrProgram(
                **kwargs,
                models=[AsrModel(
                    **kwargs,
                    name=model_name,
                    languages=["zh", "en"],
                )],
                supports_transcript_streaming=True,
            )
        ])


class VLLMHandler:
    """Handles a single Wyoming TCP client connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        config: dict[str, Any],
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._config = config
        self._session_id: str | None = None
        self._vllm: VLLMClient | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=config.get(CONF_AUDIO_BUFFER_SIZE, DEFAULT_AUDIO_BUFFER_SIZE)
        )
        self._active = True
        self._stt_task: asyncio.Task[None] | None = None
        # Audio format from AudioStart
        self._stt_rate: int = 16000
        # Signal that AudioStart has been received (for STT rate sync)
        self._audio_started = asyncio.Event()
        # Streaming TTS state
        self._tts_session_id: str | None = None
        self._tts_text = ""

    async def run(self) -> None:
        """Main event loop for this client connection."""
        try:
            async for event in self._read_events():
                if not self._active:
                    break
                await self._handle_event(event)
        except asyncio.CancelledError:
            _LOGGER.debug("Handler cancelled for session %s", self._session_id)
        except ConnectionResetError:
            _LOGGER.debug("Connection reset for session %s", self._session_id)
        except Exception:
            _LOGGER.exception("Handler error for session %s", self._session_id)
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        """Release resources."""
        self._active = False
        if self._stt_task and not self._stt_task.done():
            self._stt_task.cancel()
            try:
                await self._stt_task
            except asyncio.CancelledError:
                pass
        if self._vllm:
            await self._vllm.close()
            self._vllm = None
        if self._writer and not self._writer.is_closing():
            self._writer.close()
        # Drain audio queue to unblock producers
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if self._session_id and self._session_id in _active_sessions:
            del _active_sessions[self._session_id]

    # ── Wyoming protocol I/O ─────────────────────────────────────────

    async def _read_events(self) -> AsyncGenerator[Event]:
        """Read Wyoming events from the TCP stream."""
        while self._active and not self._reader.at_eof():
            line = await self._reader.readline()
            if not line:
                break
            try:
                event = Event.from_json(line.decode().strip())
                yield event
            except Exception:
                _LOGGER.debug("Ignoring malformed event line: %s", line[:100])

    async def _send_event(self, event: Event) -> None:
        """Send a Wyoming event to the TCP client."""
        try:
            self._writer.write(event.to_json().encode() + b"\n")
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            _LOGGER.debug("Failed to send event, client disconnected")
            self._active = False

    # ── Event dispatch ─────────────────────────────────────────────

    async def _handle_event(self, event: Event) -> None:
        """Dispatch a Wyoming event to the appropriate handler."""
        service_type = self._config.get(CONF_SERVICE_TYPE, "tts")
        # TTS events
        if Synthesize.is_type(event.type):
            if service_type != "tts":
                await self._send_event(
                    Error(code="service_disabled", message="TTS is not enabled on this instance").event()
                )
                return
            await self._handle_tts(Synthesize.from_event(event))
        elif SynthesizeStart.is_type(event.type):
            if service_type != "tts":
                await self._send_event(
                    Error(code="service_disabled", message="TTS is not enabled on this instance").event()
                )
                return
            await self._handle_tts_start(SynthesizeStart.from_event(event))
        elif SynthesizeChunk.is_type(event.type):
            await self._handle_tts_chunk(SynthesizeChunk.from_event(event))
        elif SynthesizeStop.is_type(event.type):
            await self._handle_tts_stop()
        # STT events
        elif Transcribe.is_type(event.type):
            if service_type != "stt":
                await self._send_event(
                    Error(code="service_disabled", message="STT is not enabled on this instance").event()
                )
                return
            await self._handle_stt_start(Transcribe.from_event(event))
        elif AudioStart.is_type(event):
            await self._handle_audio_start(AudioStart.from_event(event))
        elif AudioChunk.is_type(event):
            await self._handle_audio_chunk(AudioChunk.from_event(event))
        elif AudioStop.is_type(event):
            await self._handle_audio_stop()
        elif event.type == "info":
            await self._send_event(build_info_response(self._config).event())
        else:
            _LOGGER.debug("Unhandled event type: %s", event.type)

    # ── Concurrency check ──────────────────────────────────────────

    def _check_concurrency(self, session_id: str) -> bool:
        """Check if we can accept a new session. Returns True if accepted."""
        max_concurrent = self._config.get(CONF_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT)
        if len(_active_sessions) >= max_concurrent:
            _LOGGER.warning(
                "Max concurrent sessions (%d) reached, rejecting %s",
                max_concurrent,
                session_id,
            )
            return False
        _active_sessions[session_id] = self
        return True

    # ── TTS handling ───────────────────────────────────────────────

    async def _handle_tts(self, request: Synthesize) -> None:
        """Handle a non-streaming TTS request from HA."""
        session_id = self._config.get("session_id", "local")

        if not self._check_concurrency(session_id):
            await self._send_event(
                Error(
                    code="max_concurrent_reached",
                    message="Too many concurrent sessions",
                ).event()
            )
            return

        model = self._config.get(CONF_TTS_MODEL, DEFAULT_TTS_MODEL)
        voice = request.voice.name if request.voice else "default"
        sample_rate = self._config.get(CONF_OUTPUT_SAMPLE_RATE, DEFAULT_OUTPUT_SAMPLE_RATE)
        json_key_map = self._config.get(CONF_JSON_KEY_MAP)

        _LOGGER.info(
            "TTS request text_len=%d model=%s",
            len(request.text),
            model,
        )

        try:
            self._vllm = VLLMClient(
                ws_url=self._config[CONF_WS_URL],
                connect_timeout=self._config.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                json_key_map=json_key_map,
            )
            await self._vllm.connect("/v1/audio/speech")

            async for chunk in self._vllm.stream_tts(
                text=request.text,
                model=model,
                voice=voice,
                sample_rate=sample_rate,
            ):
                if isinstance(chunk, bytes):
                    await self._send_event(
                        AudioChunk(audio=chunk, rate=sample_rate).event()
                    )
                elif isinstance(chunk, dict):
                    status = chunk.get("status", "")
                    if status == "error":
                        await self._send_event(
                            Error(
                                code="vllm_error",
                                message=json.dumps(chunk),
                            ).event()
                        )
                    break

            await self._send_event(SynthesizeStopped().event())
            _LOGGER.debug("TTS streaming complete")

        except (TimeoutError, asyncio.TimeoutError):
            _LOGGER.error("TTS timeout")
            await self._send_event(
                Error(code="vllm_timeout", message="vLLM TTS request timed out").event()
            )
        except Exception:
            _LOGGER.exception("TTS error")
            await self._send_event(
                Error(code="vllm_error", message="vLLM TTS error").event()
            )
        finally:
            if self._vllm:
                await self._vllm.close()
                self._vllm = None
            if session_id in _active_sessions:
                del _active_sessions[session_id]

    async def _handle_tts_start(self, request: SynthesizeStart) -> None:
        """Handle start of streaming TTS request."""
        if self._tts_session_id is not None:
            await self._send_event(
                Error(
                    code="session_busy",
                    message="TTS session already active",
                ).event()
            )
            return
        self._tts_session_id = self._config.get("session_id", "local")
        self._tts_text = ""

        if not self._check_concurrency(self._tts_session_id):
            await self._send_event(
                Error(
                    code="max_concurrent_reached",
                    message="Too many concurrent sessions",
                ).event()
            )
            self._tts_session_id = None
            return

        _LOGGER.debug("TTS streaming session started")

    async def _handle_tts_chunk(self, chunk: SynthesizeChunk) -> None:
        """Accumulate text from streaming TTS."""
        if self._tts_session_id is None:
            return
        self._tts_text += chunk.text

    async def _handle_tts_stop(self) -> None:
        """Handle end of streaming TTS, trigger synthesis."""
        if self._tts_session_id is None:
            return

        session_id = self._tts_session_id
        text = self._tts_text
        self._tts_session_id = None
        self._tts_text = ""

        if not text:
            if session_id in _active_sessions:
                del _active_sessions[session_id]
            return

        model = self._config.get(CONF_TTS_MODEL, DEFAULT_TTS_MODEL)
        sample_rate = self._config.get(CONF_OUTPUT_SAMPLE_RATE, DEFAULT_OUTPUT_SAMPLE_RATE)
        json_key_map = self._config.get(CONF_JSON_KEY_MAP)

        _LOGGER.info("TTS streaming text_len=%d model=%s", len(text), model)

        try:
            self._vllm = VLLMClient(
                ws_url=self._config[CONF_WS_URL],
                connect_timeout=self._config.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                json_key_map=json_key_map,
            )
            await self._vllm.connect("/v1/audio/speech")

            async for chunk in self._vllm.stream_tts(
                text=text,
                model=model,
                sample_rate=sample_rate,
            ):
                if isinstance(chunk, bytes):
                    await self._send_event(
                        AudioChunk(audio=chunk, rate=sample_rate).event()
                    )
                elif isinstance(chunk, dict):
                    status = chunk.get("status", "")
                    if status == "error":
                        await self._send_event(
                            Error(
                                code="vllm_error",
                                message=json.dumps(chunk),
                            ).event()
                        )
                    break

            await self._send_event(SynthesizeStopped().event())
            _LOGGER.debug("TTS streaming complete")

        except (TimeoutError, asyncio.TimeoutError):
            _LOGGER.error("TTS timeout")
            await self._send_event(
                Error(code="vllm_timeout", message="vLLM TTS request timed out").event()
            )
        except Exception:
            _LOGGER.exception("TTS error")
            await self._send_event(
                Error(code="vllm_error", message="vLLM TTS error").event()
            )
        finally:
            if self._vllm:
                await self._vllm.close()
                self._vllm = None
            if session_id in _active_sessions:
                del _active_sessions[session_id]

    # ── STT handling ───────────────────────────────────────────────

    async def _handle_stt_start(self, request: Transcribe) -> None:
        """Handle STT session start."""
        # Cancel existing STT task if any
        if self._stt_task and not self._stt_task.done():
            old_queue = self._audio_queue
            self._audio_queue = asyncio.Queue(
                maxsize=self._config.get(CONF_AUDIO_BUFFER_SIZE, DEFAULT_AUDIO_BUFFER_SIZE)
            )
            self._audio_started.clear()
            old_queue.put_nowait(None)
            try:
                await self._stt_task
            except asyncio.CancelledError:
                pass
            self._audio_started.clear()

        self._session_id = self._config.get("session_id", "local")

        if not self._check_concurrency(self._session_id):
            await self._send_event(
                Error(
                    code="max_concurrent_reached",
                    message="Too many concurrent sessions",
                ).event()
            )
            return

        model = request.name or self._config.get(CONF_STT_MODEL, DEFAULT_STT_MODEL)
        enable_partial = self._config.get(CONF_ENABLE_PARTIAL, DEFAULT_ENABLE_PARTIAL)
        json_key_map = self._config.get(CONF_JSON_KEY_MAP)

        _LOGGER.info("STT request model=%s", model)

        self._stt_task = asyncio.create_task(
            self._run_stt(model, enable_partial, json_key_map, self._session_id)
        )

    async def _handle_audio_start(self, audio_start: AudioStart) -> None:
        """Capture audio format from AudioStart and unblock STT."""
        self._stt_rate = audio_start.rate
        self._audio_started.set()
        _LOGGER.debug("AudioStart rate=%d", self._stt_rate)

    async def _handle_audio_chunk(self, audio: AudioChunk) -> None:
        """Queue incoming audio chunk for STT processing."""
        try:
            self._audio_queue.put_nowait(audio.audio)
        except asyncio.QueueFull:
            # Backpressure: discard oldest
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(audio.audio)
            except asyncio.QueueFull:
                pass
            _LOGGER.warning("Audio queue full, dropping oldest chunk")

    async def _handle_audio_stop(self) -> None:
        """Signal end of audio streaming for STT."""
        await self._audio_queue.put(None)

    async def _run_stt(
        self,
        model: str,
        enable_partial: bool,
        json_key_map: dict[str, str] | None,
        session_id: str,
    ) -> None:
        """Run the vLLM STT session, consuming audio from the queue."""
        try:
            # Wait for AudioStart to get the correct sample rate
            try:
                await asyncio.wait_for(self._audio_started.wait(), timeout=5.0)
            except (TimeoutError, asyncio.TimeoutError):
                _LOGGER.warning("AudioStart not received, using default sample rate")

            self._vllm = VLLMClient(
                ws_url=self._config[CONF_WS_URL],
                connect_timeout=self._config.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                json_key_map=json_key_map,
            )

            async def _audio_gen() -> AsyncGenerator[bytes]:
                while self._active:
                    chunk = await self._audio_queue.get()
                    if chunk is None:
                        break
                    yield chunk

            async for result in self._vllm.stream_stt(
                model=model,
                audio_generator=_audio_gen(),
                sample_rate=self._stt_rate,
            ):
                if result["is_final"]:
                    await self._send_event(
                        Transcript(text=result["text"]).event()
                    )
                elif enable_partial and result["partial"]:
                    await self._send_event(
                        TranscriptChunk(text=result["partial"]).event()
                    )

            await self._send_event(TranscriptStop().event())
            _LOGGER.debug("STT streaming complete for session %s", session_id)

        except (TimeoutError, asyncio.TimeoutError):
            _LOGGER.error("STT timeout for session %s", session_id)
            await self._send_event(
                Error(
                    code="vllm_timeout",
                    message="vLLM STT request timed out",
                ).event()
            )
        except Exception:
            _LOGGER.exception("STT error for session %s", session_id)
            await self._send_event(
                Error(
                    code="vllm_error",
                    message="vLLM STT error",
                ).event()
            )
        finally:
            if self._vllm:
                await self._vllm.close()
                self._vllm = None
            if session_id in _active_sessions:
                del _active_sessions[session_id]
