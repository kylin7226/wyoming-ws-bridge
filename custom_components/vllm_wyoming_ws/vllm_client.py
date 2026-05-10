"""WebSocket client for vLLM audio API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import websockets

from .const import CONF_JSON_KEY_MAP

_LOGGER = logging.getLogger(__name__)

# Default vLLM JSON keys, overridable via json_key_map config
DEFAULT_KEYS = {
    "tts_input": "input",
    "tts_model": "model",
    "tts_voice": "voice",
    "tts_response_format": "response_format",
    "stt_model": "model",
    "stt_sample_rate": "sample_rate",
    "stt_stream": "stream",
    "partial_text": "partial",
    "is_final": "is_final",
    "final_text": "text",
    "status": "status",
    "usage": "usage",
}


class VLLMClient:
    """Manages a single WebSocket connection to vLLM."""

    def __init__(
        self,
        ws_url: str,
        connect_timeout: float = 10,
        json_key_map: dict[str, str] | None = None,
    ) -> None:
        self._base_url = ws_url.rstrip("/")
        self._connect_timeout = connect_timeout
        self._keys = {**DEFAULT_KEYS, **(json_key_map or {})}
        self._ws: websockets.ClientConnection | None = None

    @property
    def connected(self) -> bool:
        """Whether the WebSocket connection is open."""
        return self._ws is not None and not self._ws.closed

    async def connect(self, path: str = "/v1/audio") -> None:
        """Establish WebSocket connection."""
        base = self._base_url
        # Strip common path suffixes to get the true base URL
        for suffix in ("/v1/audio/transcriptions", "/v1/audio/speech", "/v1/audio", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
        _LOGGER.debug("Connecting to vLLM at %s", url)
        self._ws = await websockets.connect(
            url,
            open_timeout=self._connect_timeout,
        )

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> VLLMClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── TTS streaming ─────────────────────────────────────────────────

    async def stream_tts(
        self,
        text: str,
        model: str,
        voice: str = "default",
        sample_rate: int = 16000,
    ) -> AsyncGenerator[bytes | dict[str, Any]]:
        """
        Send TTS request and yield binary PCM chunks followed by a final dict.

        Yields ``bytes`` for audio chunks and a ``dict`` for the terminal frame.
        """
        if not self.connected:
            await self.connect("/v1/audio/speech")

        request = {
            self._keys["tts_model"]: model,
            self._keys["tts_input"]: text,
            self._keys["tts_voice"]: voice,
            self._keys["tts_response_format"]: "pcm",
            "sample_rate": sample_rate,
            "stream": True,
        }
        await self._ws.send(json.dumps(request))
        _LOGGER.debug("TTS request sent, model=%s voice=%s", model, voice)

        async for frame in self._ws:
            if isinstance(frame, bytes):
                yield frame
            elif isinstance(frame, str):
                try:
                    data = json.loads(frame)
                except json.JSONDecodeError:
                    _LOGGER.warning("Non-JSON text frame from vLLM: %s", frame[:200])
                    continue
                status = data.get(self._keys["status"])
                if status in ("done", "error", "finished"):
                    yield data
                    return
                # Unexpected JSON mid-stream; log but continue
                _LOGGER.debug("Mid-stream JSON frame: %s", data)

    # ── STT streaming ─────────────────────────────────────────────────

    async def stream_stt(
        self,
        model: str,
        audio_generator: AsyncGenerator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncGenerator[dict[str, Any]]:
        """
        Send STT request, stream audio chunks, yield transcription results.

        Yields dicts with keys: partial, is_final, text, etc.
        """
        if not self.connected:
            await self.connect("/v1/audio/transcriptions")

        request = {
            self._keys["stt_model"]: model,
            self._keys["stt_sample_rate"]: sample_rate,
            self._keys["stt_stream"]: True,
        }
        await self._ws.send(json.dumps(request))

        async def _send_audio() -> None:
            async for chunk in audio_generator:
                if self.connected:
                    await self._ws.send(chunk)

        send_task = asyncio.create_task(_send_audio())

        try:
            async for frame in self._ws:
                if isinstance(frame, bytes):
                    # Binary echo or unexpected; skip
                    _LOGGER.debug("Unexpected binary frame during STT")
                    continue
                try:
                    data = json.loads(frame)
                except json.JSONDecodeError:
                    _LOGGER.warning("Non-JSON text frame during STT: %s", frame[:200])
                    continue

                is_final = data.get(self._keys["is_final"], False)
                yield {
                    "partial": data.get(self._keys["partial_text"], ""),
                    "is_final": is_final,
                    "text": data.get(self._keys["final_text"], ""),
                }
                if is_final:
                    break
        finally:
            send_task.cancel()
            try:
                await send_task
            except asyncio.CancelledError:
                pass


