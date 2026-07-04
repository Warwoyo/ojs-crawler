#!/usr/bin/env python3
"""
normal_activity_crawler.py

Crawler/simulator untuk membuat dataset aktivitas normal user pada web/app
yang Anda miliki atau lingkungan lab yang Anda punya izin akses.

Fokus:
- navigasi halaman normal
- klik link internal yang aman
- optional search benign
- optional upload file dummy untuk lab yang authorized
- logging event ke JSONL dan CSV
- pembatasan scope domain/path
- optional robots.txt check
- tanpa fuzzing, brute-force, exploit, payload, atau upload file kecuali
  --enable-dummy-upload diaktifkan eksplisit

Contoh:
python3 normal_activity_crawler.py \
  --start-url "http://10.34.100.102:8033/index.php/javd-journal" \
  --scope-prefix "http://10.34.100.102:8033/index.php/javd-journal" \
  --sessions 20 \
  --max-steps 30 \
  --delay-min 1.5 \
  --delay-max 4.0 \
  --enable-search \
  --search-terms "cybersecurity,ojs,security" \
  --out-jsonl dataset_ojs_normal.jsonl \
  --out-csv dataset_ojs_normal.csv
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


DEFAULT_USER_AGENT = (
    "NormalActivityResearchBot/1.0 "
    "(authorized lab data collection; contact: researcher@example.local)"
)

DEFAULT_DENY_REGEX = (
    r"(logout|signout|delete|remove|destroy|drop|truncate|"
    r"admin|dashboard|management|settings|setup|install|upgrade|"
    r"importexport|csrf|token|edit|submit|submission|workflow|"
    r"setlocale|changelocale|/locale/|"
    r"payment|checkout|cart|password|profile|api|download|export)"
)

DEFAULT_ADMIN_DENY_REGEX = (
    r"(logout|signout|delete|remove|destroy|drop|truncate|clear|reset|"
    r"setup|install|upgrade|importexport|export|download|"
    r"csrf|token|\bedit\b|\bsubmit\b|\bwizard\b|\badd\b|\bcreate\b|"
    r"\bnew\b|\bupload\b|\bfile\b|"
    r"setlocale|changelocale|/locale/|"
    r"payment|checkout|cart|password|lostpassword|profile|"
    r"become|impersonate|disable|enable|activate|deactivate|"
    r"email|mail|notify|notification|\bmerge\b|\bmove\b|\bcopy\b)"
)

DEFAULT_ADMIN_UPLOAD_FOCUS_TERMS = (
    "management,settings,website,publication,admin,submissions,submission,"
    "manageissues,workflow,upload,file,galley,issue"
)

DEFAULT_ADMIN_UPLOAD_FOCUS_URLS = (
    "management/settings/website,admin/index,submissions,manageIssues,"
    "management/settings/publication"
)

ASYNC_UPLOAD_WAIT_URL_HINTS = (
    "management",
    "settings",
    "admin",
    "submission",
    "workflow",
    "website",
    "publication",
    "upload",
    "file",
)

UPLOAD_LINK_SKIP_REGEX = re.compile(
    r"(logout|signout|delete|remove|destroy|drop|truncate|clear|reset|"
    r"export|download|password|lostpassword|profile|become|impersonate|"
    r"disable|enable|activate|deactivate|merge|move|copy)",
    re.IGNORECASE,
)

TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)

# Minimal valid .docx (zip) with a one-paragraph document, base64 encoded.
TINY_DOCX_BASE64 = (
    "UEsDBBQAAAAIAAAAIQAAAAAAAAAAAAAAAAALAAAAX3JlbHMvLnJlbHONj80KwjAQhO99ipC7TevBg7Tp"
    "RRC8iT7Amm7bYJofSVR8e6MHwYNHmdn9puZa39qGnUn541tCr5IUq6oKW3VmLrgh/2Wr7ku0mx1pFa5"
    "AQY0zhc8hOA2Qri6o1a5xFLTaKmw2mkZBGdFOu6yqz9OhZ5nmYCXhAY0OoQyEmS5FJDzHf0iWMjLYW7"
    "9YWmm0Zj0eDPvNoQwZ8yG6FfeqZ8yq2/8k5fXwGvxg5/wBQSwMEFAAAAAgAAAAhAAAAAAAAAAAAAAAA"
    "ABMAAABbQ29udGVudF9UeXBlc10ueG1srZDBCsIwDIbvPkXpVdbqQUR0OwjeRB9gtNlW7Jql2eb2"
    "9uJhIkw8CMkl+ZP/S1a3XdvIHhI7wpXaZLmSgAaNw6ZSj/tNulEbLTZ2z6QAsIC0KaW01vso0i0s"
    "IWk4kM6MDzTMAvOCYU/lm9m4NrgO0kbYo9YvKrWMH3bAOkNRUCXWTgO0G0YU9CVwLwT7v5oq8fw"
    "iNIC3FZBLW2WK5X+eKtEuLxYbFcrNbLxdvi4wNQSwECFAAUAAAACAAAACEAAAAAAAAAAAAAAAAACwAA"
    "AAAAAAAAAAAAAAAAAAAAX3JlbHMvLnJlbHNQSwECFAAUAAAACAAAACEAAAAAAAAAAAAAAAAAEwAAAAAAA"
    "AAAAAAAAAAABAAAAAABbQ29udGVudF9UeXBlc10ueG1sUEsFBgAAAAACAAIAgAAAAHgBAAAAAA=="
)


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<<>>endobj\n"
        b"2 0 obj<< /Length 44 >>stream\n"
        b"BT /F1 12 Tf 72 720 Td (dummy upload) Tj ET\n"
        b"endstream endobj\n"
        b"trailer<< /Root 1 0 R >>\n%%EOF\n"
    )


def _txt_bytes() -> bytes:
    return (
        "Normal activity dummy upload for authorized OJS lab logging.\n"
        f"Generated at {utc_now()}.\n"
    ).encode("utf-8")


def _csv_bytes() -> bytes:
    return b"id,title,note\n1,dummy upload,authorized lab logging\n"


def _html_bytes() -> bytes:
    return b"<!doctype html><title>dummy upload</title><p>authorized lab logging</p>\n"


def _css_bytes() -> bytes:
    return b"/* normal activity dummy upload for OJS lab logging */\n"


def _png_bytes() -> bytes:
    return base64.b64decode(TINY_PNG_BASE64)


def _docx_bytes() -> bytes:
    return base64.b64decode(TINY_DOCX_BASE64)


# Ordered fallback of dummy upload variants. The crawler tries these in order and
# stops at the first extension OJS accepts. Document types come first because OJS
# submission/galley uploads usually expect manuscripts; media/text come after.
DUMMY_UPLOAD_VARIANTS: List[Tuple[str, str, Any]] = [
    ("pdf", "normal_activity_dummy_upload.pdf", _pdf_bytes),
    ("docx", "normal_activity_dummy_upload.docx", _docx_bytes),
    ("txt", "normal_activity_dummy_upload.txt", _txt_bytes),
    ("png", "normal_activity_dummy_upload.png", _png_bytes),
    ("jpg", "normal_activity_dummy_upload.jpg", _png_bytes),
    ("csv", "normal_activity_dummy_upload.csv", _csv_bytes),
    ("html", "normal_activity_dummy_upload.html", _html_bytes),
    ("css", "normal_activity_dummy_upload.css", _css_bytes),
]

# Which variant extensions match a given accept-attribute hint.
ACCEPT_EXT_HINTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("image", ("png", "jpg")),
    (".png", ("png",)),
    (".jpg", ("jpg",)),
    (".jpeg", ("jpg",)),
    (".gif", ("png",)),
    (".svg", ("png",)),
    ("pdf", ("pdf",)),
    ("word", ("docx",)),
    (".doc", ("docx",)),
    (".docx", ("docx",)),
    ("csv", ("csv",)),
    ("text/csv", ("csv",)),
    ("html", ("html",)),
    ("css", ("css",)),
    ("text", ("txt",)),
    (".txt", ("txt",)),
]

CSV_FIELDS = [
    "timestamp_utc",
    "session_id",
    "step",
    "event_type",
    "action",
    "url_before",
    "url_after",
    "http_status",
    "load_time_ms",
    "page_title",
    "candidate_count",
    "chosen_text",
    "chosen_href",
    "content_sha256",
    "viewport",
    "user_agent",
    "error",
]


@dataclass
class Candidate:
    href: str
    text: str
    title: str = ""
    aria: str = ""


@dataclass
class UploadLinkCandidate:
    index: int
    href: str
    text: str
    title: str = ""
    aria: str = ""


@dataclass
class UploadResult:
    event_type: str
    action: str
    candidate_count: int
    chosen_text: str
    chosen_href: str
    http_status: Optional[int]
    load_time_ms: int
    error: str = ""
    ext: str = ""
    accepted: bool = False
    upload_page: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def normalize_url(url: str) -> str:
    url, _frag = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    # Normalisasi ringan: hapus trailing slash kecuali root.
    if parsed.path != "/" and url.endswith("/"):
        url = url[:-1]
    return url


def append_path_segment(base_url: str, segment: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path or "/"

    # OJS context homepage often appears as /index.php/journal/index.
    # Treat the final /index as the homepage operation, not as a base folder.
    if path.endswith("/index"):
        path = path[: -len("/index")]

    if not path.endswith("/"):
        path += "/"

    new_path = path + segment.lstrip("/")
    return normalize_url(urlunparse(parsed._replace(path=new_path, query="", fragment="")))


def resolve_url_value(value: str, start_url: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""

    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        return normalize_url(value)

    if value.startswith("/"):
        start = urlparse(start_url)
        return normalize_url(urlunparse(start._replace(path=value, query="", fragment="")))

    return append_path_segment(start_url, value)


def parse_url_values(value: Optional[str], start_url: str) -> List[str]:
    if not value:
        return []

    urls: List[str] = []
    seen = set()
    for item in value.split(","):
        url = resolve_url_value(item, start_url)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def same_host_or_subdomain(host: str, allowed_host: str) -> bool:
    host = (host or "").lower()
    allowed_host = (allowed_host or "").lower()
    return host == allowed_host or host.endswith("." + allowed_host)


def is_in_scope(url: str, start_url: str, scope_prefix: Optional[str]) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    start = urlparse(start_url)

    if parsed.scheme not in ("http", "https"):
        return False

    # Default: sama scheme, host, port.
    if (
        parsed.scheme != start.scheme
        or parsed.hostname != start.hostname
        or parsed.port != start.port
    ):
        return False

    # Optional: batasi path, misalnya hanya journal tertentu di OJS.
    if scope_prefix:
        return normalize_url(url).startswith(normalize_url(scope_prefix))

    return True


def is_denied(url: str, text: str, deny_re: re.Pattern[str]) -> bool:
    haystack = f"{url} {text}".lower()
    return bool(deny_re.search(haystack))


def init_robot_parser(start_url: str, user_agent: str) -> Optional[RobotFileParser]:
    parsed = urlparse(start_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp
    except Exception:
        return None


def can_fetch(
    robot_parser: Optional[RobotFileParser],
    user_agent: str,
    url: str,
    respect_robots: bool,
) -> bool:
    if not respect_robots:
        return True
    if robot_parser is None:
        # Untuk lab lokal biasanya robots.txt tidak ada.
        # Jika ingin strict, pastikan robots.txt tersedia.
        return True
    try:
        return robot_parser.can_fetch(user_agent, url)
    except Exception:
        return True


def visible_content_hash(page) -> str:
    try:
        text = page.locator("body").inner_text(timeout=2000)
        return sha256_text(text[:20000])
    except Exception:
        return ""


def page_title(page) -> str:
    try:
        return page.title(timeout=2000)[:300]
    except Exception:
        return ""


def collect_link_candidates(
    page,
    current_url: str,
    start_url: str,
    scope_prefix: Optional[str],
    deny_re: re.Pattern[str],
    max_candidates: int,
) -> List[Candidate]:
    try:
        raw_links = page.eval_on_selector_all(
            "a[href]",
            """els => els.map(a => ({
                href: a.href || "",
                text: (a.innerText || a.textContent || "").trim(),
                title: (a.getAttribute("title") || "").trim(),
                aria: (a.getAttribute("aria-label") || "").trim()
            }))""",
        )
    except Exception:
        return []

    candidates: List[Candidate] = []
    seen = set()

    for item in raw_links:
        href = normalize_url(str(item.get("href", "")))
        text = (item.get("text") or item.get("title") or item.get("aria") or "").strip()
        if not href or href in seen:
            continue
        if not is_in_scope(href, start_url, scope_prefix):
            continue
        if is_denied(href, text, deny_re):
            continue

        seen.add(href)
        candidates.append(
            Candidate(
                href=href,
                text=text[:200],
                title=str(item.get("title", ""))[:200],
                aria=str(item.get("aria", ""))[:200],
            )
        )

        if len(candidates) >= max_candidates:
            break

    return candidates


def first_usable_locator(page, selectors: List[str], timeout_ms: int = 1000):
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if (
                loc.count() > 0
                and loc.is_visible(timeout=timeout_ms)
                and loc.is_enabled(timeout=timeout_ms)
            ):
                return loc
        except Exception:
            continue
    return None


def collect_upload_link_candidates(
    page,
    start_url: str,
    scope_prefix: Optional[str],
    max_candidates: int = 20,
) -> List[UploadLinkCandidate]:
    try:
        raw_links = page.eval_on_selector_all(
            "a[href]",
            """els => els.map((a, index) => ({
                index,
                href: a.href || "",
                text: (a.innerText || a.textContent || "").trim(),
                title: (a.getAttribute("title") || "").trim(),
                aria: (a.getAttribute("aria-label") || "").trim()
            }))""",
        )
    except Exception:
        return []

    candidates: List[UploadLinkCandidate] = []
    seen = set()
    for item in raw_links:
        href = normalize_url(str(item.get("href", "")))
        text = (item.get("text") or item.get("title") or item.get("aria") or "").strip()
        haystack = f"{href} {text}".lower()

        if not href or href in seen:
            continue
        if not is_in_scope(href, start_url, scope_prefix):
            continue
        if "upload" not in haystack and "show-file-upload-form" not in haystack:
            continue
        if UPLOAD_LINK_SKIP_REGEX.search(haystack):
            continue

        seen.add(href)
        candidates.append(
            UploadLinkCandidate(
                index=int(item.get("index", 0)),
                href=href,
                text=text[:200],
                title=str(item.get("title", ""))[:200],
                aria=str(item.get("aria", ""))[:200],
            )
        )

        if len(candidates) >= max_candidates:
            break

    return candidates


def collect_file_input_count(page) -> int:
    try:
        return page.locator("input[type='file']").count()
    except Exception:
        return 0


def page_may_have_async_upload(page) -> bool:
    current = page.url.lower()
    return any(hint in current for hint in ASYNC_UPLOAD_WAIT_URL_HINTS)


def wait_for_upload_candidate(page, upload_scan_wait_ms: int) -> None:
    if upload_scan_wait_ms <= 0 or not page_may_have_async_upload(page):
        return

    try:
        page.wait_for_selector(
            "input[type='file'], a[href*='upload' i], a[href*='show-file-upload-form' i], a:has-text('Upload')",
            timeout=upload_scan_wait_ms,
        )
    except Exception:
        pass


def first_enabled_file_input(page):
    count = collect_file_input_count(page)
    for index in range(count):
        loc = page.locator("input[type='file']").nth(index)
        try:
            if not loc.is_disabled(timeout=500):
                return loc
        except Exception:
            continue
    return None


def order_variants_by_accept(accept: str) -> List[Tuple[str, str, Any]]:
    """Return DUMMY_UPLOAD_VARIANTS reordered so extensions matching the form's
    accept attribute are tried first, followed by the remaining fallback order."""
    accept_lower = (accept or "").lower()
    preferred_exts: List[str] = []
    if accept_lower:
        for hint, exts in ACCEPT_EXT_HINTS:
            if hint in accept_lower:
                for ext in exts:
                    if ext not in preferred_exts:
                        preferred_exts.append(ext)

    by_ext = {variant[0]: variant for variant in DUMMY_UPLOAD_VARIANTS}
    ordered: List[Tuple[str, str, Any]] = []
    seen = set()
    for ext in preferred_exts:
        variant = by_ext.get(ext)
        if variant and ext not in seen:
            seen.add(ext)
            ordered.append(variant)
    for variant in DUMMY_UPLOAD_VARIANTS:
        if variant[0] not in seen:
            seen.add(variant[0])
            ordered.append(variant)
    return ordered


def build_upload_variants(
    dummy_upload_dir: Path,
    explicit_file: Optional[Path],
    accept: str,
    skip_exts: Optional[set] = None,
) -> List[Tuple[str, Path]]:
    """Prepare ordered (ext, path) dummy files to try. If an explicit file is
    given, that single file is used. Otherwise dummy files are materialised in
    priority order derived from the form's accept attribute."""
    if explicit_file is not None:
        ext = explicit_file.suffix.lstrip(".").lower() or "file"
        return [(ext, explicit_file)]

    skip_exts = skip_exts or set()
    dummy_upload_dir.mkdir(parents=True, exist_ok=True)
    variants: List[Tuple[str, Path]] = []
    for ext, filename, builder in order_variants_by_accept(accept):
        if ext in skip_exts:
            continue
        path = dummy_upload_dir / filename
        if not path.exists():
            path.write_bytes(builder())
        variants.append((ext, path))
    return variants


