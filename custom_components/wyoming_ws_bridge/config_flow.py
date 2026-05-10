"""Config and options flow for Wyoming WS Bridge."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_AUDIO_BUFFER_SIZE,
    CONF_CONNECT_TIMEOUT,
    CONF_ENABLE_PARTIAL,
    CONF_FAILURE_THRESHOLD,
    CONF_HEALTH_INTERVAL,
    CONF_HEALTH_MODE,
    CONF_JSON_KEY_MAP,
    CONF_MAX_CONCURRENT,
    CONF_OUTPUT_SAMPLE_RATE,
    CONF_SERVICE_TYPE,
    CONF_STT_MODEL,
    CONF_TTS_MODEL,
    CONF_WS_URL,
    CONF_WYOMING_HOST,
    CONF_WYOMING_PORT,
    DEFAULT_AUDIO_BUFFER_SIZE,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_ENABLE_PARTIAL,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_HEALTH_INTERVAL,
    DEFAULT_HEALTH_MODE,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_OUTPUT_SAMPLE_RATE,
    DEFAULT_STT_MODEL,
    DEFAULT_TTS_MODEL,
    DEFAULT_WS_URL,
    DEFAULT_WYOMING_HOST,
    DEFAULT_WYOMING_PORT,
    DOMAIN,
    SAMPLE_RATES,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_TTS = "tts"
SERVICE_STT = "stt"


class WyomingWSBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wyoming WS Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        self._service_type: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: choose service type (TTS or STT)."""
        if user_input is not None:
            try:
                self._service_type = user_input[CONF_SERVICE_TYPE]
                _LOGGER.debug("User selected service_type=%s", self._service_type)
            except KeyError:
                _LOGGER.error("Missing service_type in user_input: %s", user_input)
                return self.async_abort(reason="missing_service_type")
            return await self.async_step_settings()

        schema = vol.Schema({
            vol.Required(CONF_SERVICE_TYPE, default=SERVICE_TTS): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=SERVICE_TTS, label="TTS"),
                        selector.SelectOptionDict(value=SERVICE_STT, label="STT"),
                    ],
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: configure connection + service-specific settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate JSON key map
            json_map = user_input.get(CONF_JSON_KEY_MAP, "{}")
            try:
                parsed = json.loads(json_map)
                if not isinstance(parsed, dict):
                    errors[CONF_JSON_KEY_MAP] = "invalid_json_map"
            except json.JSONDecodeError:
                errors[CONF_JSON_KEY_MAP] = "invalid_json_map"

            if not errors:
                result: dict[str, Any] = {
                    CONF_SERVICE_TYPE: self._service_type,
                    CONF_WS_URL: user_input[CONF_WS_URL],
                    CONF_CONNECT_TIMEOUT: user_input[CONF_CONNECT_TIMEOUT],
                    CONF_WYOMING_HOST: user_input[CONF_WYOMING_HOST],
                    CONF_WYOMING_PORT: user_input[CONF_WYOMING_PORT],
                    CONF_MAX_CONCURRENT: user_input[CONF_MAX_CONCURRENT],
                    CONF_AUDIO_BUFFER_SIZE: user_input[CONF_AUDIO_BUFFER_SIZE],
                    CONF_JSON_KEY_MAP: parsed,
                    CONF_HEALTH_MODE: user_input[CONF_HEALTH_MODE],
                    CONF_HEALTH_INTERVAL: user_input[CONF_HEALTH_INTERVAL],
                    CONF_FAILURE_THRESHOLD: user_input[CONF_FAILURE_THRESHOLD],
                }
                if self._service_type == SERVICE_TTS:
                    result[CONF_TTS_MODEL] = user_input[CONF_TTS_MODEL]
                    result[CONF_OUTPUT_SAMPLE_RATE] = int(user_input[CONF_OUTPUT_SAMPLE_RATE])
                else:
                    result[CONF_STT_MODEL] = user_input[CONF_STT_MODEL]
                    result[CONF_ENABLE_PARTIAL] = user_input[CONF_ENABLE_PARTIAL]

                return self.async_create_entry(
                    title=f"Wyoming WS Bridge ({self._service_type.upper()})",
                    data=result,
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_WS_URL, default=DEFAULT_WS_URL): selector.TextSelector(),
                vol.Required(CONF_CONNECT_TIMEOUT, default=DEFAULT_CONNECT_TIMEOUT): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=30, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_WYOMING_HOST, default=DEFAULT_WYOMING_HOST): selector.TextSelector(),
                vol.Required(CONF_WYOMING_PORT, default=DEFAULT_WYOMING_PORT): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_MAX_CONCURRENT, default=DEFAULT_MAX_CONCURRENT): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=32, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_AUDIO_BUFFER_SIZE, default=DEFAULT_AUDIO_BUFFER_SIZE): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=16, max=256, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_JSON_KEY_MAP, default="{}"): str,
                vol.Required(CONF_HEALTH_MODE, default=DEFAULT_HEALTH_MODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["ws_connect", "http_health"],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HEALTH_INTERVAL, default=DEFAULT_HEALTH_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=120, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_FAILURE_THRESHOLD, default=DEFAULT_FAILURE_THRESHOLD): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        if self._service_type == SERVICE_TTS:
            schema = schema.extend(
                vol.Schema({
                    vol.Required(CONF_TTS_MODEL, default=DEFAULT_TTS_MODEL): selector.TextSelector(),
                    vol.Required(CONF_OUTPUT_SAMPLE_RATE, default=str(DEFAULT_OUTPUT_SAMPLE_RATE)): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[str(r) for r in SAMPLE_RATES],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }).schema
            )
        else:
            schema = schema.extend(
                vol.Schema({
                    vol.Required(CONF_STT_MODEL, default=DEFAULT_STT_MODEL): selector.TextSelector(),
                    vol.Required(CONF_ENABLE_PARTIAL, default=DEFAULT_ENABLE_PARTIAL): selector.BooleanSelector(),
                }).schema
            )

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> WyomingWSBridgeOptionsFlow:
        """Return the options flow handler."""
        return WyomingWSBridgeOptionsFlow()


