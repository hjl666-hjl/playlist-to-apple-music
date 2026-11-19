"""Export a public QQ Music playlist to cleaned CSV/TXT/JSON batches for Apple Music migration."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
QQ_PLAYLIST_API = (
    "https://i.y.qq.com/qzone-music/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
    "?type=1&json=1&utf8=1&onlysong=0&nosign=1&disstid={disstid}&format=json"
)
TRAILING_PARENS_RE = re.compile(r"\s*[\(（][^()（）]*[\)）]\s*$")
WHITESPACE_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://[^\s<>\"'\]）)]+")


def extract_disstid_from_text(text: str) -> str | None:
    """Try extracting a numeric QQ Music playlist ID from a URL or query string."""
    for pattern in (
        r"/playlist/(\d+)",
        r"[?&](?:id|disstid)=(\d+)",
        r'"disstid"\s*:\s*"?(\d+)"?',
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def resolve_disstid(source: str, timeout: int = 15) -> str:
    """Resolve raw disstid, QQ Music playlist URL, share text, or short URL into disstid."""
    stripped = source.strip()
    if stripped.isdigit():
        return stripped

    direct = extract_disstid_from_text(stripped)
    if direct:
        return direct

    url_match = URL_RE.search(stripped)
    if not url_match:
        raise ValueError(f"Could not find a playlist ID or URL in input: {source!r}")

    url = url_match.group(0)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        resolved = extract_disstid_from_text(final_url)
        if resolved:
            return resolved
        body = resp.read(200_000).decode("utf-8", errors="replace")
        resolved = extract_disstid_from_text(body)
        if resolved:
            return resolved

    raise ValueError(f"Could not extract QQ Music playlist ID from resolved URL: {url}")


def fetch_playlist(disstid: str, timeout: int = 20) -> dict[str, Any]:
    """Fetch raw playlist payload (`cdlist[0]`) from QQ Music public endpoint."""
    api_url = QQ_PLAYLIST_API.format(disstid=urllib.parse.quote(disstid, safe=""))
    req = urllib.request.Request(
        api_url,
        headers={"Referer": "https://y.qq.com/", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))

    if payload.get("code") != 0:
        raise RuntimeError(
            f"QQ Music API returned code={payload.get('code')!r} for disstid={disstid}"
        )

    cdlist = payload.get("cdlist") or []
    if not cdlist:
        raise RuntimeError(f"QQ Music API returned empty cdlist for disstid={disstid}")

    return cdlist[0]


def clean_title_or_album(text: str) -> str:
    """Decode HTML entities, normalize full-width parens, and collapse whitespace."""
    unescaped = html.unescape(text or "")
    normalized = unescaped.replace("（", "(").replace("）", ")")
    return WHITESPACE_RE.sub(" ", normalized).strip()


def clean_singer_name(name: str) -> str:
    """Decode HTML entities and strip trailing parenthetical aliases unless it empties the name."""
    unescaped = WHITESPACE_RE.sub(" ", html.unescape(name or "")).strip()
    if not unescaped:
        return ""
    stripped = TRAILING_PARENS_RE.sub("", unescaped).strip()
    return stripped if stripped else unescaped


def clean_tracks(
    songlist: list[dict[str, Any]], *, deduplicate: bool = True
) -> tuple[list[dict[str, str]], int]:
    """Normalize track metadata and optionally remove exact (title, artist) duplicates."""
    cleaned: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    dup_count = 0

    for song in songlist:
        title = clean_title_or_album(str(song.get("songname") or ""))
        album = clean_title_or_album(str(song.get("albumname") or ""))
        singers = [
            clean_singer_name(str(s.get("name") or ""))
            for s in (song.get("singer") or [])
            if isinstance(s, dict)
        ]
        artists = ", ".join(s for s in singers if s)

        if deduplicate:
            key = (title.casefold(), artists.casefold())
            if key in seen:
                dup_count += 1
                continue
            seen.add(key)

        cleaned.append(
            {
                "Track name": title,
                "Artist name": artists,
                "Album": album,
            }
        )

    return cleaned, dup_count


def sanitize_filename(name: str) -> str:
    """Sanitize a playlist name for Windows/POSIX directory names."""
    cleaned = html.unescape(name or "").strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip(" .")
    return cleaned[:80] or "playlist"


def write_csv_tracks(path: Path, tracks: list[dict[str, str]]) -> None:
    """Write tracks as UTF-8 without BOM so web importers match header names cleanly."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Track name", "Artist name", "Album"])
        writer.writeheader()
        writer.writerows(tracks)


