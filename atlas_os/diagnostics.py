"""Local setup diagnostics for Atlas OS."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from atlas_os.config import get_settings, load_env_file
from atlas_os.db.database import initialize_database
from atlas_os.greenrock.memory import load_memory_rows
from atlas_os.greenrock.scanner import latest_scan


RECOMMENDED_PROVIDER_SETUP = 'export ATLAS_MARKET_DATA_PROVIDER=yfinance\npython3 -m pip install -e ".[market-data]"'


@dataclass(frozen=True)
class ProviderDiagnostics:
    env_var_present: bool
    yfinance_installed: bool
    active_provider_name: str
    recommended_fix_command: str
    score_calculator_ready: bool
    scanner_ready: bool

    @property
    def status_label(self) -> str:
        if self.score_calculator_ready and self.scanner_ready:
            return "ready"
        if self.env_var_present and not self.yfinance_installed:
            return "configured, package missing"
        if self.active_provider_name != "none":
            return f"unsupported provider: {self.active_provider_name}"
        return "setup available"


@dataclass(frozen=True)
class DoctorReport:
    virtualenv_active: bool
    atlas_command_path: str
    provider: ProviderDiagnostics
    greenrock_logo_present: bool
    atlas_logo_present: bool
    output_dir_writable: bool
    database_initialized: bool
    latest_scan_available: bool
    memory_available: bool


def provider_diagnostics() -> ProviderDiagnostics:
    load_env_file()
    provider_name = os.getenv("ATLAS_MARKET_DATA_PROVIDER", "").strip().lower()
    yfinance_installed = importlib.util.find_spec("yfinance") is not None
    ready = provider_name == "yfinance" and yfinance_installed
    return ProviderDiagnostics(
        env_var_present=bool(provider_name),
        yfinance_installed=yfinance_installed,
        active_provider_name=provider_name or "none",
        recommended_fix_command=RECOMMENDED_PROVIDER_SETUP,
        score_calculator_ready=ready,
        scanner_ready=ready,
    )


def run_doctor() -> DoctorReport:
    settings = get_settings()
    output_dir_writable = _can_write_dir(settings.output_dir)
    database_initialized = False
    try:
        initialize_database(settings.db_path)
        database_initialized = settings.db_path.exists()
    except OSError:
        database_initialized = False
    return DoctorReport(
        virtualenv_active=bool(os.getenv("VIRTUAL_ENV")) or sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        atlas_command_path=shutil.which("atlas") or "not found",
        provider=provider_diagnostics(),
        greenrock_logo_present=(Path(__file__).resolve().parent / "static" / "greenrock_logo.png").exists(),
        atlas_logo_present=(Path(__file__).resolve().parent / "static" / "atlas_logo.png").exists(),
        output_dir_writable=output_dir_writable,
        database_initialized=database_initialized,
        latest_scan_available=latest_scan(settings.output_dir) is not None,
        memory_available=bool(load_memory_rows(settings.output_dir)),
    )


def _can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_path = path / ".atlas_write_test"
        test_path.write_text("ok\n", encoding="utf-8")
        test_path.unlink(missing_ok=True)
    except OSError:
        return False
    return True
