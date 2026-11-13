# Sontex Supercom for Home Assistant

A Home Assistant integration for Sontex radio meters read by a **Supercom 646 concentrator**. The concentrator uploads its readouts to an FTP server; this integration reads those files, decodes them itself (no Sontex software needed) and creates a device with sensors for every radio device in the readout.

The FTP server is only listed and read. Nothing is deleted or changed there.

> **Unofficial.** This project is not made, endorsed or supported by Sontex SA. Use it at your own risk. See [Disclaimer](#disclaimer).

## What you get

Each radio device becomes a Home Assistant device you can assign to an area:

| In the readout | Device in Home Assistant | Main sensors |
| --- | --- | --- |
| Heat cost allocator (566) | Heat allocator *serial* | Consumption, this month, previous month, consumption at last set day, radiator temperature, room temperature |
| Water meter radio module (581) | Water meter *serial* | Volume, this month, previous month |
| Pulse adapter (541), shown as "Other" | Water meter *serial* (or gas, see Options) | Volume, this month, previous month |
| Repeater (656) | Repeater *serial* | diagnostics only |
| Concentrator (646) | Concentrator *serial* | Last reading, devices in last readout |

Under each device, as diagnostics: tamper, battery low, battery cut, external alarm, fault and error flags, signal strength, battery operating time, last reading time, fraud duration and date, maximum temperatures, pulse value, fabrication number, firmware version and the remaining decoded fields.

The concentrator's readout also holds its network configuration, including logins. Those are deliberately never turned into entities.

Product numbers (566, 581, 541, 656) are inferred from the files Sontex Tools646 produces for this site, not read from the devices.

## Reading times and history

Readouts arrive a few times a month, and a file can reach the FTP server some time after the meters were read. Home Assistant stamps a sensor value with the moment it arrives, not the moment it was measured, so the normal history graph of a sensor can be late by hours or days. The **Last reading** sensor of each device shows when the value was really taken.

To give you correct figures regardless of when a file arrives, the integration uses what the meters store themselves:

- **This month** and **Previous month** sensors are calculated from the meter's own month-start values, so they do not depend on upload or fetch times.
- **Long-term statistics.** Each meter gets a statistic named like *Water meter 12345678 volume* or *Heat allocator 12345678 consumption*. Every reading is filed at the time the meter took it, and the meter's stored month-start values are filed at the month boundaries. Use these in the **Energy dashboard** (water or gas consumption) and in **Statistics graph** cards; monthly totals there are exact.

The meters carry their own past: heat allocators the first of each of the last 17 months, water meters and pulse adapters the last 15 months. That history appears in the statistics as soon as the first readout is processed.

Heat allocators start again from zero at their yearly set day. The statistic keeps counting upwards across that reset.

To also import older readout files from the FTP server, raise **Ignore readout files older than (days)** in the options. Older files only add points to the statistics; the sensors keep showing the newest reading.

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

## Disclaimer

- **Not affiliated with Sontex.** This is an independent, community project. It is not made, endorsed, certified or supported by Sontex SA. "Sontex" and "Supercom" are trademarks of their owner and are used here only to say which devices the integration works with. Please do not contact Sontex about problems with it.
- **No warranty, use at your own risk.** The software is provided as is, without warranty of any kind. The author is not liable for any damage, loss or cost arising from its use. See [LICENSE](LICENSE).
- **Not for billing.** The file format was worked out by comparing readouts with the output of Sontex's own software at a single site. Values may be wrong, incomplete or misinterpreted, especially for device models and firmware versions that were not available for testing. Do not use the readings for billing, cost allocation, legal metering or disputes; the meter and the official readout are the reference.
- **No effect on your devices.** The integration never talks to the meters or the concentrator and cannot change their configuration. It only reads files from the FTP server and never writes, renames or deletes anything there. Even so, prefer an FTP account that has read-only access.
- **Your data and credentials.** The FTP login is stored in Home Assistant's configuration, as for any integration. Standard FTP sends the login and the readouts unencrypted, so think about the network path between Home Assistant and the server.
- **Permission.** Make sure you are entitled to access the readout data. In a shared building the files can contain other households' consumption, which is personal data under laws such as the GDPR.
- **Readouts contain secrets.** The concentrator's part of a readout file includes its network configuration and logins. The integration does not expose them, but do not share raw readout files publicly, for example in a bug report.
- **May break.** Sontex can change the format in a firmware update, and Home Assistant updates can require changes to the integration. This is a pre-release.

## License

[MIT](LICENSE)
