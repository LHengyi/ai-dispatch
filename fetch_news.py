import feedparser
import anthropic
import smtplib
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

RSS_FEEDS = {
    "OpenAI Blog":        "https://openai.com/blog/rss.xml",
    "Google DeepMind":    "https://deepmind.google/discover/blog/rss.xml",
    "Hugging Face":       "https://huggingface.co/blog/feed.xml",
    "The Verge AI":       "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "MIT Tech Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "VentureBeat AI":     "https://venturebeat.com/category/ai/feed/",
    "TechCrunch AI":      "https://techcrunch.com/category/artificial-intelligence/feed/",
    "arxiv cs.AI":        "https://rss.arxiv.org/rss/cs.AI",
    "arxiv cs.RO":        "https://rss.arxiv.org/rss/cs.RO",
}

# 研究员博客 / Substack — 更新频率低但质量高，窗口放宽到 72 小时
BLOG_FEEDS = {
    "Interconnects (Nathan Lambert)":   "https://www.interconnects.ai/feed",
    "Ahead of AI (Sebastian Raschka)":  "https://magazine.sebastianraschka.com/feed",
    "Import AI (Jack Clark)":           "https://importai.substack.com/feed",
    "The Gradient":                     "https://thegradient.pub/rss/",
    "One Useful Thing (Ethan Mollick)": "https://www.oneusefulthing.org/feed",
    "AI Snake Oil":                     "https://aisnakeoil.substack.com/feed",
    "Lilian Weng":                      "https://lilianweng.github.io/index.xml",
    "Last Week in AI":                  "https://lastweekin.ai/feed",
}

KEYWORDS = [
    "robot", "robotics", "humanoid", "manipulation", "embodied",
    "agent", "agentic", "multi-agent", "autonomous",
    "llm", "language model", "gpt", "gemini", "claude", "qwen", "deepseek",
    "multimodal", "vision", "foundation model", "reasoning",
]


def fetch_recent_articles(hours: int = 24) -> list[dict]:
    return _fetch_feeds(RSS_FEEDS, hours=hours, per_source=50)


def fetch_recent_blogs(hours: int = 72) -> list[dict]:
    return _fetch_feeds(BLOG_FEEDS, hours=hours, per_source=5)


