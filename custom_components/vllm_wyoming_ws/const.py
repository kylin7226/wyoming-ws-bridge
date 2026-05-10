"""Constants for vLLM Wyoming WS Bridge."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "vllm_wyoming_ws"

# ── Wyoming protocol ──────────────────────────────────────────────────────
WYOMING_NAME = "vLLM Omni WS"
WYOMING_VERSION = "1.0.0"
WYOMING_PROTOCOL_VERSION = "1.8"
DEFAULT_WYOMING_HOST = "0.0.0.0"
DEFAULT_WYOMING_PORT = 10300

# ── Connection defaults ───────────────────────────────────────────────────
DEFAULT_WS_URL = "ws://192.168.1.100:8000/v1"
DEFAULT_CONNECT_TIMEOUT = 10

# ── TTS defaults ──────────────────────────────────────────────────────────
DEFAULT_TTS_MODEL = "qwen3-tts"
DEFAULT_OUTPUT_SAMPLE_RATE = 16000

# ── STT defaults ──────────────────────────────────────────────────────────
DEFAULT_STT_MODEL = "qwen3-asr"
DEFAULT_ENABLE_PARTIAL = True

# ── Performance defaults ──────────────────────────────────────────────────
DEFAULT_MAX_CONCURRENT = 8
DEFAULT_AUDIO_BUFFER_SIZE = 64

# ── Health probe defaults ─────────────────────────────────────────────────
DEFAULT_HEALTH_MODE = "ws_connect"
DEFAULT_HEALTH_INTERVAL = 30
DEFAULT_FAILURE_THRESHOLD = 3
LATENCY_WARN_MS = 500  # threshold in ms to log a warning

# ── Config entry keys ─────────────────────────────────────────────────────
CONF_WS_URL = "ws_url"
CONF_CONNECT_TIMEOUT = "connect_timeout"
CONF_SERVICE_TYPE = "service_type"
CONF_TTS_MODEL = "tts_model"
CONF_OUTPUT_SAMPLE_RATE = "output_sample_rate"
CONF_STT_MODEL = "stt_model"
CONF_ENABLE_PARTIAL = "enable_partial"
CONF_MAX_CONCURRENT = "max_concurrent"
CONF_AUDIO_BUFFER_SIZE = "audio_buffer_size"
CONF_JSON_KEY_MAP = "json_key_map"
CONF_HEALTH_MODE = "health_mode"
CONF_HEALTH_INTERVAL = "health_interval"
CONF_FAILURE_THRESHOLD = "failure_threshold"
CONF_WYOMING_HOST = "wyoming_host"
CONF_WYOMING_PORT = "wyoming_port"

# ── Health probe states ───────────────────────────────────────────────────
class HealthState(StrEnum):
    """Health probe states."""

    CHECKING = "checking"
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


SAMPLE_RATES = [16000, 22050, 24000]
