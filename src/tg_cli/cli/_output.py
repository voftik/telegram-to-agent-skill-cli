"""Shared structured output helpers for CLI commands."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any

import click
import yaml

_OUTPUT_ENV = "OUTPUT"
_SCHEMA_VERSION = "1"


def default_structured_format(*, as_json: bool, as_yaml: bool) -> str | None:
    """Resolve explicit flags first, then fall back to env and TTY defaults."""
    if as_json and as_yaml:
        raise click.UsageError("Use only one of --json or --yaml.")
    if as_yaml:
        return "yaml"
    if as_json:
        return "json"
    output_mode = os.getenv(_OUTPUT_ENV, "auto").strip().lower()
    if output_mode == "yaml":
        return "yaml"
    if output_mode == "json":
        return "json"
    if output_mode == "rich":
        return None
    if not sys.stdout.isatty():
        return "yaml"
    return None


def structured_output_options(command: Callable) -> Callable:
    """Add --json/--yaml flags to a click command.

    The flag conflict is rejected before the command body runs — a format
    error must never happen after a message was sent or a file written (#27).
    """
    import functools

    @functools.wraps(command)
    def _preflight(*args, **kwargs):
        if kwargs.get("as_json") and kwargs.get("as_yaml"):
            raise click.UsageError("Use only one of --json or --yaml.")
        return command(*args, **kwargs)

    wrapped = click.option("--yaml", "as_yaml", is_flag=True, help="Output as YAML")(_preflight)
    wrapped = click.option("--json", "as_json", is_flag=True, help="Output as JSON")(wrapped)
    return wrapped


def emit_structured(data: Any, *, as_json: bool, as_yaml: bool) -> bool:
    """Emit structured output and return True when a structured format was used."""
    fmt = default_structured_format(as_json=as_json, as_yaml=as_yaml)
    if fmt is None:
        return False
    click.echo(dump_structured(_normalize_success_payload(data), fmt=fmt))
    return True


def dump_structured(data: Any, *, fmt: str) -> str:
    """Serialize structured data to JSON or YAML text."""
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if fmt == "yaml":
        return yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    raise ValueError(f"Unsupported structured format: {fmt}")


def success_payload(data: Any) -> dict[str, Any]:
    """Wrap structured success data in the shared agent schema."""
    return {
        "ok": True,
        "schema_version": _SCHEMA_VERSION,
        "data": data,
    }


def error_payload(code: str, message: str, *, details: Any | None = None) -> dict[str, Any]:
    """Wrap structured error data in the shared agent schema."""
    error = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "schema_version": _SCHEMA_VERSION,
        "error": error,
    }


def _normalize_success_payload(data: Any) -> Any:
    """Wrap plain structured data in the shared agent success schema."""
    if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION and "ok" in data:
        return data
    return success_payload(data)


def emit_error(
    code: str,
    message: str,
    *,
    as_json: bool | None = None,
    as_yaml: bool | None = None,
    details: Any | None = None,
) -> bool:
    """Emit a structured error when the active output mode is machine-readable."""
    if as_json is None or as_yaml is None:
        ctx = click.get_current_context(silent=True)
        params = ctx.params if ctx is not None else {}
        as_json = bool(params.get("as_json", False)) if as_json is None else as_json
        as_yaml = bool(params.get("as_yaml", False)) if as_yaml is None else as_yaml

    fmt = default_structured_format(as_json=bool(as_json), as_yaml=bool(as_yaml))
    if fmt is None:
        return False
    click.echo(dump_structured(error_payload(code, message, details=details), fmt=fmt))
    return True
