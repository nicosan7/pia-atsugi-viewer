"""PIA厚木（本厚木）の設置台と過去の実績を表示するローカルサイト。"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, render_template, request

from analysis import analyze_history
from collector import CollectionService
from storage import HistoryStore

app = Flask(__name__)
app.json.ensure_ascii = False
JST = timezone(timedelta(hours=9))
STORE_ID = 14
API_BASE = "https://piagroupapp.com/api/stores/"
# The API reads form fields; JSON silently falls back to the Kawasaki store.
HEADERS = {"User-Agent": "UnityPlayer/2022.3.20f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"}
database = HistoryStore(os.environ.get("PIA_HISTORY_DB", os.path.join(app.root_path, "data", "history.sqlite3")))
collection_service = CollectionService(database)
_cache = {}
_cache_lock = threading.Lock()


def api_post(endpoint, **payload):
    response = requests.post(API_BASE + endpoint, headers=HEADERS, data=payload, timeout=(5, 20))
    response.raise_for_status()
    data = response.json()
    if data.get("status") != 0:
        raise ValueError("公式データの取得に失敗しました。")
    return data


def cached(key, loader, ttl=300):
    with _cache_lock:
        item = _cache.get(key)
        if item and time.monotonic() - item[0] < ttl:
            return item[1]
    result = loader()
    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
    return result


def load_stores():
    def fetch():
        stores = [s for s in api_post("getAllStores").get("stores", []) if str(s.get("id")) == str(STORE_ID)]
        if not stores:
            raise ValueError("PIA厚木の店舗情報が見つかりませんでした。")
        return stores
    return cached("stores", fetch, ttl=3600)


def load_detail():
    def fetch():
        store = api_post("getStoreDetail", store_id=STORE_ID).get("store", {})
        if str(store.get("store_no", "")).zfill(4) != "0014" or "厚木" not in store.get("name", ""):
            raise ValueError("取得データがPIA厚木と一致しません。")
        for kind in ("slot", "pachinko"):
            for machines in (store.get(kind) or {}).values():
                for machine in machines:
                    numbers = list(dict.fromkeys(n.strip() for n in str(machine.get("machines", "")).split(",") if n.strip()))
                    machine["machines"] = ",".join(numbers)
        return {"store": store, "tickers": []}
    return cached("detail", fetch)


def current_units():
    store = load_detail()["store"]
    return [
        {"machine_number": number.strip(), "machine_name": machine.get("name", ""), "kind": kind, "rate": str(rate)}
        for kind in ("slot", "pachinko")
        for rate, machines in (store.get(kind) or {}).items()
        for machine in machines
        for number in machine.get("machines", "").split(",") if number.strip()
    ]


def collect_history():
    return collection_service.tick(force=True)


@app.before_request
def start_history_updates():
    if app.testing or os.environ.get("PIA_DISABLE_COLLECTOR") == "1":
        return
    collection_service.start()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/stores")
def get_stores():
    try:
        return jsonify(load_stores())
    except (requests.RequestException, ValueError):
        app.logger.exception("Could not fetch Atsugi store")
        return jsonify(error="店舗情報を取得できませんでした。再読み込みしてください。"), 502


@app.get("/api/store/<int:store_id>")
def get_store(store_id):
    if store_id != STORE_ID:
        return jsonify(error="本厚木のPIA厚木のみ表示できます。"), 404
    try:
        return jsonify(load_detail())
    except (requests.RequestException, ValueError):
        app.logger.exception("Could not fetch Atsugi machines")
        return jsonify(error="PIA厚木の設置台を取得できませんでした。再読み込みしてください。"), 502


@app.get("/api/analysis")
def get_analysis():
    try:
        days = int(request.args.get("days", "90"))
    except (ValueError, TypeError):
        return jsonify(error="分析期間は7・14・30・90・180・365日のいずれかを指定してください。"), 400
    kind = request.args.get("kind", "slot")
    if days not in (7, 14, 30, 90, 180, 365) or kind not in ("slot", "pachinko"):
        return jsonify(error="分析期間または台種が正しくありません。"), 400
    today = datetime.now(JST).date()
    rows = database.load((today - timedelta(days=days)).isoformat(), today.isoformat())
    source = database.source()
    refresh = dict(running=collection_service.running, message=collection_service.message)
    collection = collection_service.status()
    try:
        units = current_units() if rows else []
    except (requests.RequestException, ValueError):
        return jsonify(status="error", candidates=[], summary={}, source=source, refresh=refresh, collection=collection,
                       reasons=["現在の設置台を確認できないため、候補の表示を保留しています。"])
    result = analyze_history(rows, days=days, kind=kind, as_of=today.isoformat(), current_units=units)
    result.update(source=source, refresh=refresh, collection=collection)
    return jsonify(result)


@app.get("/api/collection")
def get_collection():
    return jsonify(collection_service.status())


@app.get("/api/history")
def get_history():
    """Read saved history by ORIGINAL data date, including older backfills."""
    start = request.args.get("start", "0001-01-01")
    end = request.args.get("end", datetime.now(JST).date().isoformat())
    try:
        datetime.strptime(start, "%Y-%m-%d")
        exclusive_end = (datetime.strptime(end, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    except (ValueError, OverflowError):
        return jsonify(error="日付はYYYY-MM-DD形式で指定してください。"), 400
    if start > end:
        return jsonify(error="開始日は終了日以前にしてください。"), 400
    return jsonify(rows=database.load(start, exclusive_end),
                   collection=collection_service.status())


if __name__ == "__main__":
    # Start before the first HTTP request so closed browsers do not stop saving.
    start_history_updates()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
