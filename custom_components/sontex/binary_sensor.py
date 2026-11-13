"""Fault, tamper and battery flags of Sontex radio devices."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PULSE_MEDIUM, DEFAULT_PULSE_MEDIUM
from .coordinator import SontexConfigEntry
from .entity import BINARY_SENSOR, SontexEntity, describe


async def async_setup_entry(hass: HomeAssistant, entry: SontexConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    pulse_medium = entry.options.get(CONF_PULSE_MEDIUM, DEFAULT_PULSE_MEDIUM)
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        """Add entities for devices and flags not seen before."""
        new = []
        for meter in coordinator.data.values():
            for key, point in meter.points.items():
                description = describe(meter, key, point, pulse_medium)
                if description.platform != BINARY_SENSOR or (meter.address, key) in known:
                    continue
                known.add((meter.address, key))
                new.append(SontexBinarySensor(coordinator, meter, key, description,
                                              pulse_medium))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class SontexBinarySensor(SontexEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self._value
