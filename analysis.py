"""Past-results comparisons for installed machines; no prediction or win probability.

``analyze_history(rows, as_of="2026-10-21", current_units=catalog)`` accepts
normalized daily dictionaries with date, machine_number, machine_name, kind,
rate, spins, hits/bb/rb and signed net. The optional catalog uses the same unit
identity fields. ``None`` uses the latest identity actually observed in the
past data; an empty catalog intentionally produces no candidates. Completed
dates strictly before as_of are used, never today's partial results.

All scores compare a unit with OTHER sufficiently sampled units of the same
model, kind and rate. Net and positive-day frequency are preferred; without
enough signed net, hits per spin are compared within that model. This is a
descriptive heuristic, not machine learning and not a forecast of a jackpot.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import exp, isfinite, log, tanh
from statistics import median
from typing import Any, Iterable, Mapping
from unicodedata import normalize
from urllib.parse import urlsplit


SCORE_LABEL = "同一機種内の相対参考スコア（当選確率ではありません）"
JST = timezone(timedelta(hours=9))


def _model_key(name: str) -> str:
    """Only typography differences; never fuzzy-match replacement models."""
    return "".join(normalize("NFKC", name).split())


def _source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("http", "https") and parsed.netloc else None
    except ValueError:
        return None


def _number(value: Any, *, signed: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and (signed or result >= 0) else None


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _identity(row: Mapping[str, Any], default_kind: str) -> tuple[str, str, str]:
    return (
        str(row.get("kind", default_kind) or default_kind).strip(),
        str(row.get("rate", "") or "").strip(),
        str(row.get("machine_number", "") or "").strip(),
    )


def _stats(rows: list[dict[str, Any]], method: str, as_of: date) -> dict[str, float]:
    weights = [exp(-log(2) * ((as_of - r["_date"]).days - 1) / 7) for r in rows]
    weighted_spins = sum(r["spins"] * w for r, w in zip(rows, weights))
    field = "net" if method == "net" else "hits"
    weighted_value = sum(r[field] * w for r, w in zip(rows, weights))
    return {
        "rate": weighted_value / weighted_spins,
        "positive_fraction": (
            sum(w for r, w in zip(rows, weights) if r[field] > 0) / sum(weights)
            if method == "net" else 0.0
        ),
        "spins": sum(r["spins"] for r in rows),
        "days": len(rows),
        "weighted_spins": weighted_spins,
    }


def _clean_history(rows: Iterable[Mapping[str, Any]], *, kind: str, start: date,
                   as_of: date) -> tuple[list[dict[str, Any]], int]:
    dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
    count = 0
    for raw in rows:
        count += 1
        if not isinstance(raw, Mapping):
            continue
        day = _day(raw.get("date"))
        ident = _identity(raw, kind)
        name = str(raw.get("machine_name", "") or "").strip()
        spins = _number(raw.get("spins"))
        if (day is None or not start <= day < as_of or ident[0] != kind
                or not ident[2] or not name or spins is None or spins <= 0):
            continue
        hits = _number(raw.get("hits"))
        bb, rb = _number(raw.get("bb")), _number(raw.get("rb"))
        if hits is None and bb is not None and rb is not None:
            hits = bb + rb
        row = {
            "date": day.isoformat(), "_date": day, "_identity": ident,
            "machine_number": ident[2], "machine_name": name,
            "_model": _model_key(name),
            "kind": kind, "rate": ident[1], "spins": spins,
            "hits": hits, "bb": bb, "rb": rb,
            "net": _number(raw.get("net"), signed=True),
            "source_url": _source_url(raw.get("source_url")),
        }
        key = (day, *ident, row["_model"])
        # An incomplete duplicate must not overwrite a previously complete row.
        completeness = sum(row[k] is not None for k in ("net", "hits", "bb", "rb"))
        previous = dedup.get(key)
        if previous is None or completeness >= sum(
                previous[k] is not None for k in ("net", "hits", "bb", "rb")):
            dedup[key] = row
    return sorted(dedup.values(), key=lambda r: (r["date"], r["machine_number"],
                                                r["machine_name"])), count


def _history_output(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("date", "spins", "hits", "bb", "rb", "net", "source_url")}


def _observed_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe actual observations without converting them into recommendations."""
    net_rows = [r for r in rows if r["net"] is not None]
    hit_rows = [r for r in rows if r["hits"] is not None]
    return {
        "sample_days": len(rows), "total_spins": sum(r["spins"] for r in rows),
        "total_net": sum(r["net"] for r in net_rows) if net_rows else None,
        "net_days": len(net_rows),
        "positive_days": sum(r["net"] > 0 for r in net_rows) if net_rows else None,
        "hits": sum(r["hits"] for r in hit_rows) if hit_rows else None,
        "hit_rate": (sum(r["hits"] for r in hit_rows) / sum(r["spins"] for r in hit_rows)) if hit_rows else None,
    }


