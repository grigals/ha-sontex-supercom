"""How each decoded value shows up in Home Assistant."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SontexCoordinator
from .model import (
    KIND_ALLOCATOR,
    KIND_CONCENTRATOR,
    KIND_PULSE,
    KIND_REPEATER,
    KIND_WATER,
    Meter,
    counts_gas,
    device_name,
)

SENSOR = "sensor"
BINARY_SENSOR = "binary_sensor"

# Product numbers inferred from the per-product files Tools646 writes.
_MODELS = {
    KIND_ALLOCATOR: "Sontex 566 heat cost allocator",
    KIND_WATER: "Supercom 581 radio module",
    KIND_PULSE: "Supercom 541 pulse adapter",
    KIND_REPEATER: "Supercom 656 repeater",
    KIND_CONCENTRATOR: "Supercom 646 concentrator",
}


@dataclass(frozen=True)
class Description:
    name: str
    platform: str = SENSOR
    device_class: str | None = None
    unit: str | None = None
    state_class: str | None = None
    diagnostic: bool = True
    enabled: bool = True
    timestamp: bool = False


def _binary(name: str, device_class: str) -> Description:
    return Description(name, BINARY_SENSOR, device_class)


# Values the integration derives itself.
_DERIVED = {
    "reading_time": Description("Last reading", device_class=SensorDeviceClass.TIMESTAMP,
                                timestamp=True),
    "rssi": Description("Signal strength", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                        unit=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                        state_class=SensorStateClass.MEASUREMENT),
    "devices_in_readout": Description("Devices in last readout",
                                      state_class=SensorStateClass.MEASUREMENT),
    "month_to_date": Description("This month", diagnostic=False),
    "previous_month": Description("Previous month", diagnostic=False),
    "tamper": _binary("Tamper", BinarySensorDeviceClass.TAMPER),
    "battery_low": _binary("Battery low", BinarySensorDeviceClass.BATTERY),
    "external_alarm": _binary("External alarm", BinarySensorDeviceClass.PROBLEM),
    "battery_cut": _binary("Battery cut", BinarySensorDeviceClass.PROBLEM),
    "power_low": _binary("Power low", BinarySensorDeviceClass.BATTERY),
    "permanent_error": _binary("Permanent error", BinarySensorDeviceClass.PROBLEM),
    "temporary_error": _binary("Temporary error", BinarySensorDeviceClass.PROBLEM),
    "fault": _binary("Fault", BinarySensorDeviceClass.PROBLEM),
}

_TEMPERATURE = {"device_class": SensorDeviceClass.TEMPERATURE,
                "unit": UnitOfTemperature.CELSIUS,
                "state_class": SensorStateClass.MEASUREMENT}

# Decoded records with a friendlier name or a place among the main sensors.
_NAMED = {
    "heatcostallocator-0-0-0-0": Description(
        "Consumption", state_class=SensorStateClass.TOTAL_INCREASING, diagnostic=False),
    "heatcostallocator-0-0-1-0": Description("Consumption at last set day", diagnostic=False),
    "date-0-0-1-0": Description("Set day"),
    "flowtemperature-0-0-0-0": Description(
        "Radiator temperature", diagnostic=False, **_TEMPERATURE),
    "externaltemperature-0-0-0-0": Description(
        "Room temperature", diagnostic=False, **_TEMPERATURE),
    "flowtemperature-0-0-0-1": Description("Maximum radiator temperature", **_TEMPERATURE),
    "flowtemperature-0-0-1-1": Description(
        "Maximum radiator temperature of previous period", **_TEMPERATURE),
    "durationoftariff-0-1-0-0": Description(
        "Fraud duration", device_class=SensorDeviceClass.DURATION,
        unit=UnitOfTime.MINUTES),
    "date-0-1-0-0": Description("Fraud date"),
    "operatingtimebattery-0-0-0-0": Description(
        "Battery operating time", device_class=SensorDeviceClass.DURATION,
        unit=UnitOfTime.HOURS),
    "fabricationnumber-0-0-0-0": Description("Fabrication number"),
    "identificationnumber-1-0-0-0": Description("Meter number"),
    "numberoftimesthemeterwasstopped-0-0-0-0": Description("Stop count"),
    "manufacturererrorflags-0-0-0-0": Description("Error code"),
}
_MONTH_FIGURES = {"month_to_date", "previous_month"}
_MINUTES = {"device_class": SensorDeviceClass.DURATION, "unit": UnitOfTime.MINUTES}
_LITRES = {"unit": UnitOfVolume.LITERS}

# Names Sontex's own software uses, where they differ by kind of device.
_NAMED_BY_KIND = {
    (KIND_ALLOCATOR, "date-0-1-0-0"): Description("Last case opening"),
    (KIND_ALLOCATOR, "date-0-2-0-0"): Description("Commissioning date"),
    (KIND_WATER, "date-0-1-0-0"): Description("Last fraud date"),
    (KIND_WATER, "durationoftariff-0-2-0-0"): Description("Magnet tamper duration", **_MINUTES),
    (KIND_WATER, "date-0-2-0-0"): Description("Last magnet tamper date"),
    (KIND_WATER, "volume-0-0-0-0#2"): Description("Volume remainder", **_LITRES),
    (KIND_WATER, "volume_perinputpulseonchannel0-0-0-0-0"): Description("Pulse factor", unit="L/pulse"),
    (KIND_WATER, "numberoftimesthemeterwasstopped-0-0-0-0"): Description("Reset count"),
    (KIND_PULSE, "volume_perinputpulseonchannel0-1-0-0-0"): Description("Pulse factor", unit="L/pulse"),
    (KIND_PULSE, "numberoftimesthemeterwasstopped-0-0-0-0"): Description("Reset count"),
}
_MAIN_VOLUMES = {"volume-0-0-0-0", "volume-1-0-0-0"}
_OFF_BY_DEFAULT = {"StateOfParameterActivation", "DeviceAccessRightLevel",
                   "AccessNumber", "WakeupCount"}
_DURATION_UNITS = {"s": UnitOfTime.SECONDS, "min": UnitOfTime.MINUTES,
                   "h": UnitOfTime.HOURS, "d": UnitOfTime.DAYS}
_FUNCTIONS = {1: "maximum", 2: "minimum", 3: "during error"}


def describe(meter: Meter, key: str, point: dict, pulse_medium: str) -> Description:
    """Decide the name, type and unit of one value of one device."""
    if point["name"] is None:
        description = _DERIVED.get(key, Description(key.replace("_", " ").capitalize()))
        if key in _MONTH_FIGURES and point["unit"] == "m3":
            gas = counts_gas(meter, pulse_medium)
            description = replace(
                description, unit=UnitOfVolume.CUBIC_METERS,
                device_class=SensorDeviceClass.GAS if gas else SensorDeviceClass.WATER)
        return description
    if (meter.kind, key) in _NAMED_BY_KIND:
        return _NAMED_BY_KIND[meter.kind, key]
    if key in _NAMED:
        return _NAMED[key]

    name = _readable_name(key, point["name"])
    unit = point["unit"]
    numeric = isinstance(point["value"], (int, float))
    if key in _MAIN_VOLUMES and unit == "m3":
        gas = counts_gas(meter, pulse_medium)
        return Description(
            "Volume",
            device_class=SensorDeviceClass.GAS if gas else SensorDeviceClass.WATER,
            unit=UnitOfVolume.CUBIC_METERS,
            state_class=SensorStateClass.TOTAL_INCREASING, diagnostic=False)
    if unit == "°C" and numeric:
        return Description(name, **_TEMPERATURE)
    if unit == "m3" and numeric:
        return Description(name, unit=UnitOfVolume.CUBIC_METERS)
    if unit == "L" and numeric:
        return Description(name, unit=UnitOfVolume.LITERS)
    if unit in _DURATION_UNITS and numeric:
        return Description(name, device_class=SensorDeviceClass.DURATION,
                           unit=_DURATION_UNITS[unit])
    if unit and numeric:
        return Description(name, unit=unit.replace("(L)", "L"))
    enabled = point["name"] not in _OFF_BY_DEFAULT and not point["name"].startswith("Vif")
    return Description(name, enabled=enabled)


def _readable_name(key: str, record_name: str) -> str:
    """'Volume_PerInputPulseOnChannel0' -> 'Volume per input pulse on channel 0'."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|_", " ", record_name).split()
    name = " ".join(words).capitalize()
    fields, _, repeat = key.partition("#")
    sub_unit, tariff, storage, function = (int(n) for n in fields.split("-")[-4:])
    extra = []
    if sub_unit:
        extra.append(f"channel {sub_unit}")
    if tariff:
        extra.append(f"tariff {tariff}")
    if storage:
        extra.append(f"storage {storage}")
    if function:
        extra.append(_FUNCTIONS.get(function, f"function {function}"))
    if repeat:
        extra.append(repeat)
    return f"{name} ({', '.join(extra)})" if extra else name


