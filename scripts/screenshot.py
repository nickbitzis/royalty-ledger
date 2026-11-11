"""Capture README screenshots with headless Chrome. Start the API first: make run.

Usage: uv run python scripts/screenshot.py --port 8882 --payee A003 --account C029:all
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
]
DOCS = Path(__file__).resolve().parents[1] / "docs"


def chrome() -> str:
    for c in CHROME_PATHS:
        if Path(c).exists() or shutil.which(c):
            return c
    raise SystemExit("Chrome not found")


def shoot(url: str, out: Path, width: int, height: int, dark: bool = False) -> None:
    args = [
        chrome(),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        "--virtual-time-budget=8000",
        f"--screenshot={out}",
    ]
    if dark:
        args += ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"]
    else:
        args += ["--blink-settings=preferredColorScheme=1"]
    subprocess.run([*args, url], check=True, capture_output=True, timeout=90)
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8882)
    ap.add_argument("--payee", default="A003")
    ap.add_argument("--period", default="2026Q2")
    ap.add_argument("--account", default="C029:all")
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    DOCS.mkdir(exist_ok=True)
    shoot(f"{base}/?payee={a.payee}&period={a.period}#statement", DOCS / "screenshot.png", 1300, 1250)
    shoot(f"{base}/?account={a.account}#recoup", DOCS / "recoupment-dark.png", 1300, 1100, dark=True)
    shoot(f"{base}/#review", DOCS / "review.png", 1300, 1000)


if __name__ == "__main__":
    main()
