import json
import unittest

from analysis import analyze_history


def row(day, number="101", name="テスト機種", *, net=100, spins=2000,
        hits=10, kind="slot", rate="20円", **extra):
    return dict(date=f"2026-10-{day:02}", machine_number=number,
                machine_name=name, kind=kind, rate=rate, spins=spins,
                hits=hits, net=net, **extra)


def history(number, net, name="テスト機種", **kwargs):
    return [row(day, number, name, net=net, **kwargs) for day in (18, 19, 20)]


class HistoricalAnalysisTests(unittest.TestCase):
    def analyze(self, rows, **kwargs):
        return analyze_history(rows, as_of="2026-10-21", **kwargs)

    def test_better_net_and_positive_days_rank_higher(self):
        report = self.analyze(history("101", 800) + history("102", -800))
        self.assertEqual(report["status"], "ok")
        self.assertEqual([c["machine_number"] for c in report["candidates"]], ["101", "102"])
        self.assertGreater(report["candidates"][0]["score"], 50)
        self.assertLess(report["candidates"][1]["score"], 50)
        self.assertEqual(report["candidates"][0]["evidence"]["total_net"], 2400)
        self.assertIn("当選確率ではありません", report["score_label"])
        json.dumps(report, ensure_ascii=False, allow_nan=False)

    def test_today_future_and_outside_window_do_not_leak(self):
        base = history("101", 100) + history("102", -100)
        bad = [row(21, net=999999), row(22, net=999999),
               dict(row(1, net=999999), date="2026-09-01")]
        self.assertEqual(self.analyze(base)["candidates"], self.analyze(base + bad)["candidates"])

    def test_dedup_and_zero_spin_rows_do_not_add_evidence(self):
        base = history("101", 100) + history("102", -100)
        duplicate = [row(20, "101", net=None, hits=None), row(17, "101", spins=0, net=999999)]
        report = self.analyze(base + [base[0]] + duplicate)
        self.assertEqual(report["summary"]["usable_rows"], 6)
        self.assertEqual(report["candidates"][0]["evidence"]["sample_days"], 3)
        self.assertEqual(report["candidates"][0]["evidence"]["total_net"], 300)

    def test_low_sample_and_low_spin_units_are_excluded(self):
        rows = history("101", 100) + history("102", -100)
        rows += [row(20, "103", net=99999)] + history("104", 9999, spins=100)
        report = self.analyze(rows)
        self.assertEqual({c["machine_number"] for c in report["candidates"]}, {"101", "102"})
        self.assertEqual({c["machine_number"] for c in report["excluded"]}, {"103", "104"})

    def test_same_model_and_rate_peers_are_required(self):
        rows = history("101", 100, name="機種A") + history("102", -100, name="機種B")
        rows += history("103", 200, name="機種A", rate="5円")
        report = self.analyze(rows)
        self.assertEqual(report["status"], "insufficient_data")
        self.assertFalse(report["candidates"])

    def test_missing_net_uses_hits_only_with_same_model(self):
        rows = history("101", None, hits=20) + history("102", None, hits=5)
        report = self.analyze(rows)
        first = report["candidates"][0]
        self.assertEqual(first["machine_number"], "101")
        self.assertEqual(first["method"], "hit_rate")
        self.assertIsNone(first["evidence"]["total_net"])
        self.assertAlmostEqual(first["evidence"]["hit_rate"], .01)
        self.assertIsNone(first["evidence"]["weighted_net_per_1000_spins"])

    def test_hits_zero_is_valid_and_bb_rb_can_supply_hits(self):
        rows = history("101", None, hits=None, bb=5, rb=5)
        rows += history("102", None, hits=0)
        report = self.analyze(rows)
        self.assertEqual(report["candidates"][0]["evidence"]["hits"], 30)
        self.assertEqual(report["candidates"][1]["evidence"]["hits"], 0)

    def test_replacement_does_not_reuse_old_same_number_model(self):
        rows = [row(day, "101", "旧機種", net=9999) for day in (17, 18, 19)]
        rows += history("102", -100, name="旧機種")
        rows += [row(20, "101", "新機種", net=99999)]
        catalog = [dict(machine_number="101", machine_name="新機種", kind="slot", rate="20円"),
                   dict(machine_number="102", machine_name="旧機種", kind="slot", rate="20円")]
        report = self.analyze(rows, current_units=catalog)
        self.assertFalse(report["candidates"])
        self.assertIn("履歴不足", report["excluded"][0]["reason"])

    def test_unicode_width_and_spacing_match_catalog_but_not_different_models(self):
        rows = history("101", 100, name="Ｌ テスト Ｖ") + history("102", -100, name="LテストV")
        catalog = [dict(machine_number=str(number), machine_name="L テスト V", rate="20円")
                   for number in (101, 102)]
        report = self.analyze(rows, current_units=catalog)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(len(report["candidates"]), 2)
        self.assertEqual(report["candidates"][0]["machine_name"], "L テスト V")
        catalog[1]["machine_name"] = "L テスト V2"
        self.assertFalse(self.analyze(rows, current_units=catalog)["candidates"])

    def test_returning_model_cannot_reuse_previous_installation(self):
        rows = [row(day, "101", "機種A", net=9999) for day in (14, 15, 16)]
        rows += [row(17, "101", "機種B"), row(20, "101", "機種A")]
        rows += history("102", -100, name="機種A")
        report = self.analyze(rows)
        self.assertFalse(report["candidates"])
        self.assertIn("履歴不足", report["excluded"][0]["reason"])

    def test_empty_current_catalog_does_not_invent_candidates(self):
        report = self.analyze(history("101", 100) + history("102", -100), current_units=[])
        self.assertEqual(report["status"], "insufficient_data")
        self.assertFalse(report["candidates"])
        self.assertFalse(report["observations"])
        self.assertTrue(report["settings"]["catalog_verified"])

    def test_one_day_is_visible_as_observation_without_rank_or_score(self):
        url = "https://example.com/verified-report"
        report = self.analyze([row(20, source_url=url)])
        self.assertEqual(report["status"], "insufficient_data")
        self.assertFalse(report["candidates"])
        self.assertEqual(len(report["observations"]), 1)
        observed = report["observations"][0]
        self.assertNotIn("score", observed)
        self.assertNotIn("rank", observed)
        self.assertEqual(observed["evidence"]["sample_days"], 1)
        self.assertEqual(observed["evidence"]["total_spins"], 2000)
        self.assertEqual(observed["evidence"]["total_net"], 100)
        self.assertEqual(observed["evidence"]["positive_days"], 1)
        self.assertEqual(observed["source_url"], url)
        self.assertEqual(observed["history"][0]["source_url"], url)

    def test_observations_exclude_removed_or_old_model_and_future_data(self):
        rows = history("101", 9000, name="旧機種")
        rows += [row(20, "102", "設置機種", net=-100), row(21, "102", "設置機種", net=999999)]
        catalog = [dict(machine_number="101", machine_name="新機種", rate="20円"),
                   dict(machine_number="102", machine_name="設置機種", rate="20円")]
        report = self.analyze(rows, current_units=catalog)
        self.assertEqual([r["machine_number"] for r in report["observations"]], ["102"])
        self.assertEqual(report["observations"][0]["evidence"]["total_net"], -100)
        self.assertEqual(len(report["observations"][0]["history"]), 1)
        self.assertFalse(self.analyze(rows, current_units=[])["observations"])

    def test_observations_are_limited_and_not_sorted_by_profit(self):
        rows = [row(20, str(n), net=n * 100) for n in range(505, 0, -1)]
        report = self.analyze(rows)
        self.assertEqual(len(report["observations"]), 500)
        self.assertEqual(report["summary"]["observed_units"], 505)
        self.assertEqual(report["observations"][0]["machine_number"], "1")

    def test_old_sparse_samples_use_finite_weighted_denominator(self):
        rows = [dict(r, date=f"2026-08-{d:02}") for r, d in zip(history("101", 100), (1, 2, 3))]
        rows += [dict(r, date=f"2026-08-{d:02}") for r, d in zip(history("102", -100), (1, 2, 3))]
        report = self.analyze(rows, days=90)
        self.assertEqual(report["status"], "ok")
        self.assertAlmostEqual(report["candidates"][0]["evidence"]["weighted_net_per_1000_spins"], 50)
        json.dumps(report, allow_nan=False)

    def test_default_catalog_is_actual_latest_history_and_has_disclosure(self):
        report = self.analyze(history("101", 100) + history("102", -100))
        self.assertFalse(report["settings"]["catalog_verified"])
        self.assertIn("最後に確認", report["reasons"][0])

    def test_recent_days_affect_rate_more_than_older_days(self):
        # Identical totals and positive days; only the order differs.
        rows = [row(18, "101", net=-1000), row(19, "101", net=0), row(20, "101", net=1000),
                row(18, "102", net=1000), row(19, "102", net=0), row(20, "102", net=-1000)]
        report = self.analyze(rows)
        self.assertEqual(report["candidates"][0]["machine_number"], "101")

    def test_equal_data_has_stable_numeric_unit_order_and_neutral_score(self):
        rows = history("11", 100) + history("2", 100) + history("3", 100)
        report = self.analyze(reversed(rows), limit=2)
        self.assertEqual([c["machine_number"] for c in report["candidates"]], ["2", "3"])
        self.assertTrue(all(c["score"] == 50 for c in report["candidates"]))
        self.assertEqual(report["summary"]["eligible_candidates"], 3)

    def test_no_history_and_invalid_inputs(self):
        report = self.analyze([])
        self.assertEqual(report["status"], "no_data")
        self.assertEqual(report["window"]["end"], "2026-10-20")
        with self.assertRaises(ValueError):
            self.analyze([], min_days=1)
        with self.assertRaises(ValueError):
            self.analyze([], days=0)
        with self.assertRaises(ValueError):
            self.analyze([], kind="unknown")
        invalid = [row(20, spins=float("nan")), row(19, spins=-1), row(18, net=float("inf"))]
        json.dumps(self.analyze(invalid), allow_nan=False)

    def test_pachinko_kind_filter_and_threshold(self):
        rows = history("101", 5000, kind="pachinko", spins=400, rate="4円")
        rows += history("102", -5000, kind="pachinko", spins=400, rate="4円")
        rows += history("103", 99999)
        report = self.analyze(rows, kind="pachinko")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["settings"]["min_spins"], 1000)
        self.assertEqual(len(report["candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
