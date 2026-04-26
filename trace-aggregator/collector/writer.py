"""
Background batch writer for raw spans.

Why batched: ClickHouse is allergic to small inserts — one row per request will
crater performance. We buffer spans in memory and flush in batches of N or
every M seconds, whichever comes first.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Sequence

import clickhouse_connect

_logger = logging.getLogger("collector.writer")

BATCH_SIZE = 500
FLUSH_INTERVAL_SEC = 1.0
QUEUE_MAX = 50_000

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
]


class BatchWriter:
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
        self._queue: asyncio.Queue = asyncio.Queue(QUEUE_MAX)
        self._task: asyncio.Task | None = None
        self._client = None
        self._stopped = False

    def _connect(self):
        if self._client is None:
            self._client = clickhouse_connect.get_client(
                host=self.host, port=self.port, username="default", password=""
            )
        return self._client

    async def submit(self, row: tuple) -> None:
        await self._queue.put(row)

    def submit_nowait(self, row: tuple) -> bool:
        try:
            self._queue.put_nowait(row)
            return True
        except asyncio.QueueFull:
            _logger.warning("ClickHouse write buffer full, dropping %s", self.item_name)
            return False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="batch-writer")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        buf: List[tuple] = []
        last_flush = time.monotonic()
        while not self._stopped:
            timeout = max(0.05, FLUSH_INTERVAL_SEC - (time.monotonic() - last_flush))
            try:
                row = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                buf.append(row)
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

    async def _flush(self, rows: List[tuple]) -> None:
        if not rows:
            return
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
        except Exception as e:  # noqa: BLE001
            _logger.exception("Flush failed (%d %ss lost): %s", len(rows), self.item_name, e)
            # Drop the batch — alternative is a backpressure storm. In a
            # production system you'd write to a dead-letter file here.
            self._client = None
