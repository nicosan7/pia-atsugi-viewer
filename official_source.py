"""Capture the anonymous official Atsugi data without inventing a business date.

The public ranking endpoint may return several records for the same machine,
but supplies no date for those records. Its ``server_date_time`` is the time of
the response, not a date attached to the game results. Keep these responses as
raw snapshots; they must not enter the dated analysis-results table.
"""

from datetime import datetime, timedelta, timezone
import unicodedata

import requests


JST = timezone(timedelta(hours=9))
STORE_ID = 14
SOURCE_URL = "https://piagroupapp.com/"
API_BASE = SOURCE_URL + "api/stores/"
HEADERS = {"User-Agent": "PIA-Atsugi-Local-Viewer/1.0"}
# A bounded public ranking snapshot. This is not the number of installed units
# and does not establish coverage of every unit or every business day.
RANKING_LIMIT = 714


def _post(endpoint, **payload):
    # The official API reads form fields. JSON bodies silently select a default
    # store on some endpoints, so never change this to json=payload.
    response = requests.post(
        API_BASE + endpoint, headers=HEADERS, data=payload, timeout=(5, 20)
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("status") != 0:
        raise ValueError("PIA公式の公開データを取得できません")
    return result


def _inventory(store):
    """Validate the store and preserve the current unit/model/rate identity."""
    if (
        str(store.get("store_no", "")).zfill(4) != "0014"
        or "厚木" not in str(store.get("name", ""))
    ):
        raise ValueError("公式スナップショットの店舗がPIA厚木ではありません")
    rows = {}
    for kind in ("slot", "pachinko"):
        groups = store.get(kind) or {}
        if not isinstance(groups, dict):
            raise ValueError("PIA厚木の設置台情報の形式が正しくありません")
        for rate, machines in groups.items():
            for machine in machines:
                name = str(machine.get("name", "")).strip()
                if not name:
                    continue
                for value in str(machine.get("machines", "")).split(","):
                    number = value.strip()
                    if not number.isdecimal():
                        continue
                    row = {
                        "machine_number": number,
                        "machine_name": name,
                        "kind": kind,
                        "rate": str(rate),
                        "sis_machine_code": str(machine.get("sis_machine_code", "")),
                    }
                    rows[(kind, str(rate), number, name)] = row
    if not rows:
        raise ValueError("PIA厚木の設置台を確認できません")
    return sorted(rows.values(), key=lambda row: (row["kind"], int(row["machine_number"])))


def _validate_ranking(result):
    ranking = result.get("ranking")
    if not isinstance(ranking, list) or len(ranking) > RANKING_LIMIT:
        raise ValueError("公式ランキングの形式が正しくありません")
    for row in ranking:
        if not isinstance(row, dict) or str(row.get("store_id")) != str(STORE_ID):
            raise ValueError("公式ランキングにPIA厚木以外の店舗が含まれています")
        if not str(row.get("machine_no", "")).isdecimal():
            raise ValueError("公式ランキングの台番号が正しくありません")


def _normalize_name(value):
    return unicodedata.normalize("NFKC", str(value)).strip()


def fetch_daily_data(now=None):
    """Return a dated *capture* of public official data, with no dated rows.

    ``raw.captured_at`` is suitable for daily snapshot filenames. ``rows`` is
    deliberately empty because neither ranking game dates nor spin-field
    meanings have been established. Missing optional endpoints are warnings;
    an unverified/wrong-store inventory aborts the capture.
    """
    current = now or datetime.now(JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=JST)
    captured_at = current.astimezone(JST).isoformat(timespec="seconds")
    detail = _post("getStoreDetail", store_id=STORE_ID)
    store = detail.get("store")
    if not isinstance(store, dict):
        raise ValueError("PIA厚木の店舗情報が見つかりません")
    inventory = _inventory(store)
    warnings = []
    rankings = {}
    server_times = {"inventory": detail.get("server_date_time")}
    # P/S are the actual app selectors. Numeric values and 'slot' are not valid
    # equivalents and can produce the wrong kind and incorrectly scaled net.
    for kind, selector in (("slot", "S"), ("pachinko", "P")):
        try:
            result = _post(
                "getRankingTop", store_id=STORE_ID,
                machine_type=selector, limit=RANKING_LIMIT,
            )
            _validate_ranking(result)
            rankings[kind] = result
            server_times[kind] = result.get("server_date_time")
        except (requests.RequestException, ValueError, TypeError) as error:
            warnings.append(f"{kind}公式ランキングの取得・検証失敗: {error}")

    operation = None
    store_is_open = None
    try:
        operation = _post("getTickers", store_id=STORE_ID)
        if any(str(row.get("store_id")) != str(STORE_ID) for row in operation.get("tickers", [])):
            raise ValueError("公式営業情報にPIA厚木以外の店舗が含まれています")
        # Keep an unknown flag unknown. Do not infer closure from the clock.
        if operation.get("store_is_open") in (0, 1):
            store_is_open = bool(operation["store_is_open"])
        server_times["operation"] = operation.get("server_date_time")
    except (requests.RequestException, ValueError, TypeError) as error:
        operation = None
        warnings.append(f"公式営業情報の取得・検証失敗: {error}")

    current_identities = {
        (row["kind"], row["machine_number"], _normalize_name(row["machine_name"]))
        for row in inventory
    }
    observations = [
        (kind, str(row["machine_no"]), _normalize_name(row.get("name", "")))
        for kind, result in rankings.items()
        for row in result["ranking"]
    ]
    units = {(kind, number) for kind, number, _ in observations}
    current_units = {
        (kind, number) for kind, number, name in observations
        if (kind, number, name) in current_identities
    }
    message = (
        "PIA厚木の公式公開データを取得日時付きのスナップショットとして保存します。"
        "公式ランキングは営業日が付かない同一台の複数記録を含み、全台・全営業日の実績ではありません。"
        "取得日を遊技日に置き換えず、日別分析や推奨順位には使用しません。"
        "公式アプリの会員向け日別履歴は未取得です。"
    )
    if warnings:
        message += f" 公開データの一部（{len(warnings)}件）を取得できませんでした。"
    return {
        "rows": [],
        "raw": {
            "captured_at": captured_at,
            "store_id": STORE_ID,
            "store": store,
            "inventory": inventory,
            "rankings": rankings,
            "operation": operation,
        },
        "source": {
            "name": "PIA厚木 公式公開スナップショット",
            "url": SOURCE_URL,
            "status": "partial" if rankings else "unavailable",
            "coverage": "undated_official_rankings",
            "capture_status": "snapshot",
            "data_date": None,
            "captured_at": captured_at,
            "store_id": STORE_ID,
            "store_is_open": store_is_open,
            "snapshot_units": len(units),
            "snapshot_current_units": len(current_units),
            "snapshot_records": len(observations),
            "inventory_units": len(inventory),
            "upstream_server_times": server_times,
            "available_days": [],
            "message": message,
            "warnings": warnings,
        },
    }
