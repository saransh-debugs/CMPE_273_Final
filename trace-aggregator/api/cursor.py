"""
Cursor encoding and decoding for keyset-based pagination.

The cursor encodes the position of the last row returned in the previous page,
so the next query can jump directly to the next row via compound index
comparison: WHERE (sort_key1, sort_key2) < (cursor_value1, cursor_value2).

For /traces, the sort key is (latest_reconstructed_at DESC, trace_id DESC).
We carry the timestamp in ISO-8601 form so the cursor is debuggable.

Format: base64-url(json({"ts": "...", "trace_id": "..."}))
        - base64-url is URL-safe (no +, /, = characters in the payload)
        - JSON inside is for forward compatibility (easy to add fields)
        - Opaque to clients: never document the inner format, treat it as a token

If a client tampers with the cursor or it's malformed, we raise a clean
HTTP 400 — never crash the request.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


CURSOR_VERSION = 1


@dataclass(frozen=True)
class TraceCursor:
    """Position marker for the /traces endpoint.

    Fields mirror the ORDER BY clause exactly. If the ORDER BY changes, this
    must change too — keep them in lockstep.
    """
    ts: datetime         # the row's latest_reconstructed_at (UTC)
    trace_id: str        # tie-breaker when timestamps collide

    def encode(self) -> str:
        """Serialize → base64-url string safe for HTTP query params."""
        ts_utc = self.ts.astimezone(timezone.utc) if self.ts.tzinfo else self.ts.replace(tzinfo=timezone.utc)
        payload = {
            "v": CURSOR_VERSION,
            "ts": ts_utc.isoformat().replace("+00:00", "Z"),
            "trace_id": self.trace_id,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, encoded: str) -> "TraceCursor":
        """Deserialize. Raises ValueError on any malformation."""
        if not encoded:
            raise ValueError("cursor is empty")
        # Re-pad for urlsafe_b64decode
        padded = encoded + "=" * (-len(encoded) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(raw)
        except Exception as e:
            raise ValueError(f"cursor is not valid base64-json: {e}") from e

        if not isinstance(payload, dict):
            raise ValueError("cursor payload must be a JSON object")
        if payload.get("v") != CURSOR_VERSION:
            raise ValueError(
                f"cursor version mismatch (got {payload.get('v')}, expected {CURSOR_VERSION})"
            )

        ts_str = payload.get("ts")
        trace_id = payload.get("trace_id")
        if not ts_str or not trace_id:
            raise ValueError("cursor must contain ts and trace_id")

        # Accept the trailing Z our encoder writes, plus standard +00:00 form.
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError as e:
            raise ValueError(f"cursor ts is not a valid ISO-8601 datetime: {e}") from e

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)

        return cls(ts=ts, trace_id=str(trace_id))


def maybe_decode(encoded: Optional[str]) -> Optional[TraceCursor]:
    """Decode a cursor when present, return None when not. Convenience wrapper."""
    if encoded is None or encoded == "":
        return None
    return TraceCursor.decode(encoded)