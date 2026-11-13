"""Turn a decoded readout into per-device values.

Every radio device in a readout becomes one ``Meter`` holding its latest
values as plain JSON types, so it can be stored and restored as is.

This module has no Home Assistant imports.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from .mbus import Block, Device, Readout

KIND_ALLOCATOR = "heat_allocator"
KIND_WATER = "water_meter"
KIND_PULSE = "pulse_adapter"
KIND_REPEATER = "repeater"
KIND_CONCENTRATOR = "concentrator"
KIND_UNKNOWN = "unknown"

MEDIUM_OTHER = 0x00
MEDIUM_GAS = 0x03
MEDIUM_WATER = 0x07
MEDIUM_ALLOCATOR = 0x08
MEDIUM_SYSTEM = 0x0E

# Storage numbers from 2 up hold history (monthly values and the like).
FIRST_HISTORY_STORAGE = 2

# The concentrator's other records are its own settings, including the
# network login it uses, so only these are passed on.
_CONCENTRATOR_RECORDS = {"ErrorFlags", "DateAndTime", "OperatingTimeBattery",
                         "OtherSoftwareVersion", "HardwareVersion",
                         "FabricationNumber"}
_ERROR_FLAGS = ["tamper", "battery_low", "external_alarm", "battery_cut"]
_STATUS_FLAGS = {2: "power_low", 3: "permanent_error", 4: "temporary_error"}


@dataclass
class Meter:
    address: str  # unique: serial, manufacturer, version and medium
    serial: str
    kind: str
    medium: int
    version: int
    read_at: str | None = None  # ISO local time of the reading
    sw_version: str | None = None
    hw_version: str | None = None
    channel_medium: int | None = None  # what a pulse adapter says it counts
    # key -> {"value": ..., "name": record name or None, "unit": str}
    points: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Meter":
        return cls(**{name: data[name] for name in cls.__dataclass_fields__
                      if name in data})


def meters_from_readout(readout: Readout) -> list[Meter]:
    meters = [_build_meter(device, readout) for device in readout.devices]
    for meter in meters:
        if meter.kind == KIND_CONCENTRATOR:
            meter.points["devices_in_readout"] = _point(len(meters) - 1)
    return meters


def _kind(device: Device) -> str:
    medium = device.blocks[0].medium
    if medium == MEDIUM_ALLOCATOR:
        return KIND_ALLOCATOR
    if medium == MEDIUM_WATER:
        return KIND_WATER
    if medium == MEDIUM_OTHER:
        return KIND_PULSE
    if medium == MEDIUM_SYSTEM:
        return KIND_CONCENTRATOR if device.index == 0 else KIND_REPEATER
    return KIND_UNKNOWN


def _build_meter(device: Device, readout: Readout) -> Meter:
    first = device.blocks[0]
    meter = Meter(address=first.sec_address, serial=first.ident,
                  kind=_kind(device), medium=first.medium, version=first.version)
    blocks = device.blocks[:1] if meter.kind == KIND_CONCENTRATOR else device.blocks
    for block in blocks:
        _add_block(meter, block)

    status = first.status
    for bit, name in _STATUS_FLAGS.items():
        meter.points[name] = _point(bool((status >> bit) & 1))
    if meter.read_at is None and readout.date:
        meter.read_at = _iso(readout.date)
    if meter.read_at:
        meter.points["reading_time"] = _point(meter.read_at)
    return meter


def _add_block(meter: Meter, block: Block) -> None:
    for record in block.records:
        if record.storage >= FIRST_HISTORY_STORAGE:
            continue
        if meter.kind == KIND_CONCENTRATOR and (
                record.name not in _CONCENTRATOR_RECORDS or record.storage):
            continue
        plain = (record.sub_unit, record.tariff, record.storage,
                 record.function) == (0, 0, 0, 0)

        if record.name == "DateAndTime" and plain:
            if isinstance(record.native, dt.datetime):
                meter.read_at = record.native.isoformat()
        elif record.name == "OtherSoftwareVersion" and plain:
            meter.sw_version = record.value
        elif record.name == "HardwareVersion" and plain:
            meter.hw_version = record.value
        elif record.name == "DeviceType" and record.sub_unit > 0:
            meter.channel_medium = record.native
        elif record.name == "ErrorFlags" and plain:
            flags = record.native
            for bit, name in enumerate(_ERROR_FLAGS):
                meter.points[name] = _point(bool((flags >> bit) & 1))
            level = (flags >> 4) & 7
            if level and meter.kind in (KIND_WATER, KIND_PULSE):
                meter.points["rssi"] = _point(-110 + 10 * level)
        else:
            if record.name == "ManufacturerErrorFlags" and plain:
                meter.points["fault"] = _point(bool(record.native))
            key = record.access_key.lower()
            if key in meter.points:  # the same key twice in one telegram
                key += "#2"
            value = record.native
            if isinstance(value, (dt.date, dt.datetime)):
                value = value.isoformat()
            elif record.name == "Date" and str(value).startswith("R/"):
                # A date that repeats every year, such as the set day.
                value = f"{record.attrs['Day']}.{record.attrs['Month']}. every year"
            meter.points[key] = {"value": value, "name": record.name,
                                 "unit": record.unit}


def _point(value) -> dict:
    return {"value": value, "name": None, "unit": ""}


def _iso(text: str) -> str | None:
    try:
        return dt.datetime.fromisoformat(text).isoformat()
    except ValueError:
        return None
