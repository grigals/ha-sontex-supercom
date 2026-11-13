"""Turn a decoded readout into per-device values.

Every radio device in a readout becomes one ``Meter`` holding its latest
values as plain JSON types, so it can be stored and restored as is.

This module has no Home Assistant imports.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from .const import PULSE_AUTO, PULSE_GAS
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
    # [ISO local time, counter value] pairs this readout tells us about: the
    # reading itself and the values the device stored at earlier dates.
    history: list[list] = field(default_factory=list)

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
    _add_history(meter, device)
    return meter


# The counter each kind of meter keeps.
_COUNTERS = {KIND_ALLOCATOR: ("HeatCostAllocator", 0),
             KIND_WATER: ("Volume", 0),
             KIND_PULSE: ("Volume", 1)}


def _add_history(meter: Meter, device: Device) -> None:
    """Collect the dated counter values and the per-month figures."""
    if meter.kind not in _COUNTERS or not meter.read_at:
        return
    name, sub_unit = _COUNTERS[meter.kind]
    read_at = dt.datetime.fromisoformat(meter.read_at)
    values: dict[int, float] = {}  # storage number -> counter value
    dates: dict[int, dt.date] = {}
    set_day = None
    for block in device.blocks:
        for record in block.records:
            if record.tariff or record.function:
                continue
            if (record.name == name and record.sub_unit == sub_unit
                    and isinstance(record.native, (int, float))):
                values.setdefault(record.storage, record.native)
            elif record.name == "Date" and record.sub_unit == 0:
                if record.date:
                    dates[record.storage] = record.date
                elif record.storage == 1 and record.attrs.get("Year") == "*":
                    set_day = (int(record.attrs["Month"]), int(record.attrs["Day"]))
    if 0 not in values:
        return

    points: dict[dt.datetime, float] = {}
    if meter.kind == KIND_ALLOCATOR:
        # Dated monthly values, and the value kept at the yearly set day,
        # after which the allocator starts again from zero.
        for storage, day in dates.items():
            if storage >= FIRST_HISTORY_STORAGE and storage in values:
                points[_midnight(day)] = values[storage]
        if set_day and 1 in values:
            try:
                day = dt.date(read_at.year, *set_day)
                if day > read_at.date():
                    day = dt.date(read_at.year - 1, *set_day)
                points[_midnight(day)] = values[1]
            except ValueError:
                pass
    else:
        # "Monthly value -1" is the value at the start of the current month,
        # -2 the month before, and so on. Unused slots at the far end are zero.
        month = read_at.date().replace(day=1)
        monthly = []
        for storage in range(1, 16):
            if storage not in values:
                break
            monthly.append((_midnight(month), values[storage]))
            month = (month - dt.timedelta(days=1)).replace(day=1)
        while monthly and monthly[-1][1] == 0:
            monthly.pop()
        points.update(monthly)
    points = {when: value for when, value in points.items() if when <= read_at}
    points[read_at] = values[0]

    series = sorted(points.items())
    meter.history = [[when.isoformat(), value] for when, value in series]

    this_month = _midnight(read_at.date().replace(day=1))
    last_month = _midnight((this_month.date() - dt.timedelta(days=1)).replace(day=1))
    totals = dict(zip((when for when, _ in series), running_totals(series)))
    unit = meter.points.get(f"{name.lower()}-{sub_unit}-0-0-0", {}).get("unit", "")
    if this_month in totals:
        meter.points["month_to_date"] = {
            "value": round(totals[read_at] - totals[this_month], 3),
            "name": None, "unit": unit}
        if last_month in totals:
            meter.points["previous_month"] = {
                "value": round(totals[this_month] - totals[last_month], 3),
                "name": None, "unit": unit}


def running_totals(series: list[tuple]) -> list[float]:
    """Consumption since the first point, for a time-ordered series.

    A counter that drops has been reset to zero (the allocators do this at
    their set day), so what it shows afterwards is all new consumption.
    """
    totals = []
    total = 0.0
    previous = None
    for _, value in series:
        if previous is not None:
            total += value - previous if value >= previous else value
        totals.append(round(total, 6))
        previous = value
    return totals


def _midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time())


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


def counts_gas(meter: Meter, pulse_medium: str) -> bool:
    """Whether a pulse adapter is counting gas rather than water."""
    if meter.kind != KIND_PULSE:
        return False
    if pulse_medium == PULSE_AUTO:
        return meter.channel_medium == MEDIUM_GAS
    return pulse_medium == PULSE_GAS


def device_name(meter: Meter, pulse_medium: str) -> str:
    if meter.kind == KIND_ALLOCATOR:
        label = "Heat allocator"
    elif meter.kind == KIND_WATER:
        label = "Water meter"
    elif meter.kind == KIND_PULSE:
        label = "Gas meter" if counts_gas(meter, pulse_medium) else "Water meter"
    elif meter.kind == KIND_REPEATER:
        label = "Repeater"
    elif meter.kind == KIND_CONCENTRATOR:
        label = "Concentrator"
    else:
        label = "Device"
    return f"{label} {meter.serial}"