def file_input_description(file_input) -> Tuple[str, str]:
    try:
        data = file_input.evaluate(
            """el => ({
                name: el.getAttribute("name") || "",
                id: el.getAttribute("id") || "",
                accept: el.getAttribute("accept") || "",
                formAction: el.form ? el.form.action : ""
            })"""
        )
    except Exception:
        return "", ""

    label_parts = [
        f"name={data.get('name')}" if data.get("name") else "",
        f"id={data.get('id')}" if data.get("id") else "",
        f"accept={data.get('accept')}" if data.get("accept") else "",
    ]
    label = " ".join(part for part in label_parts if part)
    return label[:200], str(data.get("accept", ""))


def select_ojs_article_component_if_needed(page) -> str:
    genre = page.locator("select[name='genreId']").first
    try:
        if genre.count() == 0 or not genre.is_visible(timeout=500) or not genre.is_enabled(timeout=500):
            return ""

        options = genre.evaluate(
            """el => [...el.options]
                .map(option => ({value: option.value || "", text: option.text || ""}))
                .filter(option => option.value)"""
        )
        if not options:
            return "genre_options_missing"

        preferred = None
        for option in options:
            text = str(option.get("text", "")).lower()
            if "article text" in text or "manuscript" in text:
                preferred = option
                break
        preferred = preferred or options[0]
        genre.select_option(str(preferred.get("value")), timeout=2000)
        page.wait_for_timeout(250)
        return f"genre={preferred.get('text', '').strip()[:80]}"
    except PlaywrightError as exc:
        return f"genre_select_error:{type(exc).__name__}"
    except Exception as exc:
        return f"genre_select_error:{type(exc).__class__.__name__}"


