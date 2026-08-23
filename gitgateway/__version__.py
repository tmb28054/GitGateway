"""Version extraction from CHANGELOG.md."""

from __future__ import annotations

import re
from pathlib import Path


def _read_version() -> str:
    """Read the version from CHANGELOG.md.

    Returns:
        The latest version string from the changelog.

    Raises:
        RuntimeError: If no version entry is found in CHANGELOG.md.
    """
    changelog = Path(__file__).parent.parent / "CHANGELOG.md"
    if not changelog.exists():
        return "0.0.0"

    pattern = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
    content = changelog.read_text(encoding="utf-8")
    match = pattern.search(content)
    if match:
        return match.group(1)
    return "0.0.0"


__version__: str = _read_version()
