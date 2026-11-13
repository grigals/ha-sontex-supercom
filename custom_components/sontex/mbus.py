"""Decode Sontex Supercom 646 INI readouts.

Each ``dN-M=`` line of the INI is a base64-encoded M-Bus long frame
(EN 13757-3, CI 0x72, variable data structure). ``N`` is the device and ``M``
the telegram number within that device.

Record names, value text and units follow what Sontex Tools646 writes to its
XML, which is what the decoder is checked against. Each record also carries a
``native`` value (number, date, datetime or text) for Home Assistant. Only the
VIF codes seen in real readouts are named; anything else gets a ``Vif<hex>``
name and a hex value, so an unknown field never stops a file from decoding.

This module has no Home Assistant imports.
"""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import math
import re
import struct
from dataclasses import dataclass, field

MEDIUM_NAMES = {
    0x00: "Other",
    0x07: "Water",
    0x08: "HeatCostAllocator",
    0x0E: "BusSystemComponent",
}

# Bytes of data for each DIF data field code (13 is variable length).
_DATA_LENGTH = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 6, 7: 8, 8: 0,
                9: 1, 10: 2, 11: 3, 12: 4, 14: 6, 15: 8}
_BCD_FIELDS = {9, 10, 11, 12, 14}
_REAL_FIELD = 5
_VARIABLE_FIELD = 13

_FRAME_LINE = re.compile(r"^d(\d+)-(\d+)=(.+)$")
_ERROR_FLAG_NAMES = ["Tamper", "BatteryLow", "ExternalAlarm", "BatteryCut"]
_STATUS_FLAG_NAMES = ["PowerLow", "PermanentError", "TemporaryError",
                      "Specific1", "Specific2", "Specific3"]


class FrameError(ValueError):
    """A frame that is not a valid M-Bus long frame with CI 0x72."""


@dataclass
class Record:
    name: str
    sub_unit: int
    tariff: int
    storage: int
    function: int
    value: str
    unit: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    date: dt.date | None = None  # set for complete Date records only
    native: object = None  # int, float, str, date or datetime

    @property
    def access_key(self) -> str:
        return (f"{self.name}-{self.sub_unit}-{self.tariff}-"
                f"{self.storage}-{self.function}")


@dataclass
class Block:
    """One telegram: the fixed header and its data records."""

    ident: str
    manufacturer: str
    version: int
    medium: int
    access_number: int
    status: int
    signature: int
    records: list[Record]

    @property
    def medium_name(self) -> str:
        return MEDIUM_NAMES.get(self.medium, f"Medium{self.medium:02X}")

    @property
    def sec_address(self) -> str:
        ident = bytes.fromhex(self.ident)[::-1].hex()
        man = _encode_manufacturer(self.manufacturer)
        return f"{ident}{man}{self.version:02X}{self.medium:02X}".upper()

    @property
    def device_model(self) -> str:
        return f"{self.manufacturer}{self.version:02X}{self.medium:02X}"


@dataclass
class Device:
    index: int
    blocks: list[Block]


@dataclass
class Readout:
    central_id: str
    date: str
    devices: list[Device]
    errors: list[str]


def parse_ini(text: str) -> Readout:
    """Decode a whole INI file. Bad frames are reported in ``errors``."""
    settings: dict[str, str] = {}
    frames: dict[int, list[tuple[int, str]]] = {}
    for line in text.splitlines():
        line = line.strip()
        match = _FRAME_LINE.match(line)
        if match:
            frames.setdefault(int(match[1]), []).append((int(match[2]), match[3]))
        elif "=" in line:
            key, _, value = line.partition("=")
            settings[key.strip()] = value.strip()

    devices: list[Device] = []
    errors: list[str] = []
    for index in sorted(frames):
        blocks = []
        for number, encoded in sorted(frames[index]):
            try:
                blocks.append(parse_frame(base64.b64decode(encoded)))
            except (FrameError, ValueError) as err:
                errors.append(f"d{index}-{number}: {err}")
        if blocks:
            devices.append(Device(index, blocks))
    return Readout(settings.get("centralId", ""), settings.get("date", ""),
                   devices, errors)