def analyze_history(
    rows: Iterable[Mapping[str, Any]], *, days: int = 30, kind: str = "slot",
    as_of: str | date | None = None,
    current_units: Iterable[Mapping[str, Any]] | None = None,
    min_days: int = 3, min_spins: int | None = None, limit: int = 12,
) -> dict[str, Any]:
    """Return stable JSON-ready ranked historical comparisons and exclusions.

    Defaults require 3 completed days and 3,000 slot / 1,000 pachinko spins,
    both for the unit and for each peer. At least one eligible peer is needed.
    Newer days have a seven-day half-life. Sample shrinkage pulls small samples
    toward their model's peer baseline. Supplying ``as_of`` makes runs fully
    deterministic. The window is [as_of - days, as_of), in Japan time by default.
    """
    if kind not in ("slot", "pachinko"):
        raise ValueError("kind must be 'slot' or 'pachinko'")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 366:
        raise ValueError("days must be between 1 and 366")
    if not isinstance(min_days, int) or isinstance(min_days, bool) or min_days < 3:
        raise ValueError("min_days must be at least 3")
    threshold = (3000 if kind == "slot" else 1000) if min_spins is None else min_spins
    if (not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
            or not isfinite(threshold) or threshold <= 0):
        raise ValueError("min_spins must be positive")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be positive")
    cutoff = datetime.now(JST).date() if as_of is None else _day(as_of)
    if cutoff is None:
        raise ValueError("as_of must be an ISO date")
    start = cutoff - timedelta(days=days)
    history, input_count = _clean_history(rows, kind=kind, start=start, as_of=cutoff)
    result: dict[str, Any] = {
        "status": "no_data", "kind": kind, "score_label": SCORE_LABEL,
        "window": {"start": start.isoformat(), "end": (cutoff - timedelta(days=1)).isoformat(),
                   "as_of": cutoff.isoformat(), "days": days},
        "settings": {"min_days": min_days, "min_spins": threshold,
                     "recent_half_life_days": 7, "catalog_verified": current_units is not None},
        "summary": {"input_rows": input_count, "usable_rows": len(history),
                    "observed_days": len({r["date"] for r in history}),
                    "observed_units": 0, "eligible_candidates": 0, "excluded_candidates": 0},
        "candidates": [], "observations": [], "excluded": [], "reasons": [],
        "methodology": [
            "当日・未来のデータを除き、完了した営業日の履歴だけを使用します。",
            "同じ機種・遊技種別・貸玉料金の他台と比較します。新しい日ほど重みを付けます。",
            "差枚・差玉とプラス日の割合を優先し、差枚・差玉が足りない場合は同一機種の大当り回数÷回転数を参考にします。",
            "少ない履歴は平均との差を小さく補正します。過去の傾向であり、次回の当選や利益を保証しません。",
        ],
    }
    if not history:
        result["reasons"] = ["対象期間の完了した営業日データがありません。履歴の収集後に分析します。"]
        return result

    by_unit: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        by_unit[row["_identity"]].append(row)
    catalog: dict[tuple[str, str, str], str] = {}
    if current_units is None:
        for ident, unit_rows in by_unit.items():
            catalog[ident] = unit_rows[-1]["machine_name"]
        result["reasons"].append("設置一覧を取得できないため、期間内に最後に確認した台・機種を表示しています。")
    else:
        for unit in current_units:
            if not isinstance(unit, Mapping):
                continue
            ident = _identity(unit, kind)
            name = str(unit.get("machine_name", "") or "").strip()
            if ident[0] == kind and ident[2] and name:
                catalog[ident] = name

    # A model before a replacement is not evidence for the current model, even
    # if that model occupied the same number earlier in the selected window.
    segments: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for ident, name in catalog.items():
        unit_rows = by_unit.get(ident, [])
        other_dates = [r["_date"] for r in unit_rows if r["_model"] != _model_key(name)]
        last_replacement = max(other_dates) if other_dates else date.min
        segments[ident] = [r for r in unit_rows if r["_model"] == _model_key(name)
                           and r["_date"] > last_replacement]

    def unit_order(unit: Mapping[str, Any]) -> tuple[Any, ...]:
        number = unit["machine_number"]
        return (0, int(number)) if number.isdigit() else (1, number)

    for ident, samples in segments.items():
        if not samples:
            continue
        result["observations"].append({
            "machine_number": ident[2], "machine_name": catalog[ident],
            "kind": ident[0], "rate": ident[1],
            "evidence": _observed_evidence(samples),
            "history": [_history_output(row) for row in samples],
            "source_url": next((r["source_url"] for r in reversed(samples) if r["source_url"]), None),
        })
    result["summary"]["observed_units"] = len(result["observations"])
    result["observations"].sort(key=lambda unit: (-date.fromisoformat(unit["history"][-1]["date"]).toordinal(),
                                                 unit_order(unit), unit["rate"]))
    result["observations"] = result["observations"][:500]

    eligible: dict[tuple[tuple[str, str, str], str], list[dict[str, Any]]] = {}
    for ident, unit_rows in segments.items():
        for method, field in (("net", "net"), ("hit_rate", "hits")):
            samples = [r for r in unit_rows if r[field] is not None]
            if len(samples) >= min_days and sum(r["spins"] for r in samples) >= threshold:
                eligible[(ident, method)] = samples

    def exclude(ident: tuple[str, str, str], name: str, reason: str) -> None:
        result["excluded"].append({"machine_number": ident[2], "machine_name": name,
                                   "kind": ident[0], "rate": ident[1], "reason": reason})

    for ident, name in catalog.items():
        unit_rows = segments[ident]
        selected = None
        for method in ("net", "hit_rate"):
            samples = eligible.get((ident, method))
            if samples is None:
                continue
            peer_groups = [peer_rows for (peer_ident, peer_method), peer_rows in eligible.items()
                           if peer_method == method and peer_ident != ident
                           and peer_ident[:2] == ident[:2]
                           and _model_key(catalog[peer_ident]) == _model_key(name)]
            if peer_groups:
                selected = (method, samples, peer_groups)
                break
        if selected is None:
            if not unit_rows:
                reason = "現在の機種の対象期間内データがありません（入替前の履歴は使用しません）。"
            elif len(unit_rows) < min_days or sum(r["spins"] for r in unit_rows) < threshold:
                reason = f"履歴不足：完了した{min_days}日以上・累計{threshold:,.0f}回転以上が必要です。"
            elif not any((ident, method) in eligible for method in ("net", "hit_rate")):
                reason = "差枚・差玉または大当り回数の有効な履歴が足りません。"
            else:
                reason = "同一機種・同一料金で十分な履歴のある比較対象台がありません。"
            exclude(ident, name, reason)
            continue

        method, samples, peer_groups = selected
        peers = [r for group in peer_groups for r in group]
        own, peer = _stats(samples, method, cutoff), _stats(peers, method, cutoff)
        peer_unit_stats = [_stats(group, method, cutoff) for group in peer_groups]
        peer_rates = [p["rate"] for p in peer_unit_stats]
        center = median(peer_rates)
        mad = median(abs(value - center) for value in peer_rates) * 1.4826
        spin_shrink = own["spins"] / (own["spins"] + threshold * 2)
        day_shrink = own["days"] / (own["days"] + min_days)
        reasons = [f"完了した{len(samples)}日・累計{own['spins']:,.0f}回転を集計。比較対象は同一機種の{len(peer_groups)}台。"]
        if method == "net":
            scale = max(mad, 0.05 if kind == "slot" else 0.5)
            net_part = tanh((own["rate"] - peer["rate"]) * spin_shrink / scale)
            positive_part = tanh((own["positive_fraction"] - peer["positive_fraction"])
                                 * day_shrink / 0.25)
            score = 50 + 30 * net_part + 20 * positive_part
            reasons.append(f"直近重視の1,000回転あたり差{'枚' if kind == 'slot' else '玉'}は{own['rate'] * 1000:+,.0f}、同一機種の他台は{peer['rate'] * 1000:+,.0f}。")
            positive_days = sum(r["net"] > 0 for r in samples)
            reasons.append(f"差{'枚' if kind == 'slot' else '玉'}がプラスの日は{positive_days}/{len(samples)}日。履歴の少なさを補正した相対評価です。")
        else:
            scale = max(mad, abs(peer["rate"]) * 0.2, 0.0001)
            score = 50 + 45 * tanh((own["rate"] - peer["rate"]) * spin_shrink / scale)
            reasons.append(f"差枚・差玉の有効な比較データが不足しているため、同一機種の大当り回数÷回転数を比較しています（1,000回転あたり{own['rate'] * 1000:.2f}回、他台{peer['rate'] * 1000:.2f}回）。")
            reasons.append("大当り回数だけでは収支・設定・次回の当選確率は判断できません。")
        measured_net = [r for r in samples if r["net"] is not None]
        measured_hits = [r for r in samples if r["hits"] is not None]
        evidence = {
            "sample_days": len(samples), "total_spins": own["spins"],
            "total_net": sum(r["net"] for r in measured_net) if measured_net else None,
            "net_days": len(measured_net),
            "positive_days": sum(r["net"] > 0 for r in measured_net) if measured_net else None,
            "positive_fraction": (sum(r["net"] > 0 for r in measured_net) / len(measured_net)) if measured_net else None,
            "hits": sum(r["hits"] for r in measured_hits) if measured_hits else None,
            "hit_rate": (sum(r["hits"] for r in measured_hits) / sum(r["spins"] for r in measured_hits)) if measured_hits else None,
            "peer_units": len(peer_groups), "peer_days": len(peers),
            "weighted_net_per_1000_spins": round(own["rate"] * 1000, 3) if method == "net" else None,
            "peer_net_per_1000_spins": round(peer["rate"] * 1000, 3) if method == "net" else None,
            "weighted_hit_rate": own["rate"] if method == "hit_rate" else None,
            "peer_hit_rate": peer["rate"] if method == "hit_rate" else None,
        }
        result["candidates"].append({
            "machine_number": ident[2], "machine_name": name, "kind": ident[0],
            "rate": ident[1], "score": round(max(0, min(100, score)), 1),
            "method": method, "reasons": reasons, "evidence": evidence,
            "history": [_history_output(r) for r in unit_rows],
        })

    result["candidates"].sort(key=lambda unit: (-unit["score"], unit_order(unit), unit["rate"]))
    total = len(result["candidates"])
    result["candidates"] = [dict(candidate, rank=rank) for rank, candidate in
                            enumerate(result["candidates"][:limit], 1)]
    result["excluded"].sort(key=lambda unit: (unit_order(unit), unit["rate"]))
    result["summary"]["eligible_candidates"] = total
    result["summary"]["excluded_candidates"] = len(result["excluded"])
    result["status"] = "ok" if total else "insufficient_data"
    if not total:
        if not catalog:
            result["reasons"].append("表示対象の設置台がありません。設置一覧と履歴の収集を確認してください。")
        else:
            result["reasons"].append(f"比較に必要な履歴が不足しています。各台{min_days}日以上・{threshold:,.0f}回転以上と、同一機種の比較対象台が必要です。")
    return result
