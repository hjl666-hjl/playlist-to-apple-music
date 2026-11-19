"""Match exported QQ Music tracks against Apple Music public SSR catalog search."""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import re
import sys
import unicodedata
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError as exc:
    raise SystemExit(
        "Missing dependency 'httpx'. Install it with: pip install httpx"
    ) from exc

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SERIALIZED_DATA_RE = re.compile(
    r'<script type="application/json" id="serialized-server-data">(.*?)</script>',
    re.DOTALL,
)
TRAILING_TAGS_RE = re.compile(r"(?:\s*[\(\[（【][^\)\]）】]*[\)\]）】])+\s*$")
SINGLE_TRAILING_PAREN_RE = re.compile(r"\s*[\(（]([^()（）]*)[\)）]\s*$")
NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)
WHITESPACE_RE = re.compile(r"\s+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|/|、|&|\bfeat\.?\b|\bft\.?\b)\s*", re.IGNORECASE)

LIVE_RE = re.compile(r"(?<![a-z])live(?![a-z])|现场|演唱会", re.IGNORECASE)
BAD_VERSION_KEYWORDS = (
    "伴奏",
    "instrumental",
    "karaoke",
    "卡拉ok",
    "piano version",
    "钢琴版",
    "八音盒",
    "music box",
    "dj版",
    "dj remix",
    "cover",
    "翻唱",
    "铃声",
    "ringtone",
    "sped up",
    "slowed",
    "纯音乐",
    "lullaby",
    "tribute",
)
EXTRA_VERSION_RE = re.compile(
    r"(?<![a-z])(?:remix|jp version|japanese|acoustic|concert|world tour)(?![a-z])",
    re.IGNORECASE,
)

# Compact Traditional -> Simplified Chinese mapping for CJK song/artist matching.
TRAD_TO_SIMP_MAP: dict[str, str] = {
    "愛": "爱", "時": "时", "夢": "梦", "說": "说", "無": "无", "長": "长",
    "對": "对", "會": "会", "過": "过", "為": "为", "來": "来", "這": "这",
    "讓": "让", "聽": "听", "聲": "声", "風": "风", "雲": "云", "葉": "叶",
    "藍": "蓝", "紅": "红", "開": "开", "關": "关", "門": "门", "間": "间",
    "見": "见", "覺": "觉", "記": "记", "憶": "忆", "戀": "恋", "傷": "伤",
    "淚": "泪", "給": "给", "錯": "错", "難": "难", "別": "别", "與": "与",
    "從": "从", "後": "后", "裡": "里", "還": "还", "點": "点", "個": "个",
    "們": "们", "麼": "么", "誰": "谁", "該": "该", "樣": "样", "歡": "欢",
    "樂": "乐", "詞": "词", "語": "语", "國": "国", "華": "华", "陳": "陈",
    "張": "张", "劉": "刘", "黃": "黄", "楊": "杨", "謝": "谢", "鄧": "邓",
    "許": "许", "孫": "孙", "盧": "卢", "鄭": "郑", "蘇": "苏", "蕭": "萧",
    "羅": "罗", "鍾": "钟", "譚": "谭", "倫": "伦", "傑": "杰", "榮": "荣",
    "學": "学", "輝": "辉", "麗": "丽", "靜": "静", "詩": "诗", "韻": "韵",
    "潔": "洁", "姍": "姗", "粵": "粤", "廣": "广", "臺": "台", "灣": "湾",
    "雙": "双", "離": "离", "飛": "飞", "鳥": "鸟", "魚": "鱼", "龍": "龙",
    "鳳": "凤", "陽": "阳", "陰": "阴", "電": "电", "話": "话", "車": "车",
    "馬": "马", "頭": "头", "臉": "脸", "體": "体", "氣": "气", "歲": "岁",
    "遠": "远", "邊": "边", "塵": "尘", "盡": "尽", "終": "终", "願": "愿",
    "動": "动", "變": "变", "換": "换", "斷": "断", "續": "续", "歸": "归",
    "尋": "寻", "問": "问", "寫": "写", "畫": "画", "讀": "读", "唸": "念",
    "講": "讲", "談": "谈", "論": "论", "評": "评", "證": "证", "認": "认",
    "識": "识", "訴": "诉", "調": "调", "請": "请", "諸": "诸", "諾": "诺",
    "謀": "谋", "謂": "谓", "謎": "谜", "謹": "谨", "譜": "谱", "譯": "译",
    "議": "议", "護": "护", "譽": "誉", "讚": "赞", "憂": "忧", "鬱": "郁",
    "溫": "温", "擁": "拥", "獨": "独", "單": "单", "純": "纯", "實": "实",
    "虛": "虚", "轉": "转", "輪": "轮", "迴": "回", "舊": "旧", "當": "当",
    "應": "应", "總": "总", "結": "结", "線": "线", "絲": "丝", "經": "经",
    "緯": "纬", "緣": "缘", "輕": "轻", "重": "重", "熱": "热", "涼": "凉",
    "親": "亲", "寶": "宝", "貝": "贝", "醒": "醒", "眼": "眼",
    "萬": "万", "千": "千", "億": "亿", "兩": "两",
}
TRAD_TRANS = str.maketrans(TRAD_TO_SIMP_MAP)

