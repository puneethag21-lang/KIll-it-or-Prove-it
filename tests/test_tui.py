"""
Unit tests for Synterra KVDB Minimalist Interactive Terminal UI (TUI).
"""

import os
import unittest
import tempfile
from src.tui import SynterraTUI


class TestSynterraTUI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "tui_test.kv")
        self.tui = SynterraTUI(self.db_path)

    def tearDown(self):
        self.tui.db.close()
        if self.tui.http_server:
            self.tui.http_server.shutdown()
        self.temp_dir.cleanup()

    def test_put_get_delete_lifecycle(self):
        # 1. Put
        res = self.tui.execute_command("/put name Puneetha")
        self.assertTrue(res)
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertEqual(self.tui.last_output[1], "ACK")

        # 2. Get
        res = self.tui.execute_command("/get name")
        self.assertTrue(res)
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertEqual(self.tui.last_output[1], "Puneetha")

        # 3. Delete
        res = self.tui.execute_command("/delete name")
        self.assertTrue(res)
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertEqual(self.tui.last_output[1], "ACK")

        # 4. Get after delete -> NOTFOUND
        res = self.tui.execute_command("/get name")
        self.assertTrue(res)
        self.assertEqual(self.tui.last_output[0], "warn")
        self.assertEqual(self.tui.last_output[1], "NOTFOUND")

    def test_commands_without_slash(self):
        # Allow natural typing without '/'
        self.tui.execute_command("put city Bengaluru")
        self.assertEqual(self.tui.last_output[1], "ACK")

        self.tui.execute_command("get city")
        self.assertEqual(self.tui.last_output[1], "Bengaluru")

        self.tui.execute_command("del city")
        self.assertEqual(self.tui.last_output[1], "ACK")

    def test_check_and_compact(self):
        self.tui.execute_command("/put item1 value1")
        self.tui.execute_command("/put item2 value2")

        # Check
        self.tui.execute_command("/check")
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertIn("OK: Database integrity verified", self.tui.last_output[1])
        self.assertIn("Active Keys: 2", self.tui.last_output[1])

        # Compact
        self.tui.execute_command("/compact")
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertIn("OK: Compaction complete", self.tui.last_output[1])

    def test_batch_operations(self):
        # JSON batch
        json_batch = '[{"op":"put","key":"user:1","val":"Alice"},{"op":"put","key":"user:2","val":"Bob"}]'
        self.tui.execute_command(f"/batch {json_batch}")
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertIn("ACK", self.tui.last_output[1])

        # Verify dump
        self.tui.execute_command("/dump")
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertIn("user:1 Alice", self.tui.last_output[1])
        self.assertIn("user:2 Bob", self.tui.last_output[1])

    def test_help_and_exit(self):
        self.tui.execute_command("/help")
        self.assertEqual(self.tui.last_output[0], "success")
        self.assertIn("Available Commands:", self.tui.last_output[1])

        # /exit should return False to break the loop
        should_continue = self.tui.execute_command("/exit")
        self.assertFalse(should_continue)


if __name__ == "__main__":
    unittest.main()
