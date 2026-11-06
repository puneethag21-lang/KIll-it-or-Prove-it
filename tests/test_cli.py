"""
Unit tests for KVDB Command Line Interface (CLI).
"""

import os
import sys
import unittest
import tempfile
import subprocess


class TestCLI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "cli_test.kv")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_cli(self, *args, input_data=None):
        cmd = [sys.executable, "-m", "src.cli"] + list(args)
        proc = subprocess.run(
            cmd,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return proc

    def test_cli_crud_and_check(self):
        # 1. open
        res = self._run_cli("open", self.db_path)
        self.assertEqual(res.returncode, 0)
        self.assertIn("0 keys recovered", res.stdout)

        # 2. put
        res = self._run_cli("put", self.db_path, "name", "Alice")
        self.assertEqual(res.returncode, 0)
        self.assertIn("ACK", res.stdout)

        # 3. get existing
        res = self._run_cli("get", self.db_path, "name")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "Alice")

        # 4. get non-existing -> NOTFOUND
        res = self._run_cli("get", self.db_path, "nonexistent")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "NOTFOUND")

        # 5. check
        res = self._run_cli("check", self.db_path)
        self.assertEqual(res.returncode, 0)
        self.assertIn("OK: 1 keys", res.stdout)

        # 6. del
        res = self._run_cli("del", self.db_path, "name")
        self.assertEqual(res.returncode, 0)
        self.assertIn("ACK", res.stdout)

        res = self._run_cli("get", self.db_path, "name")
        self.assertEqual(res.stdout.strip(), "NOTFOUND")

    def test_cli_batch_and_dump(self):
        # Create a batch file
        batch_file = os.path.join(self.temp_dir.name, "batch_input.txt")
        with open(batch_file, "w", encoding="utf-8") as f:
            f.write("put city Seattle\n")
            f.write("put country USA\n")
            f.write("put temp 72\n")
            f.write("del temp\n")

        res = self._run_cli("batch", self.db_path, batch_file)
        self.assertEqual(res.returncode, 0)
        self.assertIn("ACK", res.stdout)

        # dump
        res = self._run_cli("dump", self.db_path)
        self.assertEqual(res.returncode, 0)
        lines = res.stdout.strip().splitlines()
        self.assertEqual(lines, ["city Seattle", "country USA"])

    def test_cli_serve(self):
        # Feed commands to serve
        input_stream = (
            "101 put greeting hello\n"
            "102 put subject world\n"
            "103 del greeting\n"
        )
        res = self._run_cli("serve", self.db_path, input_data=input_stream)
        self.assertEqual(res.returncode, 0)
        lines = res.stdout.strip().splitlines()
        self.assertEqual(lines, ["ACK 101", "ACK 102", "ACK 103"])

        # Check DB state
        res = self._run_cli("get", self.db_path, "subject")
        self.assertEqual(res.stdout.strip(), "world")
        res = self._run_cli("get", self.db_path, "greeting")
        self.assertEqual(res.stdout.strip(), "NOTFOUND")


if __name__ == "__main__":
    unittest.main()
