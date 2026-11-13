"""Read readout files from the FTP server the concentrator uploads to.

The server is only listed and read. Nothing is deleted, renamed or written.
These functions block, so call them from an executor.
"""

from __future__ import annotations

import datetime as dt
import ftplib
import io
import re

INI_NAME = re.compile(r"^\d+_(\d{4}-\d{2}-\d{2})_\d{6}\.ini$", re.IGNORECASE)
TIMEOUT_SECONDS = 60


def recent_names(names: list[str], today: dt.date, lookback_days: int) -> list[str]:
    """Readout files dated within the lookback window, oldest first."""
    oldest = (today - dt.timedelta(days=lookback_days)).isoformat()
    chosen = set()
    for name in names:
        name = name.rsplit("/", 1)[-1]
        match = INI_NAME.match(name)
        if match and oldest <= match[1] <= today.isoformat():
            chosen.add(name)
    return sorted(chosen, key=lambda name: name.split("_", 1)[1])


def _connect(host: str, port: int, username: str, password: str,
             directory: str) -> ftplib.FTP:
    ftp = ftplib.FTP(timeout=TIMEOUT_SECONDS)
    ftp.connect(host, port)
    ftp.login(username, password)
    if directory:
        ftp.cwd(directory)
    return ftp


def count_readouts(host: str, port: int, username: str, password: str,
                   directory: str) -> int:
    """Log in and count the readout files; used to check the settings."""
    with _connect(host, port, username, password, directory) as ftp:
        return sum(1 for name in ftp.nlst()
                   if INI_NAME.match(name.rsplit("/", 1)[-1]))


def fetch_new(host: str, port: int, username: str, password: str,
              directory: str, already_done: set[str],
              lookback_days: int) -> list[tuple[str, str]]:
    """Download recent readouts not handled before, as (name, text)."""
    files = []
    with _connect(host, port, username, password, directory) as ftp:
        names = recent_names(ftp.nlst(), dt.date.today(), lookback_days)
        for name in names:
            if name in already_done:
                continue
            buffer = io.BytesIO()
            ftp.retrbinary(f"RETR {name}", buffer.write)
            files.append((name, buffer.getvalue().decode("utf-8", errors="replace")))
    return files
