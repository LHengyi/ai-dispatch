from __future__ import annotations

import concurrent.futures
import json
import re
import socket
import ssl
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

from dispatch_utils import generate_text, get_section_language, get_max_tokens, load_config, send_email

STATE_PATH = Path(__file__).parent / "website_audit_history.json"
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "website_audit"
USER_AGENT = "AI-Dispatch-Website-Audit/1.0 (+https://github.com)"
MAX_HTML_BYTES = 2_000_000
NON_HTML_EXTENSIONS = {
    ".7z", ".avi", ".bmp", ".css", ".csv", ".doc", ".docx", ".gif", ".gz", ".ico",
    ".jpeg", ".jpg", ".js", ".json", ".m4a", ".mov", ".mp3", ".mp4", ".pdf", ".png",
    ".ppt", ".pptx", ".rar", ".svg", ".tar", ".tgz", ".txt", ".webm", ".webp", ".woff",
    ".woff2", ".xls", ".xlsx", ".xml", ".zip",
}
FALLBACK_TO_GET_STATUS_CODES = {401, 403, 405, 429, 500, 501, 502, 503}
ISSUE_PRIORITY = {
    "not_found": 0,
    "unreachable": 1,
    "server_error": 2,
    "http_error": 3,
    "access_blocked": 4,
    "rate_limited": 5,
    "request_error": 6,
}


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        for key, value in attrs:
            if key.lower() == "href" and value:
                self._current_href = value
                self._current_text = []
                break

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            stripped = " ".join(data.split())
            if stripped:
                self._current_text.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return

        anchor_text = " ".join(self._current_text).strip()
        self.links.append((self._current_href, anchor_text))
        self._current_href = None
        self._current_text = []

