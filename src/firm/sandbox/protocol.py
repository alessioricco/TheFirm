"""Frames between the firm and a sandbox worker: length-prefixed bytes.

Every message is a JSON header frame, sometimes followed by a data frame:
bars go to the worker as an Arrow IPC stream, signals come back as raw
little-endian float64 (one per bar, NaN for warm-up).
"""

from __future__ import annotations

import io
import json
import struct
from typing import Any, BinaryIO

import numpy as np
import pandas as pd
import pyarrow as pa

LENGTH = struct.Struct(">I")
MAX_FRAME = 256 << 20  # 256 MiB: far above any bar history we send


class ProtocolError(RuntimeError):
    """A malformed or oversized frame."""


def frame(data: bytes) -> bytes:
    if len(data) > MAX_FRAME:
        raise ProtocolError(f"frame of {len(data)} bytes exceeds {MAX_FRAME}")
    return LENGTH.pack(len(data)) + data


def header(obj: dict[str, Any]) -> bytes:
    return frame(json.dumps(obj, sort_keys=True).encode())


def read_frame(stream: BinaryIO) -> bytes:
    """Blocking read of one frame (the worker side)."""
    raw = _read_exact(stream, LENGTH.size)
    (n,) = LENGTH.unpack(raw)
    if n > MAX_FRAME:
        raise ProtocolError(f"frame of {n} bytes exceeds {MAX_FRAME}")
    return _read_exact(stream, n)


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise EOFError("stream closed")
        buf += chunk
    return bytes(buf)


def bars_to_bytes(bars: pd.DataFrame) -> bytes:
    table = pa.Table.from_pandas(bars, preserve_index=False)
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def bars_from_bytes(data: bytes) -> pd.DataFrame:
    with pa.ipc.open_stream(data) as reader:
        return reader.read_all().to_pandas()


def signals_to_bytes(values: np.ndarray) -> bytes:
    return np.ascontiguousarray(values, dtype="<f8").tobytes()


def signals_from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<f8").copy()
