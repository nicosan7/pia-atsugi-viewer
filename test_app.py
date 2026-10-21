import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app as viewer
from storage import HistoryStore
from collector import CollectionService


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_database = viewer.database
        self.previous_service = viewer.collection_service
        viewer.database = HistoryStore(Path(self.directory.name) / "history.sqlite3")
        viewer.collection_service = CollectionService(viewer.database)
        viewer.app.testing = True
        viewer._cache.clear()
        self.client = viewer.app.test_client()

    def tearDown(self):
        viewer.database = self.previous_database
        viewer.collection_service = self.previous_service
        self.directory.cleanup()

    def test_other_stores_cannot_be_requested(self):
        with patch.object(viewer, "api_post") as upstream:
            self.assertEqual(self.client.get("/api/store/2").status_code, 404)
            upstream.assert_not_called()

    def test_only_atsugi_in_store_list(self):
        with patch.object(viewer, "api_post", return_value={"stores": [{"id": 2}, {"id": 14}]}):
            self.assertEqual(self.client.get("/api/stores").json, [{"id": 14}])

    def test_form_encoding_and_atsugi_identity(self):
        response = Mock()
        response.json.return_value = {"status": 0, "store": {
            "store_no": "0014", "name": "PIA厚木", "slot": {"20": [{"machines": "569,569,570"}]}
        }}
        with patch.object(viewer.requests, "post", return_value=response) as post:
            data = self.client.get("/api/store/14").json
            self.assertEqual(post.call_args.kwargs["data"], {"store_id": 14})
            self.assertNotIn("Content-Type", post.call_args.kwargs["headers"])
            self.assertNotIn("json", post.call_args.kwargs)
            self.assertEqual(data["store"]["slot"]["20"][0]["machines"], "569,570")

    def test_silent_upstream_store_substitution_is_rejected(self):
        with patch.object(viewer, "api_post", return_value={"store": {"name": "PIA川崎銀柳", "store_no": "0037"}}):
            self.assertEqual(self.client.get("/api/store/14").status_code, 502)

    def test_invalid_analysis_queries_and_empty_history(self):
        for query in ("days=abc", "days=0", "kind=other", "days=999999"):
            self.assertEqual(self.client.get("/api/analysis?" + query).status_code, 400)
        with patch.object(viewer, "current_units") as catalog:
            data = self.client.get("/api/analysis?days=90&kind=slot").json
            self.assertEqual(data["status"], "no_data")
            self.assertEqual(data["candidates"], [])
            catalog.assert_not_called()

    def test_storage_upserts_corrections_and_retains_on_error(self):
        row = dict(date="2026-10-20", kind="slot", rate="20", machine_number="569", machine_name="マイジャグラーV", spins=6000, net=100)
        viewer.database.save([row], {"status": "ok", "fetched_at": "2026-10-21T00:00:00+09:00"})
        row["net"] = 200
        row["machine_name"] = "マイジャグラーＶ"
        viewer.database.save([row], {"status": "ok"})
        viewer.database.record_error("一時的な取得エラー")
        rows = viewer.database.load("2026-10-01", "2026-10-21")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["net"], 200)
        self.assertEqual(rows[0]["machine_name"], "マイジャグラーＶ")
        self.assertEqual(viewer.database.source()["status"], "error")
        self.assertEqual(viewer.database.source()["stored_days"], 1)

    def test_old_history_can_be_read_without_being_redated_into_recent_analysis(self):
        row = dict(date="2021-04-25", kind="slot", rate="20", machine_number="519", machine_name="旧機種", spins=6000, net=100)
        viewer.database.save([row], {"status": "partial"})
        data = self.client.get("/api/history?start=2021-04-25&end=2021-04-25").json
        self.assertEqual(data["rows"], [row])
        self.assertEqual(self.client.get("/api/analysis?days=365").json["status"], "no_data")
        self.assertEqual(self.client.get("/api/collection").json["first_date"], "2021-04-25")
        self.assertEqual(self.client.get("/api/history?end=bad").status_code, 400)


if __name__ == "__main__":
    unittest.main()
