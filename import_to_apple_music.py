"""Import matched Apple Music songs as Favorites (喜爱歌曲) or into a playlist via Playwright."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import (
        Page,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ImportError as exc:
    raise SystemExit(
        "Missing dependency 'playwright'. Install it with: pip install playwright"
    ) from exc

LOGGED_IN_MARKERS = (
    "资料库",
    "最近添加",
    "所有播放列表",
    "喜爱歌曲",
    "Library",
    "Recently Added",
    "All Playlists",
    "Favorite Songs",
)
MENU_ITEM_SELECTOR = (
    '[role="menuitem"], .contextual-menu-item, [role="menu"] button, ul li button'
)
ALREADY_FAVORITED_RE = re.compile(
    r"^\s*(?:取消个人收藏|取消收藏|移除个人收藏|取消喜爱|已收藏|已喜爱|"
    r"Undo Favorite|Favorited|Unfavorite|Remove from Favorites)\s*$",
    re.IGNORECASE,
)
FAVORITE_ACTION_RE = re.compile(
    r"^\s*(?:个人收藏|喜爱|Favorite)\s*$", re.IGNORECASE
)


def resolve_matched_json(path: Path | None) -> Path:
    if path is None:
        output_root = Path(__file__).resolve().parent / "output"
        candidates = sorted(
            output_root.glob("*/matched_cn.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"No matched_cn.json found under {output_root}. Pass path explicitly."
            )
        return candidates[0]
    if path.is_dir():
        candidate = path / "matched_cn.json"
        if candidate.is_file():
            return candidate
    if path.is_file():
        return path
    raise FileNotFoundError(f"matched_cn.json not found: {path}")


def default_playlist_name(matched_path: Path) -> str:
    clean_folder = re.sub(r"^\d+_", "", matched_path.parent.name).strip()
    return f"QQ音乐-{clean_folder or matched_path.parent.name}"


def load_progress(progress_path: Path, mode: str, target_label: str) -> dict[str, Any]:
    if progress_path.is_file():
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("mode", mode)
                data.setdefault("target", target_label)
                data.setdefault("added_ids", [])
                data.setdefault("failed", [])
                return data
        except Exception:
            pass
    return {
        "mode": mode,
        "target": target_label,
        "added_ids": [],
        "failed": [],
    }


def save_progress(progress_path: Path, progress: dict[str, Any]) -> None:
    progress_path.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def launch_persistent_browser(p: Any, profile_dir: Path, browser_choice: str):
    """Launch visible persistent Chromium-family browser context."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    common_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "locale": "zh-CN",
        "viewport": {"width": 1280, "height": 860},
    }
    if browser_choice == "chromium":
        return p.chromium.launch_persistent_context(**common_kwargs)

    channels = ["msedge", "chrome"] if browser_choice == "msedge" else ["chrome"]
    last_err: Exception | None = None
    for ch in channels:
        try:
            return p.chromium.launch_persistent_context(channel=ch, **common_kwargs)
        except Exception as exc:
            last_err = exc
            print(f"Could not launch channel={ch!r}: {exc}", file=sys.stderr)
    raise RuntimeError(f"Failed to launch browser ({browser_choice}): {last_err}")


def is_logged_in(page: Page) -> bool:
    """Detect whether music.apple.com has an active signed-in user session."""
    try:
        url = page.url or ""
        if not url.startswith("https://music.apple.com/"):
            return False
        if "idmsa.apple.com" in url or "appleid.apple.com" in url:
            return False

        sign_in_btn = page.locator('[data-testid="sign-in-button"]').first
        if sign_in_btn.count() > 0 and sign_in_btn.is_visible():
            return False

        account_btn = page.locator(
            '[data-testid="account-button"], '
            'button[aria-label*="账户"], '
            'button[aria-label*="Account"]'
        ).first
        if account_btn.count() > 0 and account_btn.is_visible():
            return True

        nav = page.locator("nav, [role='navigation']").first
        if nav.count() > 0 and nav.is_visible():
            nav_text = nav.inner_text(timeout=1500)
            if any(marker in nav_text for marker in LOGGED_IN_MARKERS):
                return True
    except Exception:
        return False
    return False