class WyomingWSBridgeOptionsFlow(OptionsFlow):
    """Handle options flow for Wyoming WS Bridge."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate JSON key map
            json_map = user_input.get(CONF_JSON_KEY_MAP, "{}")
            try:
                parsed = json.loads(json_map)
                if not isinstance(parsed, dict):
                    errors[CONF_JSON_KEY_MAP] = "invalid_json_map"
            except json.JSONDecodeError:
                errors[CONF_JSON_KEY_MAP] = "invalid_json_map"

            if not errors:
                merged: dict[str, Any] = {
                    CONF_SERVICE_TYPE: self.config_entry.data.get(CONF_SERVICE_TYPE, SERVICE_TTS),
                    CONF_WS_URL: user_input[CONF_WS_URL],
                    CONF_CONNECT_TIMEOUT: user_input[CONF_CONNECT_TIMEOUT],
                    CONF_WYOMING_HOST: user_input[CONF_WYOMING_HOST],
                    CONF_WYOMING_PORT: user_input[CONF_WYOMING_PORT],
                    CONF_MAX_CONCURRENT: user_input[CONF_MAX_CONCURRENT],
                    CONF_AUDIO_BUFFER_SIZE: user_input[CONF_AUDIO_BUFFER_SIZE],
                    CONF_JSON_KEY_MAP: parsed,
                    CONF_HEALTH_MODE: user_input[CONF_HEALTH_MODE],
                    CONF_HEALTH_INTERVAL: user_input[CONF_HEALTH_INTERVAL],
                    CONF_FAILURE_THRESHOLD: user_input[CONF_FAILURE_THRESHOLD],
                }
                if self.config_entry.data.get(CONF_SERVICE_TYPE, SERVICE_TTS) == SERVICE_TTS:
                    merged[CONF_TTS_MODEL] = user_input[CONF_TTS_MODEL]
                    merged[CONF_OUTPUT_SAMPLE_RATE] = int(user_input[CONF_OUTPUT_SAMPLE_RATE])
                else:
                    merged[CONF_STT_MODEL] = user_input[CONF_STT_MODEL]
                    merged[CONF_ENABLE_PARTIAL] = user_input[CONF_ENABLE_PARTIAL]

                return self.async_create_entry(data=merged)

        # Pre-fill with current values
        current = {**self.config_entry.data, **self.config_entry.options}
        service_type = current.get(CONF_SERVICE_TYPE, SERVICE_TTS)
        json_map_str = json.dumps(current.get(CONF_JSON_KEY_MAP, {}))
        type_label = "TTS（语音合成）" if service_type == SERVICE_TTS else "STT（语音识别）"

        schema = vol.Schema(
            {
                vol.Required(CONF_WS_URL, default=current.get(CONF_WS_URL, DEFAULT_WS_URL)): selector.TextSelector(),
                vol.Required(CONF_CONNECT_TIMEOUT, default=current.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=30, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_WYOMING_HOST, default=current.get(CONF_WYOMING_HOST, DEFAULT_WYOMING_HOST)): selector.TextSelector(),
                vol.Required(CONF_WYOMING_PORT, default=current.get(CONF_WYOMING_PORT, DEFAULT_WYOMING_PORT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_MAX_CONCURRENT, default=current.get(CONF_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=32, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_AUDIO_BUFFER_SIZE, default=current.get(CONF_AUDIO_BUFFER_SIZE, DEFAULT_AUDIO_BUFFER_SIZE)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=16, max=256, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_JSON_KEY_MAP, default=json_map_str): str,
                vol.Required(CONF_HEALTH_MODE, default=current.get(CONF_HEALTH_MODE, DEFAULT_HEALTH_MODE)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["ws_connect", "http_health"],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HEALTH_INTERVAL, default=current.get(CONF_HEALTH_INTERVAL, DEFAULT_HEALTH_INTERVAL)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=120, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_FAILURE_THRESHOLD, default=current.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        if service_type == SERVICE_TTS:
            schema = schema.extend(
                vol.Schema({
                    vol.Required(CONF_TTS_MODEL, default=current.get(CONF_TTS_MODEL, DEFAULT_TTS_MODEL)): selector.TextSelector(),
                    vol.Required(CONF_OUTPUT_SAMPLE_RATE, default=str(current.get(CONF_OUTPUT_SAMPLE_RATE, DEFAULT_OUTPUT_SAMPLE_RATE))): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[str(r) for r in SAMPLE_RATES],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }).schema
            )
        else:
            schema = schema.extend(
                vol.Schema({
                    vol.Required(CONF_STT_MODEL, default=current.get(CONF_STT_MODEL, DEFAULT_STT_MODEL)): selector.TextSelector(),
                    vol.Required(CONF_ENABLE_PARTIAL, default=current.get(CONF_ENABLE_PARTIAL, DEFAULT_ENABLE_PARTIAL)): selector.BooleanSelector(),
                }).schema
            )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"service_type": type_label},
        )
