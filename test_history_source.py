"""Parser tests use small synthetic HTML, never production observations."""

import unittest
from datetime import date
from unittest.mock import patch

from history_source import GOOPACHI_URL, SOURCE_URL, _discover_reports, _parse_goopachi_report, _parse_report, fetch_history


def article(body, store="PIA厚木", visit="2026/09/07", event="9月7日", heading=None):
    heading = heading or event
    return f"""<html><head><title>【{event} PIA厚木】取材結果</title></head>
    <body><h1>【{event} PIA厚木】取材結果</h1>
    <p>店舗: {store} 訪問日: {visit} 地域: 神奈川県 厚木市</p>
    <h2>{heading} PIA厚木(神奈川県)</h2><p>20スロ設置台数 377台</p>
    {body}</body></html>"""


UNIT_TABLE = """<table>
  <tr><th>台番号</th><th>機種</th><th>回転数</th><th>差枚</th></tr>
  <tr><td>519</td><td>ネオアイム</td><td>4,670G</td><td>+1,160枚</td></tr>
  <tr><td>520</td><td>ネオアイム</td><td>2,450G</td><td>-80枚</td></tr>
</table>"""


class HistorySourceTests(unittest.TestCase):
    def test_real_unit_columns_and_negative_net_are_preserved(self):
        parsed = _parse_report(article(UNIT_TABLE), "https://example.test/report")
        self.assertEqual(len(parsed["rows"]), 2)
        first, second = parsed["rows"]
        self.assertEqual(first["machine_number"], "519")
        self.assertEqual(first["spins"], 4670)
        self.assertEqual(first["net"], 1160)
        self.assertEqual(second["net"], -80)
        self.assertEqual(first["machine_name"], "ネオアイムジャグラーＥＸ")
        self.assertEqual(first["source_machine_name"], "ネオアイム")
        self.assertEqual(first["source_url"], "https://example.test/report")
        self.assertEqual(first["date"], "2026-09-07")
        self.assertNotIn("hits", first)

    def test_group_averages_do_not_become_unit_rows(self):
        aggregate = """<table><tr><th>機種</th><th>平均回転数</th><th>平均差枚</th></tr>
        <tr><td>ネオアイム 519〜524</td><td>5,000G</td><td>+1,000枚</td></tr></table>"""
        parsed = _parse_report(article(aggregate), "https://example.test/report")
        self.assertEqual(parsed["rows"], [])

    def test_wrong_store_rejected_even_if_atsugi_in_title(self):
        with self.assertRaisesRegex(ValueError, "店舗"):
            _parse_report(article(UNIT_TABLE, store="PIA川崎銀柳"), "https://example.test/report")

    def test_title_and_body_event_date_preserve_metadata_discrepancy(self):
        parsed = _parse_report(
            article(UNIT_TABLE, visit="2026/09/18", event="9月17日"),
            "https://example.test/report",
        )
        self.assertEqual(parsed["date"], "2026-09-17")
        self.assertIn("2026-09-18", parsed["date_note"])
        self.assertTrue(all(row["date_note"] == parsed["date_note"] for row in parsed["rows"]))

    def test_conflicting_body_heading_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "本文見出し"):
            _parse_report(
                article(UNIT_TABLE, visit="2026/09/18", event="9月17日", heading="9月18日"),
                "https://example.test/report",
            )

    def test_duplicate_unit_rows_do_not_inflate_sample(self):
        parsed = _parse_report(article(UNIT_TABLE + UNIT_TABLE), "https://example.test/report")
        self.assertEqual(len(parsed["rows"]), 2)
        altered = UNIT_TABLE.replace("-80枚", "-90枚")
        with self.assertRaisesRegex(ValueError, "異なる数値"):
            _parse_report(article(UNIT_TABLE + altered), "https://example.test/report")

    def test_old_goopachi_units_are_kept_without_group_average_spreading(self):
        old_html = """<title>PIA厚木本館(2021年4月25日取材結果)</title>
        <table><tr><th>取材日</th><td>4月25日(日)</td></tr>
        <tr><th>店名</th><td>PIA厚木本館</td></tr>
        <tr><th>20スロ台数</th><td>S176</td></tr></table>
        <table><tr><th>台番号</th><td>539~543</td></tr>
        <tr><th>平均差枚数</th><td>+1,000枚</td></tr></table>
        <table><tr><th>台番号</th><th>機種名</th><th>回転数</th><th>差枚数</th></tr>
        <tr><td>533</td><td>マイジャグラーIII</td><td>8,845G</td><td>+4,089枚</td></tr></table>"""
        parsed = _parse_goopachi_report(old_html, "https://judge-goopachi.com/example/")
        self.assertEqual(parsed["date"], "2021-04-25")
        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["rows"][0]["machine_name"], "マイジャグラーIII")
        self.assertEqual(parsed["rows"][0]["net"], 4089)
        with self.assertRaisesRegex(ValueError, "店舗"):
            _parse_goopachi_report(old_html.replace("PIA厚木本館", "PIA厚木アネックス店"), "https://example.test/")

    def test_archive_follows_all_advertised_pages_with_store_and_origin_checks(self):
        base = "https://777.slopachi-station.com"
        first = f"""<a href='/report1/'>PIA厚木 取材</a>
        <a href='/shopposts/page/2/?shop_id=4518'>2</a>
        <a href='https://777.slopachi-station.com.evil.test/shopposts/?shop_id=4518'>外部</a>
        <a href='/shopposts/page/3/?shop_id=9999'>別店舗</a>"""
        second = """<a href='/report2/'>PIA厚木 取材</a>
        <a href='/shopposts/?shop_id=4518'>1</a>
        <a href='/report1/'>PIA厚木 取材</a>"""
        pages = {SOURCE_URL: first, base + "/shopposts/page/2/?shop_id=4518": second}
        with patch("history_source._get_html", side_effect=lambda url: pages[url]) as get:
            found = _discover_reports(SOURCE_URL)
        self.assertEqual(found["pages"], 2)
        self.assertEqual(found["links"], [base + "/report1/", base + "/report2/"])
        self.assertEqual(get.call_count, 2)
        self.assertTrue(found["complete"])

    def test_archives_are_not_truncated_at_sixteen_reports(self):
        links = [f"https://777.slopachi-station.com/report{index}/" for index in range(23)]
        discovered = {"links": links, "pages": 2, "warnings": [], "complete": True}

        def report(url):
            number = url.split("report", 1)[1].split("/", 1)[0]
            row = {"date": "2021-04-25", "machine_number": number, "machine_name": "旧機種",
                   "kind": "slot", "rate": 20, "spins": 1000, "net": -100, "source_url": url}
            return {"date": row["date"], "rows": [row], "date_note": None}

        with patch("history_source._discover_reports", side_effect=[discovered, ValueError("unavailable")]), \
                patch("history_source._read_report", side_effect=report) as read:
            result = fetch_history(now=date(2026, 10, 21))
        self.assertEqual(len(result["rows"]), 23)
        self.assertEqual(read.call_count, 23)
        self.assertEqual(result["source"]["first_date"], "2021-04-25")
        self.assertEqual(result["source"]["backfill_requested_days"], 3650)
        self.assertFalse(result["source"]["backfill_complete"])

    def test_backfill_window_filters_fetch_result_without_claiming_deletion(self):
        report_url = "https://777.slopachi-station.com/report/"
        discovered = {"links": [report_url], "pages": 1, "warnings": [], "complete": True}
        row = {"date": "2021-04-25", "machine_number": "533", "machine_name": "旧機種",
               "kind": "slot", "rate": 20, "spins": 1000, "net": 100, "source_url": report_url}
        with patch("history_source._discover_reports", return_value=discovered), \
                patch("history_source._read_report", return_value={"date": row["date"], "rows": [row], "date_note": None}):
            result = fetch_history(lookback_days=7, now=date(2026, 10, 21))
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["source"]["sources"][0]["first_scanned_date"], "2021-04-25")
        self.assertIn("削除を意味しません", result["source"]["retention"])


if __name__ == "__main__":
    unittest.main()
