from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PDF_DIR = ROOT / "pdfs"
OUTPUT_DIR = ROOT / "output"
REPORTS_DIR = OUTPUT_DIR / "reports"
DB_PATH = DATA_DIR / "research.sqlite"


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, DATA_DIR, RAW_DIR, PDF_DIR, OUTPUT_DIR, OUTPUT_DIR / "assets", REPORTS_DIR, ROOT / "logs"):
        path.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    return {
        "sources": load_yaml(CONFIG_DIR / "sources.yaml"),
        "keywords": load_yaml(CONFIG_DIR / "keywords.yaml"),
    }


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except ModuleNotFoundError:
        return _load_simple_yaml(path)


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Small YAML reader for this project's simple config shape.

    It supports top-level lists, list entries as scalars or flat maps, and comments.
    Install PyYAML if you need richer YAML features.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None
    current_mapping: dict[str, Any] | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_key = line[:-1].strip()
            result[current_key] = []
            current_item = None
            current_mapping = None
            continue
        if current_key is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_mapping is not None:
                result[current_key] = []
                current_mapping = None
            value = stripped[2:].strip()
            if ":" in value:
                key, scalar = value.split(":", 1)
                current_item = {key.strip(): _parse_scalar(scalar.strip())}
                result[current_key].append(current_item)
            else:
                current_item = None
                result[current_key].append(_parse_scalar(value))
        elif current_item is not None and ":" in stripped:
            key, scalar = stripped.split(":", 1)
            current_item[key.strip()] = _parse_scalar(scalar.strip())
        elif ":" in stripped:
            if not isinstance(result.get(current_key), dict):
                result[current_key] = {}
            current_mapping = result[current_key]
            key, scalar = stripped.split(":", 1)
            current_mapping[key.strip()] = _parse_scalar(scalar.strip())

    return result


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", ""}:
        return ""
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value.strip('"').strip("'")