def parse_frame(raw: bytes) -> Block:
    if len(raw) < 21 or raw[0] != 0x68 or raw[3] != 0x68 or raw[-1] != 0x16:
        raise FrameError("not an M-Bus long frame")
    if raw[1] != raw[2] or raw[1] != len(raw) - 6:
        raise FrameError("length field does not match frame size")
    if sum(raw[4:-2]) & 0xFF != raw[-2]:
        raise FrameError("checksum mismatch")
    if raw[6] != 0x72:
        raise FrameError(f"unsupported CI field 0x{raw[6]:02X}")

    manufacturer = int.from_bytes(raw[11:13], "little")
    return Block(
        ident=raw[7:11][::-1].hex(),
        manufacturer="".join(chr(((manufacturer >> shift) & 0x1F) + 64)
                             for shift in (10, 5, 0)),
        version=raw[13],
        medium=raw[14],
        access_number=raw[15],
        status=raw[16],
        signature=int.from_bytes(raw[17:19], "little"),
        records=_parse_records(raw[19:-2]),
    )


def status_attrs(status: int) -> dict[str, str]:
    attrs = {"HexValue": f"{status:02X}", "BinValue": f"{status:08b}",
             "ApplicationError": str(status & 3)}
    for bit, name in enumerate(_STATUS_FLAG_NAMES, start=2):
        attrs[name] = str((status >> bit) & 1)
    return attrs


def medium_attrs(medium: int) -> dict[str, str]:
    return {"DecValue": str(medium), "HexValue": f"{medium:02X}"}


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

def _parse_records(body: bytes) -> list[Record]:
    records: list[Record] = []
    pos = 0
    try:
        while pos < len(body):
            dif = body[pos]
            pos += 1
            if dif in (0x0F, 0x1F):  # manufacturer data / more telegrams follow
                break
            if dif == 0x2F:  # idle filler
                continue

            data_field = dif & 0x0F
            function = (dif >> 4) & 3
            storage = (dif >> 6) & 1
            tariff = sub_unit = 0
            extension = 0
            byte = dif
            while byte & 0x80:
                byte = body[pos]
                pos += 1
                storage |= (byte & 0x0F) << (1 + 4 * extension)
                tariff |= ((byte >> 4) & 3) << (2 * extension)
                sub_unit |= ((byte >> 6) & 1) << extension
                extension += 1

            vif = [body[pos]]
            pos += 1
            if vif[0] & 0x7F == 0x7C:  # plain-text VIF
                pos += 1 + body[pos]
            while vif[-1] & 0x80:
                vif.append(body[pos])
                pos += 1

            if data_field == _VARIABLE_FIELD:
                length = body[pos]
                pos += 1
            else:
                length = _DATA_LENGTH[data_field]
            data = body[pos:pos + length]
            if len(data) < length:
                raise IndexError
            pos += length

            record = _build_record(vif, data_field, data,
                                   sub_unit, tariff, storage, function)
            records.append(record)
            if record.name.endswith("_CompactProfileWithRegisters"):
                records.extend(_expand_profile(record, data, records))
    except IndexError:
        pass  # truncated telegram: keep what was decoded
    return records


def _build_record(vif, data_field, data, sub_unit, tariff, storage, function):
    name, kind, exponent, unit = _describe_vif(vif)
    record = Record(name, sub_unit, tariff, storage, function, "", unit)

    if kind == "date" and len(data) == 2:
        _fill_date(record, data)
    elif kind == "datetime" and len(data) == 4:
        _fill_datetime(record, data)
    elif kind == "error_flags":
        value = int.from_bytes(data, "little")
        bits = 8 * len(data)
        record.value = str(value)
        record.native = value
        record.attrs = {"HexValue": f"{value:0{bits // 4}X}",
                        "BinValue": f"{value:0{bits}b}"}
        for bit, flag in enumerate(_ERROR_FLAG_NAMES):
            record.attrs[flag] = str((value >> bit) & 1)
        for bit in range(len(_ERROR_FLAG_NAMES), bits):
            record.attrs[f"Bit{bit}"] = str((value >> bit) & 1)
    elif kind == "medium" and len(data) == 1:
        record.value = MEDIUM_NAMES.get(data[0], f"Medium{data[0]:02X}")
        record.attrs = medium_attrs(data[0])
        record.native = data[0]
    elif kind == "version" and len(data) >= 3:
        record.value = ".".join(_bcd_or_int(b) for b in (data[2], data[1], data[0]))
    elif kind == "hardware_version" and len(data) >= 1:
        record.value = f"{data[0] // 10}.{data[0] % 10}"
    elif kind == "hex" or data_field == _VARIABLE_FIELD:
        record.value = data.hex().upper()
    else:
        record.value = _format_number(data_field, data, exponent)
        record.native = _to_number(record.value)
    if record.native is None:
        record.native = record.date or record.value
    return record


