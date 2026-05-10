"""Health probe coordinator for vLLM Wyoming WS Bridge."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CONNECT_TIMEOUT,
    CONF_FAILURE_THRESHOLD,
    CONF_HEALTH_INTERVAL,
    CONF_HEALTH_MODE,
    CONF_WS_URL,
    DEFAULT_HEALTH_MODE,
    DOMAIN,
    LATENCY_WARN_MS,
    HealthState,
)

_LOGGER = logging.getLogger(__name__)


class VLLMHealthCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Periodically probe vLLM health and track connection state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_data: Mapping[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry_data.get(CONF_HEALTH_INTERVAL, 30)
            ),
        )
        self._ws_url: str = entry_data.get(CONF_WS_URL, "")
        self._connect_timeout: float = entry_data.get(CONF_CONNECT_TIMEOUT, 10)
        self._health_mode: str = entry_data.get(CONF_HEALTH_MODE, DEFAULT_HEALTH_MODE)
        self._failure_threshold: int = entry_data.get(CONF_FAILURE_THRESHOLD, 3)

        self._consecutive_failures = 0
        self._last_latency_ms: float | None = None
        self._state: HealthState = HealthState.CHECKING

    @property
    def state(self) -> HealthState:
        """Current health state."""
        return self._state

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive failure count."""
        return self._consecutive_failures

    @property
    def last_latency_ms(self) -> float | None:
        """Last measured probe latency in ms."""
        return self._last_latency_ms

    async def _async_update_data(self) -> dict[str, Any]:
        """Probe vLLM and return status dict."""
        try:
            if self._health_mode == "http_health":
                latency = await self._probe_http()
            else:
                latency = await self._probe_ws()
        except Exception:
            self._consecutive_failures += 1
            _LOGGER.warning(
                "Health probe failed (%d/%d)",
                self._consecutive_failures,
                self._failure_threshold,
            )
            if self._consecutive_failures >= self._failure_threshold:
                self._state = HealthState.OFFLINE
            else:
                self._state = HealthState.DEGRADED
            self._last_latency_ms = None
            return {
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "latency_ms": None,
            }

        self._consecutive_failures = 0
        self._last_latency_ms = latency
        self._state = HealthState.ONLINE

        if latency > LATENCY_WARN_MS:
            _LOGGER.warning("Health probe latency %.0fms exceeds %dms", latency, LATENCY_WARN_MS)

        return {
            "state": self._state,
            "consecutive_failures": 0,
            "latency_ms": latency,
        }

    async def _probe_ws(self) -> float:
        """Measure WS handshake round-trip time."""
        import websockets

        start = time.monotonic()
        async with asyncio.timeout(self._connect_timeout):
            async with websockets.connect(
                self._ws_url,
                open_timeout=self._connect_timeout,
            ) as _:
                elapsed = (time.monotonic() - start) * 1000
                return elapsed

    async def _probe_http(self) -> float:
        """Measure HTTP GET /v1/models round-trip time."""
        import aiohttp
        from urllib.parse import urlparse

        parsed = urlparse(self._ws_url)
        scheme = parsed.scheme.replace("ws", "http")
        models_url = f"{scheme}://{parsed.netloc}/v1/models"

        start = time.monotonic()
        async with asyncio.timeout(self._connect_timeout):
            async with aiohttp.ClientSession() as session:
                async with session.get(models_url) as resp:
                    resp.raise_for_status()
                    elapsed = (time.monotonic() - start) * 1000
                    return elapsed