def response_looks_like_upload(response) -> bool:
    method = response.request.method
    url = response.url.lower()
    if method not in ("POST", "PUT", "PATCH"):
        return False

    upload_markers = [
        "upload-file",
        "save-file",
        "file-upload",
        "upload",
    ]
    return any(marker in url for marker in upload_markers)


def set_dummy_file_and_capture_upload(
    page,
    file_input,
    dummy_file: Path,
    timeout_ms: int,
) -> Tuple[Optional[int], int, str]:
    t0 = time.perf_counter()
    auto_upload_wait_ms = 7000 if page.locator("select[name='genreId']").count() > 0 else 1500
    try:
        with page.expect_response(response_looks_like_upload, timeout=min(timeout_ms, auto_upload_wait_ms)) as response_info:
            file_input.set_input_files(str(dummy_file), timeout=timeout_ms)
        response = response_info.value
        page.wait_for_timeout(750)
        return response.status, int((time.perf_counter() - t0) * 1000), ""
    except PlaywrightTimeoutError:
        # Some OJS upload forms only submit after a button click. Keep going and let submit_upload_form handle it.
        return None, int((time.perf_counter() - t0) * 1000), "upload_auto_response_timeout"
    except PlaywrightError as exc:
        return None, int((time.perf_counter() - t0) * 1000), f"set_input_files_error:{type(exc).__name__}"


def click_ojs_upload_continue_if_present(page, timeout_ms: int) -> Tuple[Optional[int], int, str]:
    continue_button = first_usable_locator(
        page,
        [
            ".ui-dialog button:has-text('Continue')",
            ".pkp_modal_panel button:has-text('Continue')",
            "form:has(input[type='file']) button:has-text('Continue')",
            "button:has-text('Continue')",
        ],
    )
    if continue_button is None:
        return None, 0, ""

    t0 = time.perf_counter()
    try:
        with page.expect_response(
            lambda response: response.request.method == "GET"
            and any(marker in response.url.lower() for marker in ["edit-metadata", "finish", "file-upload-wizard"]),
            timeout=min(timeout_ms, 7000),
        ) as response_info:
            continue_button.click(timeout=timeout_ms)
        response = response_info.value
        page.wait_for_timeout(750)
        return response.status, int((time.perf_counter() - t0) * 1000), ""
    except PlaywrightTimeoutError:
        return None, int((time.perf_counter() - t0) * 1000), "upload_continue_timeout"
    except PlaywrightError as exc:
        return None, int((time.perf_counter() - t0) * 1000), f"upload_continue_error:{type(exc).__name__}"