def _to_number(text: str) -> int | float | None:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _describe_vif(vif: list[int]) -> tuple[str, str, int, str]:
    """Return (name, kind, decimal exponent, unit) for a VIF byte sequence."""
    first = vif[0] & 0x7F
    name, kind, exponent, unit = f"Vif{bytes(vif).hex().upper()}", "hex", 0, ""
    extensions = vif[1:]

    if first == 0x7D and extensions:  # 0xFD: first extension table
        code = extensions[0] & 0x7F
        extensions = extensions[1:]
        if code == 0x08:
            name, kind = "AccessNumber", "number"
        elif code == 0x09:
            name, kind = "DeviceType", "medium"
        elif code == 0x0D:
            name, kind = "HardwareVersion", "hardware_version"
        elif code == 0x0F:
            name, kind = "OtherSoftwareVersion", "version"
        elif code == 0x17:
            name, kind = "ErrorFlags", "error_flags"
        elif 0x30 <= code <= 0x33:
            name, kind = "DurationOfTariff", "number"
            unit = ["s", "min", "h", "d"][code & 3]
        elif code == 0x66:
            name, kind = "StateOfParameterActivation", "number"
        elif 0x6C <= code <= 0x6F:
            name, kind = "OperatingTimeBattery", "number"
            unit = ["h", "d", "month", "year"][code & 3]
        elif code == 0x75:
            name, kind = "NumberOfTimesTheMeterWasStopped", "number"
    elif first == 0x7F and extensions:  # 0xFF: Sontex-specific
        code = extensions[0] & 0x7F
        extensions = extensions[1:]
        sontex = {0x25: "WakeupCount", 0x2B: "DeviceAccessRightLevel",
                  0x2C: "ManufacturerErrorFlags", 0x2D: "Coefficient"}
        if code in sontex:
            name, kind = sontex[code], "number"
    elif 0x10 <= first <= 0x17:
        name, kind, exponent, unit = "Volume", "number", (first & 7) - 6, "m3"
        if exponent < -3:  # Tools646 shows sub-litre resolutions in litres
            exponent, unit = exponent + 3, "L"
    elif 0x58 <= first <= 0x5B:
        name, kind, exponent, unit = "FlowTemperature", "number", (first & 3) - 3, "°C"
    elif 0x5C <= first <= 0x5F:
        name, kind, exponent, unit = "ReturnTemperature", "number", (first & 3) - 3, "°C"
    elif 0x64 <= first <= 0x67:
        name, kind, exponent, unit = "ExternalTemperature", "number", (first & 3) - 3, "°C"
    elif first == 0x6C:
        name, kind = "Date", "date"
    elif first == 0x6D:
        name, kind = "DateAndTime", "datetime"
    elif first == 0x6E:
        name, kind = "HeatCostAllocator", "number"
    elif first == 0x78:
        name, kind = "FabricationNumber", "number"
    elif first == 0x79:
        name, kind = "IdentificationNumber", "number"

    for code in (e & 0x7F for e in extensions):
        if code == 0x1E:
            name, kind = name + "_CompactProfileWithRegisters", "hex"
        elif code == 0x28:
            name, unit = name + "_PerInputPulseOnChannel0", f"({unit})/pulse"
        else:
            name += f"_Vife{code:02X}"
    return name, kind, exponent, unit


def _format_number(data_field: int, data: bytes, exponent: int) -> str:
    if not data:
        return ""
    if data_field in _BCD_FIELDS:
        digits = data[::-1].hex()
        if not digits.isdigit():  # error nibbles
            return digits.upper()
        return _place_point(digits, exponent)
    if data_field == _REAL_FIELD:
        return _format_real(struct.unpack("<f", data)[0] * 10.0 ** exponent)
    value = int.from_bytes(data, "little", signed=True)
    sign = "-" if value < 0 else ""
    return sign + _place_point(str(abs(value)), exponent)


def _place_point(digits: str, exponent: int) -> str:
    """Scale a digit string by 10**exponent, keeping its leading zeros."""
    if exponent >= 0:
        return digits + "0" * exponent
    digits = digits.rjust(1 - exponent, "0")
    return f"{digits[:exponent]}.{digits[exponent:]}"


