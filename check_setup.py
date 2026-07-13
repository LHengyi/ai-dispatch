"""
验证所有配置是否就绪，完成后发送一封测试邮件。
在 GitHub Actions 中运行：Actions → ✅ Check Setup → Run workflow
"""
import os
import smtplib
import sys
import yaml
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from urllib.parse import urlparse

from dispatch_utils import get_provider_model

OK = "✅"
FAIL = "❌"
errors = []


def extract_audit_targets(audit_cfg: dict) -> list[tuple[str, str]]:
    targets = audit_cfg.get("targets") or []
    if targets:
        if not isinstance(targets, list):
            return [("invalid", "website_audit.targets 不是列表")]

        normalized = []
        for index, target in enumerate(targets, start=1):
            if isinstance(target, str):
                normalized.append((f"targets[{index}]", target.strip()))
            elif isinstance(target, dict):
                normalized.append((f"targets[{index}]", str(target.get("start_url", "")).strip()))
            else:
                normalized.append((f"targets[{index}]", ""))
        return normalized

    return [("start_url", str(audit_cfg.get("start_url", "")).strip())]


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = OK if ok else FAIL
    line = f"  {status}  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    if not ok:
        errors.append(label)
    return ok


def section(title: str) -> None:
    print(f"\n── {title} {'─' * (50 - len(title))}")


# ── 0. 读取 config.yml（后续检查需要 provider 信息）──
config_path = Path(__file__).parent / "config.yml"
_cfg_raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
provider = str(_cfg_raw.get("provider", "anthropic")).strip().lower()

