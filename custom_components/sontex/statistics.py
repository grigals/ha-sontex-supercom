"""Write counter values into Home Assistant's long-term statistics.

A sensor's state is stamped with the time it reached Home Assistant, which
can be days after the meter took the reading. Statistics can carry the true
time, so each meter also gets a statistic of its own (shown in the Energy
dashboard and statistics graphs) built from the time of every reading and
the dated values the meter stored itself.
"""

from __future__ import annotations

import datetime as dt
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .model import KIND_ALLOCATOR, Meter, device_name, running_totals

try:  # Home Assistant 2025.4 and later
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # pragma: no cover
    StatisticMeanType = None

_LOGGER = logging.getLogger(__name__)


def statistic_id(meter: Meter) -> str:
    what = "consumption" if meter.kind == KIND_ALLOCATOR else "volume"
    return f"{DOMAIN}:{meter.kind}_{meter.serial}_{what}"


def build_rows(points: dict[str, float]) -> list[tuple[dt.datetime, float, float]]:
    """Turn {ISO local time: counter value} into hourly (start, state, sum).

    A statistics row covers one hour and holds the counter at its end, so a
    value is filed under the hour that ends at or after it was taken: a
    month-start value from midnight goes into the last hour of the month
    before, which makes Home Assistant's monthly figures exact.
    """
    zone = dt_util.get_default_time_zone()
    hourly: dict[dt.datetime, float] = {}
    for when, value in sorted(points.items()):
        moment = dt.datetime.fromisoformat(when) - dt.timedelta(seconds=1)
        start = moment.replace(minute=0, second=0, microsecond=0, tzinfo=zone)
        hourly[dt_util.as_utc(start)] = value  # the later value of an hour wins
    series = sorted(hourly.items())
    return [(start, value, total)
            for (start, value), total in zip(series, running_totals(series))]


def async_write_statistics(hass: HomeAssistant, meters: dict[str, Meter],
                           history: dict[str, dict[str, float]],
                           addresses: set[str], pulse_medium: str) -> None:
    """Write (or rewrite) the statistics of the given devices."""
    if "recorder" not in hass.config.components:
        return
    if not get_instance(hass).enabled:
        return
    for address in addresses:
        meter = meters.get(address)
        points = history.get(address)
        if meter is None or not points:
            continue
        volume = meter.kind != KIND_ALLOCATOR
        what = "volume" if volume else "consumption"
        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"{device_name(meter, pulse_medium)} {what}",
            source=DOMAIN,
            statistic_id=statistic_id(meter),
            unit_class="volume" if volume else None,
            unit_of_measurement=UnitOfVolume.CUBIC_METERS if volume else None,
        )
        if StatisticMeanType is not None:
            metadata["mean_type"] = StatisticMeanType.NONE
        rows = [StatisticData(start=start, state=state, sum=total)
                for start, state, total in build_rows(points)]
        try:
            async_add_external_statistics(hass, metadata, rows)
        except Exception:  # statistics are an extra; never block the sensors
            _LOGGER.exception("Could not write statistics for %s", address)
