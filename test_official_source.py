import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import requests

import official_source


class OfficialSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            "store_no": "0014", "name": "PIA厚木",
            "slot": {"20": [{"name": "機種A", "sis_machine_code": "S1", "machines": "501,501,502"}]},
            "pachinko": {"4": [{"name": "機種B", "sis_machine_code": "P1", "machines": "1001"}]},
        }

    def response(self, endpoint, **payload):
        if endpoint == "getStoreDetail":
            return {"status": 0, "store": self.store}
        if endpoint == "getTickers":
            return {"status": 0, "store_is_open": 0, "tickers": [],
                    "server_date_time": {"date": "2026-10-22", "time": "00:05:00"}}
        # The same unit appears twice with different net values and no date.
        # Neither response time nor rank order can establish a business day.
        slot = payload["machine_type"] == "S"
        return {"status": 0, "ranking": [
            {"store_id": 14, "machine_no": 501 if slot else 1001,
             "name": "機種A" if slot else "機種B", "difference": net,
             "start": 8, "out": 30000}
            for net in (2000, 1000)
        ], "server_date_time": {"date": "2026-10-22", "time": "00:05:00"}}

    def test_undated_duplicate_results_stay_raw_and_capture_uses_jst(self):
        with patch.object(official_source, "_post", side_effect=self.response) as post:
            result = official_source.fetch_daily_data(datetime(2026, 10, 21, 15, 5, tzinfo=timezone.utc))
        self.assertEqual(result["rows"], [])
        self.assertIsNone(result["source"]["data_date"])
        self.assertEqual(result["source"]["available_days"], [])
        self.assertEqual(result["raw"]["captured_at"], "2026-10-22T00:05:00+09:00")
        self.assertEqual(result["source"]["snapshot_units"], 2)
        self.assertEqual(result["source"]["snapshot_records"], 4)
        self.assertEqual(result["source"]["inventory_units"], 3)
        self.assertFalse(result["source"]["store_is_open"])
        ranking_calls = [call.kwargs for call in post.call_args_list if call.args[0] == "getRankingTop"]
        self.assertEqual([call["machine_type"] for call in ranking_calls], ["S", "P"])
        self.assertEqual(result["raw"]["rankings"]["slot"]["ranking"][0]["out"], 30000)

    def test_wrong_store_aborts_instead_of_creating_a_capture(self):
        self.store["store_no"] = "0001"
        with patch.object(official_source, "_post", side_effect=self.response):
            with self.assertRaisesRegex(ValueError, "PIA厚木ではありません"):
                official_source.fetch_daily_data()

    def test_wrong_store_rankings_are_rejected_but_verified_inventory_survives(self):
        def wrong_rankings(endpoint, **payload):
            result = self.response(endpoint, **payload)
            if endpoint == "getRankingTop":
                result["ranking"][0]["store_id"] = 1
            return result
        with patch.object(official_source, "_post", side_effect=wrong_rankings):
            result = official_source.fetch_daily_data()
        self.assertEqual(result["raw"]["rankings"], {})
        self.assertEqual(result["source"]["status"], "unavailable")
        self.assertEqual(len(result["source"]["warnings"]), 2)
        self.assertEqual(result["source"]["inventory_units"], 3)

    def test_optional_network_failure_keeps_other_kind(self):
        def partial(endpoint, **payload):
            if endpoint == "getRankingTop" and payload["machine_type"] == "S":
                raise requests.Timeout("upstream timeout")
            if endpoint == "getTickers":
                raise requests.Timeout("upstream timeout")
            return self.response(endpoint, **payload)
        with patch.object(official_source, "_post", side_effect=partial):
            result = official_source.fetch_daily_data()
        self.assertEqual(set(result["raw"]["rankings"]), {"pachinko"})
        self.assertIsNone(result["source"]["store_is_open"])
        self.assertEqual(result["source"]["snapshot_records"], 2)
        self.assertEqual(len(result["source"]["warnings"]), 2)
        self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