def _format_real(value: float) -> str:
    """Seven significant digits, engineering exponent, as Tools646 does."""
    if value == 0 or not math.isfinite(value):
        return f"{value:.6f}"
    exponent = math.floor(math.log10(abs(value)) / 3) * 3
    mantissa = value / 10.0 ** exponent
    decimals = 6 - math.floor(math.log10(abs(mantissa)))
    text = f"{mantissa:.{decimals}f}"
    return text if exponent == 0 else f"{text}E{exponent}"


def _bcd_or_int(byte: int) -> str:
    text = f"{byte:02x}"
    return str(int(text)) if text.isdigit() else str(byte)


def _fill_date(record: Record, data: bytes) -> None:
    day = data[0] & 0x1F
    month = data[1] & 0x0F
    year = ((data[0] & 0xE0) >> 5) | ((data[1] & 0xF0) >> 1)
    if year == 127:  # "every year"
        record.value = f"R/2000-{month:02d}-{day:02d}/P1Y"
        record.attrs = {"Year": "*", "YearMod100": "*"}
    else:
        record.value = f"{2000 + year}-{month:02d}-{day:02d}"
        record.attrs = {"Year": str(2000 + year), "YearMod100": f"{year:02d}"}
        try:
            record.date = dt.date(2000 + year, month, day)
        except ValueError:
            pass
    record.attrs.update(Month=f"{month:02d}", Day=f"{day:02d}")


def _fill_datetime(record: Record, data: bytes) -> None:
    minute = data[0] & 0x3F
    hour = data[1] & 0x1F
    day = data[2] & 0x1F
    month = data[3] & 0x0F
    year = ((data[2] & 0xE0) >> 5) | ((data[3] & 0xF0) >> 1)
    record.value = (f"{2000 + year}-{month:02d}-{day:02d}"
                    f"T{hour:02d}:{minute:02d}")
    record.attrs = {"Year": str(2000 + year), "YearMod100": f"{year:02d}",
                    "Month": f"{month:02d}", "Day": f"{day:02d}",
                    "Hour": f"{hour:02d}", "Minute": f"{minute:02d}"}
    try:
        record.native = dt.datetime(2000 + year, month, day, hour, minute)
    except ValueError:
        pass


def _expand_profile(profile: Record, data: bytes, earlier: list[Record]) -> list[Record]:
    """Turn a compact profile into the Date/value pairs it stands for.

    The profile holds one value per period after the base record that shares
    its storage number; each value takes the next storage number. The heat
    allocators use this for their monthly history.
    """
    if len(data) < 2:
        return []
    control, spacing = data[0], data[1]
    mode, spacing_unit, data_field = control >> 6, (control >> 4) & 3, control & 0x0F
    size = _DATA_LENGTH.get(data_field, 0)
    base = next((r for r in earlier
                 if r.name == "Date" and r.date is not None
                 and (r.sub_unit, r.tariff, r.storage, r.function)
                 == (profile.sub_unit, profile.tariff, profile.storage, profile.function)),
                None)
    # Only absolute values spaced in days or months are expanded.
    if mode != 0 or spacing_unit != 3 or size == 0 or base is None:
        return []

    name = profile.name.removesuffix("_CompactProfileWithRegisters")
    expanded: list[Record] = []
    values = [data[i:i + size] for i in range(2, len(data) - size + 1, size)]
    for step, raw in enumerate(values, start=1):
        if spacing == 254:
            when = _add_months(base.date, step)
        elif spacing == 253:
            break  # half-months: not used by these meters
        else:
            when = base.date + dt.timedelta(days=spacing * step)
        storage = profile.storage + step
        date_record = Record("Date", profile.sub_unit, profile.tariff, storage,
                             profile.function, when.isoformat(), date=when)
        date_record.attrs = {"Year": str(when.year), "YearMod100": f"{when.year % 100:02d}",
                             "Month": f"{when.month:02d}", "Day": f"{when.day:02d}"}
        date_record.native = when
        expanded.append(date_record)
        text = _format_number(data_field, raw, 0)
        expanded.append(Record(name, profile.sub_unit, profile.tariff, storage,
                               profile.function, text, native=_to_number(text)))
    return expanded


def _add_months(start: dt.date, months: int) -> dt.date:
    index = start.year * 12 + start.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _encode_manufacturer(code: str) -> str:
    value = 0
    for letter in code:
        value = (value << 5) | (ord(letter) - 64)
    return value.to_bytes(2, "little").hex()
