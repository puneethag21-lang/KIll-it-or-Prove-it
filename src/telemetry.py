"""
Edge Telemetry & Blackbox Domain Serialization Module.

Provides compact structured binary serialization for telemetry frames,
sensor packets, and tamper-evident audit trails over KVDB without altering
the underlying disk framing.
"""

import json
import struct
import time
from typing import Dict, Any, Union, Optional


# Frame header format: [magic 4B][timestamp_ns u64 8B][alert_level u8 1B][sensor_id_len u16 2B][payload_len u32 4B]
TELEMETRY_MAGIC = b"TLM1"
TELEMETRY_HDR_STRUCT = struct.Struct("<4sQBH I")
TELEMETRY_HDR_SIZE = TELEMETRY_HDR_STRUCT.size  # 19 bytes

# Standard alert levels
ALERT_DEBUG = 0
ALERT_INFO = 1
ALERT_WARN = 2
ALERT_CRITICAL = 3

ALERT_NAMES = {
    ALERT_DEBUG: "DEBUG",
    ALERT_INFO: "INFO",
    ALERT_WARN: "WARN",
    ALERT_CRITICAL: "CRITICAL",
}


class TelemetryFrame:
    """Represents a structured blackbox telemetry sensor reading or audit event."""

    __slots__ = ("timestamp_ns", "sensor_id", "alert_level", "metrics", "raw_payload")

    def __init__(
        self,
        sensor_id: str,
        metrics: Optional[Dict[str, Any]] = None,
        alert_level: int = ALERT_INFO,
        timestamp_ns: Optional[int] = None,
        raw_payload: Optional[bytes] = None,
    ):
        self.sensor_id = sensor_id
        self.metrics = metrics or {}
        self.alert_level = alert_level
        self.timestamp_ns = timestamp_ns if timestamp_ns is not None else time.time_ns()
        self.raw_payload = raw_payload

    @property
    def is_critical(self) -> bool:
        return self.alert_level >= ALERT_CRITICAL

    def make_key(self) -> str:
        """
        Generates an ordered blackbox key.
        Critical frames use 'crit:' prefix to guarantee retention during flash pressure.
        Standard frames use 'raw:' prefix.
        """
        prefix = "crit" if self.is_critical else "raw"
        return f"{prefix}:{self.sensor_id}:{self.timestamp_ns:020d}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp_ns": self.timestamp_ns,
            "sensor_id": self.sensor_id,
            "alert_level": ALERT_NAMES.get(self.alert_level, str(self.alert_level)),
            "metrics": self.metrics,
        }

    def __repr__(self) -> str:
        return (
            f"TelemetryFrame(sensor='{self.sensor_id}', "
            f"level={ALERT_NAMES.get(self.alert_level, self.alert_level)}, "
            f"ts={self.timestamp_ns}, metrics={self.metrics})"
        )


def encode_telemetry_frame(frame: TelemetryFrame) -> bytes:
    """
    Serializes a TelemetryFrame into a compact binary byte sequence.
    """
    sensor_bytes = frame.sensor_id.encode("utf-8")
    if frame.raw_payload is not None:
        payload_bytes = frame.raw_payload
    else:
        payload_bytes = json.dumps(frame.metrics, separators=(",", ":")).encode("utf-8")

    hdr = TELEMETRY_HDR_STRUCT.pack(
        TELEMETRY_MAGIC,
        frame.timestamp_ns,
        frame.alert_level,
        len(sensor_bytes),
        len(payload_bytes),
    )
    return hdr + sensor_bytes + payload_bytes


def decode_telemetry_frame(data: bytes) -> TelemetryFrame:
    """
    Deserializes a binary byte sequence into a TelemetryFrame.
    Raises ValueError on invalid format or truncation.
    """
    if len(data) < TELEMETRY_HDR_SIZE:
        raise ValueError(f"Telemetry frame too short: {len(data)} < {TELEMETRY_HDR_SIZE}")

    magic, ts_ns, alert_lvl, s_len, p_len = TELEMETRY_HDR_STRUCT.unpack_from(data, 0)
    if magic != TELEMETRY_MAGIC:
        raise ValueError(f"Invalid telemetry magic: {magic!r}")

    expected_len = TELEMETRY_HDR_SIZE + s_len + p_len
    if len(data) < expected_len:
        raise ValueError(f"Truncated telemetry payload: {len(data)} < {expected_len}")

    sensor_id = data[TELEMETRY_HDR_SIZE : TELEMETRY_HDR_SIZE + s_len].decode("utf-8", errors="replace")
    payload_raw = data[TELEMETRY_HDR_SIZE + s_len : expected_len]

    try:
        metrics = json.loads(payload_raw.decode("utf-8"))
    except Exception:
        metrics = {"_raw": payload_raw.hex()}

    return TelemetryFrame(
        sensor_id=sensor_id,
        metrics=metrics,
        alert_level=alert_lvl,
        timestamp_ns=ts_ns,
        raw_payload=payload_raw,
    )
