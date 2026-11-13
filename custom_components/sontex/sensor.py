"""Sensors for Sontex radio devices."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_PULSE_MEDIUM, DEFAULT_PULSE_MEDIUM
from .coordinator import SontexConfigEntry
from .entity import SENSOR, SontexEntity, describe


async def async_setup_entry(hass: HomeAssistant, entry: SontexConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    pulse_medium = entry.options.get(CONF_PULSE_MEDIUM, DEFAULT_PULSE_MEDIUM)
    known: set[tuple[str, str]] = set()

    @callback
    def add_new() -> None:
        """Add entities for devices and values not seen before."""
        new = []
        for meter in coordinator.data.values():
            for key, point in meter.points.items():
                description = describe(meter, key, point, pulse_medium)
                if description.platform != SENSOR or (meter.address, key) in known:
                    continue
                known.add((meter.address, key))
                new.append(SontexSensor(coordinator, meter, key, description, pulse_medium))
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class SontexSensor(SontexEntity, SensorEntity):
    def __init__(self, coordinator, meter, key, description, pulse_medium) -> None:
        super().__init__(coordinator, meter, key, description, pulse_medium)
        self._attr_native_unit_of_measurement = description.unit
        self._attr_state_class = description.state_class

    @property
    def native_value(self):
        value = self._value
        if self._description.timestamp and isinstance(value, str):
            # The devices report local time without a zone.
            return datetime.fromisoformat(value).replace(
                tzinfo=dt_util.get_default_time_zone())
        return value
