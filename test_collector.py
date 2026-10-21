import gzip
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from collector import CollectionService, JST, next_save_at
from storage import HistoryStore


class DailyCollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.directory.name) / "history.sqlite3")
        self.row = dict(date="2026-09-07", kind="slot", rate="20", machine_number="519", machine_name="ネオアイム", spins=4670, net=1160)
        self.fetch = Mock(return_value={"rows": [self.row], "source": {"status": "partial", "coverage": "selected_report_units"}})
        self.service = CollectionService(self.store, self.fetch, official_fetcher=False)

    def tearDown(self):
        self.directory.cleanup()

    def read_archive(self, day):
        record = self.store.archive_for(day)
        with gzip.open(record["path"], "rt", encoding="utf-8") as stream:
            return json.load(stream)

    def test_initial_capture_keeps_event_date_and_is_not_nightly_completion(self):
        self.service.tick(datetime(2026, 10, 21, 10, 0, tzinfo=JST))
        data = self.read_archive("2026-10-21")
        self.assertEqual(data["collection_date"], "2026-10-21")
        self.assertEqual(data["status"], "snapshot")
        self.assertEqual(data["rows"][0]["date"], "2026-09-07")
        self.assertEqual(data["data_dates"], ["2026-09-07"])

    def test_nightly_deadline_runs_even_when_next_periodic_fetch_is_later(self):
        self.service.tick(datetime(2026, 10, 21, 22, 30, tzinfo=JST))
        self.service.tick(datetime(2026, 10, 21, 23, 0, tzinfo=JST))
        self.assertEqual(self.store.archive_for("2026-10-21")["status"], "complete")
        self.assertEqual(self.fetch.call_count, 2)
        self.service.tick(datetime(2026, 10, 21, 23, 1, tzinfo=JST))
        self.assertEqual(self.fetch.call_count, 2)

    def test_new_day_retains_previous_archive_and_db_recovery_contains_history(self):
        self.service.tick(datetime(2026, 10, 21, 23, 0, tzinfo=JST))
        self.service.tick(datetime(2026, 10, 22, 23, 0, tzinfo=JST))
        self.assertEqual(self.read_archive("2026-10-21")["rows"], self.read_archive("2026-10-22")["rows"])
        recovery = Path(self.directory.name) / "daily" / "latest.sqlite3"
        connection = sqlite3.connect(recovery)
        try:
            self.assertEqual(connection.execute("select count(*) from results").fetchone()[0], 1)
        finally:
            connection.close()

    def test_fetch_failure_still_backs_up_previous_history_and_retries(self):
        self.store.save([self.row], {"status": "ok"})
        self.fetch.side_effect = RuntimeError("temporary upstream failure")
        now = datetime(2026, 10, 21, 23, 0, tzinfo=JST)
        with self.assertLogs("collector", level="ERROR"):
            self.assertFalse(self.service.tick(now))
        self.assertEqual(self.read_archive("2026-10-21")["rows"], [self.row])
        self.assertEqual(self.service.status(now)["status"], "error")
        self.assertEqual(self.service.next_fetch, now + timedelta(minutes=5))

    def test_backup_failure_is_visible_and_retry_is_not_skipped(self):
        now = datetime(2026, 10, 21, 10, 0, tzinfo=JST)
        with patch.object(self.store, "archive_daily", side_effect=OSError("disk temporarily unavailable")):
            with self.assertRaises(OSError):
                self.service.tick(now)
        self.assertEqual(self.service.status(now)["status"], "error")
        self.service.tick(now + timedelta(seconds=30))
        self.assertEqual(self.service.status(now)["status"], "ok")

    def test_restart_retains_complete_archive_and_does_not_downgrade_it(self):
        now = datetime(2026, 10, 21, 23, 0, tzinfo=JST)
        self.service.tick(now)
        restarted = CollectionService(self.store, self.fetch, official_fetcher=False)
        restarted.tick(now + timedelta(minutes=5))
        self.assertEqual(self.store.archive_for("2026-10-21")["status"], "complete")
        self.assertEqual(len(self.store.load("2020-01-01", "2027-01-01")), 1)

    def test_daily_schedule_is_japan_time(self):
        utc = timezone.utc
        before = datetime(2026, 10, 21, 13, 59, tzinfo=utc)
        after = datetime(2026, 10, 21, 14, 0, tzinfo=utc)
        self.assertEqual(next_save_at(before), datetime(2026, 10, 21, 23, 0, tzinfo=JST))
        self.assertEqual(next_save_at(after), datetime(2026, 10, 22, 23, 0, tzinfo=JST))

    def test_undated_official_capture_is_saved_but_never_invented_as_daily_rows(self):
        now = datetime(2026, 10, 21, 23, 0, tzinfo=JST)
        self.service.official_fetcher = Mock(return_value={"rows": [], "raw": {"captured_at": now.isoformat(), "rankings": {"slot": [{"machine_no": 519, "difference": 1234}]}}, "source": {"snapshot_units": 1, "data_date": None}})
        self.service.tick(now)
        snapshot = self.store.get_metadata("official:snapshot")
        with gzip.open(snapshot["path"], "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        self.assertEqual(raw["collection_date"], "2026-10-21")
        self.assertIsNone(raw["source"]["data_date"])
        self.assertEqual(self.store.coverage()["total_rows"], 1)
        self.assertEqual(self.store.coverage()["last_date"], "2026-09-07")
        self.assertEqual(self.service.status(now)["official_snapshot_units"], 1)


if __name__ == "__main__":
    unittest.main()
