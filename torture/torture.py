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
        # In-flight operations for current run: op_id -> (op_type, details)
        self.in_flight: Dict[str, Tuple[str, list]] = {}
        # Errored operations for current run: op_id -> (op_type, details)
        self.errored_ops: Dict[str, Tuple[str, list]] = {}
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

    def record_error(self, op_id: str):
        with self.lock:
            if op_id in self.in_flight:
                op_type, details = self.in_flight.pop(op_id)
                self.errored_ops[op_id] = (op_type, details)

    def resolve_after_pass(self, dumped_state: Dict[bytes, bytes]):
        """
        After verify_against_dump PASSES for an iteration, resolve state:
        set acked_state to exactly the dump contents, clear in_flight,
        clear errored_ops, keep known_values_per_key intact.
        """
        with self.lock:
            self.acked_state = dict(dumped_state)
            self.in_flight.clear()
            self.errored_ops.clear()

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

                # If key was never in acked_state, it must be explained by an in-flight un-ACKed op
                if k not in self.acked_state:
                    in_flight_explains = any(
                        any(item[0] == k and item[1] == found_v for item in details)
                        for _, details in self.in_flight.values()
                    )
                    if not in_flight_explains:
                        return False, f"Key {k} was never ACKed and not in-flight, but found in dump with value: {found_v}"

            # 3. Check batch all-or-nothing for in-flight batches
            for op_id, (op_type, details) in self.in_flight.items():
                if op_type == "batch" and len(details) > 0:
                    final_batch_ops: Dict[bytes, Optional[bytes]] = {}
                    for item in details:
                        k, v = item[0], item[1]
                        final_batch_ops[k] = v

                    matched_keys = []
                    for k, expected_v in final_batch_ops.items():
                        found_v = dumped_state.get(k)
                        if expected_v is not None:
                            if found_v == expected_v:
                                matched_keys.append(k)
                        else:
                            if found_v is None and self.acked_state.get(k) is not None:
                                matched_keys.append(k)

                    total_final = len(final_batch_ops)
                    matched_count = len(matched_keys)

                    if 0 < matched_count < total_final:
                        return False, (
                            f"partial batch {op_id}: found {matched_count}/{total_final} keys "
                            f"(found sample: {matched_keys[:5]}, missing sample: {[k for k in final_batch_ops if k not in matched_keys][:5]})"
                        )

            # 4. Check errored ops: no errored operation may be visible in dump
            for op_id, (op_type, details) in self.errored_ops.items():
                for item in details:
                    k, v = item[0], item[1]
                    if v is not None and dumped_state.get(k) == v:
                        return False, f"Errored op {op_id} produced visible state in dump for key {k}: {v}"

            return True, "OK"


