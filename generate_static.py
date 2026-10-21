"""Refresh real PIA history and build GitHub Pages JSON assets."""

from __future__ import annotations

import json
import gzip
import os
import time
import shutil
from pathlib import Path

import app as pia


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "docs"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def use_saved_store_snapshot() -> None:
    """Keep the last verified store catalog available during a source outage."""
    metadata = pia.database.get_metadata("official:snapshot", {})
    snapshot_path = Path((metadata or {}).get("path", ""))
    if snapshot_path.is_file():
        with gzip.open(snapshot_path, "rt", encoding="utf-8") as source:
            payload = json.load(source)
        store = payload["raw"]["store"]
    else:
        # Each scheduled run checks out the last published static catalog.
        previous = OUTPUT / "data" / "api" / "store-14.json"
        if not previous.is_file():
            raise RuntimeError("No saved official PIA inventory is available")
        store = json.loads(previous.read_text(encoding="utf-8"))["store"]
    if str(store.get("store_no", "")).zfill(4) != "0014":
        raise RuntimeError("Saved store inventory is not PIA Atsugi")
    pia._cache["detail"] = (time.monotonic(), {"store": store, "tickers": []})
    info = store.get("store_info", {})
    pia._cache["stores"] = (time.monotonic(), [{
        "id": 14, "address": info.get("address", "本厚木エリア"),
        "latitude": info.get("latitude"), "longitude": info.get("longitude"),
    }])


def main() -> None:
    # A scheduled GitHub Actions runner takes the place of the always-on server.
    # Keep prior dated records in the SQLite database between workflow runs.
    pia.app.testing = True
    if os.environ.get("PIA_SKIP_COLLECT") != "1":
        try:
            pia.collection_service.tick(force=os.environ.get("PIA_FORCE_COLLECT") == "true")
        except Exception as exc:  # retain old published history when a source is temporarily unavailable
            print(f"History refresh failed; keeping saved records: {exc}")

    client = pia.app.test_client()
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "index.html").write_text(
        (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        .replace('<html lang="ja">', '<html lang="ja" data-static-site="true">'),
        encoding="utf-8",
    )
    shutil.copytree(ROOT / "static", OUTPUT / "static", dirs_exist_ok=True)

    endpoints = {
        "stores.json": "/api/stores",
        "store-14.json": "/api/store/14",
        "collection.json": "/api/collection",
    }
    for name, endpoint in endpoints.items():
        response = client.get(endpoint)
        if response.status_code != 200:
            use_saved_store_snapshot()
            response = client.get(endpoint)
        if response.status_code != 200:
            raise RuntimeError(f"Could not build {endpoint}: HTTP {response.status_code}")
        write_json(OUTPUT / "data" / "api" / name, response.get_json())

    for kind in ("slot", "pachinko"):
        for days in (7, 14, 30, 90, 180, 365):
            response = client.get(f"/api/analysis?kind={kind}&days={days}")
            if response.status_code != 200:
                raise RuntimeError(f"Could not build analysis {kind}/{days}: HTTP {response.status_code}")
            analysis = response.get_json()
            if analysis.get("source", {}).get("fetched_at"):
                analysis["generated_at"] = analysis["source"]["fetched_at"]
            write_json(OUTPUT / "data" / "api" / f"analysis-{kind}-{days}.json", analysis)

    print(f"Built static site at {OUTPUT} with {pia.database.coverage()['total_rows']} dated records")


if __name__ == "__main__":
    main()
