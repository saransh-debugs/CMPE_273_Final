"""Shared tenant/auth helpers for the trace aggregator."""
from __future__ import annotations

import json
import os
from typing import Mapping, Optional


DEFAULT_TENANT_ID = os.environ.get("TRACE_TENANT_ID", "default")
DEFAULT_API_KEY = os.environ.get("TRACE_API_KEY", "dev-secret")


class AuthError(ValueError):
    pass


def _tenant_key_map() -> dict[str, str]:
    raw = os.environ.get("TRACE_TENANT_KEYS", "")
    if raw:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise AuthError("TRACE_TENANT_KEYS must be a JSON object")
        return {str(k): str(v) for k, v in parsed.items()}
    return {DEFAULT_TENANT_ID: DEFAULT_API_KEY}


def tenant_api_key(tenant_id: str) -> str:
    return _tenant_key_map().get(tenant_id, DEFAULT_API_KEY)


def client_call_metadata(tenant_id: Optional[str] = None, api_key: Optional[str] = None) -> tuple[tuple[str, str], ...]:
    resolved_tenant = tenant_id or DEFAULT_TENANT_ID
    resolved_key = api_key or tenant_api_key(resolved_tenant)
    return (
        ("x-tenant-id", resolved_tenant),
        ("authorization", f"Bearer {resolved_key}"),
    )


def resolve_request_tenant(headers: Mapping[str, str], *, allow_default: bool = True) -> str:
    tenant_id = (headers.get("x-tenant-id") or headers.get("X-Tenant-ID") or "").strip()
    authorization = (headers.get("authorization") or headers.get("Authorization") or "").strip()
    if not tenant_id and not authorization and allow_default:
        return DEFAULT_TENANT_ID
    if not tenant_id or not authorization:
        raise AuthError("missing tenant credentials")
    if not authorization.lower().startswith("bearer "):
        raise AuthError("authorization must be a Bearer token")
    provided_key = authorization.split(" ", 1)[1].strip()
    expected_key = _tenant_key_map().get(tenant_id)
    if expected_key is None:
        raise AuthError(f"unknown tenant: {tenant_id}")
    if provided_key != expected_key:
        raise AuthError("invalid tenant credentials")
    return tenant_id