from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).parent

DEFAULT_EMAIL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f0f0f5; margin: 0; padding: 20px; color: #222; }
.wrapper { max-width: 700px; margin: auto; background: #fff;
           border-radius: 10px; overflow: hidden;
           box-shadow: 0 2px 12px rgba(0,0,0,.10); }
.header { background: #0f0f1a; color: #fff; padding: 28px 36px; }
.header h1 { margin: 0; font-size: 22px; letter-spacing: -.3px; }
.body { padding: 28px 36px; }
.footer { padding: 16px 36px; font-size: 12px; color: #bbb;
          border-top: 1px solid #eee; text-align: center; }
"""

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "deepseek": "deepseek-v4-flash",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-5.5",
}


def load_config() -> dict[str, Any]:
    path = BASE_DIR / "config.yml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_provider(cfg: dict[str, Any]) -> str:
    provider = str(cfg.get("provider", "anthropic")).strip().lower()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"Unsupported provider: {provider}")
    return provider


def get_provider_model(cfg: dict[str, Any], section_name: str | None = None) -> str:
    provider = get_provider(cfg)
    model_key = f"{provider}_model"

    if section_name:
        section = cfg.get(section_name) or {}
        if section.get(model_key):
            return str(section[model_key])

    digest = cfg.get("digest") or {}
    if digest.get(model_key):
        return str(digest[model_key])

    return DEFAULT_MODELS[provider]


def get_section_language(cfg: dict[str, Any], section_name: str | None = None) -> str:
    if section_name:
        section = cfg.get(section_name) or {}
        if section.get("output_language"):
            return str(section["output_language"])

    digest = cfg.get("digest") or {}
    if digest.get("output_language"):
        return str(digest["output_language"])

    return "English"


def get_max_tokens(cfg: dict[str, Any], section_name: str | None = None, default: int = 6000) -> int:
    if section_name:
        section = cfg.get(section_name) or {}
        if section.get("max_tokens") is not None:
            return int(section["max_tokens"])

    digest = cfg.get("digest") or {}
    if digest.get("max_tokens") is not None:
        return int(digest["max_tokens"])

    return default


def generate_text(
    prompt: str,
    cfg: dict[str, Any],
    section_name: str | None = None,
    max_tokens: int | None = None,
) -> str:
    provider = get_provider(cfg)
    model = get_provider_model(cfg, section_name=section_name)
    token_limit = max_tokens if max_tokens is not None else get_max_tokens(cfg, section_name)

    if provider == "gemini":
        from google import genai as google_genai
        from google.genai import types as genai_types

        client = google_genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=token_limit,
            ),
        )
        text = response.text
        if not text:
            finish = response.candidates[0].finish_reason if response.candidates else "no candidates"
            raise RuntimeError(f"Gemini returned empty response (finish_reason={finish})")
        return text

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=token_limit,
        )
        text = getattr(response, "output_text", None)
        if text:
            return text

        chunks = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "output_text" and getattr(content, "text", None):
                    chunks.append(content.text)
        if chunks:
            return "\n".join(chunks)
        raise RuntimeError("OpenAI returned empty response")

    if provider == "deepseek":
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=token_limit,
        )
        text = response.choices[0].message.content
        if isinstance(text, str) and text.strip():
            return text
        raise RuntimeError("DeepSeek returned empty response")

    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=token_limit,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def send_email(
    subject: str,
    html_body: str,
    *,
    header_title: str,
    footer_text: str,
    css: str = DEFAULT_EMAIL_CSS,
) -> None:
    full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head>
<body>
<div class="wrapper">
  <div class="header"><h1>{header_title}</h1></div>
  <div class="body">{html_body}</div>
  <div class="footer">{footer_text}</div>
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["GMAIL_USER"]
    msg["To"] = os.environ["RECIPIENT_EMAIL"]
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"])
        server.sendmail(os.environ["GMAIL_USER"], os.environ["RECIPIENT_EMAIL"], msg.as_string())
