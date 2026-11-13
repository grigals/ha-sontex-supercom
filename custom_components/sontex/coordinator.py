"""Fetches new readouts and keeps the latest values of every device."""

from __future__ import annotations

import ftplib
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DIRECTORY,
    CONF_LOOKBACK_DAYS,
    CONF_PULSE_MEDIUM,
    CONF_SCAN_HOURS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_PORT,
    DEFAULT_PULSE_MEDIUM,
    DEFAULT_SCAN_HOURS,
    DOMAIN,
    MANUFACTURER,
)
from .ftp import fetch_new
from .mbus import parse_ini
from .model import KIND_CONCENTRATOR, Meter, meters_from_readout
from .statistics import async_write_statistics

_LOGGER = logging.getLogger(__name__)
_MAX_REMEMBERED = 2000

type SontexConfigEntry = ConfigEntry[SontexCoordinator]


class SontexCoordinator(DataUpdateCoordinator[dict[str, Meter]]):
    def __init__(self, hass: HomeAssistant, entry: SontexConfigEntry) -> None:
        hours = entry.options.get(CONF_SCAN_HOURS, DEFAULT_SCAN_HOURS)
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(hours=hours))
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._processed: list[str] = []
        self.meters: dict[str, Meter] = {}
        # address -> {ISO local time: counter value}, for the statistics
        self.history: dict[str, dict[str, float]] = {}
        self._statistics_written = False
        # identity -> the address a device was first seen under
        self._addresses: dict[str, str] = {}
        self.concentrator: str | None = None
        self.concentrator_device_id: str | None = None

    async def async_load(self) -> None:
        """Restore what earlier readouts told us."""
        stored = await self._store.async_load() or {}
        self.meters = {address: Meter.from_dict(data)
                       for address, data in stored.get("meters", {}).items()}
        if "history" in stored:
            self._processed = stored.get("processed", [])
            self.history = stored["history"]
        # Data saved before history was kept: read the recent files again.
        self._merge_duplicates()
        self._register_concentrator()

    def _merge_duplicates(self) -> None:
        """Fold devices stored twice, under an old and a new version, into one.

        Earlier releases treated a device as new after a firmware update.
        """
        registry = dr.async_get(self.hass)
        newest_first = sorted(self.meters.values(),
                              key=lambda meter: meter.read_at or "", reverse=True)
        for meter in newest_first:
            kept = self._addresses.setdefault(_identity(meter.address), meter.address)
            if kept == meter.address:
                continue
            _LOGGER.info("Merging duplicate device %s into %s", meter.address, kept)
            older = self.history.pop(meter.address, {})
            self.history[kept] = {**older, **self.history.get(kept, {})}
            del self.meters[meter.address]
            for device in dr.async_entries_for_config_entry(
                    registry, self.config_entry.entry_id):
                if (DOMAIN, meter.address) in device.identifiers:
                    registry.async_remove_device(device.id)

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    async def _async_update_data(self) -> dict[str, Meter]:
        entry = self.config_entry
        try:
            files = await self.hass.async_add_executor_job(
                fetch_new,
                entry.data[CONF_HOST],
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
                entry.data.get(CONF_DIRECTORY, ""),
                set(self._processed),
                entry.options.get(CONF_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS),
            )
        except (OSError, EOFError, ftplib.Error) as err:
            if self.meters:  # keep showing the last readings
                _LOGGER.warning("Could not reach the FTP server: %s", err)
                return dict(self.meters)
            raise UpdateFailed(f"Could not reach the FTP server: {err}") from err

        changed: set[str] = set()
        for name, text in files:
            try:
                readout = parse_ini(text)
                for error in readout.errors:
                    _LOGGER.warning("%s: skipped frame %s", name, error)
                for meter in meters_from_readout(readout):
                    # A firmware update changes the version in the address;
                    # the device stays the one it was first seen as.
                    meter.address = self._addresses.setdefault(
                        _identity(meter.address), meter.address)
                    if meter.history:
                        self.history.setdefault(meter.address, {}).update(
                            {when: value for when, value in meter.history})
                        changed.add(meter.address)
                        meter.history = []
                    self._merge(meter)
            except Exception:  # one bad file must not block the others
                _LOGGER.exception("Could not decode %s", name)
            self._processed.append(name)

        if files:
            _LOGGER.info("Handled %d new readout file(s)", len(files))
            self._processed = self._processed[-_MAX_REMEMBERED:]
            self._register_concentrator()
            await self._store.async_save({
                "processed": self._processed,
                "meters": {address: meter.as_dict()
                           for address, meter in self.meters.items()},
                "history": self.history,
            })

        # Everything once after a start (cheap, and repairs a cleared
        # database), then only the devices with new values.
        if not self._statistics_written:
            changed = set(self.history)
            self._statistics_written = True
        if changed:
            async_write_statistics(
                self.hass, self.meters, self.history, changed,
                entry.options.get(CONF_PULSE_MEDIUM, DEFAULT_PULSE_MEDIUM))
        return dict(self.meters)

    def _merge(self, meter: Meter) -> None:
        """Keep the newest reading of each device."""
        known = self.meters.get(meter.address)
        if known and known.read_at and meter.read_at and meter.read_at < known.read_at:
            return
        self.meters[meter.address] = meter

    def _register_concentrator(self) -> None:
        """Create the concentrator's device so the others can hang off it."""
        for meter in self.meters.values():
            if meter.kind == KIND_CONCENTRATOR:
                self.concentrator = meter.address
                device = dr.async_get(self.hass).async_get_or_create(
                    config_entry_id=self.config_entry.entry_id,
                    identifiers={(DOMAIN, meter.address)},
                    manufacturer=MANUFACTURER,
                    name=f"Concentrator {meter.serial}",
                )
                self.concentrator_device_id = device.id
                return


def _identity(address: str) -> str:
    """The address without its version byte: serial, manufacturer, medium."""
    return address[:12] + address[14:]