def _fetch_feeds(feeds: dict, hours: int, per_source: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []

    for source, url in feeds.items():
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
            for entry in feed.entries[:per_source]:
                published = None
                for attr in ("published_parsed", "updated_parsed"):
                    t = getattr(entry, attr, None)
                    if t:
                        published = datetime(*t[:6], tzinfo=timezone.utc)
                        break

                if published and published < cutoff:
                    continue

                title = entry.get("title", "")
                summary = entry.get("summary", "")
                text = (title + " " + summary).lower()

                # arxiv: only keep robotics/agent-related papers
                if source.startswith("arxiv") and not any(kw in text for kw in KEYWORDS):
                    continue

                articles.append({
                    "source": source,
                    "title": title,
                    "url": entry.get("link", ""),
                    "summary": summary[:1000] if summary else "",
                    "published": published.strftime("%Y-%m-%d %H:%M UTC") if published else "Unknown",
                })
        except Exception as e:
            print(f"[WARN] {source}: {e}", file=sys.stderr)

    return articles


def summarize_with_claude(articles: list[dict], blogs: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    articles_text = "\n\n---\n\n".join(
        f"[{a['source']}] ({a['published']})\n标题: {a['title']}\n链接: {a['url']}\n摘要: {a['summary']}"
        for a in articles
    )

    blogs_text = "\n\n---\n\n".join(
        f"[博客·{b['source']}] ({b['published']})\n标题: {b['title']}\n链接: {b['url']}\n内容: {b['summary']}"
        for b in blogs
    ) if blogs else "（今日无新博客更新）"

    today = datetime.now().strftime("%Y年%m月%d日")

    prompt = f"""你是一位 AI 领域的资深研究员，为顶级机构的同行撰写每日深度简报。读者是熟悉该领域的专业人士，不需要解释基础概念，需要的是洞察和判断。

【新闻资讯】过去 24 小时，共 {len(articles)} 条：

{articles_text}

【研究员博客】过去 72 小时（更新频率低但质量高），共 {len(blogs)} 条：

{blogs_text}

请完成以下五个部分，严格使用 HTML 格式输出（不要加 markdown 代码块、不要加 ```html）：

第一部分：重点新闻（10-15条）
每条包含：发生了什么（1句）、技术/商业意义（2-3句，要有判断和立场）、与其他动态的关联（如有）。

第二部分：趋势分析
基于今日所有资讯，识别 2-3 个值得关注的技术或行业趋势，需有证据引用，给出预判。

第三部分：值得深挖
列出 2-3 篇值得精读的论文或报告（优先 arxiv），说明核心贡献和阅读重点。

第四部分：今日推荐博客
从博客列表中挑选 1 篇最值得精读的文章（若无合适则从新闻中选最具深度的长文）。给出：
- 为什么这篇值得花 15-30 分钟细读
- 3 个核心观点/论点（bullet）
- 适合谁读（研究员 / 工程师 / 产品经理）

第五部分：今日信号
最关键的一个判断，不超过 60 字。

HTML 格式模板：

<h2>🤖 AI 深度简报 · {today}</h2>
<p class="intro">新闻 {len(articles)} 条 · 博客 {len(blogs)} 篇 · Robotics / Agent / 大模型</p>

<div class="section-title">📌 重点新闻</div>

<div class="item">
  <h3><a href="URL">标题（中文）</a></h3>
  <span class="meta">来源：XXX · 时间</span>
  <p><strong>事件：</strong>……</p>
  <p><strong>意义：</strong>……</p>
  <p class="tag">关联：……</p>
</div>

<div class="section-title">📈 趋势分析</div>

<div class="trend">
  <h3>趋势名称</h3>
  <p>……</p>
</div>

<div class="section-title">🔬 值得深挖</div>

<div class="deep-read">
  <h3><a href="URL">论文/报告标题</a></h3>
  <p>……</p>
</div>

<div class="section-title">📖 今日推荐博客</div>

<div class="blog-pick">
  <h3><a href="URL">文章标题</a></h3>
  <span class="meta">作者/来源 · 时间</span>
  <p class="blog-why">……为什么值得读……</p>
  <ul>
    <li>核心观点一</li>
    <li>核心观点二</li>
    <li>核心观点三</li>
  </ul>
  <p class="blog-audience">适合：……</p>
</div>

<div class="closing">
  <strong>今日信号：</strong>……
</div>"""

    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


EMAIL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f0f0f5; margin: 0; padding: 20px; color: #222; }
.wrapper { max-width: 700px; margin: auto; background: #fff;
           border-radius: 10px; overflow: hidden;
           box-shadow: 0 2px 12px rgba(0,0,0,.10); }
.header { background: #0f0f1a; color: #fff; padding: 28px 36px; }
.header h1 { margin: 0; font-size: 22px; letter-spacing: -.3px; }
.body { padding: 28px 36px; }
h2 { color: #0f0f1a; margin-top: 0; font-size: 20px; }
.intro { color: #666; font-size: 13px; margin-bottom: 28px; }
.section-title { font-weight: 700; font-size: 11px; text-transform: uppercase;
                 letter-spacing: .1em; color: #999; margin: 32px 0 14px;
                 padding-bottom: 6px; border-bottom: 1px solid #eee; }
.item { border-left: 3px solid #4f46e5; padding: 14px 18px;
        margin-bottom: 18px; background: #fafafa; border-radius: 0 8px 8px 0; }
.item h3 { margin: 0 0 4px; font-size: 15px; line-height: 1.4; }
.item h3 a { color: #1a1a2e; text-decoration: none; }
.item h3 a:hover { text-decoration: underline; }
.meta { font-size: 11px; color: #aaa; display: block; margin-bottom: 8px; }
.item p { margin: 6px 0 0; font-size: 14px; line-height: 1.7; color: #444; }
.item p.tag { font-size: 12px; color: #7c6fcd; margin-top: 8px; }
.trend { border-left: 3px solid #059669; padding: 14px 18px;
         margin-bottom: 18px; background: #f0fdf4; border-radius: 0 8px 8px 0; }
.trend h3 { margin: 0 0 8px; font-size: 15px; color: #065f46; }
.trend p { margin: 0; font-size: 14px; line-height: 1.7; color: #444; }
.deep-read { border-left: 3px solid #d97706; padding: 14px 18px;
             margin-bottom: 18px; background: #fffbeb; border-radius: 0 8px 8px 0; }
.deep-read h3 { margin: 0 0 8px; font-size: 15px; }
.deep-read h3 a { color: #92400e; text-decoration: none; }
.deep-read p { margin: 0; font-size: 14px; line-height: 1.7; color: #444; }
.blog-pick { border-left: 3px solid #db2777; padding: 14px 18px;
             margin-bottom: 18px; background: #fdf2f8; border-radius: 0 8px 8px 0; }
.blog-pick h3 { margin: 0 0 4px; font-size: 15px; }
.blog-pick h3 a { color: #831843; text-decoration: none; }
.blog-pick h3 a:hover { text-decoration: underline; }
.blog-why { margin: 10px 0 8px; font-size: 14px; line-height: 1.7; color: #444; }
.blog-pick ul { margin: 8px 0; padding-left: 20px; font-size: 14px;
                line-height: 1.8; color: #444; }
.blog-audience { font-size: 12px; color: #9d174d; margin: 8px 0 0; }
.closing { background: #1a1a2e; color: #e0e0ff; border-radius: 8px;
           padding: 16px 20px; margin-top: 28px; font-size: 14px; line-height: 1.6; }
.closing strong { color: #fff; }
.footer { padding: 16px 36px; font-size: 12px; color: #bbb;
          border-top: 1px solid #eee; text-align: center; }
"""


def send_email(html_body: str) -> None:
    today = datetime.now().strftime("%m/%d")
    subject = f"🤖 AI Daily Brief · {today}"

    full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{EMAIL_CSS}</style>
</head>
<body>
<div class="wrapper">
  <div class="header"><h1>AI Daily Brief</h1></div>
  <div class="body">{html_body}</div>
  <div class="footer">Powered by Claude + GitHub Actions · 每日 07:00 UTC 自动发送</div>
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


if __name__ == "__main__":
    print("Fetching news articles...")
    articles = fetch_recent_articles(hours=24)
    print(f"Found {len(articles)} news articles")

    print("Fetching blog posts...")
    blogs = fetch_recent_blogs(hours=72)
    print(f"Found {len(blogs)} blog posts")

    if not articles and not blogs:
        print("No content found, skipping.")
        sys.exit(0)

    print("Summarizing with Claude...")
    summary = summarize_with_claude(articles, blogs)

    print("Sending email...")
    send_email(summary)
    print("Done!")