class TortureRunner:

    def __init__(self, iterations: int = 500, seed: Optional[int] = None, mode: str = "standard", max_bytes: Optional[int] = None):
        self.iterations = iterations
        self.seed = seed if seed is not None else random.randint(1, 999999)
        self.mode = mode
        if self.mode == "disk-full" and (max_bytes is None or max_bytes <= 0):
            self.max_bytes = 200000
        else:
            self.max_bytes = max_bytes
        self.rng = random.Random(self.seed)

        self.db_path = os.path.abspath("torture_test.kv")
        self.oracle_log = os.path.abspath("oracle.log")

        # Global counter for guaranteeing unique values across the entire run
        self.global_val_counter = 0
        self.counter_lock = threading.Lock()

    def next_val_id(self) -> int:
        with self.counter_lock:
            self.global_val_counter += 1
            return self.global_val_counter

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
        if self.max_bytes is not None and self.max_bytes > 0:
            cmd.extend(["--max-bytes", str(self.max_bytes)])

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
        acks_count = 0
        errors_count = 0
        stats_lock = threading.Lock()

        # Reader thread listening for ACKs and ERRs - drains stdout completely until EOF
        def ack_listener():
            nonlocal acks_count, errors_count
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
                        with stats_lock:
                            acks_count += 1
                elif line_str.startswith("ERR"):
                    parts = line_str.split()
                    if len(parts) >= 2:
                        op_id = parts[1]
                        oracle.record_error(op_id)
                        with stats_lock:
                            errors_count += 1

        listener_thread = threading.Thread(target=ack_listener, daemon=True)
        listener_thread.start()

        # Worker generator
        def worker(worker_id: int):
            w_rng = random.Random(self.seed + iter_num * 100 + worker_id)
            seq = 0
            while not stop_workers.is_set() and proc.poll() is None:
                seq += 1
                op_id = f"w{worker_id}_{iter_num}_{seq}"
                val_id = self.next_val_id()

                if self.mode == "large-values":
                    k = f"k_{worker_id}_{w_rng.randint(1, 10)}".encode()
                    v_size = w_rng.randint(500 * 1024, 1024 * 1024)
                    prefix = f"val_it{iter_num}_w{worker_id}_c{val_id}_".encode()
                    pad_len = max(0, v_size - len(prefix))
                    v = prefix + os.urandom(pad_len)
                    oracle.register_in_flight(op_id, "put", [(k, v)])
                    msg = f"{op_id} put {k.decode()} {v.hex()}\n".encode()

                elif self.mode == "large-batch":
                    batch_ops = []
                    batch_size = w_rng.randint(2000, 10000)
                    msg_parts = [f"{op_id} batch "]
                    for b_idx in range(batch_size):
                        b_val_id = self.next_val_id()
                        bk = f"bk_{op_id}_{b_idx}".encode()
                        bv = f"bv_{op_id}_{b_val_id}".encode()
                        batch_ops.append((bk, bv))
                        msg_parts.append(f"put {bk.decode()} {bv.decode()};")
                    oracle.register_in_flight(op_id, "batch", batch_ops)
                    msg = "".join(msg_parts).rstrip(";").encode() + b"\n"

                elif self.mode == "disk-full":
                    k = f"k_df_{worker_id}_{w_rng.randint(1, 40)}".encode()
                    v_payload = "x" * 400
                    v = f"v_df_it{iter_num}_w{worker_id}_c{val_id}_{v_payload}".encode()
                    oracle.register_in_flight(op_id, "put", [(k, v)])
                    msg = f"{op_id} put {k.decode()} {v.decode()}\n".encode()

                elif self.mode == "compaction":
                    if w_rng.random() < 0.25:
                        msg = f"{op_id} compact\n".encode()
                        oracle.register_in_flight(op_id, "compact", [])
                    else:
                        k = f"k_{w_rng.randint(1, 20)}".encode()
                        v = f"v_it{iter_num}_w{worker_id}_c{val_id}".encode()
                        oracle.register_in_flight(op_id, "put", [(k, v)])
                        msg = f"{op_id} put {k.decode()} {v.decode()}\n".encode()

                else:  # Standard mode
                    roll = w_rng.random()
                    k = f"key_{w_rng.randint(1, 40)}".encode()
                    if roll < 0.65:
                        v = f"val_it{iter_num}_w{worker_id}_c{val_id}".encode()
                        oracle.register_in_flight(op_id, "put", [(k, v)])
                        msg = f"{op_id} put {k.decode()} {v.decode()}\n".encode()
                    elif roll < 0.85:
                        oracle.register_in_flight(op_id, "del", [(k, None)])
                        msg = f"{op_id} del {k.decode()}\n".encode()
                    else:
                        k2 = f"key_{w_rng.randint(1, 40)}".encode()
                        val_id2 = self.next_val_id()
                        v1 = f"b1_it{iter_num}_w{worker_id}_c{val_id}".encode()
                        v2 = f"b2_it{iter_num}_w{worker_id}_c{val_id2}".encode()
                        oracle.register_in_flight(op_id, "batch", [(k, v1), (k2, v2)])
                        msg = f"{op_id} batch put {k.decode()} {v1.decode()};put {k2.decode()} {v2.decode()}\n".encode()

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
        if self.mode == "disk-full":
            t_df_wait = time.time()
            while time.time() - t_df_wait < 0.25:
                with stats_lock:
                    if errors_count > 0:
                        break
                time.sleep(0.002)
            time.sleep(self.rng.uniform(0.005, 0.020))
        else:
            kill_delay = self.rng.uniform(0.020, 0.060)
            time.sleep(kill_delay)

        # UNANNOUNCED KILL - TerminateProcess immediately (kill -9)
        stop_workers.set()
        proc.kill()
        proc.wait(timeout=1.0)

        w1.join(timeout=0.2)
        w2.join(timeout=0.2)
        listener_thread.join(timeout=1.0)

        # Warning when 0 ACKs occurred in this iteration
        if acks_count == 0:
            print(f"WARN: 0 ACKs this iteration (iter {iter_num})")

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

        if self.mode == "disk-full":
            if acks_count == 0 and errors_count == 0:
                return False, f"Vacuous pass: zero ACKs and zero error replies in disk-full mode (iter {iter_num})"

        passed, reason = oracle.verify_against_dump(dumped_state)
        if passed:
            # Step 1b: Resolve upon pass
            oracle.resolve_after_pass(dumped_state)

            if self.mode == "disk-full" and dumped_state:
                # (4) after deleting keys and running compact, writes succeed again
                keys_to_del = list(dumped_state.keys())[:max(1, len(dumped_state) // 2)]
                for dk in keys_to_del:
                    subprocess.run(
                        [sys.executable, cli_script, "del", self.db_path, dk.decode()],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                compact_res = subprocess.run(
                    [sys.executable, cli_script, "compact", self.db_path],
                    capture_output=True, text=True
                )
                if compact_res.returncode != 0:
                    return False, f"Compaction failed after deletes in disk-full mode: {compact_res.stderr.strip()}"

                resume_k = f"res_k_{iter_num}"
                resume_v = f"res_v_{iter_num}"
                with oracle.lock:
                    oracle.known_values_per_key.setdefault(resume_k.encode(), set()).add(resume_v.encode())

                put_res = subprocess.run(
                    [sys.executable, cli_script, "put", self.db_path, resume_k, resume_v],
                    capture_output=True, text=True
                )
                if put_res.returncode != 0:
                    return False, f"Write failed after delete+compact under max-bytes: {put_res.stderr.strip()}"

                get_res = subprocess.run(
                    [sys.executable, cli_script, "get", self.db_path, resume_k],
                    capture_output=True, text=True
                )
                if get_res.returncode != 0 or get_res.stdout.strip() != resume_v:
                    return False, f"Get failed after resume write: {get_res.stdout.strip()}"

                # Clean up test resume key
                subprocess.run(
                    [sys.executable, cli_script, "del", self.db_path, resume_k],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

                new_dump = subprocess.run(
                    [sys.executable, cli_script, "dump", self.db_path],
                    capture_output=True
                )
                new_state = {}
                for line in new_dump.stdout.splitlines():
                    line = line.strip()
                    if line:
                        parts = line.split(b" ", 1)
                        new_state[parts[0]] = parts[1] if len(parts) > 1 else b""
                oracle.resolve_after_pass(new_state)
        # On failure, do not resolve; report the failure

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
                active_keys = len(oracle.acked_state)
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
