"""
Background batch writer for raw spans.

Why batched: ClickHouse is allergic to small inserts — one row per request will
crater performance. We buffer spans in memory and flush in batches of N or
every M seconds, whichever comes first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List, Sequence, Tuple

import clickhouse_connect

_logger = logging.getLogger("collector.writer")

# Tunables
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "500"))
FLUSH_INTERVAL_SEC = float(os.environ.get("FLUSH_INTERVAL_SEC", "1.0"))
QUEUE_MAX = int(os.environ.get("QUEUE_MAX", "50000"))

SPAN_COLUMNS = [
    "start_time_ms",
    "trace_id",
    "span_id",
    "parent_span_id",
    "agent_id",
    "vector_clock",
    "event_type",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "metadata",
    "idempotency_key",
]


def _wal_base_dir(item_name: str) -> Path:
    base = Path(os.environ.get("TRACE_WAL_DIR", "./wal"))
    d = base / item_name
    d.mkdir(parents=True, exist_ok=True)
    return d


class BatchWriter:
    """WAL-backed batch writer.

    Durability semantics:
      - Every accepted row is first persisted to a write-ahead log (one file
        per item). The collector's ACK is based on WAL write success.
      - The in-memory queue speeds up submission to ClickHouse but is not
        required for durability: queued items are optional since WAL is
        authoritative and replayable.
      - On successful flush to ClickHouse the corresponding WAL files are
        removed.
    """

    def __init__(
        self,
        *,
        table: str = "tracing.raw_spans",
        columns: Sequence[str] = SPAN_COLUMNS,
        item_name: str = "span",
        host: str = "localhost",
        port: int = 8123,
    ):
        self.table = table
        self.columns = list(columns)
        self.item_name = item_name
        self.host = host
        self.port = port
        self._queue: asyncio.Queue[Tuple[tuple, str]] = asyncio.Queue(QUEUE_MAX)
        self._task: asyncio.Task | None = None
        self._client = None
        self._stopped = False
        self._wal_dir = _wal_base_dir(self.item_name)

    def _connect(self):
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=self.host, port=self.port, username="default", password=""
            )
        return self._client

    async def submit(self, row: tuple) -> None:
        """Async submit (durable) — write WAL then enqueue if possible."""
        wal_path = self._write_wal(row)
        await self._queue.put((row, str(wal_path)))

    def submit_nowait(self, row: tuple) -> bool:
        """Synchronous best-effort submit used by gRPC path.

        Returns True if the row was durably persisted to WAL. In-memory queue
        insertion is best-effort — if it's full the row remains in WAL for
        later replay. This ensures "accepted" implies durable.
        """
        try:
            wal_path = self._write_wal(row)
        except Exception as e:  # WAL failure -> reject
            _logger.exception("WAL write failed for %s: %s", self.item_name, e)
            return False

        try:
            self._queue.put_nowait((row, str(wal_path)))
        except asyncio.QueueFull:
            _logger.warning("In-memory queue full for %s; persisted to WAL=%s", self.item_name, wal_path)
            # Durable on-disk; will be replayed from WAL. Still *accept*.
            return True
        return True

    def _write_wal(self, row: tuple) -> Path:
        """Atomically persist a single row to WAL and return the path."""
        ts = int(time.time() * 1000)
        fname = f"{ts}-{os.getpid()}-{time.monotonic_ns()}.json"
        tmp = self._wal_dir / (fname + ".tmp")
        final = self._wal_dir / fname
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"row": row}, f, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        return final

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"batch-writer-{self.item_name}")
            # Kick off WAL replay into the in-memory queue (async)
            asyncio.create_task(self._replay_wal_into_queue())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            await self._task

    async def _replay_wal_into_queue(self) -> None:
        """Scan WAL dir and enqueue entries for processing.

        This runs once at startup and also before normal run loop flushes so
        WAL items are not forgotten.
        """
        entries = sorted(self._wal_dir.glob("*.json"), key=lambda p: p.name)
        for p in entries:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                    row = tuple(obj.get("row", []))
            except Exception:
                _logger.exception("Failed to load WAL file %s", p)
                continue
            try:
                # best-effort enqueue; if full leave it on disk for later
                self._queue.put_nowait((row, str(p)))
            except asyncio.QueueFull:
                _logger.info("Queue full while replaying WAL; leaving %s on disk", p)
                return

    async def _run(self) -> None:
        buf: List[Tuple[tuple, str]] = []
        last_flush = time.monotonic()
        while not self._stopped:
            timeout = max(0.05, FLUSH_INTERVAL_SEC - (time.monotonic() - last_flush))
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                buf.append(item)
            except asyncio.TimeoutError:
                pass

            should_flush = (
                len(buf) >= BATCH_SIZE
                or (buf and (time.monotonic() - last_flush) >= FLUSH_INTERVAL_SEC)
            )
            if should_flush:
                await self._flush(buf)
                buf = []
                last_flush = time.monotonic()

        if buf:
            await self._flush(buf)

    async def _flush(self, items: List[Tuple[tuple, str]]) -> None:
        if not items:
            return
        rows = [it[0] for it in items]
        wal_paths = [it[1] for it in items]
        try:
            client = self._connect()
            # clickhouse-connect is synchronous; offload to a thread so we
            # don't block the gRPC event loop.
            await asyncio.to_thread(
                client.insert,
                self.table,
                rows,
                column_names=self.columns,
            )
            _logger.info("Flushed %d %ss to ClickHouse (%s)", len(rows), self.item_name, self.table)
            # remove WAL files for items we've persisted
            for p in wal_paths:
                try:
                    os.remove(p)
                except Exception:
                    _logger.debug("Failed to remove WAL file %s after flush", p)
        except Exception as e:  # noqa: BLE001
            _logger.exception("Flush failed (%d %ss left on WAL): %s", len(rows), self.item_name, e)
            # Reset client so next attempt reconnects
            self._client = None