def ensure_logged_in(page: Page, storefront: str, login_timeout: int) -> None:
    """Open Apple Music home and wait without blocking stdin for manual login if needed."""
    page.goto(
        f"https://music.apple.com/{storefront}/home",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    try:
        page.bring_to_front()
    except Exception:
        pass
    page.wait_for_timeout(3000)

    if is_logged_in(page):
        print("已检测到 Apple Music 登录状态，开始执行...", flush=True)
        return

    print(
        "WAITING_FOR_LOGIN: 请在弹出的浏览器窗口手动登录你的 Apple ID"
        "（登录完成后脚本将自动继续，无需按回车）...",
        flush=True,
    )
    deadline = time.monotonic() + max(10, login_timeout)
    consecutive_ok = 0
    while time.monotonic() < deadline:
        if is_logged_in(page):
            consecutive_ok += 1
            if consecutive_ok >= 2:
                print("已检测到登录成功，继续执行...", flush=True)
                return
        else:
            consecutive_ok = 0
        page.wait_for_timeout(2000)

    raise TimeoutError(
        f"Timed out after {login_timeout}s waiting for Apple ID login on music.apple.com"
    )


def find_more_button(page: Page):
    """Locate the primary song header 'More' / '更多' button within ~8s."""
    primary = page.locator(
        'main button.contextual-menu__trigger[aria-label="更多"], '
        'main button[aria-label="More"]'
    ).first
    try:
        primary.wait_for(state="visible", timeout=5000)
        return primary
    except PlaywrightTimeoutError:
        pass

    fallback = page.locator(
        'button.contextual-menu__trigger[aria-label="更多"], '
        'button[aria-label="More"]'
    ).first
    try:
        fallback.wait_for(state="visible", timeout=3000)
        return fallback
    except PlaywrightTimeoutError as exc:
        raise RuntimeError("Could not find song 'More / 更多' menu button on page") from exc


def collect_visible_diagnostics(page: Page) -> dict[str, Any]:
    """Gather visible header buttons, menu labels, dialog text, and URL for debugging."""
    info: dict[str, Any] = {
        "url": page.url,
        "header_buttons": [],
        "menu_items": [],
        "dialogs": [],
    }
    try:
        info["header_buttons"] = page.eval_on_selector_all(
            "main button",
            """els => els.map(e => {
                const r = e.getBoundingClientRect();
                return {
                    text: (e.innerText || '').trim().slice(0, 40),
                    aria: e.getAttribute('aria-label'),
                    testid: e.getAttribute('data-testid'),
                    y: Math.round(r.y)
                };
            }).filter(x => x.y >= 0 && x.y < 600 && (x.aria || x.testid || x.text)).slice(0, 25)""",
        )
    except Exception:
        pass
    try:
        items = page.eval_on_selector_all(
            MENU_ITEM_SELECTOR + ", ul[role='menu'] li",
            "els => els.map(e => (e.innerText || e.textContent || '').trim()).filter(Boolean)",
        )
        seen: list[str] = []
        for x in items:
            if x not in seen:
                seen.append(x)
        info["menu_items"] = seen[:30]
    except Exception:
        pass
    try:
        dialogs = page.eval_on_selector_all(
            'dialog, [role="dialog"], .modal',
            "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)",
        )
        info["dialogs"] = dialogs[:5]
    except Exception:
        pass
    return info


def capture_error_screenshot(page: Page, debug_dir: Path) -> Path | None:
    try:
        shot_path = debug_dir / "debug_last_error.png"
        page.screenshot(path=str(shot_path))
        return shot_path
    except Exception:
        return None


def find_visible_header_button(page: Page, selector: str):
    """Return the first visible button matching selector in the top song header (y < 600)."""
    loc = page.locator(selector)
    count = min(loc.count(), 6)
    for idx in range(count):
        btn = loc.nth(idx)
        try:
            if btn.is_visible():
                box = btn.bounding_box()
                if box and box["y"] < 600:
                    return btn
        except Exception:
            continue
    return None


def is_favorited_btn_state(aria: str, pressed: str, cls: str) -> bool:
    """Return True if button attributes indicate the song is already favorited."""
    if pressed.strip().lower() == "true":
        return True
    aria_clean = aria.strip()
    if ALREADY_FAVORITED_RE.match(aria_clean):
        return True
    aria_low = aria_clean.lower()
    if any(tok in aria_low for tok in ("取消", "移除", "undo", "remove")):
        return True
    cls_low = cls.lower()
    if "favorited" in cls_low:
        return True
    cls_tokens = set(cls_low.split())
    return any(
        tok in ("is-favorited", "active", "is-active", "selected")
        or tok.endswith(("--favorited", "--active"))
        for tok in cls_tokens
    )


def favorite_song(
    page: Page,
    storefront: str,
    song_id: str,
    *,
    debug_dir: Path,
) -> str:
    """Navigate to song page and mark it as Favorite (个人收藏). Returns status string."""
    url = f"https://music.apple.com/{storefront}/song/{song_id}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        fav_btn = page.locator('main button[data-testid="favorite-button"]').first
        has_direct_btn = False
        try:
            fav_btn.wait_for(state="visible", timeout=8000)
            has_direct_btn = True
        except PlaywrightTimeoutError:
            has_direct_btn = False

        if has_direct_btn:
            aria_before = fav_btn.get_attribute("aria-label") or ""
            pressed_before = fav_btn.get_attribute("aria-pressed") or ""
            cls_before = fav_btn.get_attribute("class") or ""
            html_before = fav_btn.inner_html()

            if is_favorited_btn_state(aria_before, pressed_before, cls_before):
                return "already_favorited"

            fav_btn.click()
            for _ in range(10):
                page.wait_for_timeout(250)
                aria_after = fav_btn.get_attribute("aria-label") or ""
                pressed_after = fav_btn.get_attribute("aria-pressed") or ""
                cls_after = fav_btn.get_attribute("class") or ""
                html_after = fav_btn.inner_html()
                if (
                    aria_after != aria_before
                    or pressed_after.strip().lower() == "true"
                    or cls_after != cls_before
                    or html_after != html_before
                ):
                    break
            page.wait_for_timeout(300)
            return "favorited"

        more_btn = find_more_button(page)
        more_btn.click()
        menu_items = page.locator(MENU_ITEM_SELECTOR)
        menu_items.first.wait_for(state="visible", timeout=4000)

        already_item = menu_items.filter(has_text=ALREADY_FAVORITED_RE).first
        if already_item.count() > 0 and already_item.is_visible():
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            return "already_favorited"

        fav_item = menu_items.filter(has_text=FAVORITE_ACTION_RE).first
        fav_item.wait_for(state="visible", timeout=3000)
        fav_item.click()
        page.wait_for_timeout(800)
        return "favorited"
    except Exception as exc:
        shot = capture_error_screenshot(page, debug_dir)
        diag = collect_visible_diagnostics(page)
        raise RuntimeError(
            f"{exc} | diag={json.dumps(diag, ensure_ascii=False)} | screenshot={shot}"
        ) from exc


def handle_duplicate_dialog(page: Page) -> None:
    """Click '跳过' / 'Skip' if Apple Music asks about a duplicate track."""
    for skip_label in ("跳过", "Skip"):
        btn = page.locator(
            f'dialog button:has-text("{skip_label}"), '
            f'[role="dialog"] button:has-text("{skip_label}")'
        ).first
        try:
            if btn.count() > 0 and btn.is_visible():
                btn.click()
                page.wait_for_timeout(400)
                return
        except Exception:
            pass


def create_playlist_from_modal(page: Page, playlist_name: str) -> None:
    """Fill the New Playlist modal input and submit it."""
    dialog_input = page.locator(
        'dialog input:not([type="hidden"]):not([type="checkbox"]):not([type="search"]), '
        '[role="dialog"] input:not([type="hidden"]):not([type="checkbox"]):not([type="search"]), '
        '.modal input:not([type="hidden"]):not([type="checkbox"]):not([type="search"])'
    ).first
    try:
        dialog_input.wait_for(state="visible", timeout=5000)
        modal_input = dialog_input
    except PlaywrightTimeoutError:
        modal_input = page.locator(
            'input[placeholder*="播放列表"], input[placeholder*="Playlist"], '
            'input[type="text"]:not([type="search"])'
        ).last
        modal_input.wait_for(state="visible", timeout=3000)

    modal_input.fill(playlist_name)

    confirm_btn = page.locator(
        'dialog button, [role="dialog"] button, .modal button'
    ).filter(
        has_text=re.compile(
            r"^\s*(?:创建|Create|完成|Done|好|OK|确认|保存|Save)\s*$", re.IGNORECASE
        )
    ).first
    if confirm_btn.count() > 0 and confirm_btn.is_visible():
        confirm_btn.click()
    else:
        submit_btn = page.locator(
            'dialog button[type="submit"], '
            '[role="dialog"] button[type="submit"], '
            '.modal button[type="submit"]'
        ).first
        if submit_btn.count() > 0 and submit_btn.is_visible():
            submit_btn.click()
        else:
            modal_input.press("Enter")
    page.wait_for_timeout(1500)


def add_song_to_playlist(
    page: Page,
    storefront: str,
    song_id: str,
    playlist_name: str,
    *,
    allow_create: bool,
    debug_dir: Path,
) -> bool:
    """Navigate to a song page and add it to the target playlist; returns True if created."""
    url = f"https://music.apple.com/{storefront}/song/{song_id}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)

        more_btn = find_more_button(page)
        more_btn.click()

        menu_items = page.locator(MENU_ITEM_SELECTOR)
        add_item = menu_items.filter(
            has_text=re.compile(r"添加到歌单|添加到播放列表|Add to Playlist", re.IGNORECASE)
        ).first
        add_item.wait_for(state="visible", timeout=5000)

        target_pattern = re.compile(rf"^\s*{re.escape(playlist_name)}\s*$")
        target_item = menu_items.filter(has_text=target_pattern).first
        new_item = menu_items.filter(
            has_text=re.compile(r"新歌单|新建播放列表|New Playlist", re.IGNORECASE)
        ).first

        add_item.hover()
        try:
            target_item.wait_for(state="visible", timeout=1000)
        except PlaywrightTimeoutError:
            try:
                new_item.wait_for(state="visible", timeout=400)
            except PlaywrightTimeoutError:
                add_item.click()
                page.wait_for_timeout(600)

        if target_item.count() > 0 and target_item.is_visible():
            target_item.click()
            page.wait_for_timeout(800)
            handle_duplicate_dialog(page)
            return False

        if allow_create:
            new_item.wait_for(state="visible", timeout=3000)
            new_item.click()
            create_playlist_from_modal(page, playlist_name)
            handle_duplicate_dialog(page)
            return True

        raise RuntimeError(f"Playlist {playlist_name!r} not found in submenu")
    except Exception as exc:
        shot = capture_error_screenshot(page, debug_dir)
        diag = collect_visible_diagnostics(page)
        raise RuntimeError(
            f"{exc} | diag={json.dumps(diag, ensure_ascii=False)} | screenshot={shot}"
        ) from exc


def format_eta(seconds: float) -> str:
    secs = max(0, round(seconds))
    mins, rem = divmod(secs, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h{mins:02d}m"
    if mins > 0:
        return f"{mins}m{rem:02d}s"
    return f"{rem}s"


def run_import(
    matched_path: Path,
    *,
    mode: str,
    playlist_name: str,
    include_uncertain: bool,
    limit: int,
    delay: float,
    login_timeout: int,
    profile_dir: Path,
    browser_choice: str,
) -> int:
    data = json.loads(matched_path.read_text(encoding="utf-8"))
    storefront = str((data.get("summary") or {}).get("storefront") or "cn")
    allowed_statuses = {"matched", "uncertain"} if include_uncertain else {"matched"}

    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in data.get("results") or []:
        if item.get("status") not in allowed_statuses:
            continue
        best = item.get("best")
        if not best or not best.get("id"):
            continue
        sid = str(best["id"])
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        candidates.append(
            {
                "id": sid,
                "title": best.get("title") or item.get("qq_title") or sid,
                "artists": ", ".join(best.get("artists") or []) or item.get("qq_artists") or "",
                "index": item.get("index"),
            }
        )

    progress_name = (
        "import_progress_favorite.json"
        if mode == "favorite"
        else "import_progress.json"
    )
    progress_path = matched_path.parent / progress_name
    target_label = "喜爱歌曲 (Favorite)" if mode == "favorite" else playlist_name
    progress = load_progress(progress_path, mode, target_label)
    progress["mode"] = mode
    progress["target"] = target_label
    added_set = set(str(x) for x in progress["added_ids"])

    pending = [c for c in candidates if c["id"] not in added_set]
    if limit > 0:
        pending = pending[:limit]

    print(
        f"模式: {target_label} | 候选总计={len(candidates)} "
        f"| 已导入={len(added_set)} | 本次待处理={len(pending)}",
        flush=True,
    )
    if not pending:
        print("没有待导入曲目，已完成。", flush=True)
        return 0

    consecutive_failures = 0
    playlist_created_or_confirmed = len(added_set) > 0
    failed_by_id: dict[str, dict[str, Any]] = {
        str(f["id"]): f
        for f in (progress.get("failed") or [])
        if isinstance(f, dict) and f.get("id")
    }

    with sync_playwright() as p:
        context = launch_persistent_browser(p, profile_dir, browser_choice)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            ensure_logged_in(page, storefront, login_timeout)

            total = len(pending)
            start_ts = time.monotonic()
            for pos, track in enumerate(pending, start=1):
                sid = track["id"]
                label = f"{track['title']} - {track['artists']}".strip(" -")
                try:
                    if mode == "favorite":
                        action_status = favorite_song(
                            page,
                            storefront,
                            sid,
                            debug_dir=matched_path.parent,
                        )
                    else:
                        created = add_song_to_playlist(
                            page,
                            storefront,
                            sid,
                            playlist_name,
                            allow_create=not playlist_created_or_confirmed,
                            debug_dir=matched_path.parent,
                        )
                        playlist_created_or_confirmed = True
                        action_status = "created+added" if created else "added"

                    if sid not in added_set:
                        added_set.add(sid)
                        progress["added_ids"].append(sid)
                    failed_by_id.pop(sid, None)
                    progress["failed"] = list(failed_by_id.values())
                    save_progress(progress_path, progress)
                    consecutive_failures = 0

                    elapsed = time.monotonic() - start_ts
                    eta_str = format_eta((elapsed / pos) * (total - pos))
                    print(
                        f"[{pos}/{total}] OK ({action_status}) | ETA {eta_str} | {label} ({sid})",
                        flush=True,
                    )
                except Exception as exc:
                    consecutive_failures += 1
                    failed_by_id[sid] = {
                        "id": sid,
                        "title": track["title"],
                        "artists": track["artists"],
                        "error": str(exc),
                    }
                    progress["failed"] = list(failed_by_id.values())
                    save_progress(progress_path, progress)
                    print(
                        f"[{pos}/{total}] FAIL: {label} ({sid}) -> {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if consecutive_failures >= 5:
                        print(
                            "连续失败 5 次已安全暂停。请检查报错和 debug_last_error.png，"
                            f"进度已保存至 {progress_path}",
                            file=sys.stderr,
                            flush=True,
                        )
                        return 1
                if pos < total and delay > 0:
                    time.sleep(delay)
        finally:
            context.close()

    print(
        f"导入结束 ({target_label})：累计完成={len(progress['added_ids'])}，"
        f"失败={len(progress['failed'])}。进度文件：{progress_path}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    default_profile = Path(__file__).resolve().parent / ".browser_profile"
    parser = argparse.ArgumentParser(
        description="Import matched Apple Music tracks as Favorites or into a playlist via Playwright."
    )
    parser.add_argument(
        "matched_json",
        nargs="?",
        type=Path,
        default=None,
        help="Path to matched_cn.json or playlist output dir (default: latest output/*/matched_cn.json)",
    )
    parser.add_argument(
        "--mode",
        choices=("favorite", "playlist"),
        default="favorite",
        help="Import target: 'favorite' marks 喜爱歌曲 (default), 'playlist' adds to a named playlist",
    )
    parser.add_argument(
        "--playlist-name",
        default="",
        help="Target Apple Music playlist name when --mode=playlist (default: QQ音乐-<playlist_name>)",
    )
    parser.add_argument(
        "--include-uncertain",
        action="store_true",
        help="Also import tracks with status='uncertain'",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Import at most N pending tracks (useful for smoke testing, e.g. --limit 3)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Seconds to pause between songs (default: 0.8)",
    )
    parser.add_argument(
        "--login-timeout",
        type=int,
        default=300,
        help="Max seconds to wait for manual Apple ID sign-in (default: 300)",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=default_profile,
        help=f"Persistent browser profile directory (default: {default_profile})",
    )
    parser.add_argument(
        "--browser",
        choices=("msedge", "chrome", "chromium"),
        default="msedge",
        help="Browser channel to launch (default: msedge, falls back to chrome)",
    )
    args = parser.parse_args(argv)

    matched_path = resolve_matched_json(args.matched_json)
    playlist_name = args.playlist_name.strip() or default_playlist_name(matched_path)
    return run_import(
        matched_path,
        mode=args.mode,
        playlist_name=playlist_name,
        include_uncertain=args.include_uncertain,
        limit=args.limit,
        delay=args.delay,
        login_timeout=args.login_timeout,
        profile_dir=args.profile_dir,
        browser_choice=args.browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
