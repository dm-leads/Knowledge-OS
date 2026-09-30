"""Synthetic recovery checks; never access real conversations."""
import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import codex_import_sync as sync


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / "home"
        self.output = root / "backup"
        self.home.mkdir()
        self.output.mkdir()
        self.rollout = self.home / "rollout.jsonl"
        self.old = b'{"type":"session_meta","payload":{"id":"example","history_mode":"paginated"}}\n'
        self.new = self.old.replace(b'paginated', b'legacy')
        self.rollout.write_bytes(self.old)
        with sync.database(self.home / "state_5.sqlite") as conn:
            conn.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, history_mode TEXT)")
            conn.execute("INSERT INTO threads VALUES(?,?,?)", ("example", str(self.rollout), "paginated"))
        self.record = {"imported_thread_id": "example", "content_sha256": "source-before"}
        (self.home / "external_agent_session_imports.json").write_text(json.dumps({"records": [self.record]}))
        import hashlib
        self.report = {"thread_id": "example", "home": str(self.home),
                       "status": "rollout_metadata_replaced_sql_pending",
                       "rollout_sha256_before": hashlib.sha256(self.old).hexdigest(),
                       "converted_sha256": hashlib.sha256(self.new).hexdigest(),
                       "history_mode_before": "paginated", "ledger_record_before": self.record}
        self.args = SimpleNamespace(home=self.home, output=self.output, thread="example")

    def recover(self):
        sync.write_report(self.output, self.report)
        with patch.object(sync, "desktop_or_cli_running", return_value=False), \
                patch.object(sync, "writer_lock", return_value=contextlib.nullcontext()):
            sync.recover(self.args)

    def mode(self):
        with sync.database(self.home / "state_5.sqlite") as conn:
            return conn.execute("SELECT history_mode FROM threads").fetchone()[0]

    def test_interrupted_replace_aligns_database_to_unchanged_rollout(self):
        self.rollout.write_bytes(self.new)
        self.recover()
        self.assertEqual(self.mode(), "legacy")
        self.assertEqual(self.rollout.read_bytes(), self.new)

    def test_before_replace_keeps_original_mode(self):
        self.report["status"] = "converting_metadata"
        self.recover()
        self.assertEqual(self.mode(), "paginated")
        self.assertEqual(self.rollout.read_bytes(), self.old)

    def test_unknown_import_outcome_refuses_recovery(self):
        self.report["status"] = "native_import_running"
        with self.assertRaises(ValueError):
            self.recover()
        self.assertEqual(self.mode(), "paginated")

    def test_new_destination_content_refuses_recovery(self):
        self.rollout.write_bytes(self.new + b'{"type":"response_item","payload":{}}\n')
        with self.assertRaises(ValueError):
            self.recover()
        self.assertEqual(self.mode(), "paginated")


if __name__ == "__main__":
    unittest.main()
