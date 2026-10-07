"""Runtime settings and the version-controlled configuration files.

All settings come from environment variables (prefix ``SCDPOC_``) so the
demonstrator can be started from a clean clone with no secrets.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml


def _find_home() -> Path:
    env = os.environ.get("SCDPOC_HOME")
    if env:
        return Path(env).resolve()
    for p in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (p / "config" / "affinity-domain.yml").is_file():
            return p
    return Path.cwd()


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    home: Path = field(default_factory=_find_home)
    data_dir: Path | None = None
    base_url: str = field(default_factory=lambda: os.environ.get("SCDPOC_BASE_URL", "http://localhost:8080"))
    # XDS endpoints the demo Document Source / Consumer call. Default: this same service.
    xds_endpoint_base: str | None = field(default_factory=lambda: os.environ.get("SCDPOC_XDS_ENDPOINT_BASE"))
    verapdf_cli: str | None = field(default_factory=lambda: os.environ.get("SCDPOC_VERAPDF_CLI"))
    verapdf_url: str | None = field(default_factory=lambda: os.environ.get("SCDPOC_VERAPDF_URL"))
    hl7_validator_jar: str | None = field(default_factory=lambda: os.environ.get("SCDPOC_HL7_VALIDATOR_JAR"))
    require_verapdf: bool = field(default_factory=lambda: _env_bool("SCDPOC_REQUIRE_VERAPDF", False))
    require_hl7_validator: bool = field(default_factory=lambda: _env_bool("SCDPOC_REQUIRE_HL7_VALIDATOR", False))
    demo_only: bool = True  # not configurable: this software is a demonstrator

    def __post_init__(self) -> None:
        if self.data_dir is None:
            self.data_dir = Path(os.environ.get("SCDPOC_DATA_DIR", self.home / "var")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def config_dir(self) -> Path:
        return self.home / "config"

    @property
    def fixtures_dir(self) -> Path:
        return self.home / "fixtures" / "synthetic-patients"

    @cached_property
    def affinity_domain(self) -> dict[str, Any]:
        return yaml.safe_load((self.config_dir / "affinity-domain.yml").read_text(encoding="utf-8"))

    @cached_property
    def ips_package(self) -> dict[str, Any]:
        return yaml.safe_load((self.config_dir / "ips-package.yml").read_text(encoding="utf-8"))

    @cached_property
    def profile_digest(self) -> dict[str, Any]:
        return json.loads((self.config_dir / "ips-profile-digest.json").read_text(encoding="utf-8"))

    @cached_property
    def ehds_register(self) -> dict[str, Any]:
        return yaml.safe_load((self.config_dir / "ehds-readiness.yml").read_text(encoding="utf-8"))