def click_first_upload_link(
    page,
    upload_links: List[UploadLinkCandidate],
    timeout_ms: int,
) -> Tuple[Optional[UploadLinkCandidate], str]:
    if not upload_links:
        return None, ""

    chosen = upload_links[0]
    try:
        page.locator("a[href]").nth(chosen.index).click(timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(750)
        return chosen, ""
    except PlaywrightError as exc:
        return chosen, f"upload_link_click_error:{type(exc).__name__}"


def submit_upload_form(page, timeout_ms: int) -> Tuple[Optional[int], int, str]:
    submit = first_usable_locator(
        page,
        [
            "form:has(input[type='file']) button[type='submit']",
            "form:has(input[type='file']) input[type='submit']",
            "form:has(input[type='file']) button:has-text('Upload')",
            "form:has(input[type='file']) input[value*='Upload']",
            ".ui-dialog button:has-text('Upload')",
            ".pkp_modal_panel button:has-text('Upload')",
        ],
    )
    if submit is None:
        return None, 0, "upload_submit_button_missing"

    t0 = time.perf_counter()
    try:
        with page.expect_response(
            lambda response: response.request.method in ("POST", "PUT", "PATCH"),
            timeout=timeout_ms,
        ) as response_info:
            submit.click(timeout=timeout_ms)
        response = response_info.value
        load_ms = int((time.perf_counter() - t0) * 1000)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        return response.status, load_ms, ""
    except PlaywrightTimeoutError:
        load_ms = int((time.perf_counter() - t0) * 1000)
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass
        return None, load_ms, "upload_submit_timeout"
    except PlaywrightError as exc:
        load_ms = int((time.perf_counter() - t0) * 1000)
        return None, load_ms, f"upload_submit_error:{type(exc).__name__}"


UPLOAD_REJECT_MARKERS = (
    "not allowed",
    "invalid file",
    "file type is not",
    "not permitted",
    "unsupported file",
    "tidak diizinkan",
    "tidak diperbolehkan",
    "format file",
    "invalid extension",
    "invalid_file_type",
)


def upload_rejected(page) -> bool:
    """Detect OJS client-side / plupload file-type rejection after a file is set."""
    selectors = [
        ".pkp_form_error",
        ".pkpUploadError",
        ".plupload_error",
        ".ui-pnotify-text",
        ".pkp_notification_error",
    ]
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if loc.count() > 0 and loc.is_visible(timeout=300):
                text = loc.inner_text(timeout=300).lower()
                if any(marker in text for marker in UPLOAD_REJECT_MARKERS):
                    return True
        except Exception:
            continue
    return False


def attempt_upload_with_file(
    page,
    file_input,
    dummy_file: Path,
    submit_after_set: bool,
    timeout_ms: int,
) -> Tuple[str, Optional[int], str]:
    """Try one dummy file. Returns (outcome, http_status, note) where outcome is
    one of: accepted, rejected, prepared, error."""
    genre_note = select_ojs_article_component_if_needed(page)
    note = genre_note if "error" in genre_note else ""

    status, _set_ms, set_error = set_dummy_file_and_capture_upload(
        page, file_input, dummy_file, timeout_ms
    )
    if set_error.startswith("set_input_files_error"):
        return "error", None, set_error

    if upload_rejected(page):
        return "rejected", status, "client_rejected_file_type"

    if status is not None:
        # OJS auto-submitted the upload; optionally advance the wizard.
        if submit_after_set:
            cont_status, _cms, cont_err = click_ojs_upload_continue_if_present(page, timeout_ms)
            if cont_status is not None:
                status = cont_status
            if cont_err and "timeout" not in cont_err:
                note = ";".join(x for x in [note, cont_err] if x)
        if upload_rejected(page):
            return "rejected", status, "client_rejected_file_type"
        return "accepted", status, note

    if not submit_after_set:
        return "prepared", None, note

    sub_status, _sms, sub_error = submit_upload_form(page, timeout_ms)
    if upload_rejected(page):
        return "rejected", sub_status, sub_error or "client_rejected_file_type"
    if sub_status is not None:
        return "accepted", sub_status, note
    # No response even after clicking submit: treat as rejected so we try the next
    # extension instead of hammering this page with the same file.
    return "rejected", None, sub_error or "upload_submit_timeout"


def try_dummy_upload(
    page,
    start_url: str,
    scope_prefix: Optional[str],
    dummy_upload_dir: Path,
    explicit_dummy_file: Optional[Path],
    submit_after_set: bool,
    timeout_ms: int,
    upload_scan_wait_ms: int,
    skip_exts: Optional[set] = None,
) -> Optional[List[UploadResult]]:
    t0 = time.perf_counter()
    initial_file_inputs = collect_file_input_count(page)
    upload_links = collect_upload_link_candidates(page, start_url, scope_prefix)
    candidate_count = initial_file_inputs + len(upload_links)

    if candidate_count == 0:
        wait_for_upload_candidate(page, upload_scan_wait_ms)
        initial_file_inputs = collect_file_input_count(page)
        upload_links = collect_upload_link_candidates(page, start_url, scope_prefix)
        candidate_count = initial_file_inputs + len(upload_links)

    if candidate_count == 0:
        return None

    upload_page = normalize_url(page.url) or page.url

    def open_form() -> Tuple[Optional[UploadLinkCandidate], str]:
        if collect_file_input_count(page) > 0:
            return None, ""
        links = collect_upload_link_candidates(page, start_url, scope_prefix)
        return click_first_upload_link(page, links, timeout_ms)

    chosen_link, click_error = open_form()
    if click_error:
        return [
            UploadResult(
                event_type="dummy_upload_skipped",
                action="open_upload_form",
                candidate_count=candidate_count,
                chosen_text=chosen_link.text if chosen_link else "[upload_link]",
                chosen_href=chosen_link.href if chosen_link else page.url,
                http_status=None,
                load_time_ms=int((time.perf_counter() - t0) * 1000),
                error=click_error,
                upload_page=upload_page,
            )
        ]

    file_input = first_enabled_file_input(page)
    if file_input is None:
        return [
            UploadResult(
                event_type="dummy_upload_skipped",
                action="find_file_input",
                candidate_count=candidate_count,
                chosen_text=chosen_link.text if chosen_link else "[file_input]",
                chosen_href=chosen_link.href if chosen_link else page.url,
                http_status=None,
                load_time_ms=int((time.perf_counter() - t0) * 1000),
                error="file_input_missing_after_upload_form",
                upload_page=upload_page,
            )
        ]

    input_label, accept = file_input_description(file_input)
    variants = build_upload_variants(dummy_upload_dir, explicit_dummy_file, accept, skip_exts)
    if not variants:
        return [
            UploadResult(
                event_type="dummy_upload_skipped",
                action="build_variants",
                candidate_count=candidate_count,
                chosen_text=f"[all_exts_tried] {input_label}".strip(),
                chosen_href=chosen_link.href if chosen_link else page.url,
                http_status=None,
                load_time_ms=int((time.perf_counter() - t0) * 1000),
                error="no_untried_extension",
                upload_page=upload_page,
            )
        ]

    results: List[UploadResult] = []
    tried_exts: List[str] = []

    for ext, dummy_file in variants:
        # Re-locate (and if needed re-open) the upload form for each attempt.
        file_input = first_enabled_file_input(page)
        if file_input is None:
            open_form()
            file_input = first_enabled_file_input(page)
        if file_input is None:
            results.append(
                UploadResult(
                    event_type="dummy_upload_skipped",
                    action="find_file_input",
                    candidate_count=candidate_count,
                    chosen_text=f"[file_input_lost] tried={','.join(tried_exts)}",
                    chosen_href=chosen_link.href if chosen_link else page.url,
                    http_status=None,
                    load_time_ms=int((time.perf_counter() - t0) * 1000),
                    error="file_input_missing_between_attempts",
                    upload_page=upload_page,
                )
            )
            break

        tried_exts.append(ext)
        outcome, status, note = attempt_upload_with_file(
            page, file_input, dummy_file, submit_after_set, timeout_ms
        )
        chosen_text = f"{dummy_file.name} [{ext}] {input_label} {note}".strip()

        if outcome == "accepted":
            results.append(
                UploadResult(
                    event_type="dummy_upload",
                    action="auto_dummy_upload_continue" if submit_after_set else "auto_dummy_upload",
                    candidate_count=candidate_count,
                    chosen_text=chosen_text,
                    chosen_href=chosen_link.href if chosen_link else page.url,
                    http_status=status,
                    load_time_ms=int((time.perf_counter() - t0) * 1000),
                    error=note if "error" in note else "",
                    ext=ext,
                    accepted=True,
                    upload_page=upload_page,
                )
            )
            return results

        if outcome == "prepared":
            results.append(
                UploadResult(
                    event_type="dummy_upload_prepared",
                    action="set_dummy_file",
                    candidate_count=candidate_count,
                    chosen_text=chosen_text,
                    chosen_href=chosen_link.href if chosen_link else page.url,
                    http_status=None,
                    load_time_ms=int((time.perf_counter() - t0) * 1000),
                    error=note,
                    ext=ext,
                    upload_page=upload_page,
                )
            )
            return results

        if outcome == "error":
            results.append(
                UploadResult(
                    event_type="dummy_upload_skipped",
                    action="set_dummy_file",
                    candidate_count=candidate_count,
                    chosen_text=chosen_text,
                    chosen_href=chosen_link.href if chosen_link else page.url,
                    http_status=None,
                    load_time_ms=int((time.perf_counter() - t0) * 1000),
                    error=note,
                    ext=ext,
                    upload_page=upload_page,
                )
            )
            break

        # rejected: log the attempt and continue to the next extension.
        results.append(
            UploadResult(
                event_type="dummy_upload_attempt",
                action=f"try_ext_{ext}",
                candidate_count=candidate_count,
                chosen_text=chosen_text,
                chosen_href=chosen_link.href if chosen_link else page.url,
                http_status=status,
                load_time_ms=int((time.perf_counter() - t0) * 1000),
                error=note or "rejected",
                ext=ext,
                upload_page=upload_page,
            )
        )
        # Reset the upload form so the next extension starts clean.
        open_form()

    # All variants exhausted without acceptance.
    results.append(
        UploadResult(
            event_type="dummy_upload_failed",
            action="all_extensions_rejected",
            candidate_count=candidate_count,
            chosen_text=f"[tried] {','.join(tried_exts)} {input_label}".strip(),
            chosen_href=chosen_link.href if chosen_link else page.url,
            http_status=None,
            load_time_ms=int((time.perf_counter() - t0) * 1000),
            error=f"rejected_exts={','.join(tried_exts)}",
            upload_page=upload_page,
        )
    )
    return results


def choose_candidate(
    candidates: List[Candidate],
    visit_counts: Dict[str, int],
    max_url_revisit: int,
    focus_terms: Optional[List[str]] = None,
    focus_probability: float = 0.0,
) -> Optional[Candidate]:
    fresh = [c for c in candidates if visit_counts.get(c.href, 0) < max_url_revisit]
    if not fresh:
        return None

    if focus_terms and random.random() < focus_probability:
        prioritized = []
        for candidate in fresh:
            haystack = f"{candidate.href} {candidate.text} {candidate.title} {candidate.aria}".lower()
            score = sum(1 for term in focus_terms if term and term in haystack)
            if score > 0:
                prioritized.append((score, candidate))

        if prioritized:
            max_score = max(score for score, _candidate in prioritized)
            best = [candidate for score, candidate in prioritized if score == max_score]
            never_visited_best = [c for c in best if visit_counts.get(c.href, 0) == 0]
            return random.choice(never_visited_best or best)

    # Prioritaskan URL yang belum pernah dikunjungi, tetapi tetap ada randomness.
    never_visited = [c for c in fresh if visit_counts.get(c.href, 0) == 0]
    pool = never_visited if never_visited and random.random() < 0.75 else fresh
    return random.choice(pool)


def choose_focus_seed_url(
    focus_seed_urls: List[str],
    visit_counts: Dict[str, int],
    max_url_revisit: int,
) -> Optional[str]:
    eligible = [url for url in focus_seed_urls if visit_counts.get(url, 0) < max_url_revisit]
    if not eligible:
        return None

    never_visited = [url for url in eligible if visit_counts.get(url, 0) == 0]
    return random.choice(never_visited or eligible)


def find_search_box(page):
    selectors = [
        "input[type='search']",
        "input[name*='search' i]",
        "input[id*='search' i]",
        "input[placeholder*='search' i]",
        "input[placeholder*='cari' i]",
        "input[name*='query' i]",
        "input[id*='query' i]",
    ]
    return first_usable_locator(page, selectors)


def try_benign_search(
    page,
    term: str,
    timeout_ms: int,
) -> Tuple[bool, str]:
    box = find_search_box(page)
    if box is None:
        return False, "no_search_box"

    try:
        box.click(timeout=timeout_ms)
        box.fill(term, timeout=timeout_ms)
        box.press("Enter", timeout=timeout_ms)
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        return True, ""
    except PlaywrightTimeoutError:
        return False, "search_timeout"
    except PlaywrightError as exc:
        return False, f"search_error:{type(exc).__name__}"


def login_looks_successful(page, login_url: str) -> bool:
    logout_selectors = [
        "a[href*='logout' i]",
        "a[href*='signout' i]",
        "a:has-text('Logout')",
        "a:has-text('Log Out')",
        "a:has-text('Sign out')",
    ]
    for selector in logout_selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue

    password_box = first_usable_locator(page, ["input[type='password']"], timeout_ms=500)
    current = normalize_url(page.url).lower()
    login_path = urlparse(login_url).path.lower()

    if password_box is not None and login_path and login_path in urlparse(current).path.lower():
        return False

    try:
        body_text = page.locator("body").inner_text(timeout=1000).lower()
        failed_markers = [
            "invalid username",
            "invalid password",
            "username or password",
            "login failed",
            "incorrect",
        ]
        if any(marker in body_text for marker in failed_markers):
            return False
    except Exception:
        pass

    return password_box is None


def page_looks_authorization_denied(page) -> bool:
    current = page.url.lower()
    denied_markers = [
        "authorizationdenied",
        "accessdenied",
        "rolebasedaccessdenied",
        "permissiondenied",
    ]
    if any(marker in current for marker in denied_markers):
        return True

    try:
        body_text = page.locator("body").inner_text(timeout=1000).lower()
        body_markers = [
            "authorization denied",
            "access denied",
            "role based access denied",
            "you do not have permission",
            "not authorized",
        ]
        return any(marker in body_text for marker in body_markers)
    except Exception:
        return False


def perform_login(
    page,
    login_url: str,
    username: str,
    password: str,
    timeout_ms: int,
) -> Tuple[bool, Optional[int], int, str]:
    status, initial_load_ms, err = goto_url(page, login_url, timeout_ms)
    if err:
        return False, status, initial_load_ms, err

    username_box = first_usable_locator(
        page,
        [
            "input[name='username']",
            "input#username",
            "input[name*='user' i]",
            "input[id*='user' i]",
            "input[type='text']",
        ],
    )
    password_box = first_usable_locator(
        page,
        [
            "input[name='password']",
            "input#password",
            "input[type='password']",
        ],
    )

    if username_box is None:
        return False, status, initial_load_ms, "login_form_missing:username"
    if password_box is None:
        return False, status, initial_load_ms, "login_form_missing:password"

    try:
        username_box.fill(username, timeout=timeout_ms)
        password_box.fill(password, timeout=timeout_ms)
    except PlaywrightError as exc:
        return False, status, initial_load_ms, f"login_fill_error:{type(exc).__name__}"

    submit = first_usable_locator(
        page,
        [
            "form#login button[type='submit']",
            "form button[type='submit']",
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Sign in')",
        ],
    )

    t0 = time.perf_counter()
    submit_status: Optional[int] = None
    submit_err = ""

    try:
        if submit is not None:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms) as nav_info:
                submit.click(timeout=timeout_ms)
            response = nav_info.value
            submit_status = response.status if response is not None else None
        else:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms) as nav_info:
                password_box.press("Enter", timeout=timeout_ms)
            response = nav_info.value
            submit_status = response.status if response is not None else None
    except PlaywrightTimeoutError:
        submit_err = "login_navigation_timeout"
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
    except PlaywrightError as exc:
        submit_ms = int((time.perf_counter() - t0) * 1000)
        return False, submit_status or status, initial_load_ms + submit_ms, f"login_submit_error:{type(exc).__name__}"

    submit_ms = int((time.perf_counter() - t0) * 1000)
    ok = login_looks_successful(page, login_url)
    if ok:
        submit_err = ""
    elif not submit_err:
        submit_err = "login_failed"

    return ok, submit_status or status, initial_load_ms + submit_ms, submit_err


