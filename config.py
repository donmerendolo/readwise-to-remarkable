#!/usr/bin/env python3
"""Configuration management for the Readwise to reMarkable sync tool."""

import configparser
import sys
from pathlib import Path


class Config:
    """Configuration management for the sync tool."""

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).parent / "config.cfg"

        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from config.cfg file."""
        if not self.config_path.exists():
            self.create_default_config()

        self.config.read(self.config_path)

    def create_default_config(self) -> None:
        """Create a default configuration file."""
        default_config = """[readwise]
access_token = your_readwise_access_token_here

[remarkable]
rmapi_path = rmapi
folder = Readwise

[sync]
locations = new,later,shortlist
tag = remarkable
"""
        with Path.open(self.config_path, "w") as f:
            f.write(default_config)

        print(f"Created default config at {self.config_path}")
        print("Please edit the config file with your settings and run again.")
        sys.exit(1)

    @property
    def readwise_token(self) -> str:
        return self.config.get("readwise", "access_token")

    @property
    def rmapi_path(self) -> str:
        return self.config.get("remarkable", "rmapi_path", fallback="rmapi")

    @property
    def remarkable_folder(self) -> str:
        return self.config.get("remarkable", "folder", fallback="Readwise")

    @property
    def locations(self) -> list[str]:
        locations_str = self.config.get(
            "sync",
            "locations",
            fallback="new,later,shortlist",
        )
        return [loc.strip() for loc in locations_str.split(",")]

    @property
    def tag(self) -> str:
        return self.config.get("sync", "tag", fallback="remarkable")
