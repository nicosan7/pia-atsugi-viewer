"""Persist daily results so history survives server restarts."""

import gzip
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


class HistoryStore:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS results (
                date TEXT NOT NULL, kind TEXT NOT NULL, rate TEXT NOT NULL,
                machine_number TEXT NOT NULL, machine_name TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(date, kind, rate, machine_number, machine_name))""")
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_archives (
                archive_date TEXT PRIMARY KEY, saved_at TEXT NOT NULL,
                status TEXT NOT NULL, row_count INTEGER NOT NULL, path TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, rows, source, source_key="reports"):
        with self.connect() as connection:
            for row in rows:
                if not all(row.get(key) for key in ("date", "kind", "machine_number", "machine_name")):
                    continue
                # A corrected source label must replace its old alias on the
                # same day; retaining both would falsely look like a swap.
                connection.execute("DELETE FROM results WHERE date=? AND kind=? AND rate=? AND machine_number=? AND machine_name<>?", (
                    row["date"], row["kind"], str(row.get("rate", "")), str(row["machine_number"]), row["machine_name"],
                ))
                connection.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)", (
                    row["date"], row["kind"], str(row.get("rate", "")), str(row["machine_number"]),
                    row["machine_name"], json.dumps(row, ensure_ascii=False),
                ))
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('source',?)", (json.dumps(source, ensure_ascii=False),))
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", ("source:" + source_key, json.dumps(source, ensure_ascii=False)))

    def get_metadata(self, key, default=None):
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_metadata(self, key, value):
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def coverage(self):
        with self.connect() as connection:
            days, total, first, last = connection.execute(
                "SELECT COUNT(DISTINCT date), COUNT(*), MIN(date), MAX(date) FROM results"
            ).fetchone()
        return dict(stored_days=days, total_rows=total, first_date=first, last_date=last)

    def latest_archive(self):
        with self.connect() as connection:
            row = connection.execute("SELECT archive_date,saved_at,status,row_count,path FROM daily_archives ORDER BY archive_date DESC LIMIT 1").fetchone()
        return dict(zip(("archive_date", "saved_at", "status", "row_count", "path"), row)) if row else None

    def archive_for(self, day):
        with self.connect() as connection:
            row = connection.execute("SELECT archive_date,saved_at,status,row_count,path FROM daily_archives WHERE archive_date=?", (day,)).fetchone()
        return dict(zip(("archive_date", "saved_at", "status", "row_count", "path"), row)) if row else None

    def save_official_snapshot(self, result, now):
        """Store undated official responses as captures, never dated results."""
        now = now.astimezone(timezone(timedelta(hours=9)))
        day = now.date().isoformat()
        source = dict(result.get("source", {}))
        captured_at = (result.get("raw") or {}).get("captured_at") or now.isoformat(timespec="seconds")
        directory = os.path.join(os.path.dirname(self.path), "daily")
        os.makedirs(directory, exist_ok=True)
        destination = os.path.join(directory, day + ".official.json.gz")
        handle, temporary = tempfile.mkstemp(prefix=".official-", dir=directory)
        os.close(handle)
        try:
            with gzip.open(temporary, "wt", encoding="utf-8") as stream:
                json.dump(dict(collection_date=day, captured_at=captured_at, source=source, raw=result.get("raw", {})), stream, ensure_ascii=False)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.set_metadata("official:snapshot", dict(source=source, captured_at=captured_at, path=destination))

    def archive_daily(self, now=None, complete=False):
        """Atomically preserve a dated server snapshot and a SQLite recovery copy.

        Archive date is the collection date, never assigned to old gameplay
        rows. 'complete' means the nightly BACKUP finished, not that every
        machine's daily result is available from a public data source.
        """
        now = now or datetime.now(timezone(timedelta(hours=9)))
        now = now.astimezone(timezone(timedelta(hours=9)))
        day, saved_at = now.date().isoformat(), now.isoformat(timespec="seconds")
        previous = self.archive_for(day)
        if previous and previous["status"] == "complete" and not complete:
            return previous
        rows = self.load("0001-01-01", "9999-12-31")
        source = self.source()
        payload = dict(
            schema_version=1, store_id=14, collection_date=day, saved_at=saved_at,
            status="complete" if complete else "snapshot", coverage=self.coverage(),
            data_dates=sorted({row["date"] for row in rows}), source=source,
            note="保存日は取得・バックアップ日です。各実績の日付はrows内のdateを参照してください。公開されていない日付の実績は含みません。",
            rows=rows, official_snapshot=self.get_metadata("official:snapshot"),
        )
        directory = os.path.join(os.path.dirname(self.path), "daily")
        os.makedirs(directory, exist_ok=True)
        destination = os.path.join(directory, day + ".json.gz")
        handle, temporary = tempfile.mkstemp(prefix=".capture-", dir=directory)
        os.close(handle)
        try:
            with gzip.open(temporary, "wt", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        # Keep one atomic latest SQLite recovery copy in addition to immutable
        # per-day JSON archives; historical records remain in the main DB.
        handle, temporary = tempfile.mkstemp(prefix=".recovery-", suffix=".sqlite3", dir=directory)
        os.close(handle)
        try:
            with self.connect() as connection:
                recovery = sqlite3.connect(temporary)
                try:
                    connection.backup(recovery)
                finally:
                    recovery.close()
            os.replace(temporary, os.path.join(directory, "latest.sqlite3"))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        record = dict(archive_date=day, saved_at=saved_at, status=payload["status"], row_count=len(rows), path=destination)
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO daily_archives VALUES (?,?,?,?,?)", tuple(record.values()))
        return record

    def load(self, start, end):
        with self.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT payload FROM results WHERE date >= ? AND date < ? ORDER BY date, kind, machine_number", (start, end)
            )]

    def source(self):
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
            coverage = connection.execute("SELECT COUNT(DISTINCT date), MAX(date) FROM results").fetchone()
        source = json.loads(row[0]) if row else {
            "name": "PIA公式データ", "url": "https://piagroupapp.com/",
            "status": "loading", "message": "過去データを取得しています。",
        }
        source["stored_days"], source["stored_last_date"] = coverage
        return source

    def record_error(self, message):
        source = self.source()
        source.update(status="error", message=message, attempted_at=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"))
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('source',?)", (json.dumps(source, ensure_ascii=False),))