def save_state(reports: list[dict]) -> None:
    sites = [
        {
            "name": report["target_name"],
            "start_url": report["start_url"],
            "issue_count": report["issue_count"],
        }
        for report in reports
    ]
    STATE_PATH.write_text(
        json.dumps(
            {
                "last_sent_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "last_site_count": len(reports),
                "last_total_issue_count": sum(report["issue_count"] for report in reports),
                "sites": sites,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "site"


def normalize_target_entry(target: str | dict, index: int) -> dict:
    if isinstance(target, str):
        target_cfg = {"start_url": target}
    elif isinstance(target, dict):
        target_cfg = dict(target)
    else:
        raise ValueError(f"website_audit.targets[{index}] must be a URL string or mapping")

    start_url = str(target_cfg.get("start_url", "")).strip()
    if not start_url:
        raise ValueError(f"website_audit.targets[{index}].start_url is required")

    parsed = urlparse(start_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"website_audit.targets[{index}].start_url must begin with http:// or https://")

    name = str(target_cfg.get("name", "")).strip() or canonical_host(start_url) or f"site-{index}"
    target_cfg["name"] = name
    target_cfg["start_url"] = start_url
    return target_cfg


def resolve_targets(cfg: dict) -> list[dict]:
    audit_cfg = cfg.get("website_audit") or {}
    targets = audit_cfg.get("targets") or []

    if targets:
        if not isinstance(targets, list):
            raise ValueError("website_audit.targets must be a list")
        return [normalize_target_entry(target, index) for index, target in enumerate(targets, start=1)]

    start_url = str(audit_cfg.get("start_url", "")).strip()
    if not start_url:
        raise ValueError("website_audit.start_url or website_audit.targets is required")

    return [
        normalize_target_entry(
            {
                "name": audit_cfg.get("name"),
                "start_url": start_url,
            },
            1,
        )
    ]


def build_effective_audit_cfg(cfg: dict, target_cfg: dict) -> dict:
    audit_cfg = dict(cfg.get("website_audit") or {})
    audit_cfg.pop("targets", None)
    audit_cfg.update(target_cfg)
    return audit_cfg


def canonical_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_internal_url(url: str, root_host: str) -> bool:
    return canonical_host(url) == root_host


def normalize_link(base_url: str, href: str) -> str | None:
    raw = href.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None

    absolute = urljoin(base_url, raw)
    normalized, _ = urldefrag(absolute)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        return None
    return normalized


def should_crawl_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in NON_HTML_EXTENSIONS)


def decode_html(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset" and value.strip():
            charset = value.strip()
            break

    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def classify_issue(status_code: int | None, error_type: str | None) -> str:
    if status_code in {404, 410}:
        return "not_found"
    if status_code in {401, 403}:
        return "access_blocked"
    if status_code == 429:
        return "rate_limited"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if error_type in {"dns_error", "ssl_error", "timeout"}:
        return "unreachable"
    if status_code is not None and status_code >= 400:
        return "http_error"
    return "request_error"


def describe_exception(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ssl.SSLError):
        return "ssl_error", str(exc)
    if isinstance(exc, TimeoutError | socket.timeout):
        return "timeout", str(exc)
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return "ssl_error", str(reason)
        if isinstance(reason, TimeoutError | socket.timeout):
            return "timeout", str(reason)
        if isinstance(reason, socket.gaierror):
            return "dns_error", str(reason)
        return "url_error", str(reason)
    if isinstance(exc, OSError) and "timed out" in str(exc).lower():
        return "timeout", str(exc)
    return "request_error", str(exc)


def request_url(
    url: str,
    *,
    timeout_seconds: int,
    method: str = "GET",
    read_body: bool = False,
) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT}, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_HTML_BYTES) if read_body else b""
            return {
                "ok": True,
                "status_code": getattr(response, "status", 200),
                "final_url": response.geturl(),
                "content_type": content_type,
                "body": body,
                "error_type": None,
                "error_detail": None,
                "checked_method": method,
            }
    except HTTPError as exc:
        body = exc.read(MAX_HTML_BYTES) if read_body else b""
        return {
            "ok": False,
            "status_code": exc.code,
            "final_url": exc.geturl(),
            "content_type": exc.headers.get("Content-Type", ""),
            "body": body,
            "error_type": "http_error",
            "error_detail": str(exc),
            "checked_method": method,
        }
    except Exception as exc:  # pragma: no cover - network variability
        error_type, detail = describe_exception(exc)
        return {
            "ok": False,
            "status_code": None,
            "final_url": None,
            "content_type": "",
            "body": b"",
            "error_type": error_type,
            "error_detail": detail,
            "checked_method": method,
        }


def fetch_page(url: str, timeout_seconds: int) -> dict:
    result = request_url(url, timeout_seconds=timeout_seconds, method="GET", read_body=True)
    result["is_html"] = "text/html" in (result.get("content_type") or "").lower()
    if result["ok"] and result["is_html"]:
        result["html"] = decode_html(result["body"], result["content_type"])
    else:
        result["html"] = ""
    return result


def probe_link(url: str, timeout_seconds: int) -> dict:
    head_result = request_url(url, timeout_seconds=timeout_seconds, method="HEAD", read_body=False)
    if head_result["ok"]:
        return head_result

    if head_result["status_code"] in {404, 410}:
        return head_result

    if head_result["status_code"] in FALLBACK_TO_GET_STATUS_CODES or head_result["status_code"] is None:
        return request_url(url, timeout_seconds=timeout_seconds, method="GET", read_body=False)

    return head_result


def build_page_fetch_summary(url: str, result: dict) -> dict:
    issue_kind = classify_issue(result.get("status_code"), result.get("error_type")) if not result["ok"] else None
    return {
        "url": url,
        "final_url": result.get("final_url"),
        "ok": result["ok"],
        "status_code": result.get("status_code"),
        "content_type": result.get("content_type"),
        "checked_method": "GET",
        "error_type": result.get("error_type"),
        "error_detail": result.get("error_detail"),
        "issue_kind": issue_kind,
    }


def audit_site(cfg: dict, target_cfg: dict | None = None) -> dict:
    audit_cfg = build_effective_audit_cfg(cfg, target_cfg or {})
    start_url = str(audit_cfg.get("start_url", "")).strip()
    if not start_url:
        raise ValueError("website_audit.start_url is required")

    parsed_start = urlparse(start_url)
    if parsed_start.scheme not in {"http", "https"}:
        raise ValueError("website_audit.start_url must begin with http:// or https://")

    root_host = canonical_host(start_url)
    target_name = str(audit_cfg.get("name", "")).strip() or root_host
    timeout_seconds = max(1, int(audit_cfg.get("request_timeout_seconds", 15)))
    max_pages = max(1, int(audit_cfg.get("max_pages", 25)))
    max_links_per_page = max(1, int(audit_cfg.get("max_links_per_page", 150)))
    max_workers = max(1, int(audit_cfg.get("max_workers", 8)))
    check_external_links = bool(audit_cfg.get("check_external_links", True))

    queue = deque([start_url])
    enqueued = {start_url}
    visited_pages: set[str] = set()
    link_occurrences: dict[str, list[dict]] = defaultdict(list)
    page_status_cache: dict[str, dict] = {}
    page_fetches: list[dict] = []

    while queue and len(visited_pages) < max_pages:
        page_url = queue.popleft()
        if page_url in visited_pages:
            continue

        visited_pages.add(page_url)
        result = fetch_page(page_url, timeout_seconds)
        page_status_cache[page_url] = build_page_fetch_summary(page_url, result)
        page_fetches.append(page_status_cache[page_url])

        if not result["ok"] or not result["is_html"]:
            continue

        parser = AnchorParser()
        parser.feed(result["html"])

        current_url = result.get("final_url") or page_url
        for href, anchor_text in parser.links[:max_links_per_page]:
            normalized = normalize_link(current_url, href)
            if not normalized:
                continue

            internal = is_internal_url(normalized, root_host)
            if not internal and not check_external_links:
                continue

            link_occurrences[normalized].append(
                {
                    "source_url": page_url,
                    "anchor_text": anchor_text[:120],
                    "internal": internal,
                }
            )

            if internal and normalized not in visited_pages and normalized not in enqueued and should_crawl_url(normalized):
                queue.append(normalized)
                enqueued.add(normalized)

    prefetched_links = dict(page_status_cache)
    urls_to_probe = [url for url in link_occurrences if url not in prefetched_links]
    checked_links: dict[str, dict] = {}

    if urls_to_probe:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(probe_link, url, timeout_seconds): url
                for url in urls_to_probe
            }
            for future in concurrent.futures.as_completed(future_map):
                url = future_map[future]
                checked_links[url] = future.result()

    findings = []
    for url, occurrences in link_occurrences.items():
        first_occurrence = occurrences[0]
        internal = bool(first_occurrence["internal"])
        raw_result = prefetched_links.get(url) or checked_links.get(url)
        if not raw_result:
            continue

        ok = raw_result["ok"]
        status_code = raw_result.get("status_code")
        error_type = raw_result.get("error_type")
        issue_kind = None if ok else classify_issue(status_code, error_type)
        source_pages = list(dict.fromkeys(item["source_url"] for item in occurrences))[:5]
        anchor_samples = list(
            dict.fromkeys(item["anchor_text"] for item in occurrences if item["anchor_text"])
        )[:3]

        findings.append(
            {
                "url": url,
                "internal": internal,
                "ok": ok,
                "status_code": status_code,
                "final_url": raw_result.get("final_url"),
                "content_type": raw_result.get("content_type"),
                "checked_method": raw_result.get("checked_method", "HEAD"),
                "error_type": error_type,
                "error_detail": raw_result.get("error_detail"),
                "issue_kind": issue_kind,
                "source_count": len(occurrences),
                "source_pages": source_pages,
                "anchor_samples": anchor_samples,
            }
        )

    findings.sort(
        key=lambda item: (
            item["ok"],
            0 if item["internal"] else 1,
            ISSUE_PRIORITY.get(item["issue_kind"] or "request_error", 99),
            -item["source_count"],
            item["url"],
        )
    )

    issue_findings = [item for item in findings if not item["ok"]]
    internal_issues = sum(1 for item in issue_findings if item["internal"])
    external_issues = len(issue_findings) - internal_issues
    issue_pages = len({page for item in issue_findings for page in item["source_pages"]})

    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "target_name": target_name,
        "start_url": start_url,
        "root_host": root_host,
        "pages_crawled": len(visited_pages),
        "page_fetches": page_fetches,
        "unique_links_checked": len(findings),
        "issue_count": len(issue_findings),
        "internal_issue_count": internal_issues,
        "external_issue_count": external_issues,
        "pages_with_issues": issue_pages,
        "check_external_links": check_external_links,
        "max_pages": max_pages,
        "findings": findings,
    }