def write_txt_tracks(path: Path, tracks: list[dict[str, str]]) -> None:
    """Write tracks as UTF-8 `Track name - Artist name` lines."""
    lines = [
        f"{t['Track name']} - {t['Artist name']}" if t["Artist name"] else t["Track name"]
        for t in tracks
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def export_playlist(
    source: str,
    out_dir: Path,
    *,
    batch_size: int = 500,
    deduplicate: bool = True,
) -> Path:
    """Fetch, clean, and export a QQ Music playlist into raw JSON, CSV, and TXT files."""
    if batch_size <= 0:
        raise ValueError("--batch-size must be a positive integer")

    disstid = resolve_disstid(source)
    cd = fetch_playlist(disstid)

    playlist_title = html.unescape(str(cd.get("dissname") or f"playlist_{disstid}")).strip()
    creator = html.unescape(str(cd.get("nickname") or "")).strip()
    songlist = cd.get("songlist") or []
    reported_total = int(cd.get("songnum") or len(songlist))
    fetched_total = len(songlist)

    if fetched_total < reported_total:
        print(
            f"WARNING: fetched {fetched_total} tracks, fewer than reported songnum={reported_total}.",
            file=sys.stderr,
        )

    tracks, dup_count = clean_tracks(songlist, deduplicate=deduplicate)

    target_dir = out_dir / f"{disstid}_{sanitize_filename(playlist_title)}"
    target_dir.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []

    raw_path = target_dir / "raw.json"
    raw_path.write_text(json.dumps(cd, ensure_ascii=False, indent=2), encoding="utf-8")
    generated.append(raw_path)

    all_csv = target_dir / "tracks_all.csv"
    write_csv_tracks(all_csv, tracks)
    generated.append(all_csv)

    all_txt = target_dir / "tracks_all.txt"
    write_txt_tracks(all_txt, tracks)
    generated.append(all_txt)

    if len(tracks) > batch_size:
        for idx, start_zero in enumerate(range(0, len(tracks), batch_size), start=1):
            chunk = tracks[start_zero : start_zero + batch_size]
            start_num = start_zero + 1
            end_num = start_zero + len(chunk)
            stem = f"tracks_part{idx}_{start_num}-{end_num}"

            part_csv = target_dir / f"{stem}.csv"
            write_csv_tracks(part_csv, chunk)
            generated.append(part_csv)

            part_txt = target_dir / f"{stem}.txt"
            write_txt_tracks(part_txt, chunk)
            generated.append(part_txt)

    print(f"Playlist   : {playlist_title} (disstid={disstid})")
    print(f"Creator    : {creator or '-'}")
    print(f"Raw count  : reported={reported_total}, fetched={fetched_total}")
    print(f"Duplicates : {dup_count} removed ({'dedup on' if deduplicate else 'dedup off'})")
    print(f"Final count: {len(tracks)}")
    print(f"Output dir : {target_dir}")
    print("Generated files:")
    for path in generated:
        print(f"  - {path}")

    return target_dir


def main(argv: list[str] | None = None) -> int:
    default_out = Path(__file__).resolve().parent / "output"
    parser = argparse.ArgumentParser(
        description="Export a public QQ Music playlist to cleaned CSV/TXT/JSON batches."
    )
    parser.add_argument(
        "source",
        help="QQ Music playlist ID, URL, share text, or short link",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=default_out,
        help=f"Output root directory (default: {default_out})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Split into batch files when track count exceeds this size (default: 500)",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Keep duplicate (Track name, Artist name) entries instead of deduplicating",
    )
    args = parser.parse_args(argv)

    export_playlist(
        args.source,
        args.out_dir,
        batch_size=args.batch_size,
        deduplicate=not args.no_dedup,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
