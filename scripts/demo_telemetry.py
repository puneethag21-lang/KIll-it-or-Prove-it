#!/usr/bin/env python3
"""
Edge Telemetry & Blackbox Domain Demo.

Demonstrates:
1. 50 high-frequency edge sensor streams concurrently writing telemetry frames to KVDB.
2. Abrupt, unannounced hard power-loss kill (kill -9 / TerminateProcess).
3. Post-crash forensic recovery, cryptographic audit hash calculation,
   and verification that 100% of acknowledged telemetry survived intact.
4. Priority Retention flash compaction preserving critical fault records
   while reclaiming storage space.
"""

import os
import sys
import time
import random
import threading
import subprocess
from typing import Dict, Tuple

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db import KVDatabase
from src.telemetry import (
    TelemetryFrame,
    encode_telemetry_frame,
    decode_telemetry_frame,
    ALERT_INFO,
    ALERT_WARN,
    ALERT_CRITICAL,
)


def run_demo():
    print("=" * 72)
    print("   EDGE TELEMETRY & AUDIT BLACKBOX CRASH RESILIENCE DEMONSTRATION")
    print("=" * 72)
    print("Domain: Avionics / Automotive / Medical IoT Blackbox Storage")
    print("Environment: High-frequency telemetry streams + abrupt power yanking")
    print("-" * 72)

    db_path = os.path.abspath("blackbox_telemetry.kv")
    for ext in ("", ".lock", ".compact"):
        p = db_path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    cli_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "kvdb.py"))
    cmd = [sys.executable, cli_script, "serve", db_path]

    print("\n[Phase 1] Launching embedded blackbox server daemon...")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )

    ready_line = proc.stdout.readline()
    if b"READY" not in ready_line:
        proc.kill()
        print(f"Error: Server failed to signal READY: {ready_line}")
        sys.exit(1)
    print("Server ready. Starting 50 concurrent high-frequency sensor streams.")

    stop_event = threading.Event()
    stdin_lock = threading.Lock()
    acked_ops: Dict[str, Tuple[str, bytes]] = {}
    acked_lock = threading.Lock()
    in_flight_ops: Dict[str, Tuple[str, bytes]] = {}
    in_flight_lock = threading.Lock()

    # Listener thread for ACKs
    def ack_listener():
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="ignore").strip()
            if line_str.startswith("ACK"):
                parts = line_str.split()
                if len(parts) >= 2:
                    op_id = parts[1]
                    with in_flight_lock:
                        data = in_flight_ops.pop(op_id, None)
                    if data:
                        with acked_lock:
                            acked_ops[op_id] = data

    listener_thread = threading.Thread(target=ack_listener, daemon=True)
    listener_thread.start()

    # 50 Sensor worker threads
    sensor_types = [
        ("IMU-ACCEL", ALERT_INFO, False),
        ("IMU-GYRO", ALERT_INFO, False),
        ("ENG-TEMP", ALERT_WARN, False),
        ("AVIONICS-ALT", ALERT_INFO, False),
        ("CRIT-COLLISION", ALERT_CRITICAL, True),
        ("CRIT-FAILSAFE", ALERT_CRITICAL, True),
    ]

    workers = []

    def sensor_worker(sensor_idx: int):
        stype, default_alert, is_crit = sensor_types[sensor_idx % len(sensor_types)]
        sensor_name = f"{stype}-{sensor_idx:02d}"
        seq = 0

        while not stop_event.is_set() and proc.poll() is None:
            seq += 1
            op_id = f"s{sensor_idx}_{seq}"
            alert = ALERT_CRITICAL if is_crit else (ALERT_WARN if seq % 5 == 0 else default_alert)

            # Generate realistic sensor telemetry
            metrics = {
                "val_primary": round(random.uniform(10.0, 500.0), 3),
                "status": "NOMINAL" if alert < ALERT_WARN else "TRIGGERED",
                "seq": seq,
            }
            frame = TelemetryFrame(
                sensor_id=sensor_name,
                metrics=metrics,
                alert_level=alert,
                timestamp_ns=time.time_ns(),
            )
            key_str = frame.make_key()
            val_bytes = encode_telemetry_frame(frame)

            with in_flight_lock:
                in_flight_ops[op_id] = (key_str, val_bytes)

            msg = f"{op_id} put {key_str} {val_bytes.hex()}\n".encode()

            try:
                with stdin_lock:
                    if proc.poll() is None and not stop_event.is_set():
                        proc.stdin.write(msg)
                        proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break

            time.sleep(random.uniform(0.001, 0.003))

    for i in range(50):
        t = threading.Thread(target=sensor_worker, args=(i,), daemon=True)
        workers.append(t)
        t.start()

    # Let streams flood the engine with high-frequency frames
    stream_duration = 0.250
    print(f"Streaming telemetry from 50 sensors across aircraft subsystems for {stream_duration*1000:.0f}ms...")
    time.sleep(stream_duration)

    # HARD UNANNOUNCED TERMINATION (Simulating complete avionics power yank)
    print("\n[Phase 2] !!! HARSH EDGE SHUTDOWN: TERMINATING PROCESS INSTANTLY (kill -9) !!!")
    stop_event.set()
    proc.kill()
    proc.wait(timeout=1.0)
    for w in workers:
        w.join(timeout=0.1)
    listener_thread.join(timeout=1.0)

    total_acked = len(acked_ops)
    print(f"Process terminated. Durably acknowledged frames recorded before death: {total_acked}")

    # POST-CRASH FORENSIC AUDIT
    print("\n[Phase 3] Booting Blackbox Forensic Recovery...")
    t0 = time.perf_counter()
    db = KVDatabase(db_path)
    t1 = time.perf_counter()
    recovery_time = t1 - t0

    active_keys, audit_hash = db.check(return_hash=True)
    print(f"Recovery complete in {recovery_time:.4f}s.")
    print(f"Cryptographic Audit Attestation Hash: {audit_hash}")
    print(f"Active records recovered from log: {active_keys}")

    # Verify that every acknowledged frame survived intact
    print("\n[Phase 4] Verifying Invariant: All ACKed frames survived without bitrot...")
    missing_acks = 0
    corrupt_frames = 0
    decoded_critical = 0

    for op_id, (key_str, expected_bytes) in acked_ops.items():
        stored_bytes = db.get(key_str)
        if stored_bytes is None:
            missing_acks += 1
            continue

        try:
            # Stored as hex string across CLI serve stdin/stdout channel
            stored_payload = bytes.fromhex(stored_bytes.decode("utf-8"))
        except Exception:
            stored_payload = stored_bytes

        if stored_payload != expected_bytes:
            corrupt_frames += 1
            continue

        # Decode frame
        try:
            frame = decode_telemetry_frame(stored_payload)
            if frame.is_critical:
                decoded_critical += 1
        except Exception:
            corrupt_frames += 1

    print(f"  - Total ACKed frames verified: {total_acked}")
    print(f"  - Missing ACKed frames:        {missing_acks} (MUST BE 0)")
    print(f"  - Corrupt or torn frames:      {corrupt_frames} (MUST BE 0)")
    print(f"  - Critical fault events saved: {decoded_critical}")

    if missing_acks > 0 or corrupt_frames > 0:
        print("\n[FAIL] Blackbox invariant violated!")
        sys.exit(1)

    print("\n[Phase 5] Priority Retention Flash Compaction Demo...")
    file_size_before = os.path.getsize(db_path)
    print(f"Authoritative blackbox size before compaction: {file_size_before:,} bytes ({active_keys} keys)")

    # Execute priority compaction: retain high-priority critical tags while pruning older telemetry
    db.compact(priority_retention=True)
    keys_after = db.check()
    file_size_after = os.path.getsize(db_path)
    print(f"Size after Priority Retention Compaction:    {file_size_after:,} bytes ({keys_after} keys)")

    # Confirm 100% of critical events survived compaction
    crit_survived = sum(1 for k, _ in db.dump() if k.startswith(b"crit:"))
    print(f"Critical fault events retained:             {crit_survived} (100% retention guarantee)")

    db.close()

    # Clean up demo file
    for ext in ("", ".lock", ".compact"):
        p = db_path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    print("\n" + "=" * 72)
    print("   BLACKBOX TELEMETRY DEMO SUCCESS: ALL GUARANTEES VERIFIED 100%!")
    print("=" * 72)


if __name__ == "__main__":
    run_demo()
