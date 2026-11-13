"""Constants for the Sontex Supercom integration."""

DOMAIN = "sontex"
MANUFACTURER = "Sontex"

CONF_DIRECTORY = "directory"
CONF_SCAN_HOURS = "scan_interval_hours"
CONF_LOOKBACK_DAYS = "lookback_days"
CONF_PULSE_MEDIUM = "pulse_medium"

DEFAULT_PORT = 21
DEFAULT_SCAN_HOURS = 6
DEFAULT_LOOKBACK_DAYS = 30

# What pulse adapters ("Other" in the readout) are counting.
PULSE_AUTO = "auto"
PULSE_WATER = "water"
PULSE_GAS = "gas"
DEFAULT_PULSE_MEDIUM = PULSE_AUTO