# ── 1. 环境变量 ──────────────────────────────────
section("GitHub Secrets")
provider_key_names = {
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
llm_key_name = provider_key_names.get(provider, "ANTHROPIC_API_KEY")
required_secrets = {
    llm_key_name:        os.getenv(llm_key_name),
    "GMAIL_USER":        os.getenv("GMAIL_USER"),
    "GMAIL_APP_PASSWORD": os.getenv("GMAIL_APP_PASSWORD"),
    "RECIPIENT_EMAIL":   os.getenv("RECIPIENT_EMAIL"),
}
for name, value in required_secrets.items():
    check(name, bool(value), "已设置" if value else "未找到，请在 Settings → Secrets 中添加")

# ── 2. config.yml ────────────────────────────────
section("config.yml")
cfg = None
if check("config.yml 存在", config_path.exists()):
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        check("YAML 格式正确", True)
        check("topics 已配置", bool(cfg.get("topics")),
              f"{len(cfg.get('topics', []))} 个主题")
        check("news_feeds 已配置", bool(cfg.get("news_feeds")),
              f"{len(cfg.get('news_feeds', {}))} 个来源")
        check("blog_feeds 已配置", bool(cfg.get("blog_feeds")),
              f"{len(cfg.get('blog_feeds', {}))} 个博客")
        classics = cfg.get("classics") or []
        check("classics 已配置", True,
              f"{len(classics)} 篇（0 篇也可以，此项可选）")
        audit_cfg = cfg.get("website_audit") or {}
        audit_enabled = bool(audit_cfg.get("enabled", False))
        check("website_audit 已配置", True,
              "已启用" if audit_enabled else "已禁用（可选功能）")
        if audit_enabled:
            targets = extract_audit_targets(audit_cfg)
            valid_targets = [target for target in targets if target[0] != "invalid"]
            check("website_audit.targets", bool(valid_targets),
                  f"{len(valid_targets)} 个网站" if valid_targets else "未设置")
            for label, start_url in valid_targets:
                parsed = urlparse(start_url)
                check(f"website_audit.{label}", bool(start_url) and parsed.scheme in {"http", "https"},
                      start_url or "未设置")
            follow_internal_links = audit_cfg.get("follow_internal_links", True)
            follow_detail = (
                "继续抓站内页" if follow_internal_links is True
                else "仅检查起始页" if follow_internal_links is False
                else str(follow_internal_links)
            )
            check("website_audit.follow_internal_links", isinstance(follow_internal_links, bool),
                  follow_detail)
    except Exception as e:
        check("YAML 格式正确", False, str(e))

# ── 3. LLM API ───────────────────────────────────
section(f"LLM API ({provider})")
api_key = os.getenv(llm_key_name)
default_models = {
    "deepseek": "deepseek-v4-flash",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-4-6",
}
model = get_provider_model(cfg) if cfg else default_models.get(provider, "claude-sonnet-4-6")
if api_key:
    try:
        if provider == "gemini":
            from google import genai as google_genai
            client = google_genai.Client(api_key=api_key)
            client.models.generate_content(model=model, contents="hi")
        elif provider == "deepseek":
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=16,
            )
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.responses.create(model=model, input="hi")
        else:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=api_key)
            client.messages.create(
                model=model, max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
        check(f"API 连接成功 ({model})", True)
    except Exception as e:
        check("API 连接", False, str(e))
else:
    check(f"API 连接（跳过，{llm_key_name} 未设置）", False)

# ── 4. Gmail SMTP ────────────────────────────────
section("Gmail SMTP")
gmail_user = os.getenv("GMAIL_USER")
gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
if gmail_user and gmail_pass:
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
        check("Gmail 登录成功", True, gmail_user)
    except smtplib.SMTPAuthenticationError:
        check("Gmail 登录", False,
              "认证失败——请确认已开启两步验证并使用「应用专用密码」而非账号密码")
    except Exception as e:
        check("Gmail 登录", False, str(e))
else:
    check("Gmail 登录（跳过，凭据未设置）", False)

# ── 5. 发送测试邮件 ──────────────────────────────
section("测试邮件")
recipient = os.getenv("RECIPIENT_EMAIL")
all_ok = not errors

if all_ok and gmail_user and gmail_pass and recipient:
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:20px">
<h2 style="color:#1a1a2e">✅ AI Dispatch — 配置验证成功</h2>
<p>你的环境已就绪，每周简报将按时自动发送到这个邮箱。</p>
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <tr><td style="padding:8px;color:#666">验证时间</td><td>{now}</td></tr>
  <tr><td style="padding:8px;color:#666">发件账号</td><td>{gmail_user}</td></tr>
  <tr><td style="padding:8px;color:#666">收件邮箱</td><td>{recipient}</td></tr>
  <tr><td style="padding:8px;color:#666">新闻来源</td>
      <td>{len(cfg.get('news_feeds', {}))} 个</td></tr>
  <tr><td style="padding:8px;color:#666">博客订阅</td>
      <td>{len(cfg.get('blog_feeds', {}))} 个</td></tr>
  <tr><td style="padding:8px;color:#666">经典收藏</td>
      <td>{len(cfg.get('classics') or [])} 篇</td></tr>
  <tr><td style="padding:8px;color:#666">使用模型</td>
      <td>{model}</td></tr>
  <tr><td style="padding:8px;color:#666">网站巡检</td>
      <td>{"启用（继续抓站内页）" if ((cfg.get('website_audit') or {}).get('enabled') and (cfg.get('website_audit') or {}).get('follow_internal_links', True)) else ("启用（仅检查起始页）" if (cfg.get('website_audit') or {}).get('enabled') else "未启用")}</td></tr>
</table>
<p style="margin-top:24px;color:#888;font-size:12px">
  AI Dispatch · 新闻周报固定为 UTC 周一 00:07 / 北京时间周一 08:07；网站巡检固定为 UTC 周一 02:07 / 北京时间周一 10:07
</p>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "✅ AI Dispatch 配置验证成功"
        msg["From"] = gmail_user
        msg["To"] = recipient
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, recipient, msg.as_string())

        check("测试邮件已发送", True, f"请检查 {recipient}")
    except Exception as e:
        check("发送测试邮件", False, str(e))
else:
    if not all_ok:
        print(f"  ⚠️  存在配置错误，跳过发送测试邮件")
    else:
        print(f"  ⚠️  邮件凭据不完整，跳过发送")

# ── 结果汇总 ─────────────────────────────────────
print("\n" + "═" * 54)
if not errors:
    print("  🎉  所有检查通过！查收测试邮件后即可等待每周简报。")
else:
    print(f"  ❌  {len(errors)} 项需要修复：")
    for e in errors:
        print(f"       · {e}")
    print("\n  参考 README.md 完成配置后重新运行此检查。")
print("═" * 54)

sys.exit(0 if not errors else 1)
