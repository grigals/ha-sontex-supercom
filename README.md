# Sontex Supercom for Home Assistant

A Home Assistant integration for Sontex radio meters read by a **Supercom 646 concentrator**. The concentrator uploads its readouts to an FTP server; this integration reads those files, decodes them itself (no Sontex software needed) and creates a device with sensors for every radio device in the readout.

The FTP server is only listed and read. Nothing is deleted or changed there.

## What you get

Each radio device becomes a Home Assistant device you can assign to an area:

| In the readout | Device in Home Assistant | Main sensors |
| --- | --- | --- |
| Heat cost allocator (566) | Heat allocator *serial* | Consumption, consumption at set day, radiator temperature, room temperature |
| Water meter radio module (581) | Water meter *serial* | Volume |
| Pulse adapter (541), shown as "Other" | Water meter *serial* (or gas, see Options) | Volume |
| Repeater (656) | Repeater *serial* | diagnostics only |
| Concentrator (646) | Concentrator *serial* | Last reading, devices in last readout |

Under each device, as diagnostics: tamper, battery low, battery cut, external alarm, fault and error flags, signal strength, battery operating time, last reading time, fraud duration and date, maximum temperatures, pulse value, fabrication number, firmware version and the remaining decoded fields.

Not included yet:

- **History.** Monthly values stored inside the meters, and older readout files, are not imported into Home Assistant statistics. Sensors show the latest reading only.
- **Concentrator settings.** Its readout also holds its network configuration, including logins. Those are deliberately never turned into entities.

Product numbers (566, 581, 541, 656) are inferred from the files Sontex Tools646 produces for this site, not read from the devices.

## Install

### With HACS

HACS can only read public GitHub repositories.

1. In HACS open the menu at the top right and choose **Custom repositories**.
2. Add this repository's URL with type **Integration**.
3. Find **Sontex Supercom** in HACS, download it, and restart Home Assistant.

### By hand

Copy the `custom_components/sontex` folder into the `custom_components` folder of your Home Assistant configuration and restart Home Assistant.

## Set up

1. **Settings > Devices & services > Add integration > Sontex Supercom**.
2. Enter the FTP server, port, username, password and the folder holding the readout files (named like `10000000_2026-02-20_101602.ini`).

The first run reads the files of the last 30 days and keeps the newest reading of each device.

### Options

Open the integration and choose **Configure**:

- **Pulse adapters count**: a pulse adapter reports itself as "Other". By default the integration uses what the adapter says its input is and falls back to water. Set this to *Water* or *Gas* to decide it yourself.
- **Check for new readouts every (hours)**: default 6.
- **Ignore readout files older than (days)**: default 30.

## Development

The tests run inside Home Assistant's test harness:

```
pip install pytest-homeassistant-custom-component
pytest
```

`tests/fixtures` holds three real readouts with the concentrator and repeaters removed and the serial numbers replaced by made-up ones, next to the XML Sontex Tools646 produced from the same files. The decoder is checked record by record against that XML.