RETRY_DELAYS = (1.5, 4.0, 8.0)


def to_simplified(text: str) -> str:
    return (text or "").translate(TRAD_TRANS)


def norm_text(text: str) -> str:
    """Normalize text with Trad->Simp, NFKC, casefold, and punctuation removal."""
    simplified = to_simplified(unicodedata.normalize("NFKC", text or "")).casefold()
    return NON_WORD_RE.sub("", simplified)


def extract_base_title(title: str) -> str:
    """Strip trailing parenthetical/bracket tags like (Live), (粤语版), [Explicit]."""
    stripped = TRAILING_TAGS_RE.sub("", title or "").strip()
    return stripped if stripped else (title or "").strip()


def make_query_title(title: str) -> str:
    """Strip non-Live CJK-translation parenthetical when title starts with Latin letters."""
    cleaned = WHITESPACE_RE.sub(" ", title or "").strip()
    if not cleaned or not re.match(r"^[A-Za-z0-9]", cleaned):
        return cleaned
    m = SINGLE_TRAILING_PAREN_RE.search(cleaned)
    if m:
        inner = m.group(1)
        if CJK_RE.search(inner) and not LIVE_RE.search(inner):
            candidate = cleaned[: m.start()].strip()
            if candidate:
                return candidate
    return cleaned


def contains_bad_version_keyword(text: str) -> set[str]:
    low = to_simplified(unicodedata.normalize("NFKC", text or "")).casefold()
    hits: set[str] = set()
    for kw in BAD_VERSION_KEYWORDS:
        if kw.isascii():
            if re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", low):
                hits.add(kw)
        elif kw in low:
            hits.add(kw)
    return hits


