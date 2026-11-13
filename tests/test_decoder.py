"""Check the decoder against XML that Sontex Tools646 made from the same files.

The fixtures are real readouts with the serial numbers replaced by made-up
ones and the concentrator and repeaters removed
(their frames hold the site's network settings).
"""

import gzip
import xml.etree.ElementTree as ET

import pytest

from custom_components.sontex.mbus import parse_ini
from custom_components.sontex.model import (
    KIND_ALLOCATOR,
    KIND_CONCENTRATOR,
    KIND_PULSE,
    KIND_REPEATER,
    KIND_WATER,
    MEDIUM_WATER,
    meters_from_readout,
)

from .conftest import FIXTURES, FULL_SAMPLES, full_sample_text

FLAG_ATTRS = ["Tamper", "BatteryLow", "ExternalAlarm", "BatteryCut"]


def expected_devices(xml_bytes):
    """SecAddress -> per telegram (header values, records) from Tools646."""
    found = {}
    root = ET.fromstring(xml_bytes)
    for device in root.find("Devices646Data").findall("Device"):
        blocks = []
        for block in device.find("Data").findall("MbusBlock"):
            header = {r.get("AccessKey"): r.find("Value").text
                      for r in block.find("Header")}
            records = []
            for record in block.find("Records"):
                value = record.find("Value")
                flags = ([value.get(a) for a in FLAG_ATTRS]
                         if record.get("Name") == "ErrorFlags" else None)
                records.append((record.get("AccessKey"), value.text or "",
                                record.findtext("PhysicalUnit") or "", flags))
            blocks.append((header, records))
        found[device.get("SecAddress")] = blocks
    return found


def decoded_devices(readout):
    found = {}
    for device in readout.devices:
        blocks = []
        for block in device.blocks:
            header = {
                "IdentificationNumber": block.ident,
                "Manufacturer": block.manufacturer,
                "Version": str(block.version),
                "DeviceType": block.medium_name,
                "AccessNumber": str(block.access_number),
                "Status": str(block.status),
                "Signature": str(block.signature),
            }
            records = [(r.access_key, r.value, r.unit,
                        [r.attrs[a] for a in FLAG_ATTRS] if r.name == "ErrorFlags" else None)
                       for r in block.records]
            blocks.append((header, records))
        found[device.blocks[0].sec_address] = blocks
    return found


@pytest.mark.parametrize("ini", sorted(FIXTURES.glob("*.ini")), ids=lambda p: p.stem)
def test_records_match_tools646(ini):
    readout = parse_ini(ini.read_text(encoding="utf-8"))
    assert readout.errors == []
    ours = decoded_devices(readout)
    theirs = expected_devices(gzip.decompress((FIXTURES / f"{ini.stem}.xml.gz").read_bytes()))
    assert len(theirs) == 69
    assert sorted(ours) == sorted(theirs)
    for address, blocks in theirs.items():
        assert ours[address] == blocks, address


def test_meters_have_native_values(sample_text):
    meters = {m.serial: m for m in meters_from_readout(parse_ini(sample_text))}
    kinds = [m.kind for m in meters.values()]
    assert (kinds.count(KIND_ALLOCATOR), kinds.count(KIND_WATER),
            kinds.count(KIND_PULSE)) == (50, 17, 2)

    allocator = meters["10000014"]
    assert allocator.read_at == "2026-02-20T10:06:00"
    assert allocator.sw_version == "1.4.0"
    assert allocator.points["heatcostallocator-0-0-0-0"]["value"] == 56
    assert allocator.points["flowtemperature-0-0-0-0"]["value"] == 22.03
    assert allocator.points["fault"]["value"] is False
    # Monthly history is not turned into values.
    assert not any("-48-" in key or "-65-" in key for key in allocator.points)

    water = meters["10000074"]
    assert water.points["volume-0-0-0-0"]["value"] == 41.708
    assert water.points["volume-0-0-0-0#2"]["unit"] == "L"
    assert water.points["tamper"]["value"] is False
    assert water.points["rssi"]["value"] == -60

    pulse = meters["10000001"]
    assert pulse.channel_medium == MEDIUM_WATER
    assert pulse.points["volume-1-0-0-0"]["value"] == 2248.0
    assert pulse.sw_version == "1.0.1"


@pytest.mark.skipif(not FULL_SAMPLES.exists(), reason="complete readouts are not in git")
def test_concentrator_and_repeaters():
    readout = parse_ini(full_sample_text())
    assert readout.errors == []
    meters = meters_from_readout(readout)
    kinds = [m.kind for m in meters]
    assert kinds.count(KIND_CONCENTRATOR) == 1
    assert kinds.count(KIND_REPEATER) == 2

    concentrator = next(m for m in meters if m.kind == KIND_CONCENTRATOR)
    assert concentrator.serial == readout.central_id
    assert concentrator.sw_version == "1.7.1"
    assert concentrator.hw_version == "1.4"
    assert concentrator.points["devices_in_readout"]["value"] == len(meters) - 1
    # None of its settings (network login and so on) are passed on.
    names = {point["name"] for point in concentrator.points.values()}
    assert names <= {None, "OperatingTimeBattery", "FabricationNumber"}

    repeater = next(m for m in meters if m.kind == KIND_REPEATER)
    assert repeater.hw_version == "1.4"
    assert repeater.points["operatingtimebattery-0-0-0-0"]["value"] > 0
