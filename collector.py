"""Server-side collection and daily backups, independent of browser visits."""

import logging
import threading
from datetime import datetime, time, timedelta, timezone

JST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)


def next_save_at(now):
    now = now.astimezone(JST)
    planned = datetime.combine(now.date(), time(23), JST)
    return planned if now < planned else planned + timedelta(days=1)


class CollectionService:
    def __init__(self, database, report_fetcher=None, official_fetcher=None):
        self.database = database
        self.report_fetcher = report_fetcher
        self.official_fetcher = official_fetcher
        self.lock = threading.Lock()
        self.started = False
        self.running = False
        previous = database.get_metadata("collector:last_fetch", {}) or {}
        self.next_fetch = None
        if previous.get("at"):
            try:
                last_fetch = datetime.fromisoformat(previous["at"]).astimezone(JST)
                self.next_fetch = last_fetch + (timedelta(hours=6) if previous.get("ok") else timedelta(minutes=5))
            except (TypeError, ValueError):
                pass
        self.message = "サーバー稼働中は毎日23:00（日本時間）に取得済み履歴を保存します。"

    def start(self):
        with self.lock:
            if self.started:
                return
            self.started = True
        threading.Thread(target=self._loop, name="pia-daily-collector", daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("Unexpected collector failure; will retry")
            # Event wait is interruptible by process exit and keeps HTTP free.
            threading.Event().wait(30)

    def tick(self, now=None, force=False):
        now = (now or datetime.now(JST)).astimezone(JST)
        if not self.lock.acquire(blocking=False):
            return False
        self.running = True
        try:
            day = now.date().isoformat()
            archive = self.database.archive_for(day)
            nightly_due = now.hour >= 23 and (not archive or archive["status"] != "complete")
            fetch_due = force or self.next_fetch is None or now >= self.next_fetch
            if not (fetch_due or nightly_due or archive is None):
                return False
            fetch_ok = False
            try:
                if self.report_fetcher is None:
                    from history_source import fetch_history
                    fetch = fetch_history
                else:
                    fetch = self.report_fetcher
                result = fetch()
                source = dict(result.get("source", {}))
                source["fetched_at"] = now.isoformat(timespec="seconds")
                self.database.save(result.get("rows", []), source)
                fetch_ok = source.get("status") != "error"
                self.next_fetch = now + timedelta(hours=6) if fetch_ok else now + timedelta(minutes=5)
                self.database.set_metadata("collector:last_fetch", dict(at=now.isoformat(timespec="seconds"), ok=fetch_ok))
                self.message = "取得済みの実績はサーバーに保存されています。毎日23:00（日本時間）に日付別バックアップを作成します。"
            except Exception:
                logger.exception("Could not refresh public history")
                self.next_fetch = now + timedelta(minutes=5)
                self.message = "データ取得に失敗しました。保存済みの履歴を維持し、5分後に再試行します。"
                self.database.record_error(self.message)
                self.database.set_metadata("collector:last_fetch", dict(at=now.isoformat(timespec="seconds"), ok=False))
            if not self._capture_official(now):
                self.next_fetch = min(self.next_fetch, now + timedelta(minutes=5))
            # A morning restart creates today's initial snapshot immediately.
            # Nightly backup always runs even when upstream is temporarily down.
            # Only the backup's success is recorded; source failure stays visible.
            if force or nightly_due or archive is None or (fetch_ok and now.hour >= 23):
                try:
                    record = self.database.archive_daily(now=now, complete=now.hour >= 23)
                    self.database.set_metadata("collector:last_snapshot_at", record["saved_at"])
                    self.database.set_metadata("collector:backup_error", None)
                except Exception:
                    self.database.set_metadata("collector:backup_error", now.isoformat(timespec="seconds"))
                    self.message = "日次バックアップを保存できませんでした。履歴DBを維持し、自動で再試行します。"
                    raise
            return fetch_ok
        finally:
            self.running = False
            self.lock.release()

    def _capture_official(self, now):
        if self.official_fetcher is False:
            return True
        try:
            if self.official_fetcher is None:
                from official_source import fetch_daily_data
                fetch = fetch_daily_data
            else:
                fetch = self.official_fetcher
            result = fetch()
            # The public rankings do not expose their event dates. Preserve
            # the official response and capture date, without manufacturing
            # gameplay history from an undated top list.
            self.database.save_official_snapshot(result, now)
            self.database.set_metadata("official:last_error", None)
            return True
        except Exception:
            logger.exception("Could not capture official public data")
            self.database.set_metadata("official:last_error", dict(at=now.isoformat(timespec="seconds"), message="公式データの取得に失敗しました。保存済みデータを維持して再試行します。"))
            return False

    def status(self, now=None):
        now = (now or datetime.now(JST)).astimezone(JST)
        archive = self.database.latest_archive() or {}
        last_fetch = self.database.get_metadata("collector:last_fetch", {})
        backup_error = self.database.get_metadata("collector:backup_error")
        official = self.database.get_metadata("official:snapshot", {})
        official_source = official.get("source", {})
        official_error = self.database.get_metadata("official:last_error")
        return dict(
            timezone="Asia/Tokyo", daily_save_time="23:00", next_save_at=next_save_at(now).isoformat(timespec="seconds"),
            last_snapshot_at=archive.get("saved_at"), last_archive_date=archive.get("archive_date"),
            last_archive_status=archive.get("status"), status="error" if backup_error or official_error or last_fetch.get("ok") is False else "running" if self.running else "ok",
            official_snapshot_at=official.get("captured_at"), official_snapshot_units=official_source.get("snapshot_units", 0),
            official_data_date=official_source.get("data_date"), official_message=official_source.get("message"),
            message=official_error["message"] if official_error else self.message, **self.database.coverage(),
        )