def parse_ssr_candidates(body: str, storefront: str) -> list[dict[str, Any]]:
    """Extract unique song candidates from Apple Music SSR serialized-server-data."""
    match = SERIALIZED_DATA_RE.search(body)
    if not match:
        return []
    try:
        doc = json.loads(match.group(1))
        sections = doc["data"][0]["data"]["sections"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return []

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add_candidate(
        song_id: str,
        title: str,
        artists: list[str],
        album_url: str,
    ) -> None:
        if not song_id or song_id in seen_ids:
            return
        seen_ids.add(song_id)
        candidates.append(
            {
                "id": song_id,
                "title": html.unescape(title or "").strip(),
                "artists": [html.unescape(a).strip() for a in artists if a and a.strip()],
                "album_url": album_url or "",
                "url": f"https://music.apple.com/{storefront}/song/{song_id}",
                "rank": len(candidates),
            }
        )

    for sec in sections:
        kind = sec.get("itemKind")
        items = sec.get("items") or []
        if kind == "trackLockup":
            for it in items:
                cd = it.get("contentDescriptor") or {}
                song_id = str((cd.get("identifiers") or {}).get("storeAdamID") or "")
                artists = [
                    x.get("title", "")
                    for x in (it.get("subtitleLinks") or [])
                    if isinstance(x, dict) and x.get("title")
                ]
                add_candidate(song_id, str(it.get("title") or ""), artists, str(cd.get("url") or ""))
        elif kind == "topSearchLockup":
            for it in items:
                cd = it.get("contentDescriptor") or {}
                if cd.get("kind") != "song":
                    continue
                song_id = str((cd.get("identifiers") or {}).get("storeAdamID") or "")
                artists = [
                    x.get("title", "")
                    for x in (it.get("subtitleLinks") or [])
                    if isinstance(x, dict) and x.get("title")
                ]
                if not artists:
                    sub = str(it.get("subtitle") or "")
                    if "·" in sub:
                        raw_artist = sub.split("·")[-1].strip()
                        if raw_artist:
                            artists = [raw_artist]
                add_candidate(song_id, str(it.get("title") or ""), artists, str(cd.get("url") or ""))

    return candidates


def parse_top_artist(body: str) -> tuple[str, str] | None:
    """Return (artist_name, storeAdamID) if topSearchLockup[0] is an artist."""
    match = SERIALIZED_DATA_RE.search(body)
    if not match:
        return None
    try:
        doc = json.loads(match.group(1))
        sections = doc["data"][0]["data"]["sections"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None

    for sec in sections:
        if sec.get("itemKind") == "topSearchLockup":
            items = sec.get("items") or []
            if not items:
                return None
            first = items[0]
            cd = first.get("contentDescriptor") or {}
            if cd.get("kind") == "artist":
                aid = str((cd.get("identifiers") or {}).get("storeAdamID") or "")
                title = html.unescape(str(first.get("title") or "")).strip()
                if aid and title:
                    return title, aid
            return None
    return None


async def resolve_verified_artist_alias(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    storefront: str,
    artist: str,
    cache: dict[str, list[str]],
    locks: dict[str, asyncio.Lock],
) -> list[str]:
    """Resolve localized storefront artist name (e.g. Avril Lavigne -> 艾薇儿) via US ID verification."""
    key = norm_text(artist)
    if not key:
        return []
    lock = locks.setdefault(key, asyncio.Lock())
    async with lock:
        if key in cache:
            return cache[key]
        aliases: list[str] = []
        try:
            url = f"https://music.apple.com/{storefront}/search?{urllib.parse.urlencode({'term': artist})}"
            headers = {
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            async with sem:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                top_artist = parse_top_artist(resp.text)
                if top_artist:
                    local_name, artist_id = top_artist
                    if norm_text(local_name) != key:
                        async with sem:
                            lu = await client.get(
                                f"https://itunes.apple.com/lookup?id={artist_id}&country=us"
                            )
                        if lu.status_code == 200:
                            results = (lu.json() or {}).get("results") or []
                            us_name = str(results[0].get("artistName") or "") if results else ""
                            nu = norm_text(us_name)
                            if nu and (
                                nu == key
                                or (len(nu) >= 2 and len(key) >= 2 and (nu in key or key in nu))
                            ):
                                aliases.append(local_name)
        except Exception:
            pass
        cache[key] = aliases
        return aliases


async def search_apple_music(
    client: httpx.AsyncClient, storefront: str, term: str
) -> list[dict[str, Any]]:
    """Query Apple Music public web search with polite retry and backoff."""
    if not term.strip():
        return []
    url = f"https://music.apple.com/{storefront}/search?{urllib.parse.urlencode({'term': term})}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last_err: Exception | None = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return parse_ssr_candidates(resp.text, storefront)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_err = exc
            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
            else:
                raise RuntimeError(f"Search failed for {term!r}: {exc}") from exc
    if last_err:
        raise last_err
    return []


def split_artists(artists_str: str) -> list[str]:
    parts = [p.strip() for p in ARTIST_SPLIT_RE.split(artists_str or "") if p.strip()]
    return parts if parts else ([artists_str.strip()] if artists_str.strip() else [])


def compute_artist_score(qq_artists_raw: str, cand_artists_raw: list[str]) -> int:
    """Compute best artist similarity score in [0, 40]."""
    qq_list = split_artists(qq_artists_raw)
    if not qq_list or not cand_artists_raw:
        return 0

    cand_variants: list[str] = []
    for ca in cand_artists_raw:
        n_full = norm_text(ca)
        if n_full:
            cand_variants.append(n_full)
        for sub in split_artists(ca):
            n_sub = norm_text(sub)
            if n_sub and n_sub not in cand_variants:
                cand_variants.append(n_sub)

    if not cand_variants:
        return 0

    best = 0
    for idx, qa in enumerate(qq_list):
        nq = norm_text(qa)
        if not nq:
            continue
        for nc in cand_variants:
            if nq == nc:
                score = 40 if idx == 0 else 34
            elif len(nq) >= 2 and len(nc) >= 2 and (nq in nc or nc in nq):
                score = 34
            else:
                ratio = SequenceMatcher(None, nq, nc).ratio()
                score = round(ratio * 36) if ratio >= 0.75 else 0
            if score > best:
                best = score
    return best


def score_candidate(
    qq_title: str, qq_artists: str, cand: dict[str, Any], rank: int
) -> tuple[int, dict[str, Any]]:
    """Score a single Apple Music candidate against a QQ Music track."""
    cand_title = str(cand.get("title") or "")
    nq_title = norm_text(qq_title)
    nc_title = norm_text(cand_title)
    nq_base = norm_text(extract_base_title(qq_title))
    nc_base = norm_text(extract_base_title(cand_title))

    qq_is_live = bool(LIVE_RE.search(to_simplified(qq_title)))
    cand_is_live = bool(LIVE_RE.search(to_simplified(cand_title)))
    live_mismatch = qq_is_live != cand_is_live

    # Title score (0-55)
    if nq_title and nq_title == nc_title:
        title_score = 55
    elif nq_base and nc_base and nq_base == nc_base:
        title_score = 44 if live_mismatch else 50
    elif (
        len(nq_base) >= 2
        and len(nc_base) >= 2
        and (nq_base in nc_base or nc_base in nq_base)
    ):
        ratio = SequenceMatcher(None, nq_base, nc_base).ratio()
        title_score = max(40, min(48, round(40 + ratio * 8)))
    else:
        ratio = SequenceMatcher(None, nq_base or nq_title, nc_base or nc_title).ratio()
        title_score = round(ratio * 48)

    artist_score = compute_artist_score(qq_artists, cand.get("artists") or [])

    # Penalties
    penalty = 0
    qq_bad = contains_bad_version_keyword(qq_title)
    cand_bad = contains_bad_version_keyword(cand_title)
    if cand_bad - qq_bad:
        penalty += 35

    if live_mismatch:
        penalty += 8

    qq_extra = set(m.group(0).casefold() for m in EXTRA_VERSION_RE.finditer(qq_title))
    cand_extra = set(m.group(0).casefold() for m in EXTRA_VERSION_RE.finditer(cand_title))
    if cand_extra - qq_extra:
        penalty += 12

    rank_bonus = max(0, 5 - rank)
    raw_total = title_score + artist_score - penalty + rank_bonus

    # Cap total score when there is no artist overlap so homonymous songs never auto-match.
    if artist_score < 15:
        total = min(raw_total, 45)
    else:
        total = raw_total
    total = max(0, min(100, total))

    detail = {
        "id": cand["id"],
        "title": cand_title,
        "artists": cand.get("artists") or [],
        "url": cand["url"],
        "album_url": cand.get("album_url") or "",
        "score": total,
        "title_score": title_score,
        "artist_score": artist_score,
        "penalty": penalty,
        "rank": rank,
    }
    return total, detail


def classify_score(score: int) -> str:
    if score >= 78:
        return "matched"
    if score >= 55:
        return "uncertain"
    return "unmatched"


async def match_single_track(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    storefront: str,
    idx: int,
    row: dict[str, str],
    alias_cache: dict[str, list[str]],
    alias_locks: dict[str, asyncio.Lock],
) -> dict[str, Any]:
    """Run fallback searches and score candidates for a single track."""
    qq_title = (row.get("Track name") or "").strip()
    qq_artists = (row.get("Artist name") or "").strip()
    qq_album = (row.get("Album") or "").strip()

    first_artist = split_artists(qq_artists)[0] if qq_artists else ""
    query_title = make_query_title(qq_title)
    base_title = extract_base_title(qq_title)

    q1 = f"{query_title} {first_artist}".strip()
    merged_by_id: dict[str, dict[str, Any]] = {}
    used_queries: list[str] = []
    extra_artists: list[str] = []
    error_msg: str | None = None

    async def run_query(q: str) -> None:
        nonlocal error_msg
        if not q or q in used_queries:
            return
        used_queries.append(q)
        try:
            async with sem:
                cands = await search_apple_music(client, storefront, q)
            for c in cands:
                cid = c["id"]
                if cid not in merged_by_id:
                    merged_by_id[cid] = c
        except Exception as exc:
            error_msg = str(exc)

    def evaluate() -> list[dict[str, Any]]:
        combined_artists = ", ".join([a for a in [qq_artists, *extra_artists] if a])
        scored = [
            score_candidate(qq_title, combined_artists, c, c["rank"])[1]
            for c in merged_by_id.values()
        ]
        scored.sort(key=lambda x: (-x["score"], x["rank"]))
        return scored

    await run_query(q1)
    scored = evaluate()
    best_score = scored[0]["score"] if scored else 0

    if best_score < 78 and base_title and norm_text(base_title) != norm_text(query_title):
        q2 = f"{base_title} {first_artist}".strip()
        await run_query(q2)
        scored = evaluate()
        best_score = scored[0]["score"] if scored else 0

    if best_score < 78 and len(norm_text(base_title)) >= 2 and first_artist:
        await run_query(base_title)
        scored = evaluate()
        best_score = scored[0]["score"] if scored else 0

    # If a candidate matches the title well but failed artist overlap, check localized artist alias.
    if (
        best_score < 78
        and first_artist
        and any(c["title_score"] >= 44 and c["artist_score"] < 15 for c in scored)
    ):
        aliases = await resolve_verified_artist_alias(
            client, sem, storefront, first_artist, alias_cache, alias_locks
        )
        if aliases:
            extra_artists.extend(aliases)
            scored = evaluate()
            best_score = scored[0]["score"] if scored else 0
            if best_score < 78:
                await run_query(f"{query_title} {aliases[0]}".strip())
                scored = evaluate()
                best_score = scored[0]["score"] if scored else 0

    best = scored[0] if scored else None
    status = classify_score(best_score) if best else "unmatched"

    return {
        "index": idx,
        "qq_title": qq_title,
        "qq_artists": qq_artists,
        "qq_album": qq_album,
        "status": status,
        "best_score": best_score,
        "best": best,
        "top_candidates": scored[:3],
        "queries": used_queries,
        "error": error_msg if not scored else None,
    }


def build_summary(results: list[dict[str, Any]], storefront: str) -> dict[str, Any]:
    counts = {"matched": 0, "uncertain": 0, "unmatched": 0}
    for r in results:
        st = r.get("status", "unmatched")
        counts[st] = counts.get(st, 0) + 1
    return {
        "storefront": storefront,
        "total": len(results),
        "matched": counts["matched"],
        "uncertain": counts["uncertain"],
        "unmatched": counts["unmatched"],
    }


def write_matched_outputs(
    out_dir: Path,
    storefront: str,
    ordered_results: list[dict[str, Any]],
    *,
    final: bool,
) -> None:
    """Persist matched_cn.json checkpoint and, on completion, URLs and HTML report."""
    summary = build_summary(ordered_results, storefront)
    payload = {"summary": summary, "results": ordered_results}
    (out_dir / "matched_cn.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not final:
        return

    seen_urls: set[str] = set()
    urls: list[str] = []
    for r in ordered_results:
        if r.get("status") == "matched" and r.get("best"):
            u = r["best"]["url"]
            if u not in seen_urls:
                seen_urls.add(u)
                urls.append(u)
    (out_dir / "matched_urls.txt").write_text(
        "\n".join(urls) + ("\n" if urls else ""), encoding="utf-8"
    )
    (out_dir / "match_report.html").write_text(
        render_html_report(summary, ordered_results), encoding="utf-8"
    )


def render_html_report(
    summary: dict[str, Any], results: list[dict[str, Any]]
) -> str:
    """Build a self-contained HTML review report with filter tabs and search."""
    rows_html: list[str] = []
    for r in results:
        st = r["status"]
        best = r.get("best")
        best_cell = "<span class='muted'>—</span>"
        if best:
            b_title = html.escape(best["title"])
            b_art = html.escape(", ".join(best.get("artists") or []))
            b_url = html.escape(best["url"])
            best_cell = (
                f"<a href='{b_url}' target='_blank' rel='noopener'>{b_title}</a>"
                f"<div class='sub'>{b_art}</div>"
            )

        cand_items: list[str] = []
        if st in ("uncertain", "unmatched") and r.get("top_candidates"):
            for c in r["top_candidates"]:
                ct = html.escape(c["title"])
                ca = html.escape(", ".join(c.get("artists") or []))
                cu = html.escape(c["url"])
                cand_items.append(
                    f"<li><span class='score-pill'>{c['score']}</span> "
                    f"<a href='{cu}' target='_blank' rel='noopener'>{ct}</a> — {ca}</li>"
                )
        cands_cell = (
            f"<ul class='cands'>{''.join(cand_items)}</ul>"
            if cand_items
            else "<span class='muted'>—</span>"
        )

        search_blob = html.escape(
            f"{r['qq_title']} {r['qq_artists']} {r['qq_album']} "
            f"{best['title'] if best else ''} {' '.join(best['artists']) if best else ''}".lower()
        )
        rows_html.append(
            f"<tr data-status='{st}' data-search=\"{search_blob}\">"
            f"<td>{r['index']}</td>"
            f"<td><span class='badge badge-{st}'>{st}</span></td>"
            f"<td><b>{html.escape(r['qq_title'])}</b>"
            f"<div class='sub'>{html.escape(r['qq_artists'])} · {html.escape(r['qq_album'])}</div></td>"
            f"<td class='num'>{r['best_score']}</td>"
            f"<td>{best_cell}</td>"
            f"<td>{cands_cell}</td>"
            f"</tr>"
        )

    total = summary["total"] or 1
    pct = round(summary["matched"] * 100 / total, 1)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>Apple Music ({html.escape(summary['storefront'])}) Match Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2328; background: #f6f8fa; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 10px; padding: 18px 22px; margin-bottom: 16px; }}
  h1 {{ margin: 0 0 10px; font-size: 20px; }}
  .stats {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 14px; }}
  .stat {{ background: #f6f8fa; padding: 8px 14px; border-radius: 8px; border: 1px solid #e5e7eb; }}
  .controls {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }}
  .tab {{ border: 1px solid #d0d7de; background: #fff; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
  .tab.active {{ background: #0969da; color: #fff; border-color: #0969da; }}
  input[type=search] {{ padding: 6px 10px; border: 1px solid #d0d7de; border-radius: 6px; min-width: 260px; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d0d7de; border-radius: 8px; overflow: hidden; font-size: 13px; }}
  th, td {{ padding: 9px 10px; border-bottom: 1px solid #eaeef2; text-align: left; vertical-align: top; }}
  th {{ background: #f6f8fa; position: sticky; top: 0; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .sub {{ color: #656d76; font-size: 12px; margin-top: 2px; }}
  .muted {{ color: #8c959f; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 600; }}
  .badge-matched {{ background: #dafbe1; color: #1a7f37; }}
  .badge-uncertain {{ background: #fff8c5; color: #9a6700; }}
  .badge-unmatched {{ background: #ffebe9; color: #cf222e; }}
  .cands {{ margin: 0; padding-left: 16px; }}
  .cands li {{ margin-bottom: 3px; }}
  .score-pill {{ display: inline-block; min-width: 24px; text-align: center; background: #eaeef2; border-radius: 4px; padding: 0 4px; font-size: 11px; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<div class="card">
  <h1>Apple Music ({html.escape(summary['storefront'])}) 曲库匹配报告</h1>
  <div class="stats">
    <div class="stat">总曲目: <b>{summary['total']}</b></div>
    <div class="stat">已匹配 (≥78): <b>{summary['matched']}</b> ({pct}%)</div>
    <div class="stat">待确认 (55–77): <b>{summary['uncertain']}</b></div>
    <div class="stat">未匹配 (&lt;55): <b>{summary['unmatched']}</b></div>
  </div>
</div>
<div class="controls">
  <button class="tab active" data-filter="all">全部 ({summary['total']})</button>
  <button class="tab" data-filter="matched">已匹配 ({summary['matched']})</button>
  <button class="tab" data-filter="uncertain">待确认 ({summary['uncertain']})</button>
  <button class="tab" data-filter="unmatched">未匹配 ({summary['unmatched']})</button>
  <input id="q" type="search" placeholder="搜索歌名 / 歌手 / 专辑..." />
</div>
<table>
  <thead>
    <tr><th>#</th><th>状态</th><th>QQ 音乐曲目</th><th class="num">得分</th><th>Apple Music 最佳匹配</th><th>候选项 (待确认/未匹配)</th></tr>
  </thead>
  <tbody id="rows">
    {''.join(rows_html)}
  </tbody>
</table>
<script>
(function() {{
  let curFilter = 'all';
  const tabs = document.querySelectorAll('.tab');
  const q = document.getElementById('q');
  const rows = Array.from(document.querySelectorAll('#rows tr'));
  function apply() {{
    const kw = q.value.trim().toLowerCase();
    for (const tr of rows) {{
      const okSt = curFilter === 'all' || tr.dataset.status === curFilter;
      const okKw = !kw || tr.dataset.search.includes(kw);
      tr.style.display = (okSt && okKw) ? '' : 'none';
    }}
  }}
  tabs.forEach(btn => btn.addEventListener('click', () => {{
    tabs.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    curFilter = btn.dataset.filter;
    apply();
  }}));
  q.addEventListener('input', apply);
}})();
</script>
</body>
</html>
"""


def resolve_input_csv(input_path: Path) -> Path:
    if input_path.is_dir():
        candidate = input_path / "tracks_all.csv"
        if candidate.is_file():
            return candidate
    if input_path.is_file():
        return input_path
    raise FileNotFoundError(f"Input CSV not found: {input_path}")


async def run_matching(
    csv_path: Path,
    *,
    storefront: str,
    concurrency: int,
    limit: int,
    resume: bool,
) -> Path:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        all_rows = list(csv.DictReader(f))

    target_rows = all_rows[:limit] if limit > 0 else all_rows
    out_dir = csv_path.parent
    ckpt_path = out_dir / "matched_cn.json"

    existing_by_index: dict[int, dict[str, Any]] = {}
    if resume and ckpt_path.is_file():
        try:
            old = json.loads(ckpt_path.read_text(encoding="utf-8"))
            for item in old.get("results") or []:
                idx = int(item["index"])
                if 1 <= idx <= len(target_rows):
                    row = target_rows[idx - 1]
                    if (
                        item.get("qq_title") == (row.get("Track name") or "").strip()
                        and item.get("qq_artists") == (row.get("Artist name") or "").strip()
                    ):
                        existing_by_index[idx] = item
        except Exception:
            existing_by_index = {}

    pending: list[tuple[int, dict[str, str]]] = [
        (i, row)
        for i, row in enumerate(target_rows, start=1)
        if i not in existing_by_index
    ]

    total_target = len(target_rows)
    if existing_by_index:
        print(
            f"Resuming: {len(existing_by_index)}/{total_target} cached, {len(pending)} remaining.",
            flush=True,
        )

    results_by_index = dict(existing_by_index)
    sem = asyncio.Semaphore(max(1, concurrency))
    alias_cache: dict[str, list[str]] = {}
    alias_locks: dict[str, asyncio.Lock] = {}

    if pending:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            tasks = [
                asyncio.create_task(
                    match_single_track(
                        client, sem, storefront, idx, row, alias_cache, alias_locks
                    )
                )
                for idx, row in pending
            ]
            completed_now = 0
            for coro in asyncio.as_completed(tasks):
                res = await coro
                results_by_index[res["index"]] = res
                completed_now += 1
                total_done = len(results_by_index)
                if completed_now % 25 == 0 or total_done == total_target:
                    ordered_partial = [
                        results_by_index[k] for k in sorted(results_by_index)
                    ]
                    write_matched_outputs(
                        out_dir, storefront, ordered_partial, final=False
                    )
                    summary = build_summary(ordered_partial, storefront)
                    print(
                        f"[{total_done}/{total_target}] "
                        f"matched={summary['matched']} "
                        f"uncertain={summary['uncertain']} "
                        f"unmatched={summary['unmatched']}",
                        flush=True,
                    )

    ordered_final = [results_by_index[k] for k in sorted(results_by_index)]
    write_matched_outputs(out_dir, storefront, ordered_final, final=True)
    summary = build_summary(ordered_final, storefront)
    print(
        f"Done: total={summary['total']}, matched={summary['matched']}, "
        f"uncertain={summary['uncertain']}, unmatched={summary['unmatched']}",
        flush=True,
    )
    print(f"JSON  : {ckpt_path}")
    print(f"URLs  : {out_dir / 'matched_urls.txt'}")
    print(f"Report: {out_dir / 'match_report.html'}")
    return ckpt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Match exported QQ Music CSV tracks against Apple Music SSR catalog search."
    )
    parser.add_argument(
        "input_csv",
        type=Path,
        help="Path to tracks_all.csv or its parent playlist output directory",
    )
    parser.add_argument(
        "--storefront",
        default="cn",
        help="Apple Music storefront code (default: cn)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent search requests (default: 5)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N tracks (0 = all)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any existing matched_cn.json and re-match from scratch",
    )
    args = parser.parse_args(argv)

    csv_path = resolve_input_csv(args.input_csv)
    asyncio.run(
        run_matching(
            csv_path,
            storefront=args.storefront,
            concurrency=args.concurrency,
            limit=args.limit,
            resume=not args.no_resume,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
