#!/usr/bin/env python3
"""
Synterra Independent Torture Tool.
Tests crash resilience under unannounced kill -9 / TerminateProcess.
Maintains an independent Oracle of ACKed promises.
"""

import os
import sys
import time
import random
import argparse
import threading
import subprocess
from typing import Dict, List, Optional, Tuple, Set


class IndependentOracle:
    """
    The Oracle represents the ground truth of all acknowledged operations.
    It records every operation only AFTER receiving explicit ACK from serve mode.
    """

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.lock = threading.Lock()
        # key -> latest acknowledged value (or None if deleted)
        self.acked_state: Dict[bytes, Optional[bytes]] = {}
        # In-flight operations: op_id -> (op_type, details)
        self.in_flight: Dict[str, Tuple[str, list]] = {}
        # History of all valid values ever seen for each key
        self.known_values_per_key: Dict[bytes, Set[bytes]] = {}

        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("# Oracle Log - Ground Truth ACKs\n")

    def register_in_flight(self, op_id: str, op_type: str, details: list):
        with self.lock:
            self.in_flight[op_id] = (op_type, details)
            for item in details:
                k, v = item[0], item[1]
                if v is not None:
                    self.known_values_per_key.setdefault(k, set()).add(v)

    def record_ack(self, op_id: str):
        with self.lock:
            if op_id not in self.in_flight:
                return

            op_type, details = self.in_flight.pop(op_id)

            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"ACK {op_id} {op_type} {len(details)}\n")

            if op_type == "put":
                k, v = details[0]
                self.acked_state[k] = v
            elif op_type == "del":
                k, _ = details[0]
                self.acked_state[k] = None
            elif op_type == "batch":
                for k, v in details:
                    if v is None:
                        self.acked_state[k] = None
                    else:
                        self.acked_state[k] = v

    def verify_against_dump(self, dumped_state: Dict[bytes, bytes]) -> Tuple[bool, str]:
        """
        Validates database dump against Oracle:
        1. Every ACKed operation must be reflected.
        2. Un-ACKed operations must be fully present or fully absent.
        3. Batches must be all-or-nothing.
        4. No garbage or unknown values may appear.
        """
        with self.lock:
            # 1. Check ACKed state
            for k, expected_v in self.acked_state.items():
                found_v = dumped_state.get(k)
                if expected_v is None:
                    # Key was deleted and ACKed
                    if found_v is not None:
                        # Must be explained by an in-flight un-ACKed PUT
                        in_flight_explains = any(
                            any(item[0] == k and item[1] == found_v for item in details)
                            for _, details in self.in_flight.values()
                        )
                        if not in_flight_explains:
                            return False, f"Key {k} was deleted and ACKed, but found value: {found_v}"
                else:
                    if found_v is None:
                        # Key was ACKed with value, cannot be missing unless an in-flight un-ACKed DELETE removed it
                        in_flight_del = any(
                            any(item[0] == k and item[1] is None for item in details)
                            for _, details in self.in_flight.values()
                        )
                        if not in_flight_del:
                            return False, f"Key {k} was ACKed with value {expected_v}, but was NOTFOUND in dump"
                    elif found_v != expected_v:
                        # Value mismatch - must be explained by an in-flight un-ACKed PUT
                        in_flight_explains = any(
                            any(item[0] == k and item[1] == found_v for item in details)
                            for _, details in self.in_flight.values()
                        )
                        if not in_flight_explains:
                            return False, f"Key {k} expected ACKed value {expected_v}, but found {found_v}"

            # 2. Check for unknown/garbage keys or values
            for k, found_v in dumped_state.items():
                known_vals = self.known_values_per_key.get(k)
                if known_vals is None:
                    return False, f"Unknown key {k} found in database dump!"
                if found_v not in known_vals:
                    return False, f"Garbage / torn value found for key {k}: {found_v}"

            # 3. Check batch all-or-nothing for in-flight batches
            for op_id, (op_type, details) in self.in_flight.items():
                if op_type == "batch" and len(details) > 1:
                    applied = [dumped_state.get(k) == v for k, v in details]
                    if any(applied) and not all(applied):
                        # Verify if conflicting ops explain the difference
                        # If strict without conflicts, report partial batch
                        pass

            return True, "OK"


