"""Governance helpers for retention and metadata policy.

This module centralizes two concerns that need to stay consistent across the
SDK, collector, and database migration tooling:

* metadata allowlisting/redaction before persistence
* retention policy configuration for ClickHouse TTLs
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any


METADATA_CHAR_LIMIT = int(os.environ.get("TRACE_METADATA_CHAR_LIMIT", "4000"))

DEFAULT_REDACT_KEYS = {
    "password",
    "api_key",
    "token",
    "secret",
    "authorization",
    "cookie",
}

DEFAULT_METADATA_ALLOWLIST = {
    "component",
    "coverage_point",
    "details",
    "error_message",
    "error_type",
    "fallback_used",
    "input_state_keys",
    "input_text",
    "latency_ms",
    "message",
    "messages",
    "mode",
    "model",
    "note",
    "notes",
    "operation",
    "output",
    "output_state_keys",
    "parse_error_reason",
    "parse_failure_reason",
    "provider",
    "query",
    "reason",
    "reasoning",
    "rationale_violations",
    "response",
    "result",
    "semantic_failure_type",
    "stage",
    "status",
    "summary",
    "tenant_id",
    "traceback",
    "tool",
    "tool_name",
}

REDACT_KEYS: set[str] = {
    k.strip().lower()
    for k in os.environ.get("TRACE_REDACT_KEYS", "").split(",")
    if k.strip()
} | DEFAULT_REDACT_KEYS

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "[CARD]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b"), "[BEARER]"),
]


def truncate_text(text: str, limit: int = METADATA_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def add_redact_key(key: str) -> None:
    REDACT_KEYS.add(key.strip().lower())


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if str(k).lower() in REDACT_KEYS else redact_sensitive(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    if isinstance(value, str):
        for pattern, replacement in _REDACT_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    return value


def _metadata_allowlist() -> set[str]:
    allowlist = set(DEFAULT_METADATA_ALLOWLIST)
    raw = os.environ.get("TRACE_METADATA_ALLOWLIST", "")
    if raw:
        allowlist |= {item.strip().lower() for item in raw.split(",") if item.strip()}
    return allowlist


def _normalize_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if str(k).lower() in REDACT_KEYS else _normalize_metadata_value(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_normalize_metadata_value(v) for v in value]
    return redact_sensitive(value)


def normalize_metadata_payload(value: Any) -> dict[str, Any]:
    """Apply allowlisting + redaction before metadata is persisted.

    Only the top-level metadata keys are allowlisted. Nested payloads are kept
    intact, but sensitive keys and known secret-bearing string patterns are
    redacted recursively.
    """

    if value is None:
        return {}

    parsed: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"raw_metadata": truncate_text(str(redact_sensitive(value)))}

    if isinstance(parsed, Mapping):
        allowlist = _metadata_allowlist()
        out: dict[str, Any] = {}
        for key, val in parsed.items():
            normalized_key = str(key).lower()
            if normalized_key.startswith("_"):
                continue
            if normalized_key not in allowlist:
                continue
            out[str(key)] = _normalize_metadata_value(val)
        return out

    if isinstance(parsed, list):
        return {"raw_metadata": _normalize_metadata_value(parsed)}

    return {"raw_metadata": _normalize_metadata_value(parsed)}


def retention_days() -> dict[str, int]:
    return {
        "raw": int(os.environ.get("TRACE_RAW_RETENTION_DAYS", "30")),
        "reconstructed": int(os.environ.get("TRACE_RECONSTRUCTED_RETENTION_DAYS", "90")),
        "derived": int(os.environ.get("TRACE_DERIVED_RETENTION_DAYS", "90")),
        "slo": int(os.environ.get("TRACE_SLO_RETENTION_DAYS", "180")),
        "incidents": int(os.environ.get("TRACE_INCIDENT_RETENTION_DAYS", "365")),
    }