def findings_for_prompt(report: dict, limit: int) -> str:
    issue_findings = [item for item in report["findings"] if not item["ok"]][:limit]
    if not issue_findings:
        return "没有发现非 2xx/3xx 的链接问题。"

    lines = []
    for index, item in enumerate(issue_findings, start=1):
        scope = "INTERNAL" if item["internal"] else "EXTERNAL"
        status = item["status_code"] if item["status_code"] is not None else "n/a"
        sources = "; ".join(item["source_pages"]) or "n/a"
        anchors = "; ".join(item["anchor_samples"]) or "n/a"
        detail = item["error_detail"] or item["final_url"] or "n/a"
        lines.append(
            f"{index}. [{scope}] {item['issue_kind']} | status={status} | url={item['url']} | "
            f"occurrences={item['source_count']} | sources={sources} | anchors={anchors} | detail={detail}"
        )
    return "\n".join(lines)


def build_llm_prompt(report: dict, cfg: dict, target_cfg: dict | None = None) -> str:
    audit_cfg = build_effective_audit_cfg(cfg, target_cfg or {})
    language = str(audit_cfg.get("output_language") or get_section_language(cfg, section_name="website_audit"))
    prompt_limit = max(1, int(audit_cfg.get("max_findings_in_prompt", 25)))
    findings_text = findings_for_prompt(report, prompt_limit)

    return f"""你是 AI Dispatch 的网站质量审计编辑。你会收到一个由程序抓取和探测得到的网站链接审计结果。
请你基于这些结构化事实，写一封发给网站负责人的 HTML 邮件摘要。不要虚构未给出的页面、链接或错误。

要求：
1. 所有输出使用 {language}。
2. 严格输出 HTML 片段，不要加 markdown 代码块，不要加 ```html。
3. 重点优先级：内部链接问题 > 外部链接问题。
4. 对 403 / 429 这类结果要谨慎措辞，说明它们可能是权限或反爬限制，不一定是用户可见的坏链。
5. 如果没有发现问题，也要给出简短结论，并明确说明这是静态 HTTP 抓取，可能遗漏 JavaScript 动态渲染出的链接。

审计元数据：
- 审计时间：{report['generated_at_utc']}
- 站点名称：{report['target_name']}
- 起始页面：{report['start_url']}
- 站点主机：{report['root_host']}
- 实际抓取页面数：{report['pages_crawled']} / 上限 {report['max_pages']}
- 唯一链接检查数：{report['unique_links_checked']}
- 问题链接数：{report['issue_count']}
- 内部问题：{report['internal_issue_count']}
- 外部问题：{report['external_issue_count']}
- 涉及页面数：{report['pages_with_issues']}
- 是否检查外链：{report['check_external_links']}

问题样本：
{findings_text}

请按这个 HTML 结构输出：

<h2>🔎 Website Audit · 日期</h2>
<p class="intro">一句话总结整体状态和范围</p>

<div class="section-title">Executive Summary</div>
<p>...</p>

<div class="section-title">Highest Priority Findings</div>
<div class="issue">
  <h3>问题标题</h3>
  <span class="meta">内部/外部 · 状态码/错误类型 · 受影响次数</span>
  <p><strong>What happened:</strong> ...</p>
  <p><strong>Why it matters:</strong> ...</p>
  <p><strong>Where to look:</strong> ...</p>
</div>

<div class="section-title">Patterns & Risks</div>
<div class="pattern">
  <h3>模式名称</h3>
  <p>...</p>
</div>

<div class="section-title">Recommended Fixes</div>
<ol>
  <li>...</li>
</ol>

<div class="section-title">Audit Metadata</div>
<ul>
  <li>...</li>
</ul>
"""