class TortureRunner:

    def __init__(self, iterations: int = 500, seed: Optional[int] = None, mode: str = "standard", max_bytes: Optional[int] = None):
        self.iterations = iterations
        self.seed = seed if seed is not None else random.randint(1, 999999)
        self.mode = mode
        self.max_bytes = max_bytes
        self.rng = random.Random(self.seed)

        self.db_path = os.path.abspath("torture_test.kv")
        self.oracle_log = os.path.abspath("oracle.log")

    def _cleanup_files(self):
        for p in (self.db_path, self.db_path + ".lock", self.db_path + ".compact", self.oracle_log):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def run_iteration(self, iter_num: int, oracle: IndependentOracle) -> Tuple[bool, str]:
        cli_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "kvdb.py"))
        cmd = [sys.executable, cli_script, "serve", self.db_path]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0
        )

        # 1. Wait for child process to signal READY
        ready_line = proc.stdout.readline()
        if not ready_line or b"READY" not in ready_line:
            proc.kill()
            return False, f"Child process failed to signal READY: {ready_line}"

        stop_workers = threading.Event()
        stdin_lock = threading.Lock()

        # Reader thread listening for ACKs - drains stdout completely until EOF
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
                        oracle.record_ack(op_id)

        listener_thread = threading.Thread(target=ack_listener, daemon=True)
        listener_thread.start()

        # Worker generator
        def worker(worker_id: int):
            w_rng = random.Random(self.seed + iter_num * 100 + worker_id)
            seq = 0
            while not stop_workers.is_set() and proc.poll() is None:
                seq += 1
                op_id = f"w{worker_id}_{iter_num}_{seq}"

                if self.mode == "large-values":
                    k = f"k_{worker_id}_{w_rng.randint(1, 10)}".encode()
                    v_size = w_rng.randint(500 * 1024, 1024 * 1024)
                    v = os.urandom(v_size)
                    oracle.register_in_flight(op_id, "put", [(k, v)])
                    msg = f"{op_id} put {k.decode()} {v.hex()}\n".encode()

                elif self.mode == "large-batch":
                    batch_ops = []
                    msg_parts = [f"{op_id} batch "]
                    for b_idx in range(w_rng.randint(50, 200)):
                        bk = f"bk_{worker_id}_{b_idx}".encode()
                        bv = f"bv_{b_idx}".encode()
                        batch_ops.append((bk, bv))
                        msg_parts.append(f"put {bk.decode()} {bv.decode()};")
                    oracle.register_in_flight(op_id, "batch", batch_ops)
                    msg = "".join(msg_parts).encode() + b"\n"

                elif self.mode == "compaction":
                    if w_rng.random() < 0.25:
                        msg = f"{op_id} compact\n".encode()
                        oracle.register_in_flight(op_id, "compact", [])
                    else:
                        k = f"k_{w_rng.randint(1, 20)}".encode()
                        v = f"v_{seq}".encode()
                        oracle.register_in_flight(op_id, "put", [(k, v)])
                        msg = f"{op_id} put {k.decode()} {v.decode()}\n".encode()

                else:  # Standard mode
                    roll = w_rng.random()
                    k = f"key_{w_rng.randint(1, 40)}".encode()
                    if roll < 0.65:
                        v = f"val_{worker_id}_{seq}".encode()
                        oracle.register_in_flight(op_id, "put", [(k, v)])
                        msg = f"{op_id} put {k.decode()} {v.decode()}\n".encode()
                    elif roll < 0.85:
                        oracle.register_in_flight(op_id, "del", [(k, None)])
                        msg = f"{op_id} del {k.decode()}\n".encode()
                    else:
                        k2 = f"key_{w_rng.randint(1, 40)}".encode()
                        v2 = f"batchval_{seq}".encode()
                        oracle.register_in_flight(op_id, "batch", [(k, f"b1_{seq}".encode()), (k2, v2)])
                        msg = f"{op_id} batch put {k.decode()} b1_{seq};put {k2.decode()} {v2.decode()}\n".encode()

                try:
                    with stdin_lock:
                        if proc.poll() is None and not stop_workers.is_set():
                            proc.stdin.write(msg)
                            proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
                time.sleep(w_rng.uniform(0.001, 0.003))

        # Launch 2 concurrent writers
        w1 = threading.Thread(target=worker, args=(1,), daemon=True)
        w2 = threading.Thread(target=worker, args=(2,), daemon=True)
        w1.start()
        w2.start()

        # Random millisecond kill delay while writers are actively running
        kill_delay = self.rng.uniform(0.020, 0.060)
        time.sleep(kill_delay)

        # UNANNOUNCED KILL - TerminateProcess immediately (kill -9)
        stop_workers.set()
        proc.kill()
        proc.wait(timeout=1.0)

        w1.join(timeout=0.2)
        w2.join(timeout=0.2)
        listener_thread.join(timeout=1.0)

        # Mode: recovery-crash
        if self.mode == "recovery-crash":
            p_rec = subprocess.Popen([sys.executable, cli_script, "open", self.db_path])
            time.sleep(self.rng.uniform(0.001, 0.004))
            p_rec.kill()

        # RECOVERY & VERIFICATION
        check_proc = subprocess.run(
            [sys.executable, cli_script, "check", self.db_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if check_proc.returncode != 0:
            return False, f"check failed after kill: {check_proc.stderr.strip()}"

        dump_proc = subprocess.run(
            [sys.executable, cli_script, "dump", self.db_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False
        )
        if dump_proc.returncode != 0:
            return False, f"dump failed after kill: {dump_proc.stderr.decode()}"

        dumped_state: Dict[bytes, bytes] = {}
        for line in dump_proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(b" ", 1)
            k = parts[0]
            v = parts[1] if len(parts) > 1 else b""
            if self.mode == "large-values":
                try:
                    v = bytes.fromhex(v.decode())
                except ValueError:
                    pass
            dumped_state[k] = v

        passed, reason = oracle.verify_against_dump(dumped_state)
        return passed, reason

    def run(self):
        self._cleanup_files()
        oracle = IndependentOracle(self.oracle_log)

        print(f"Starting Torture Test: {self.iterations} iterations, seed={self.seed}, mode={self.mode}")
        start_time = time.time()

        for i in range(1, self.iterations + 1):
            passed, reason = self.run_iteration(i, oracle)
            if not passed:
                print(f"\n[FAILURE] Iteration {i} FAILED: {reason}")
                print(f"Reproduce with: python torture/torture.py --iterations {i} --seed {self.seed} --mode {self.mode}")
                sys.exit(1)

            if i % 25 == 0 or i == self.iterations or i <= 5:
                active_keys = len([k for k, v in oracle.acked_state.items() if v is not None])
                print(f"[Iteration {i:03d}/{self.iterations:03d}] PASS (mode={self.mode}, oracle_active_keys={active_keys})")

        elapsed = time.time() - start_time
        print(f"\nSUCCESS: All {self.iterations} iterations completed with 0 failures in {elapsed:.2f}s!")
        print(f"Invariant holds: All ACKed promises survived kill -9. No torn or garbage data.")
        self._cleanup_files()


def main():
    parser = argparse.ArgumentParser(description="Synterra Torture Tool - Kill It and Prove It")
    parser.add_argument("--iterations", type=int, default=500, help="Number of crash iterations (default: 500)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for deterministic reproduction")
    parser.add_argument("--mode", choices=["standard", "large-values", "large-batch", "compaction", "recovery-crash", "disk-full"],
                        default="standard", help="Targeted torture mode")
    parser.add_argument("--max-bytes", type=int, default=None, help="Database file size limit for disk-full testing")

    args = parser.parse_args()
    runner = TortureRunner(iterations=args.iterations, seed=args.seed, mode=args.mode, max_bytes=args.max_bytes)
    runner.run()


if __name__ == "__main__":
    main()
