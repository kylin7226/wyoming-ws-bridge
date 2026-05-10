"""Binary sensor entity for vLLM health status."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HealthState
from .coordinator import VLLMHealthCoordinator
from . import WyomingWSBridgeConfigEntry

ENTITY_DESC = BinarySensorEntityDescription(
    key="bridge_status",
    name="Wyoming WS Bridge Status",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WyomingWSBridgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: VLLMHealthCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([VLLMHealthSensor(coordinator, entry.entry_id)])


class VLLMHealthSensor(CoordinatorEntity[VLLMHealthCoordinator], BinarySensorEntity):
    """Binary sensor reflecting vLLM connection health."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VLLMHealthCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self.entity_description = ENTITY_DESC
        self._attr_unique_id = f"{entry_id}_bridge_status"

    @property
    def is_on(self) -> bool:
        """Return True if vLLM is online."""
        return self.coordinator.state == HealthState.ONLINE

    @property
    def extra_state_attributes(self) -> dict:
        """Additional diagnostic attributes."""
        return {
            "health_state": self.coordinator.state,
            "consecutive_failures": self.coordinator.consecutive_failures,
            "latency_ms": self.coordinator.last_latency_ms,
        }
