"""Configuration management for GitGateway.

Manages ~/.gitgateway/config.json with load, save, and merge semantics.
Config fallback order: CLI flags -> config file -> interactive prompt.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

LOG = logging.getLogger(__name__)

CONFIG_DIR: Path = Path.home() / ".gitgateway"
CONFIG_FILE: Path = CONFIG_DIR / "config.json"

VALID_KEYS: set[str] = {
    "region",
    "profile",
    "api_url",
    "idp_url",
    "client_id",
    "domain_name",
}


class ConfigError(Exception):
    """Raised when configuration operations fail."""


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from the config file.

    Args:
        config_path: Optional path to config file. Defaults to
            ~/.gitgateway/config.json.

    Returns:
        Dictionary of configuration values. Empty dict if file
        does not exist.

    Raises:
        ConfigError: If the config file exists but cannot be parsed.
    """
    path = config_path or CONFIG_FILE
    if not path.exists():
        LOG.debug("Config file not found at %s, returning empty config", path)
        return {}

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in config file {path}: {exc}",
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {path} must contain a JSON object, "
            f"got {type(data).__name__}",
        )

    return data


def save_config(data: dict, config_path: Path | None = None) -> None:
    """Save configuration to the config file.

    Merges the provided data with existing config, preserving keys
    not present in the new data.

    Args:
        data: Dictionary of configuration values to save.
        config_path: Optional path to config file. Defaults to
            ~/.gitgateway/config.json.

    Raises:
        ConfigError: If the configuration cannot be written.
    """
    path = config_path or CONFIG_FILE

    # Load existing config and merge
    existing = load_config(path)
    existing.update(data)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(existing, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ConfigError(
            f"Cannot write config file {path}: {exc}",
        ) from exc

    LOG.debug("Configuration saved to %s", path)


def get_config_value(
    key: str,
    default: str | None = None,
    config_path: Path | None = None,
) -> str | None:
    """Get a single configuration value.

    Args:
        key: The configuration key to retrieve.
        default: Default value if key is not found.
        config_path: Optional path to config file.

    Returns:
        The configuration value, or default if not found.
    """
    config = load_config(config_path)
    return config.get(key, default)