class SontexEntity(CoordinatorEntity[SontexCoordinator]):
    """One value of one radio device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SontexCoordinator, meter: Meter, key: str,
                 description: Description, pulse_medium: str) -> None:
        super().__init__(coordinator)
        self._address = meter.address
        self._key = key
        self._description = description
        self._attr_unique_id = f"{meter.address}_{key}"
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_entity_registry_enabled_default = description.enabled
        if description.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter.address)},
            name=device_name(meter, pulse_medium),
            manufacturer=MANUFACTURER,
            model=_MODELS.get(meter.kind),
            serial_number=meter.serial,
            sw_version=meter.sw_version,
            hw_version=meter.hw_version,
        )
        concentrator = coordinator.concentrator
        if concentrator and concentrator != meter.address:
            # Newer Home Assistant wants the parent's device id.
            if "via_device_id" in DeviceInfo.__annotations__ and coordinator.concentrator_device_id:
                self._attr_device_info["via_device_id"] = coordinator.concentrator_device_id
            else:
                self._attr_device_info["via_device"] = (DOMAIN, concentrator)

    @property
    def _value(self):
        meter = self.coordinator.data.get(self._address)
        point = meter.points.get(self._key) if meter else None
        return point["value"] if point else None

    @property
    def available(self) -> bool:
        # Readouts arrive a few times a month; a failed poll changes nothing.
        return self._value is not None