def goto_url(page, url: str, timeout_ms: int) -> Tuple[Optional[int], int, str]:
    t0 = time.perf_counter()
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        load_ms = int((time.perf_counter() - t0) * 1000)
        status = response.status if response is not None else None
        return status, load_ms, ""
    except PlaywrightTimeoutError:
        load_ms = int((time.perf_counter() - t0) * 1000)
        return None, load_ms, "navigation_timeout"
    except PlaywrightError as exc:
        load_ms = int((time.perf_counter() - t0) * 1000)
        return None, load_ms, f"navigation_error:{type(exc).__name__}"


def write_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv(path: Path, records: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def make_record(
    *,
    session_id: str,
    step: int,
    event_type: str,
    action: str,
    url_before: str,
    url_after: str,
    http_status: Optional[int],
    load_time_ms: Optional[int],
    page_title_value: str,
    candidate_count: int,
    chosen_text: str,
    chosen_href: str,
    content_sha256: str,
    viewport: str,
    user_agent: str,
    error: str = "",
) -> Dict[str, Any]:
    return {
        "timestamp_utc": utc_now(),
        "session_id": session_id,
        "step": step,
        "event_type": event_type,
        "action": action,
        "url_before": url_before,
        "url_after": url_after,
        "http_status": http_status if http_status is not None else "",
        "load_time_ms": load_time_ms if load_time_ms is not None else "",
        "page_title": page_title_value,
        "candidate_count": candidate_count,
        "chosen_text": chosen_text,
        "chosen_href": chosen_href,
        "content_sha256": content_sha256,
        "viewport": viewport,
        "user_agent": user_agent,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Persistent per-target notes ("catatan") and path self-learning
# ---------------------------------------------------------------------------

def site_key(start_url: str) -> str:
    """Key notes per target context so a different journal/URL learns separately."""
    parsed = urlparse(start_url)
    path = (parsed.path or "").rstrip("/")
    parts = [p for p in path.split("/") if p]
    # Keep index.php/<context> so /publicknowledge and /efgh are distinct keys.
    ctx = "/".join(parts[:3])
    return f"{parsed.netloc}/{ctx}" if ctx else parsed.netloc


def load_notes(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_notes(path: Path, notes: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def empty_site_notes() -> Dict[str, Any]:
    return {
        "visited": {},
        "dead_or_denied": [],
        "upload_pages": {},
        "learned_paths": {},
        "last_seen": "",
    }


def path_template(url: str, start_url: str) -> str:
    """Generalise a URL into a path template, collapsing numeric ids to {id}.
    e.g. .../workflow/index/13/1 -> workflow/index/{id}/{id}."""
    p = urlparse(url).path or ""
    base = (urlparse(start_url).path or "").rstrip("/")
    rel = p[len(base):] if base and p.startswith(base) else p
    parts = [s for s in rel.split("/") if s]
    if parts[:1] == ["index.php"]:
        parts = parts[2:]
    tmpl = ["{id}" if s.isdigit() else s for s in parts]
    return "/".join(tmpl) or "(root)"


def categorize_path(template: str) -> str:
    t = template.lower()
    if any(k in t for k in ("management", "admin", "settings", "stats", "user/")):
        return "admin"
    if "workflow" in t or "submission" in t:
        return "workflow"
    if any(k in t for k in ("issue", "article", "catalog", "search")):
        return "content"
    if any(k in t for k in ("about", "information", "announcement")):
        return "public"
    return "other"


def learn_from_candidates(
    candidates: List[Candidate],
    learned: Dict[str, Any],
    start_url: str,
) -> None:
    for candidate in candidates:
        tmpl = path_template(candidate.href, start_url)
        if tmpl == "(root)":
            continue
        entry = learned.get(tmpl)
        if entry is None:
            learned[tmpl] = {
                "count": 1,
                "category": categorize_path(tmpl),
                "example": candidate.href,
            }
        else:
            entry["count"] = entry.get("count", 0) + 1
            if not entry.get("example"):
                entry["example"] = candidate.href


# Priority of learned categories when seeding the simulate phase.
LEARNED_CATEGORY_PRIORITY = {"admin": 0, "workflow": 1, "content": 2, "public": 3, "other": 4}


def build_dynamic_focus(
    learned: Dict[str, Any],
    max_seeds: int = 14,
    max_terms: int = 18,
) -> Tuple[List[str], List[str]]:
    """Derive focus seed URLs + focus terms from the learned path map."""
    items = [(tmpl, meta) for tmpl, meta in learned.items() if meta.get("example")]

    def sort_key(item):
        tmpl, meta = item
        cat = meta.get("category", "other")
        return (LEARNED_CATEGORY_PRIORITY.get(cat, 4), -int(meta.get("count", 0)))

    items.sort(key=sort_key)

    seeds: List[str] = []
    for _tmpl, meta in items:
        example = meta.get("example")
        if example and example not in seeds:
            seeds.append(example)
        if len(seeds) >= max_seeds:
            break

    term_counts: Dict[str, int] = {}
    for tmpl, meta in learned.items():
        for seg in tmpl.split("/"):
            seg = seg.strip().lower()
            if not seg or seg == "{id}" or len(seg) < 3:
                continue
            term_counts[seg] = term_counts.get(seg, 0) + int(meta.get("count", 0))
    terms = [t for t, _ in sorted(term_counts.items(), key=lambda kv: -kv[1])][:max_terms]
    return seeds, terms


def page_has_upload_point(page) -> bool:
    if collect_file_input_count(page) > 0:
        return True
    try:
        return page.locator(
            "a[href*='upload' i], a[href*='show-file-upload-form' i], a:has-text('Upload')"
        ).count() > 0
    except Exception:
        return False


def run_recon_session(
    browser,
    *,
    args,
    start_url: str,
    login_url: str,
    password: Optional[str],
    auth_requested: bool,
    recon_seeds: List[str],
    deny_re: "re.Pattern[str]",
    robot_parser,
    viewport: Dict[str, int],
    viewport_str: str,
    learned: Dict[str, Any],
    visit_counts: Dict[str, int],
    upload_pages: Dict[str, Any],
    jsonl_path: Path,
) -> List[Dict[str, Any]]:
    """Breadth-first recon: enumerate reachable in-scope paths, build the learned
    map, and note upload points. Traffic is logged as recon_enumerate events."""
    session_id = str(uuid.uuid4())
    records: List[Dict[str, Any]] = []
    context = browser.new_context(
        user_agent=args.user_agent,
        viewport=viewport,
        locale="id-ID",
        timezone_id="Asia/Jakarta",
    )
    page = context.new_page()
    step = 0

    try:
        if auth_requested:
            ok, status, load_ms, err = perform_login(
                page, login_url, args.username or "", password or "", args.timeout_ms
            )
            record = make_record(
                session_id=session_id,
                step=step,
                event_type="login" if ok else "login_failed",
                action="recon_login",
                url_before="",
                url_after=page.url,
                http_status=status,
                load_time_ms=load_ms,
                page_title_value=page_title(page),
                candidate_count=0,
                chosen_text="[recon]",
                chosen_href=login_url,
                content_sha256=visible_content_hash(page),
                viewport=viewport_str,
                user_agent=args.user_agent,
                error=err,
            )
            write_jsonl(jsonl_path, record)
            records.append(record)
            step += 1
            if not ok:
                page.close()
                context.close()
                return records

        frontier: List[str] = []
        seen: set = set()
        for seed in ([start_url] + list(recon_seeds)):
            if seed and seed not in frontier:
                frontier.append(seed)

        while frontier and step <= args.recon_steps:
            url = frontier.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not is_in_scope(url, start_url, args.scope_prefix):
                continue
            if not can_fetch(robot_parser, args.user_agent, url, args.respect_robots):
                continue

            url_before = page.url
            status, load_ms, err = goto_url(page, url, args.timeout_ms)
            url_after = page.url
            normalized_after = normalize_url(url_after)
            if normalized_after:
                visit_counts[normalized_after] = visit_counts.get(normalized_after, 0) + 1

            denied = page_looks_authorization_denied(page)
            candidates = collect_link_candidates(
                page, url_after, start_url, args.scope_prefix, deny_re, args.max_candidates_per_page
            )
            learn_from_candidates(candidates, learned, start_url)

            if page_has_upload_point(page):
                upload_pages.setdefault(
                    normalized_after or url_after, {"accepted_ext": "", "failed_ext": []}
                )

            record = make_record(
                session_id=session_id,
                step=step,
                event_type="recon" if not denied else "recon_denied",
                action="recon_enumerate",
                url_before=url_before,
                url_after=url_after,
                http_status=status,
                load_time_ms=load_ms,
                page_title_value=page_title(page),
                candidate_count=len(candidates),
                chosen_text=path_template(url_after, start_url),
                chosen_href=url,
                content_sha256=visible_content_hash(page),
                viewport=viewport_str,
                user_agent=args.user_agent,
                error=err,
            )
            write_jsonl(jsonl_path, record)
            records.append(record)
            step += 1

            # Breadth-first: enqueue fresh in-scope, non-denied candidates.
            for candidate in candidates:
                href = candidate.href
                if href and href not in seen and href not in frontier:
                    frontier.append(href)

            time.sleep(random.uniform(args.delay_min, args.delay_max) * 0.5)
    finally:
        page.close()
        context.close()

    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authorized normal user activity crawler/dataset generator."
    )
    parser.add_argument("--start-url", required=True, help="URL awal.")
    parser.add_argument(
        "--scope-prefix",
        default=None,
        help="Opsional: batasi URL agar hanya diawali prefix ini, misalnya path journal OJS.",
    )
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-candidates-per-page", type=int, default=80)
    parser.add_argument("--max-url-revisit", type=int, default=2)
    parser.add_argument("--delay-min", type=float, default=1.0)
    parser.add_argument("--delay-max", type=float, default=3.0)
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--headful", action="store_true", help="Tampilkan browser UI.")
    parser.add_argument("--out-jsonl", default="normal_activity_dataset.jsonl")
    parser.add_argument("--out-csv", default="normal_activity_dataset.csv")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument(
        "--respect-robots",
        action="store_true",
        help="Aktifkan pengecekan robots.txt. Untuk lab lokal boleh tidak dipakai.",
    )
    parser.add_argument(
        "--deny-regex",
        default=None,
        help=(
            "Regex untuk menghindari URL/action sensitif. "
            "Default publik menolak admin; default admin membolehkan admin tapi menolak aksi state-changing."
        ),
    )
    parser.add_argument(
        "--login-url",
        default=None,
        help="URL halaman login. Jika kosong dan username diisi, default: <start-url>/login.",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="Username untuk mode authenticated/admin.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password untuk mode authenticated/admin. Untuk menghindari shell history, gunakan --password-env.",
    )
    parser.add_argument(
        "--password-env",
        default=None,
        help="Nama environment variable yang berisi password.",
    )
    parser.add_argument(
        "--admin-start-url",
        default=None,
        help=(
            "URL/path awal setelah login. Bisa absolute URL, path, atau segment relatif. "
            "Jika kosong saat login aktif, default: submissions."
        ),
    )
    parser.add_argument(
        "--admin-mode",
        action="store_true",
        help="Gunakan deny-regex admin walau tidak memakai login.",
    )
    parser.add_argument(
        "--focus-admin-upload",
        action="store_true",
        help="Prioritaskan crawl ke area admin/settings/submissions/upload, tapi tetap fallback ke crawl normal.",
    )
    parser.add_argument(
        "--focus-prob",
        type=float,
        default=0.75,
        help="Probabilitas memilih link admin/upload saat --focus-admin-upload aktif.",
    )
    parser.add_argument(
        "--focus-terms",
        default=None,
        help=(
            "Keyword prioritas untuk mode fokus, dipisahkan koma. "
            "Jika kosong, dipelajari otomatis dari hasil recon (self-learning)."
        ),
    )
    parser.add_argument(
        "--focus-seed-urls",
        default=None,
        help=(
            "URL/path fokus yang bisa dikunjungi ulang. "
            "Jika kosong, dipelajari otomatis dari hasil recon (self-learning)."
        ),
    )
    parser.add_argument(
        "--notes-file",
        default=".crawler_notes.json",
        help="File catatan persisten per-target (visited, upload, learned paths).",
    )
    parser.add_argument(
        "--reset-notes",
        action="store_true",
        help="Hapus catatan lama untuk target ini sebelum mulai.",
    )
    parser.add_argument(
        "--learn",
        dest="learn",
        action="store_true",
        default=None,
        help="Aktifkan recon + self-learning path (default aktif untuk mode admin).",
    )
    parser.add_argument(
        "--no-learn",
        dest="learn",
        action="store_false",
        help="Matikan recon + self-learning.",
    )
    parser.add_argument(
        "--recon-steps",
        type=int,
        default=40,
        help="Anggaran langkah untuk sesi recon awal (session 0). 0 untuk lewati recon.",
    )
    parser.add_argument(
        "--enable-search",
        action="store_true",
        help="Aktifkan simulasi search benign jika ada kotak pencarian.",
    )
    parser.add_argument(
        "--enable-dummy-upload",
        action="store_true",
        help="Aktifkan upload file dummy saat crawler menemukan form/link upload.",
    )
    parser.add_argument(
        "--submit-dummy-upload",
        action="store_true",
        help="Setelah file dummy dipasang, coba klik tombol Upload/submit pada form upload.",
    )
    parser.add_argument(
        "--dummy-upload-file",
        default=None,
        help="Opsional: file dummy khusus yang akan diupload. Jika kosong, crawler membuat dummy sesuai accept.",
    )
    parser.add_argument(
        "--dummy-upload-dir",
        default=".dummy_uploads",
        help="Direktori untuk file dummy otomatis.",
    )
    parser.add_argument(
        "--max-dummy-uploads-per-session",
        type=int,
        default=1,
        help="Batas percobaan upload dummy per sesi.",
    )
    parser.add_argument(
        "--upload-scan-wait-ms",
        type=int,
        default=800,
        help="Waktu tunggu pendek untuk link/form upload async. Set 0 agar tidak menunggu.",
    )
    parser.add_argument(
        "--search-prob",
        type=float,
        default=0.15,
        help="Probabilitas melakukan search pada satu langkah.",
    )
    parser.add_argument(
        "--search-terms",
        default="cybersecurity,ojs,security,article,journal",
        help="Daftar keyword benign dipisahkan koma.",
    )
    parser.add_argument("--seed", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.delay_min < 0 or args.delay_max < args.delay_min:
        print("Error: delay harus valid.", file=sys.stderr)
        return 2

    if args.sessions <= 0 or args.max_steps <= 0:
        print("Error: sessions dan max-steps harus > 0.", file=sys.stderr)
        return 2

    if args.max_dummy_uploads_per_session < 0:
        print("Error: max-dummy-uploads-per-session harus >= 0.", file=sys.stderr)
        return 2

    if args.upload_scan_wait_ms < 0:
        print("Error: upload-scan-wait-ms harus >= 0.", file=sys.stderr)
        return 2

    if not 0 <= args.focus_prob <= 1:
        print("Error: focus-prob harus antara 0 dan 1.", file=sys.stderr)
        return 2

    if args.seed is not None:
        random.seed(args.seed)

    start_url = normalize_url(args.start_url)
    if not start_url:
        print("Error: start-url harus http/https.", file=sys.stderr)
        return 2

    password = args.password
    if args.password_env:
        password = password or os.environ.get(args.password_env)
        if password is None:
            print(f"Error: env var password tidak ditemukan: {args.password_env}", file=sys.stderr)
            return 2

    auth_requested = bool(args.username or password or args.login_url)
    if auth_requested and (not args.username or password is None):
        print(
            "Error: mode login perlu --username dan --password atau --password-env.",
            file=sys.stderr,
        )
        return 2

    admin_mode = (
        args.admin_mode
        or auth_requested
        or bool(args.admin_start_url)
        or args.focus_admin_upload
    )
    deny_regex = args.deny_regex or (
        DEFAULT_ADMIN_DENY_REGEX if admin_mode else DEFAULT_DENY_REGEX
    )
    deny_re = re.compile(deny_regex, re.IGNORECASE)

    login_url = ""
    if auth_requested:
        login_url = normalize_url(args.login_url) if args.login_url else append_path_segment(start_url, "login")
        if not login_url:
            print("Error: login-url harus http/https.", file=sys.stderr)
            return 2

    learn_enabled = args.learn if args.learn is not None else admin_mode

    # Persistent per-target notes ("catatan") for cross-run memory + self-learning.
    notes_path = Path(args.notes_file).expanduser()
    if args.reset_notes and notes_path.exists():
        notes_path.unlink()
    notes = load_notes(notes_path)
    key = site_key(start_url)
    site_notes = notes.get(key) or empty_site_notes()
    notes[key] = site_notes
    for field, default in empty_site_notes().items():
        site_notes.setdefault(field, default)
    learned: Dict[str, Any] = site_notes["learned_paths"]
    upload_pages: Dict[str, Any] = site_notes["upload_pages"]

    # Cold-start focus (used until recon learns paths, or when learning is off).
    explicit_focus_seeds = args.focus_seed_urls or DEFAULT_ADMIN_UPLOAD_FOCUS_URLS
    explicit_focus_terms = args.focus_terms or DEFAULT_ADMIN_UPLOAD_FOCUS_TERMS

    admin_start_values = args.admin_start_url
    if args.focus_admin_upload and not admin_start_values:
        admin_start_values = explicit_focus_seeds
    if auth_requested and not admin_start_values:
        admin_start_values = "submissions"
    admin_start_urls = parse_url_values(admin_start_values, start_url)

    focus_terms = [term.strip().lower() for term in explicit_focus_terms.split(",") if term.strip()]
    focus_seed_urls = parse_url_values(explicit_focus_seeds, start_url)

    # Seed focus from any previously-learned paths so run 1 already benefits.
    if learn_enabled and learned:
        learned_seeds, learned_terms = build_dynamic_focus(learned)
        if args.focus_seed_urls is None and learned_seeds:
            focus_seed_urls = parse_url_values(",".join(learned_seeds), start_url)
        if args.focus_terms is None and learned_terms:
            focus_terms = learned_terms

    explicit_dummy_upload_file = (
        Path(args.dummy_upload_file).expanduser() if args.dummy_upload_file else None
    )
    if explicit_dummy_upload_file is not None and not explicit_dummy_upload_file.is_file():
        print(f"Error: dummy-upload-file tidak ditemukan: {explicit_dummy_upload_file}", file=sys.stderr)
        return 2

    dummy_upload_dir = Path(args.dummy_upload_dir).expanduser()
    search_terms = [x.strip() for x in args.search_terms.split(",") if x.strip()]

    jsonl_path = Path(args.out_jsonl)
    csv_path = Path(args.out_csv)

    # Mulai file baru.
    if jsonl_path.exists():
        jsonl_path.unlink()

    robot_parser = init_robot_parser(start_url, args.user_agent) if args.respect_robots else None

    all_records: List[Dict[str, Any]] = []
    viewport = {"width": 1366, "height": 768}
    viewport_str = f"{viewport['width']}x{viewport['height']}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        shared_context = None
        if not auth_requested:
            shared_context = browser.new_context(
                user_agent=args.user_agent,
                viewport=viewport,
                locale="id-ID",
                timezone_id="Asia/Jakarta",
            )

        # Phase 1: recon. Enumerate the live target, build the learned path map,
        # then derive the focus seeds/terms used by the simulate phase.
        if learn_enabled and args.recon_steps > 0:
            recon_records = run_recon_session(
                browser,
                args=args,
                start_url=start_url,
                login_url=login_url,
                password=password,
                auth_requested=auth_requested,
                recon_seeds=admin_start_urls,
                deny_re=deny_re,
                robot_parser=robot_parser,
                viewport=viewport,
                viewport_str=viewport_str,
                learned=learned,
                visit_counts=site_notes["visited"],
                upload_pages=upload_pages,
                jsonl_path=jsonl_path,
            )
            all_records.extend(recon_records)
            if learned:
                learned_seeds, learned_terms = build_dynamic_focus(learned)
                if args.focus_seed_urls is None and learned_seeds:
                    focus_seed_urls = parse_url_values(",".join(learned_seeds), start_url)
                if args.focus_terms is None and learned_terms:
                    focus_terms = learned_terms
            site_notes["last_seen"] = utc_now()
            save_notes(notes_path, notes)
            print(f"RECON: {len(recon_records)} events, {len(learned)} learned path templates")

        # Phase 2: simulate normal admin/user activity using the learned map.
        for session_index in range(args.sessions):
            session_id = str(uuid.uuid4())
            visit_counts: Dict[str, int] = {}
            dummy_uploads_done = 0
            attempted_upload_pages = set()
            context = shared_context
            if auth_requested:
                context = browser.new_context(
                    user_agent=args.user_agent,
                    viewport=viewport,
                    locale="id-ID",
                    timezone_id="Asia/Jakarta",
                )

            if context is None:
                raise RuntimeError("browser context tidak tersedia")

            page = context.new_page()
            next_step = 0

            if auth_requested:
                ok, status, load_ms, err = perform_login(
                    page,
                    login_url,
                    args.username or "",
                    password or "",
                    args.timeout_ms,
                )
                normalized_after_login = normalize_url(page.url)
                if normalized_after_login:
                    visit_counts[normalized_after_login] = (
                        visit_counts.get(normalized_after_login, 0) + 1
                    )

                candidates = collect_link_candidates(
                    page,
                    page.url,
                    start_url,
                    args.scope_prefix,
                    deny_re,
                    args.max_candidates_per_page,
                )

                record = make_record(
                    session_id=session_id,
                    step=0,
                    event_type="login" if ok else "login_failed",
                    action="submit_login",
                    url_before="",
                    url_after=page.url,
                    http_status=status,
                    load_time_ms=load_ms,
                    page_title_value=page_title(page),
                    candidate_count=len(candidates),
                    chosen_text="[username]",
                    chosen_href=login_url,
                    content_sha256=visible_content_hash(page),
                    viewport=viewport_str,
                    user_agent=args.user_agent,
                    error=err,
                )
                write_jsonl(jsonl_path, record)
                all_records.append(record)
                next_step = 1

                if not ok:
                    page.close()
                    if auth_requested:
                        context.close()
                    continue

                post_login_url = normalized_after_login
                if admin_start_urls and next_step <= args.max_steps:
                    admin_start_url = admin_start_urls[session_index % len(admin_start_urls)]
                    url_before = page.url

                    if (
                        not is_in_scope(admin_start_url, start_url, args.scope_prefix)
                        or is_denied(admin_start_url, "admin_start", deny_re)
                    ):
                        record = make_record(
                            session_id=session_id,
                            step=next_step,
                            event_type="navigation_skipped",
                            action="open_admin_start_url",
                            url_before=url_before,
                            url_after=url_before,
                            http_status="",
                            load_time_ms="",
                            page_title_value=page_title(page),
                            candidate_count=len(candidates),
                            chosen_text="[admin_start]",
                            chosen_href=admin_start_url,
                            content_sha256=visible_content_hash(page),
                            viewport=viewport_str,
                            user_agent=args.user_agent,
                            error="admin_start_out_of_scope_or_denied",
                        )
                        write_jsonl(jsonl_path, record)
                        all_records.append(record)
                    elif not can_fetch(
                        robot_parser,
                        args.user_agent,
                        admin_start_url,
                        args.respect_robots,
                    ):
                        record = make_record(
                            session_id=session_id,
                            step=next_step,
                            event_type="blocked_by_robots",
                            action="open_admin_start_url",
                            url_before=url_before,
                            url_after=url_before,
                            http_status="",
                            load_time_ms="",
                            page_title_value=page_title(page),
                            candidate_count=len(candidates),
                            chosen_text="[admin_start]",
                            chosen_href=admin_start_url,
                            content_sha256=visible_content_hash(page),
                            viewport=viewport_str,
                            user_agent=args.user_agent,
                            error="robots_disallow",
                        )
                        write_jsonl(jsonl_path, record)
                        all_records.append(record)
                    else:
                        status, load_ms, err = goto_url(page, admin_start_url, args.timeout_ms)
                        action = "open_admin_start_url"
                        if page_looks_authorization_denied(page) and post_login_url:
                            fallback_status, fallback_load_ms, fallback_err = goto_url(
                                page,
                                post_login_url,
                                args.timeout_ms,
                            )
                            load_ms += fallback_load_ms
                            status = fallback_status if fallback_status is not None else status
                            action = "open_admin_start_url_fallback"
                            err_parts = [x for x in [err, "authorization_denied", fallback_err] if x]
                            err = ";".join(err_parts)

                        normalized_admin_start = normalize_url(page.url)
                        if normalized_admin_start:
                            visit_counts[normalized_admin_start] = (
                                visit_counts.get(normalized_admin_start, 0) + 1
                            )

                        candidates = collect_link_candidates(
                            page,
                            page.url,
                            start_url,
                            args.scope_prefix,
                            deny_re,
                            args.max_candidates_per_page,
                        )

                        record = make_record(
                            session_id=session_id,
                            step=next_step,
                            event_type="page_view",
                            action=action,
                            url_before=url_before,
                            url_after=page.url,
                            http_status=status,
                            load_time_ms=load_ms,
                            page_title_value=page_title(page),
                            candidate_count=len(candidates),
                            chosen_text="[admin_start]",
                            chosen_href=admin_start_url,
                            content_sha256=visible_content_hash(page),
                            viewport=viewport_str,
                            user_agent=args.user_agent,
                            error=err,
                        )
                        write_jsonl(jsonl_path, record)
                        all_records.append(record)

                    next_step += 1
            else:
                current_url = start_url
                status, load_ms, err = goto_url(page, current_url, args.timeout_ms)
                visit_counts[current_url] = visit_counts.get(current_url, 0) + 1

                candidates = collect_link_candidates(
                    page,
                    current_url,
                    start_url,
                    args.scope_prefix,
                    deny_re,
                    args.max_candidates_per_page,
                )

                record = make_record(
                    session_id=session_id,
                    step=0,
                    event_type="page_view",
                    action="open_start_url",
                    url_before="",
                    url_after=page.url,
                    http_status=status,
                    load_time_ms=load_ms,
                    page_title_value=page_title(page),
                    candidate_count=len(candidates),
                    chosen_text="",
                    chosen_href="",
                    content_sha256=visible_content_hash(page),
                    viewport=viewport_str,
                    user_agent=args.user_agent,
                    error=err,
                )
                write_jsonl(jsonl_path, record)
                all_records.append(record)
                next_step = 1

            restart_url = admin_start_urls[0] if auth_requested and admin_start_urls else start_url

            for step in range(next_step, args.max_steps + 1):
                time.sleep(random.uniform(args.delay_min, args.delay_max))
                url_before = page.url

                page_key = normalize_url(url_before) or url_before
                if (
                    args.enable_dummy_upload
                    and dummy_uploads_done < args.max_dummy_uploads_per_session
                    and page_key not in attempted_upload_pages
                ):
                    # Skip extensions already known to be rejected on this page (from notes).
                    prior = upload_pages.get(page_key, {})
                    skip_exts = set(prior.get("failed_ext", []))
                    upload_results = try_dummy_upload(
                        page,
                        start_url,
                        args.scope_prefix,
                        dummy_upload_dir,
                        explicit_dummy_upload_file,
                        args.submit_dummy_upload,
                        args.timeout_ms,
                        args.upload_scan_wait_ms,
                        skip_exts=skip_exts,
                    )
                    if upload_results:
                        dummy_uploads_done += 1
                        attempted_upload_pages.add(page_key)
                        for upload_result in upload_results:
                            url_after = page.url
                            note_key = upload_result.upload_page or page_key
                            entry = upload_pages.setdefault(
                                note_key, {"accepted_ext": "", "failed_ext": []}
                            )
                            if upload_result.accepted and upload_result.ext:
                                entry["accepted_ext"] = upload_result.ext
                            elif upload_result.event_type == "dummy_upload_attempt" and upload_result.ext:
                                if upload_result.ext not in entry["failed_ext"]:
                                    entry["failed_ext"].append(upload_result.ext)

                            record = make_record(
                                session_id=session_id,
                                step=step,
                                event_type=upload_result.event_type,
                                action=upload_result.action,
                                url_before=url_before,
                                url_after=url_after,
                                http_status=upload_result.http_status,
                                load_time_ms=upload_result.load_time_ms,
                                page_title_value=page_title(page),
                                candidate_count=upload_result.candidate_count,
                                chosen_text=upload_result.chosen_text,
                                chosen_href=upload_result.chosen_href,
                                content_sha256=visible_content_hash(page),
                                viewport=viewport_str,
                                user_agent=args.user_agent,
                                error=upload_result.error,
                            )
                            write_jsonl(jsonl_path, record)
                            all_records.append(record)
                        save_notes(notes_path, notes)
                        continue

                # Search benign sesekali.
                if args.enable_search and search_terms and random.random() < args.search_prob:
                    term = random.choice(search_terms)
                    ok, search_err = try_benign_search(page, term, args.timeout_ms)
                    url_after = page.url
                    candidates = collect_link_candidates(
                        page,
                        url_after,
                        start_url,
                        args.scope_prefix,
                        deny_re,
                        args.max_candidates_per_page,
                    )

                    record = make_record(
                        session_id=session_id,
                        step=step,
                        event_type="search" if ok else "search_skipped",
                        action="benign_search",
                        url_before=url_before,
                        url_after=url_after,
                        http_status="",
                        load_time_ms="",
                        page_title_value=page_title(page),
                        candidate_count=len(candidates),
                        chosen_text=term,
                        chosen_href="",
                        content_sha256=visible_content_hash(page),
                        viewport=viewport_str,
                        user_agent=args.user_agent,
                        error=search_err,
                    )
                    write_jsonl(jsonl_path, record)
                    all_records.append(record)
                    if ok:
                        visit_counts[normalize_url(url_after)] = visit_counts.get(normalize_url(url_after), 0) + 1
                    continue

                candidates = collect_link_candidates(
                    page,
                    url_before,
                    start_url,
                    args.scope_prefix,
                    deny_re,
                    args.max_candidates_per_page,
                )
                chosen = choose_candidate(
                    candidates,
                    visit_counts,
                    args.max_url_revisit,
                    focus_terms if args.focus_admin_upload else None,
                    args.focus_prob if args.focus_admin_upload else 0.0,
                )

                if chosen is None:
                    focus_seed = (
                        choose_focus_seed_url(
                            focus_seed_urls,
                            visit_counts,
                            args.max_url_revisit,
                        )
                        if args.focus_admin_upload
                        else None
                    )
                    if focus_seed:
                        chosen_href = focus_seed
                        chosen_text = "[focus_seed]"
                        action = "open_focus_seed_url"
                    else:
                        # Dead-end: kembali ke start URL sebagai perilaku normal "mulai ulang".
                        chosen_href = restart_url
                        chosen_text = "[restart]"
                        action = "restart_to_start_url"
                else:
                    chosen_href = chosen.href
                    chosen_text = chosen.text or chosen.title or chosen.aria
                    action = "open_internal_link"

                if not can_fetch(robot_parser, args.user_agent, chosen_href, args.respect_robots):
                    record = make_record(
                        session_id=session_id,
                        step=step,
                        event_type="blocked_by_robots",
                        action=action,
                        url_before=url_before,
                        url_after=url_before,
                        http_status="",
                        load_time_ms="",
                        page_title_value=page_title(page),
                        candidate_count=len(candidates),
                        chosen_text=chosen_text,
                        chosen_href=chosen_href,
                        content_sha256=visible_content_hash(page),
                        viewport=viewport_str,
                        user_agent=args.user_agent,
                        error="robots_disallow",
                    )
                    write_jsonl(jsonl_path, record)
                    all_records.append(record)
                    continue

                status, load_ms, err = goto_url(page, chosen_href, args.timeout_ms)
                url_after = page.url
                normalized_after = normalize_url(url_after)
                if normalized_after:
                    visit_counts[normalized_after] = visit_counts.get(normalized_after, 0) + 1

                after_candidates = collect_link_candidates(
                    page,
                    url_after,
                    start_url,
                    args.scope_prefix,
                    deny_re,
                    args.max_candidates_per_page,
                )
                # Keep self-learning during the simulate phase too.
                if learn_enabled:
                    learn_from_candidates(after_candidates, learned, start_url)

                record = make_record(
                    session_id=session_id,
                    step=step,
                    event_type="navigation",
                    action=action,
                    url_before=url_before,
                    url_after=url_after,
                    http_status=status,
                    load_time_ms=load_ms,
                    page_title_value=page_title(page),
                    candidate_count=len(after_candidates),
                    chosen_text=chosen_text,
                    chosen_href=chosen_href,
                    content_sha256=visible_content_hash(page),
                    viewport=viewport_str,
                    user_agent=args.user_agent,
                    error=err,
                )
                write_jsonl(jsonl_path, record)
                all_records.append(record)

            page.close()
            if auth_requested:
                context.close()

            if learn_enabled:
                site_notes["last_seen"] = utc_now()
                save_notes(notes_path, notes)

        if shared_context is not None:
            shared_context.close()
        browser.close()

    if learn_enabled:
        site_notes["last_seen"] = utc_now()
        save_notes(notes_path, notes)

    write_csv(csv_path, all_records)
    print(f"OK: {len(all_records)} records")
    print(f"JSONL: {jsonl_path.resolve()}")
    print(f"CSV  : {csv_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
