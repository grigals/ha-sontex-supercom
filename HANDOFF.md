# Sontex Supercom integration for Home Assistant

## What this is

A Home Assistant custom integration (`custom_components/sontex`, installable through HACS) that reads the readout files a Sontex Supercom 646 concentrator uploads to an FTP server, decodes them, and creates one HA device per radio device with sensors and fault flags. See `README.md` for what it exposes and how to install it.

## How it got here

The project started as "move the laptop's daily Tools646 conversion to the HA box": FTP → Tools646 XML → Google Drive → Apps Script → Sheets. A decoder, an HA add-on and an Apps Script upload endpoint were built and worked. On 2026-11-13 the direction changed: drop everything Google, make it a general Sontex integration, and put the data into HA as devices and sensors. The add-on, the XML writer and the upload endpoint were removed.

The laptop job (a scheduled PowerShell script that runs Tools646) is untouched and still feeds the Google Sheets. Feeding Google from HA may come back later as a separate step.

## Decisions

- **Integration, not add-on.** An integration can create devices and entities directly, has a UI setup flow, runs on any HA install, and installs through HACS. An add-on can only reach HA through MQTT or the REST API, and HACS does not distribute add-ons.
- **FTP is read-only.** Only login, change folder, list and download. Handled file names are remembered in HA's `.storage`.
- **Pulse adapters** report medium "Other". Theirs is taken from the sub-unit device type they report (water here), with an option to force water or gas.
- **Concentrator settings are never exposed.** Its telegrams hold APN, FTP login and so on. Only an allow-list of harmless records becomes entities.
- **`useful/` is git-ignored** (Tools646, full samples with those settings), as is `secrets.json` (FTP login for local tests).

## Findings about the format

- Each INI has one `[AutomaticReading]` section; every `dN-M=` line is a base64 M-Bus long frame (EN 13757-3, CI `0x72`). `N` is the device, `M` the telegram. `d0` is the concentrator.
- Device types seen (all manufacturer `SON`): version/medium `16/08` heat cost allocator, `16/07` water radio module, `0A/00` pulse adapter, `1E/0E` repeater, `11/0E` concentrator. The product numbers 566, 581, 541, 656, 646 are inferred from the per-product `.rlv` files Tools646 writes, by matching device counts.
- Heat allocators send their monthly history as one compact-profile record; the decoder expands it into date/value pairs (storage 49 up). Water meters send 15 history values without dates.
- `ErrorFlags` bits 0-3 are tamper, battery low, external alarm, battery cut; bits 4-6 are a signal level, RSSI = -110 + 10 × level dBm.
- Readouts arrive a few times a month, not daily.

## Verification

- Decoder: every record's key, value text and unit matches Tools646 for the 69 meters in each fixture (`tests/test_decoder.py`). Earlier, six full sample pairs matched (19,614 records).
- Integration: config flow, device and entity creation, pulse-medium option, restart and FTP-outage behaviour, month sensors and statistics (against the real recorder) run in HA's test harness (`pytest`, needs `pytest-homeassistant-custom-component`; HA 2026.2.3 was used).
- The FTP code was run read-only against the real server: 61 files listed, the five from the last 30 days decoded with no errors and no unnamed fields.
- Installed through HACS in the real Home Assistant on 2026-11-13 (version 0.1.1): devices loaded.

## History and statistics

- Sensor states cannot be backdated in Home Assistant, so each meter also gets an external statistic (`sontex:<kind>_<serial>_volume` or `_consumption`) written with true timestamps. External statistics were chosen over the sensors' own statistics because the recorder compiles those itself from state changes, and rewriting them risks double counting.
- Points come from each readout: the reading at its own time, plus the values the meter stored. Water meters and pulse adapters: storage 1 to 15 are "Monthly value -1 to -15", where -1 is the value at 00:00 on the 1st of the current month (confirmed across 61 readouts: the values shift on the 1st). Heat allocators: dated month-start values from the compact profile, and the value at the yearly set day, after which the counter restarts at zero.
- A value is filed under the hour that ends at or after it was taken, so month-start values land in the last hour of the previous month and monthly sums are exact.
- All points are kept in the integration's store and the statistics of a meter are rewritten in full whenever it gets new points, so readouts may arrive in any order.
- Checked on the 61 real readouts: no conflicting values for the same moment, water volumes never decrease, allocators drop only at the set day.

## Not built yet

- Feeding the Google Sheets from HA.
- Statistics for temperatures.

## Loose ends from the Google phase

The Apps Script project still has the `doPost` upload endpoint deployed as a web app. It is protected by the token but no longer used: archive the deployment (**Deploy > Manage deployments > Archive**) and delete the pasted block. `secrets.json` still holds the unused `upload_url` and `upload_token`.
