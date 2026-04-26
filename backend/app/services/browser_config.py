from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


@dataclass(frozen=True)
class BrowserLaunchConfig:
    name: str
    executable_path: str | None = None
    channel: str | None = None

    @property
    def display_name(self) -> str:
        if self.name == "chromium":
            return "Playwright Chromium"
        if self.name == "brave":
            return "Brave"
        if self.name == "chrome":
            return "Google Chrome"
        if self.name == "edge":
            return "Microsoft Edge"
        return self.name

    def kwargs(self) -> dict:
        options: dict = {}
        if self.executable_path:
            options["executable_path"] = self.executable_path
        elif self.channel:
            options["channel"] = self.channel
        return options


def _first_existing(paths: list[str]) -> str | None:
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.exists():
            return str(path)
    return None


def _default_executable(browser: str) -> str | None:
    browser = browser.lower().strip()
    if browser == "brave":
        return _first_existing(
            [
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "/usr/bin/brave-browser",
                "/usr/bin/brave",
                "/snap/bin/brave",
            ]
        ) or shutil.which("brave-browser") or shutil.which("brave")
    if browser == "chrome":
        return _first_existing(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
            ]
        ) or shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if browser == "edge":
        return _first_existing(
            [
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/usr/bin/microsoft-edge",
                "/usr/bin/microsoft-edge-stable",
            ]
        ) or shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable")
    return None


def resolve_apply_browser() -> BrowserLaunchConfig:
    settings = get_settings()
    requested = (settings.apply_browser or "chromium").strip().lower()
    explicit_path = settings.apply_browser_executable_path
    executable_path = str(explicit_path.expanduser()) if explicit_path else None

    if requested in {"chromium", "playwright", "playwright-chromium"}:
        return BrowserLaunchConfig(name="chromium")
    if requested == "brave":
        return BrowserLaunchConfig(name="brave", executable_path=executable_path or _default_executable("brave"))
    if requested in {"chrome", "google-chrome", "google_chrome"}:
        return BrowserLaunchConfig(name="chrome", executable_path=executable_path or _default_executable("chrome"), channel=None if executable_path else "chrome")
    if requested in {"edge", "msedge", "microsoft-edge"}:
        return BrowserLaunchConfig(name="edge", executable_path=executable_path or _default_executable("edge"), channel=None if executable_path else "msedge")
    if executable_path:
        return BrowserLaunchConfig(name=requested, executable_path=executable_path)
    return BrowserLaunchConfig(name="chromium")
