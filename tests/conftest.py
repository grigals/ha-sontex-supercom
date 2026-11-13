"""Shared test setup."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
# Optional: a folder of complete readouts, concentrator included. They hold
# the site's network settings, so they are kept out of git.
FULL_SAMPLES = next(iter(sorted((ROOT / "useful").glob("**/*SampleData"))), ROOT / "missing")
SAMPLE = "10000000_2026-02-20_101602"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""
    return


@pytest.fixture
def sample_text() -> str:
    return (FIXTURES / f"{SAMPLE}.ini").read_text(encoding="utf-8")


def full_sample_text() -> str:
    """The newest complete readout."""
    return sorted(FULL_SAMPLES.glob("*.ini"))[-1].read_text(encoding="utf-8")