AUDIT_EMAIL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: linear-gradient(180deg, #eef5ff 0%, #f9fbff 100%);
       margin: 0; padding: 20px; color: #1f2937; }
.wrapper { max-width: 760px; margin: auto; background: #fff;
           border-radius: 14px; overflow: hidden;
           box-shadow: 0 6px 24px rgba(15, 23, 42, 0.08); }
.header { background: #0b3b66; color: #fff; padding: 28px 36px; }
.header h1 { margin: 0; font-size: 22px; letter-spacing: -.2px; }
.body { padding: 28px 36px; }
h2 { color: #0b3b66; margin-top: 0; font-size: 22px; }
.intro { color: #536173; font-size: 13px; margin-bottom: 28px; }
.section-title { font-weight: 700; font-size: 11px; text-transform: uppercase;
                 letter-spacing: .1em; color: #7c8aa5; margin: 32px 0 14px;
                 padding-bottom: 6px; border-bottom: 1px solid #e5edf6; }
.issue { border-left: 3px solid #d97706; padding: 14px 18px;
         margin-bottom: 18px; background: #fff7ed; border-radius: 0 8px 8px 0; }
.issue h3 { margin: 0 0 4px; font-size: 15px; line-height: 1.4; color: #9a3412; }
.meta { font-size: 11px; color: #9aa5b1; display: block; margin-bottom: 8px; }
.issue p { margin: 6px 0 0; font-size: 14px; line-height: 1.7; color: #374151; }
.pattern { border-left: 3px solid #2563eb; padding: 14px 18px;
           margin-bottom: 18px; background: #eff6ff; border-radius: 0 8px 8px 0; }
.pattern h3 { margin: 0 0 8px; font-size: 15px; color: #1d4ed8; }
.pattern p { margin: 0; font-size: 14px; line-height: 1.7; color: #374151; }
ol, ul { margin: 8px 0; padding-left: 22px; color: #374151; }
li { margin-bottom: 8px; line-height: 1.7; font-size: 14px; }
a { color: #0b5db1; }
.footer { padding: 16px 36px; font-size: 12px; color: #94a3b8;
          border-top: 1px solid #e5edf6; text-align: center; }
"""


def write_artifacts(report: dict, email_html: str, index: int) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    site_dir = ARTIFACT_DIR / f"{index:02d}_{slugify(report['target_name'])}"
    site_dir.mkdir(parents=True, exist_ok=True)

    (site_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (site_dir / "report_email.html").write_text(email_html, encoding="utf-8")

    issue_lines = [
        f"- {'INTERNAL' if item['internal'] else 'EXTERNAL'} | {item['issue_kind']} | "
        f"{item['status_code'] or 'n/a'} | {item['url']}"
        for item in report["findings"]
        if not item["ok"]
    ]
    summary_md = "\n".join(
        [
            "# Website Audit",
            "",
            f"- Site name: {report['target_name']}",
            f"- Target: {report['start_url']}",
            f"- Generated: {report['generated_at_utc']}",
            f"- Pages crawled: {report['pages_crawled']}",
            f"- Unique links checked: {report['unique_links_checked']}",
            f"- Issues found: {report['issue_count']}",
            "",
            "## Issues",
            *(issue_lines or ["- No issues found"]),
        ]
    )
    (site_dir / "summary.md").write_text(summary_md, encoding="utf-8")
    return site_dir


def send_audit_email(report: dict, html_body: str) -> None:
    today = datetime.now().strftime("%m/%d")
    subject = f"🔎 Website Audit · {report['target_name']} · {today}"
    send_email(
        subject,
        html_body,
        header_title="🔎 Website Audit",
        footer_text="AI Dispatch · Static crawl + LLM analysis · GitHub Actions delivery",
        css=AUDIT_EMAIL_CSS,
    )


def write_run_summary(reports: list[dict]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Website Audit Run",
        "",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Sites audited: {len(reports)}",
        f"- Total issues: {sum(report['issue_count'] for report in reports)}",
        "",
    ]

    for report in reports:
        lines.extend(
            [
                f"## {report['target_name']}",
                f"- Target: {report['start_url']}",
                f"- Pages crawled: {report['pages_crawled']}",
                f"- Unique links checked: {report['unique_links_checked']}",
                f"- Issues found: {report['issue_count']}",
                "",
            ]
        )

    (ARTIFACT_DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    cfg = load_config()
    targets = resolve_targets(cfg)
    reports = []

    for index, target_cfg in enumerate(targets, start=1):
        report = audit_site(cfg, target_cfg)
        reports.append(report)

        issue_count = report["issue_count"]
        print(f"[{index}/{len(targets)}] Auditing {report['target_name']} ({report['start_url']})")
        print(f"Crawled {report['pages_crawled']} pages and checked {report['unique_links_checked']} links")
        print(f"Found {issue_count} link issues ({report['internal_issue_count']} internal / {report['external_issue_count']} external)")

        prompt = build_llm_prompt(report, cfg, target_cfg)
        html_body = generate_text(
            prompt,
            cfg,
            section_name="website_audit",
            max_tokens=max(1, int(target_cfg.get("max_tokens", get_max_tokens(cfg, section_name="website_audit", default=3500)))),
        )

        write_artifacts(report, html_body, index)
        send_audit_email(report, html_body)

    write_run_summary(reports)
    save_state(reports)
    print(f"Website audit reports sent successfully for {len(reports)} site(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
