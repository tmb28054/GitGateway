"""Tests for gitgateway.config module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitgateway.config import (
    ConfigError,
    get_config_value,
    load_config,
    save_config,
)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        """Returns empty dict when config file does not exist."""
        result = load_config(tmp_path / "nonexistent.json")
        assert result == {}

    def test_loads_valid_json(self, tmp_path: Path) -> None:
        """Loads and returns a valid JSON config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"region": "us-west-2", "profile": "dev"}),
            encoding="utf-8",
        )
        result = load_config(config_file)
        assert result == {"region": "us-west-2", "profile": "dev"}

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        """Raises ConfigError on malformed JSON."""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(config_file)

    def test_raises_on_non_object_json(self, tmp_path: Path) -> None:
        """Raises ConfigError when JSON is not a dict/object."""
        config_file = tmp_path / "config.json"
        config_file.write_text('["a", "b"]', encoding="utf-8")
        with pytest.raises(ConfigError, match="must contain a JSON object"):
            load_config(config_file)

    def test_loads_empty_object(self, tmp_path: Path) -> None:
        """Loads an empty JSON object without error."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")
        result = load_config(config_file)
        assert result == {}


class TestSaveConfig:
    """Tests for save_config function."""

    def test_creates_directory_and_file(self, tmp_path: Path) -> None:
        """Creates the config directory and file if they don't exist."""
        config_file = tmp_path / "subdir" / "config.json"
        save_config({"region": "eu-west-1"}, config_file)

        assert config_file.exists()
        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert data == {"region": "eu-west-1"}

    def test_merges_with_existing_config(self, tmp_path: Path) -> None:
        """Preserves existing keys when saving new data."""
        config_file = tmp_path / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            json.dumps({"region": "us-east-1", "profile": "prod"}),
            encoding="utf-8",
        )

        save_config({"api_url": "https://api.example.com"}, config_file)

        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert data == {
            "api_url": "https://api.example.com",
            "profile": "prod",
            "region": "us-east-1",
        }

    def test_overwrites_existing_key(self, tmp_path: Path) -> None:
        """Overwrites a key that already exists."""
        config_file = tmp_path / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            json.dumps({"region": "us-east-1"}),
            encoding="utf-8",
        )

        save_config({"region": "eu-west-1"}, config_file)

        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert data["region"] == "eu-west-1"

    def test_saves_to_new_file(self, tmp_path: Path) -> None:
        """Saves config when file does not previously exist."""
        config_file = tmp_path / "config.json"
        save_config({"idp_url": "https://idp.example.com"}, config_file)

        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert data == {"idp_url": "https://idp.example.com"}

    def test_raises_on_write_error(self, tmp_path: Path) -> None:
        """Raises ConfigError when the file cannot be written."""
        # Use a path that cannot be created (file as dir)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file", encoding="utf-8")
        config_file = blocker / "subdir" / "config.json"

        with pytest.raises(ConfigError, match="Cannot write config file"):
            save_config({"region": "us-east-1"}, config_file)

    def test_output_is_sorted_and_indented(self, tmp_path: Path) -> None:
        """Output JSON is sorted by key and indented."""
        config_file = tmp_path / "config.json"
        save_config(
            {"region": "us-east-1", "api_url": "https://x.com"},
            config_file,
        )

        content = config_file.read_text(encoding="utf-8")
        assert content == (
            '{\n  "api_url": "https://x.com",\n'
            '  "region": "us-east-1"\n}\n'
        )


class TestGetConfigValue:
    """Tests for get_config_value function."""

    def test_returns_value_when_key_exists(self, tmp_path: Path) -> None:
        """Returns the value for an existing key."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"region": "ap-southeast-1"}),
            encoding="utf-8",
        )
        result = get_config_value("region", config_path=config_file)
        assert result == "ap-southeast-1"

    def test_returns_default_when_key_missing(self, tmp_path: Path) -> None:
        """Returns default value when key is not in config."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        result = get_config_value(
            "nonexistent",
            default="fallback",
            config_path=config_file,
        )
        assert result == "fallback"

    def test_returns_none_when_key_missing_no_default(
        self,
        tmp_path: Path,
    ) -> None:
        """Returns None when key missing and no default provided."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")
        result = get_config_value("missing", config_path=config_file)
        assert result is None

    def test_returns_default_when_file_missing(self, tmp_path: Path) -> None:
        """Returns default when config file does not exist."""
        result = get_config_value(
            "region",
            default="us-east-1",
            config_path=tmp_path / "nope.json",
        )
        assert result == "us-east-1"
