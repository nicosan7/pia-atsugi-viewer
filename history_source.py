"""Read published, per-machine PIA Atsugi reports without inventing daily data.

This public source covers selected machines on report dates only. It is not the
PIA member-only seven-day history and must never be described as store coverage.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import re
from threading import Lock
import time
import unicodedata
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import requests


SOURCE_NAME = "スロパチステーション 公開取材データ"
SOURCE_URL = "https://777.slopachi-station.com/shopposts/?shop_id=4518"
SOURCE_HOST = "777.slopachi-station.com"
GOOPACHI_NAME = "グーパチ 公開取材データ"
GOOPACHI_URL = "https://judge-goopachi.com/?s=PIA%E5%8E%9A%E6%9C%A8"
GOOPACHI_HOST = "judge-goopachi.com"
JST = timezone(timedelta(hours=9))
_REPORT_CACHE = {}
_CACHE_LOCK = Lock()
_CACHE_SECONDS = 6 * 60 * 60

# Explicit identities verified against PIA Atsugi's official installation list.
# No fuzzy match: a replacement at the same unit number must remain a different
# machine. Keep the original report label as provenance on every row.
_ALIASES = {
    "ネオアイム": "ネオアイムジャグラーＥＸ",
    "乙女5": "Ｌ戦国乙女５",
    "カバネリ海門決戦": "Ｌ甲鉄城のカバネリ海門決戦",
    "スマスロゴッド": "Ｌミリオンゴッド神々の軌跡",
    "マギレコ": "Ｌマギアレコード",
    "東京喰種": "Ｌ東京喰種",
    "SAO2": "Ｌソードアート・オンラインＩＩ",
    "ヴヴヴ2": "Ｌ革命機ヴァルヴレイヴ２",
    "ミスター": "ミスタージャグラー",
    "ゴージャグ3": "ゴーゴージャグラー３",
    "とある禁書目録2": "Ｌとある魔術の禁書目録２",
    "マイジャグ5": "マイジャグラーＶ",
    "ファンキー2": "ファンキージャグラー２ＫＴ",
    "リコリコ": "Ｌリコリス・リコイル",
    "北斗転生2": "Ｌ北斗 転生の章２",
    "無職転生": "Ｌ無職転生",
    "ダリフラ": "Ｌダーリン・イン・ザ・フランキス",
    "タクトオーパス": "Ｌタクトオーパス デスティニー",
    "かのかり": "Ｌ彼女、お借りします",
}


def _text(value):
    return unicodedata.normalize("NFKC", value).strip()


def _number(value):
    value = _text(value).replace(",", "").replace("−", "-")
    match = re.fullmatch(r"([+-]?\d+)\s*(?:G|枚|玉)?", value)
    return int(match.group(1)) if match else None


def _get_html(url):
    response = requests.get(
        url,
        headers={"User-Agent": "PIA-Atsugi-Local-Viewer/1.0"},
        timeout=(6, 20),
    )
    response.raise_for_status()
    return response.content


def _parse_report(html, source_url):
    """Return only explicit unit rows; aggregate/group results are not rows."""
    soup = BeautifulSoup(html, "html.parser")
    text = _text(soup.get_text(" ", strip=True))
    # Do not accept an unrelated store merely because Atsugi appears in links.
    store = re.search(r"店舗:\s*(\S+)\s*訪問日:", text)
    if not store or store.group(1) not in ("PIA厚木", "PIA厚木本館"):
        raise ValueError("取材記事の店舗がPIA厚木ではありません")
    visit = re.search(r"訪問日:\s*(\d{4})/(\d{1,2})/(\d{1,2})", text)
    if not visit:
        raise ValueError("取材記事の日付を確認できません")
    metadata_date = date(*(int(v) for v in visit.groups()))

    # Event date is stated in both article title and the data section heading.
    # Some articles have a next-day 'visit' metadata label; retain that mismatch
    # explicitly rather than silently replacing the event date with that label.
    title = _text(soup.title.get_text()) if soup.title else ""
    event = re.search(r"【(\d{1,2})月(\d{1,2})日\s+PIA厚木】", title)
    event_date = metadata_date
    date_note = None
    if event:
        month, day = (int(v) for v in event.groups())
        matching_heading = any(
            re.search(rf"(?<!\d){month}月{day}日\s+PIA厚木", _text(h.get_text(" ", strip=True)))
            for h in soup.find_all(["h2", "h3"])
        )
        if not matching_heading:
            raise ValueError("取材日を本文見出しで照合できません")
        event_date = date(metadata_date.year, month, day)
        if event_date != metadata_date:
            date_note = (
                f"記事タイトル・本文の取材日{event_date.isoformat()}を採用。"
                f"記事の訪問日欄は{metadata_date.isoformat()}と表記されています。"
            )

    # These reports explicitly identify their data section as 20-yen slots.
    if not re.search(r"20スロ\s*(?:設置|全|\d)", text):
        return {"rows": [], "date": event_date.isoformat(), "date_note": date_note}

    rows = {}
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        headers = [_text(cell.get_text(" ", strip=True)) for cell in trs[0].find_all(["th", "td"])]
        expected = ("台番号", "機種", "回転数", "差枚")
        if not all(name in headers for name in expected):
            continue
        indexes = [headers.index(name) for name in expected]
        for tr in trs[1:]:
            cells = [_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["td", "th"])]
            if len(cells) <= max(indexes):
                continue
            unit, name, spins, net = (cells[index] for index in indexes)
            spins, net = _number(spins), _number(net)
            if not re.fullmatch(r"\d{1,5}", unit) or not name or spins is None or net is None or spins < 0:
                continue
            row = {
                "date": event_date.isoformat(),
                "machine_number": unit,
                "machine_name": _ALIASES.get(name, name),
                "source_machine_name": name,
                "kind": "slot",
                "rate": 20,
                "spins": spins,
                "net": net,
                "source_url": source_url,
                "source_name": SOURCE_NAME,
                "coverage": "selected_report_units",
            }
            if date_note:
                row["date_note"] = date_note
            # Duplicate or contradictory entries must not inflate sample size.
            if unit in rows and rows[unit] != row:
                raise ValueError(f"同一取材日に台番号{unit}の異なる数値があります")
            rows[unit] = row
    return {"rows": list(rows.values()), "date": event_date.isoformat(), "date_note": date_note}


def _read_report(url):
    with _CACHE_LOCK:
        cached = _REPORT_CACHE.get(url)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    parser = _parse_goopachi_report if urlparse(url).hostname == GOOPACHI_HOST else _parse_report
    parsed = parser(_get_html(url), url)
    with _CACHE_LOCK:
        _REPORT_CACHE[url] = (time.monotonic(), parsed)
    return parsed


def _parse_goopachi_report(html, source_url):
    """Read only explicitly numbered individual units from the older archive."""
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.title.get_text()) if soup.title else ""
    event = re.search(r"PIA厚木本館\((\d{4})年(\d{1,2})月(\d{1,2})日取材結果", title)
    if not event:
        raise ValueError("取材記事の店舗・日付がPIA厚木本館ではありません")
    event_date = date(*(int(value) for value in event.groups()))
    # This exact store check deliberately rejects the former Atsugi Annex.
    store_ok = day_ok = False
    for tr in soup.find_all("tr"):
        cells = [_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
        if len(cells) >= 2:
            if cells[0] == "店名" and cells[1] == "PIA厚木本館":
                store_ok = True
            if cells[0] == "取材日" and re.match(rf"{event_date.month}月{event_date.day}日(?:\D|$)", cells[1]):
                day_ok = True
    if not store_ok or not day_ok:
        raise ValueError("本文の店舗・取材日を照合できません")
    text = _text(soup.get_text(" ", strip=True))
    rows = {}
    if "20スロ台数" in text:
        for table in soup.find_all("table"):
            trs = table.find_all("tr")
            if not trs:
                continue
            headers = [_text(cell.get_text(" ", strip=True)) for cell in trs[0].find_all(["th", "td"])]
            expected = ("台番号", "機種名", "回転数", "差枚数")
            if not all(name in headers for name in expected):
                continue
            indexes = [headers.index(name) for name in expected]
            for tr in trs[1:]:
                cells = [_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
                if len(cells) <= max(indexes):
                    continue
                unit, name, spins, net = (cells[index] for index in indexes)
                spins, net = _number(spins), _number(net)
                if not re.fullmatch(r"\d{1,5}", unit) or not name or spins is None or spins < 0 or net is None:
                    continue
                row = {
                    "date": event_date.isoformat(), "machine_number": unit,
                    "machine_name": name, "source_machine_name": name,
                    "kind": "slot", "rate": 20, "spins": spins, "net": net,
                    "source_url": source_url, "source_name": GOOPACHI_NAME,
                    "coverage": "selected_report_units",
                }
                if unit in rows and rows[unit] != row:
                    raise ValueError(f"同一取材日に台番号{unit}の異なる数値があります")
                rows[unit] = row
    return {"rows": list(rows.values()), "date": event_date.isoformat(), "date_note": None}


def _archive_page_url(url, archive_url):
    """Accept only same-store advertised archive pagination, never other URLs."""
    parsed, base = urlparse(url), urlparse(archive_url)
    if parsed.scheme != "https" or parsed.netloc != base.netloc:
        return False
    query = parse_qs(parsed.query)
    if base.hostname == SOURCE_HOST:
        return query.get("shop_id") == ["4518"] and bool(re.fullmatch(r"/shopposts(?:/page/\d+)?/?", parsed.path))
    return query.get("s") == ["PIA厚木"] and bool(re.fullmatch(r"/(?:page/\d+/?)?", parsed.path))


def _canonical_url(url):
    parsed = urlparse(url)
    path = re.sub(r"/page/1/?$", "/", parsed.path or "/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", urlencode(sorted(parse_qsl(parsed.query))), ""))


def _discover_reports(archive_url, max_pages=50):
    """Follow all advertised pages with deduplication and a bounded page guard."""
    queue, visited, links, warnings = [_canonical_url(archive_url)], set(), [], []
    base = urlparse(archive_url)
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        try:
            soup = BeautifulSoup(_get_html(url), "html.parser")
        except requests.RequestException as error:
            if not visited:
                raise
            warnings.append(f"取材一覧ページの取得失敗: {url} ({error})")
            visited.add(url)
            continue
        visited.add(url)
        for anchor in soup.find_all("a", href=True):
            target = _canonical_url(urljoin(url, anchor["href"]))
            title = _text(anchor.get_text(" ", strip=True))
            if _archive_page_url(target, archive_url):
                if target not in visited and target not in queue:
                    queue.append(target)
                continue
            parsed = urlparse(target)
            if parsed.scheme != "https" or parsed.netloc != base.netloc:
                continue
            if base.hostname == SOURCE_HOST:
                is_report = "PIA厚木" in title and not any(part in parsed.path for part in ("shop_data", "shopposts"))
            else:
                is_report = "PIA厚木本館" in title and bool(re.fullmatch(r"/\d{4}/\d{2}/\d{2}/[^/]+/?", parsed.path))
            if is_report and target not in links:
                links.append(target)
    if queue:
        warnings.append(f"一覧が{max_pages}ページを超えたため残りのページは未取得です")
    if not links:
        raise ValueError("PIA厚木の公開取材記事一覧を確認できません")
    return {"links": links, "pages": len(visited), "warnings": warnings, "complete": not warnings}


def fetch_history(lookback_days=3650, now=None):
    """Fetch real published unit observations for PIA Atsugi only.

    ``available_days`` is a sorted list of ISO dates. Network failure on the
    archive raises a requests exception so the caller can retain saved data.
    A failed individual article yields partial results and a visible warning.
    """
    current = now or datetime.now(JST)
    today = current.date() if isinstance(current, datetime) else current
    cutoff = today - timedelta(days=max(1, min(int(lookback_days), 3650)))
    warnings, rows, notes, source_details = [], {}, [], []

    def read(url):
        try:
            return _read_report(url), None
        except (requests.RequestException, ValueError) as error:
            return None, f"公開記事の取得・検証失敗: {url} ({error})"

    # No first-page or first-16-articles truncation. Network concurrency remains
    # bounded, and every previously read report is cached for six hours.
    for source_name, archive_url in ((SOURCE_NAME, SOURCE_URL), (GOOPACHI_NAME, GOOPACHI_URL)):
        try:
            discovered = _discover_reports(archive_url)
        except (requests.RequestException, ValueError) as error:
            warnings.append(f"{source_name}の取材一覧を取得できません: {error}")
            source_details.append({"name": source_name, "url": archive_url, "status": "unavailable", "backfill_complete": False})
            continue
        warnings.extend(discovered["warnings"])
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(read, discovered["links"]))
        scanned_dates, unit_reports, empty_reports, errors = [], 0, 0, 0
        for parsed, error in results:
            if error:
                warnings.append(error)
                errors += 1
                continue
            report_date = date.fromisoformat(parsed["date"])
            scanned_dates.append(parsed["date"])
            if parsed["rows"]:
                unit_reports += 1
            else:
                empty_reports += 1
            if report_date < cutoff or report_date > today:
                continue
            if parsed["date_note"] and parsed["date_note"] not in notes:
                notes.append(parsed["date_note"])
            for row in parsed["rows"]:
                rows[(row["date"], row["kind"], row["rate"], row["machine_number"])] = row
        source_details.append({
            "name": source_name, "url": archive_url, "status": "partial",
            "archive_pages": discovered["pages"], "report_count": len(discovered["links"]),
            "unit_data_reports": unit_reports, "reports_without_unit_data": empty_reports,
            "failed_reports": errors,
            "first_scanned_date": min(scanned_dates) if scanned_dates else None,
            "last_scanned_date": max(scanned_dates) if scanned_dates else None,
            "backfill_complete": discovered["complete"] and errors == 0,
        })
    if source_details and all(source["status"] == "unavailable" for source in source_details):
        raise requests.RequestException("公開取材データの取得先に接続できません。保存済み履歴を保持してください。")

    days = sorted({row["date"] for row in rows.values()})
    message = (
        "公開取材で掲載された一部のスロット台だけを自動取得しています。"
        "全台・全営業日の履歴ではなく、好成績の台が選ばれる偏りがあります。"
        "グループ平均だけの記事は個別台データに含めません。"
        "PIA公式アプリの会員限定履歴は取得していません。"
        "公開一覧の過去ページもたどり、台ごとの数値が残る古い取材記事まで保存対象にします。"
    )
    if notes:
        message += " " + " ".join(notes)
    if warnings:
        message += f" {len(warnings)}件の記事を取得できませんでした。"
    return {
        "rows": sorted(rows.values(), key=lambda row: (row["date"], int(row["machine_number"]))),
        "source": {
            "name": "公開取材データ（スロパチ・グーパチ）",
            "url": SOURCE_URL,
            "status": "partial",
            "message": message,
            "available_days": days,
            "last_date": days[-1] if days else None,
            "first_date": days[0] if days else None,
            "coverage": "selected_report_units",
            "report_count": sum(source.get("report_count", 0) for source in source_details),
            "archive_pages": sum(source.get("archive_pages", 0) for source in source_details),
            "backfill_complete": all(source["backfill_complete"] for source in source_details),
            "backfill_scope": "公開記事一覧に掲載された取材記事の巡回完了。全営業日の履歴を意味しません",
            "backfill_requested_days": int(lookback_days),
            "retention": "取得期間の指定は保存済みデータの削除を意味しません",
            "sources": source_details,
            "warnings": warnings,
            "date_notes": notes,
        },
    }
