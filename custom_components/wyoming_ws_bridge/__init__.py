"""Wyoming WS Bridge integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_WYOMING_HOST,
    CONF_WYOMING_PORT,
    DOMAIN,
    DEFAULT_WYOMING_HOST,
    DEFAULT_WYOMING_PORT,
)
from .coordinator import VLLMHealthCoordinator
from .server import VLLMHandler

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor"]

type WyomingWSBridgeConfigEntry = ConfigEntry[dict[str, Any]]


def _merge_config(entry: ConfigEntry) -> dict[str, Any]:
    """Merge data and options into a single config dict."""
    config: dict[str, Any] = {**entry.data}
    config.update(entry.options)
    return config


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wyoming WS Bridge from a config entry."""
    config = _merge_config(entry)
    host = config.get(CONF_WYOMING_HOST, DEFAULT_WYOMING_HOST)
    port = config.get(CONF_WYOMING_PORT, DEFAULT_WYOMING_PORT)

    # ── Check for port conflicts ──────────────────────────────────────
    for existing in hass.config_entries.async_entries(DOMAIN):
        if existing.entry_id == entry.entry_id or not existing.state.domain:
            continue
        existing_data = _merge_config(existing)
        if (
            existing_data.get(CONF_WYOMING_HOST, DEFAULT_WYOMING_HOST) == host
            and existing_data.get(CONF_WYOMING_PORT, DEFAULT_WYOMING_PORT) == port
        ):
            _LOGGER.error(
                "Port %d already in use by entry %s", port, existing.title
            )
            return False

    # ── Health coordinator ──────────────────────────────────────────
    coordinator = VLLMHealthCoordinator(hass, config)

    # Store state early so platforms and the server task can access it
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "config": config,
        "server_task": None,
    }

    # First health probe (timeout from HA triggers ConfigEntryNotReady retry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Non-critical: integration can still start, sensor will show unknown
        _LOGGER.warning("Initial health probe failed, continuing anyway")

    # ── Wyoming TCP server ─────────────────────────────────────────

    async def _start_server() -> None:
        """Run the Wyoming TCP server, accepting client connections."""
        async def _handle_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            peer = writer.get_extra_info("peername")
            _LOGGER.debug("Wyoming client connected from %s", peer)
            handler = VLLMHandler(reader, writer, config)
            try:
                await handler.run()
            finally:
                _LOGGER.debug("Wyoming client disconnected from %s", peer)

        try:
            server = await asyncio.start_server(_handle_client, host, port)
            _LOGGER.info("Wyoming server listening on %s:%d", host, port)
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            _LOGGER.info("Wyoming server shutting down")
        except OSError as exc:
            _LOGGER.error("Failed to bind Wyoming server on %s:%d: %s", host, port, exc)

    # Start server as background task
    task = hass.async_create_background_task(_start_server(), "wyoming_ws_bridge_server")
    hass.data[DOMAIN][entry.entry_id]["server_task"] = task

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.pop(entry.entry_id, None)
    if entry_data:
        task = entry_data.get("server_task")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return unload_ok
