# AI Dispatch 二次开发实施报告

## 1. 报告信息

- 项目名称：AI Dispatch
- 当前仓库：`ai-dispatch`
- 报告范围：从 fork 原始代码仓开始，到当前版本落地为止的实施过程、关键问题、流程复盘与经验总结
- 报告日期：2026-07-13
- 当前参考版本：`d20f9eb` `fix: improve website audit GitCode link detection`
- 说明：本报告基于当前仓库提交记录、现有代码结构以及本轮实施过程整理，适合作为后续维护、交接和功能继续演进的内部文档

---

## 2. 项目背景与目标

原始仓库的核心能力是：

- 基于 GitHub Actions 定时抓取 AI 新闻与博客
- 使用 LLM 生成日报邮件
- 通过 Gmail SMTP 将结果发送到指定邮箱

本轮二次开发的主要目标，不再只是“跑通原项目”，而是围绕实际使用场景把它扩展成一个更完整的自动化信息派发工具，重点新增并完善以下能力：

- 新增 Website Audit 任务，用于定期检查指定网站或页面的失效链接
- 支持多站点配置，而不是单个 `start_url`
- 让 Website Audit 的报告通过 LLM 总结，并最终以单封周报邮件发送
- 提升报告可读性，明确列出坏链、anchor text、来源页面和建议修复动作
- 扩展 LLM provider 选择，支持 `openai` 与 `deepseek`
- 调整任务调度逻辑，使 Website Audit 在北京时间每周一上午 `10:07` 定时执行
- 解决 GitCode 页面软错误、空内容页等静态 HTTP 难识别的问题

---

## 3. 实施过程概览

本次实施可以分成 4 个阶段：

### 阶段 A：接管原仓并跑通基础流程

- fork 原始仓库
- 使用已有 `Setup` / `Check Setup` workflow 理解原始运行方式
- 明确仓库的基础工作流：
  - `setup.yml`：写入配置
  - `check_setup.yml`：检查 secrets、LLM API、Gmail SMTP、测试邮件
  - `daily_news.yml`：按日定时发送 AI 日报

这一阶段的重点不是改代码，而是先确认：

- 原始项目完全依赖 GitHub Actions 运行
- 邮件发送链路依赖 Gmail SMTP
- 配置入口以 `config.yml` 为主
- 运行状态依赖历史文件，如 `sent_history.json`

### 阶段 B：新增 Website Audit 基线能力

首先落地了 Website Audit 的第一版：

- 新增 `website_audit.py`
- 新增 `.github/workflows/website_audit.yml`
- 支持对页面进行静态抓取
- 提取链接并做 HEAD / GET 探测
- 使用 LLM 生成邮件报告
- 将运行结果写入 `website_audit_history.json`

这一阶段实现了“从无到有”，但很快暴露出配置表达、邮件组织、页面判错精度等问题。

### 阶段 C：围绕真实使用场景持续迭代

在用户实际配置和测试过程中，需求逐步细化，Website Audit 随之做了多轮增强：

- 从单目标 `start_url` 升级为多目标 `targets`
- 从“每个目标一封邮件”改为“多个目标合并成一封周报”
- 从“只有 LLM 自由发挥”改为“LLM 总结 + 程序化结构化坏链清单”
- 从“默认继续爬站内页”改为显式使用 `follow_internal_links`
- 从“配置里再写一层发送小时”改为 Website Audit 只由 workflow cron 控制
- 从“静态 HTTP 普通 404 检测”升级为“GitCode 软 404 + 空 tree 内容检测”

### 阶段 D：补齐 provider、调度、可维护性

除了 Website Audit 本身，本轮也同步补齐了与落地体验直接相关的能力：

- 新增 `openai` provider
- 将 OpenAI 默认模型设为 `gpt-5.5`
- 新增 `deepseek` provider，并设默认模型为 `deepseek-v4-flash`
- 更新 `setup.py` / `check_setup.py` / `dispatch_utils.py` / GitHub Actions workflow / README
- 调整 Website Audit 的调度为北京时间周一 `10:07`

---

## 4. 关键里程碑时间线

以下时间线基于当前仓库提交记录整理：

| 日期 | 提交 | 里程碑 |
|---|---|---|
| 2026-05-31 | `50d8ba4` | README 与 setup 流程优化 |
| 2026-07-11 | `990e2fd` | 新增 Website Audit 第一版 |
| 2026-07-12 | `8e77c84` | 新增 OpenAI provider |
| 2026-07-12 | `db7c63d` | OpenAI 默认模型调整为 `gpt-5.5` |
| 2026-07-12 | `f9e1973` | 新增 DeepSeek provider |
| 2026-07-12 | `973a5ec` | Website Audit 配置表达优化 |
| 2026-07-12 | `bbd69d7` | 新增 GitCode soft 404 检测 |
| 2026-07-12 | `8b955b1` | 调整 Website Audit 执行周期 |
| 2026-07-12 | `238ed1b` | 多 target 结果合并成单封邮件 |
| 2026-07-12 | `dbe60e0` | Website Audit 报告结构优化 |
| 2026-07-13 | `813d5da` | 调整定时发送时间 |
| 2026-07-13 | `d20f9eb` | 增强 GitCode link detection，补充 Nuxt/empty tree 识别 |

---

## 5. 当前版本已经实现的能力

### 5.1 日报能力

- 仍保留原始 AI 日报主流程
- `provider` 可选：
  - `gemini`
  - `deepseek`
  - `openai`
  - `anthropic`
- 日报由 `.github/workflows/daily_news.yml` 定时执行

### 5.2 Website Audit 能力

当前 Website Audit 的实现具备以下特性：

- 支持多个目标站点配置
- 支持“只检查起始页”或“继续抓同站内页”
- 支持检查外链
- 支持多目标合并成一封周报邮件
- 周报除了 LLM 总结，还会附带结构化坏链清单
- 报告中可展示：
  - 失效链接 URL
  - internal / external 分类
  - 错误类型
  - anchor text
  - 来源页面
  - 建议修复动作

### 5.3 调度能力

当前 Website Audit 调度逻辑为：

- workflow：`.github/workflows/website_audit.yml`
- cron：`7 2 * * 1`
- 含义：每周一 `02:07 UTC`
- 北京时间：每周一 `10:07`

并且 Website Audit 现在只由 cron 控制时间，不再额外依赖 `website_audit.send_hour_utc` 进行二次判断。

---

## 6. 实施过程中遇到的关键问题与解决方案

### 问题 1：初版 Website Audit 只能配置一个网站

#### 现象

用户希望同时检查多个目标，但初版只有单个 `start_url`。

#### 解决方式

- 在 `config.yml` 中新增 `website_audit.targets`
- 保留旧的 `start_url` 兼容能力
- 在 `website_audit.py` 中统一解析多目标配置

#### 结果

实现了向后兼容，同时把后续扩展能力建立在更合理的配置结构上。

---

### 问题 2：单页检查场景下，任务抓取了过多页面和链接

#### 现象

用户只想检查 `GitCode CANN` 首页，却看到日志中出现：

- `Crawled 25 pages`
- `checked 1481 links`

这与用户“只检查一个页面”的预期不一致。

#### 原因

- 第一版默认继续抓取同站内页
- 配置表达不够直观

#### 解决方式

- 增加 `follow_internal_links`
- 将其语义明确为：
  - `false`：只检查当前页面
  - `true`：继续抓站内页
- 在 README 和 `check_setup.py` 中同步补充说明

#### 结果

用户可以明确控制检查范围，避免“看起来像坏链任务，其实在做小规模爬站”的误解。

---

### 问题 3：GitCode 存在“HTTP 200 但页面资源实际不存在”的软错误

#### 现象

例如：

- 某些 GitCode `blob` 链接点击后页面显示“文件不存在”
- 但 HTTP 状态仍可能是 `200`

#### 原因

- GitCode 并不是简单地返回标准 404 页面
- 静态 HTTP 层面看不到异常

#### 解决方式

- 对 GitCode `blob/tree` 页面追加 HTML 正文检查
- 检查“为空或不存在”“文件不存在”等标记

#### 结果

解决了第一类被漏掉的 GitCode 软 404 问题。

---

### 问题 4：GitCode 还存在“200 + 空目录/空内容 tree”的异常路径

#### 现象

例如用户指出：

- `https://gitcode.com/cann/cann-recipes-infer/blob/master/models/deepseek-v4`

页面没有显式报错，但实际没有任何有效内容。

#### 原因

- GitCode 会把该链接重定向到 `tree` 页面
- HTTP 返回 `200`
- 页面并非普通 404，而是 Nuxt SSR 数据中对应 tree 节点的 `items` 为空数组

#### 解决方式

- 解析 GitCode 页面中的 `__NUXT_DATA__`
- 根据 URL 反推出 tree key
- 如果对应 tree 节点存在但 `items` 为空，则判定为无效内容链接

#### 结果

解决了第二类 GitCode 隐式坏链问题。

---

### 问题 5：坏链报告虽然生成成功，但可读性不够

#### 现象

LLM 报告能生成，但用户反馈：

- 不够清楚
- 没有显式列出坏链
- 没写清 anchor text
- 没写清修复建议

#### 解决方式

- 优化 LLM prompt，强制它按列表输出
- 同时增加程序化生成的结构化坏链清单
- 将两部分合并到同一封邮件正文

#### 结果

报告不再完全依赖 LLM 自由发挥，可读性和稳定性明显提升。

---

### 问题 6：多个 target 各发一封邮件，不符合用户“周报”预期

#### 现象

用户配置了多个 target，但收到的是拆开的邮件。

#### 解决方式

- 收集多个站点的审计结果
- 统一构造一个多站点 prompt
- 统一生成一封 weekly report 邮件

#### 结果

现在多个 target 会合并为一封周报，更符合“周期性审计摘要”的使用方式。

---

### 问题 7：定时逻辑分散在 cron 和 config 两处，容易混淆

#### 现象

一开始 Website Audit 的发送时间既受 workflow cron 影响，也受配置里的 `send_hour_utc` 影响。

这会造成：

- 用户不知道该改哪一处
- 出现“workflow 已触发，但代码判断不发送”的双重逻辑问题

#### 解决方式

- 删除 Website Audit 里的 `send_hour_utc` 参与调度判断
- 只保留 `enabled` 与“当天是否已发送”的保护逻辑
- 将实际时间控制完全交给 workflow cron

#### 结果

时间控制入口统一，理解成本明显下降。

---

### 问题 8：整点调度容易延迟，用户感知不好

#### 现象

GitHub Scheduled Workflow 在整点附近容易因为平台调度拥塞出现延迟。

#### 解决方式

- 不再使用 `00` 分触发
- 调整为周一 `10:07` 北京时间执行

#### 结果

降低了整点拥塞带来的调度波动风险。

---

### 问题 9：用户误以为 ChatGPT Plus 可直接抵扣 OpenAI API 用量

#### 现象

用户在 GitHub Actions 中调用 OpenAI API 时遇到 `insufficient_quota`。

#### 原因

- ChatGPT Plus 订阅与 OpenAI API 计费不是同一体系

#### 解决方式

- 明确区分：
  - ChatGPT Plus：面向 ChatGPT 产品
  - OpenAI API：需要单独开通 API 计费与额度

#### 结果

帮助用户正确理解 provider 成本与 API 配额。

---

### 问题 10：用户想用 Outlook 邮件，但当前实现只有 Gmail SMTP

#### 现象

用户提出希望改用 Microsoft Outlook 邮件服务。

#### 现状判断

- 当前仓库发送链路完全基于 Gmail SMTP
- Setup / Check Setup / README / 环境变量设计也都围绕 Gmail

#### 处理结论

本轮未改成 Outlook，当前实现仍以 Gmail 为唯一正式支持的发信方式。

---

## 7. 流程上的问题复盘

从整个实施过程看，除了代码问题，还暴露出一些流程层面的共性问题：

### 7.1 需求是在运行中逐步收敛的

最初需求只是“检查坏链”，但在真正使用过程中，逐步演变成：

- 单页还是全站
- 单站还是多站
- 每站一封还是周报合并
- 只靠 LLM 还是结构化输出
- 静态检测是否足够

说明早期需求更偏方向性，真正的产品定义是在多轮试跑和反馈中完成的。

### 7.2 配置语义必须尽量直观

类似 `follow_internal_links` 这样的配置，是实践后才被证明“更接近用户心智”的。

经验是：

- 用户不关心内部实现
- 用户关心的是“我到底在检查一个页面，还是整站”

### 7.3 不能只依赖 HTTP 状态码理解现代网站

GitCode 这类站点已经是一个应用系统，而不是传统静态网页：

- 可能重定向
- 可能返回 200 但业务上不存在
- 可能通过 SSR / 前端状态表达错误

因此坏链检测如果只看 HEAD/GET 状态码，会长期漏报。

### 7.4 Prompt 优化不能替代结构化数据输出

报告类任务里，LLM 适合做归纳和总结，但不适合单独承担“结构化可追溯输出”的责任。

最稳妥的方式是：

- 用程序提供确定性的结构化事实
- 让 LLM 在这些事实之上做总结和优先级分析

### 7.5 定时任务要避免双重控制

调度逻辑同时存在于 cron 和代码时，后续排查会非常痛苦。

时间控制最好始终遵循一个原则：

- workflow 负责“什么时候触发”
- 代码只负责“触发后是否应该跳过”

---

## 8. 当前版本的架构落点

### 核心文件职责

- `dispatch_utils.py`
  - 统一 provider 选择
  - 统一模型读取
  - 统一 LLM 调用
  - 统一 Gmail SMTP 发信

- `website_audit.py`
  - Website Audit 主逻辑
  - 多目标解析
  - 静态抓取与坏链探测
  - GitCode 特殊规则处理
  - LLM 周报构建
  - 结果 artifact 输出

- `config.yml`
  - 所有用户可调配置入口

- `.github/workflows/website_audit.yml`
  - Website Audit 的定时和执行编排

- `check_setup.py`
  - 安装与配置后的连通性验证

### 当前运行方式

- 日报：每天通过 `daily_news.yml` 运行
- 网站巡检：每周一 `10:07` 北京时间通过 `website_audit.yml` 运行
- 发信方式：Gmail SMTP
- LLM provider：Gemini / DeepSeek / OpenAI / Anthropic

---

## 9. 当前版本仍然存在的边界

虽然当前版本已经能满足主要使用目标，但仍有明确边界：

### 9.1 仍然以静态 HTTP 抓取为主

当前已经补了：

- HTML 正文检查
- Nuxt SSR 数据解析

但如果链接只在浏览器执行 JS 之后才出现，仍可能漏报。

### 9.2 发信仍然依赖 Gmail

当前没有完成 Outlook SMTP 或 Microsoft Graph 发信接入。

### 9.3 远端 GitHub Actions 仍受平台调度影响

虽然已经避开整点，但 GitHub Scheduled Workflow 依然不能保证绝对秒级准时。

### 9.4 手动运行与状态文件存在相互影响

如果同一天先手动运行过 Website Audit，`website_audit_history.json` 里写入了当天日期，则同一天再次定时触发时会跳过。

---

## 10. 经验总结

本次实施的经验可以归纳为以下几点：

1. 先让任务“可跑”，再逐步让它“好用”。
2. 用户真正关心的不是内部实现，而是配置表达是否符合直觉。
3. 链接检测不是简单网络问题，而是“页面业务语义识别”问题。
4. 只靠 LLM 生成报告不够，必须有程序生成的结构化清单兜底。
5. 定时调度与业务判断要解耦。
6. 多轮真实测试比纸面设计更容易暴露边界问题。
7. 对第三方站点，必须针对具体平台做特殊规则适配，GitCode 就是典型例子。

---

## 11. 后续建议

如果继续演进，建议优先考虑下面几个方向：

### 建议 1：增加浏览器渲染模式

引入 Playwright 或类似能力，作为 Website Audit 的可选模式，用于处理纯前端渲染页面。

### 建议 2：为 GitCode 规则增加回归测试样本

把以下场景做成本地 fixture：

- 明确 not found
- 200 + soft 404
- 200 + empty tree
- 正常 tree 页面

这样后续改动时不容易回归。

### 建议 3：抽象邮件发送层

将 `send_email` 从 Gmail 专用实现抽象成 provider 接口，为未来接 Outlook / Graph API 做准备。

### 建议 4：为 Website Audit 增加更细粒度的配置

未来可以考虑支持：

- `render_mode: static | browser`
- `per_target.max_links_per_page`
- `per_target.follow_internal_links`
- `exclude_patterns`

### 建议 5：补一套端到端测试说明

包括：

- 如何手动触发 Website Audit
- 如何判断是调度没触发，还是代码执行失败
- 如何查看 artifact
- 如何理解 `website_audit_history.json`

---

## 12. 结论

本轮二次开发已经把 AI Dispatch 从“日报邮件工具”扩展成了一个“日报 + 周度网站巡检”的自动化派发工具，并且完成了以下关键落地：

- Website Audit 从无到有
- 多目标支持
- 单封周报输出
- 报告结构清晰化
- OpenAI / DeepSeek provider 接入
- 周一定时巡检
- GitCode 软错误与空内容路径识别增强

从工程角度看，当前版本已经具备稳定使用价值；从产品角度看，当前最大的剩余边界是“纯前端动态渲染页面的覆盖率”以及“Gmail-only 的发信方式限制”。

如果后续继续迭代，优先级最高的建议是：增加浏览器渲染版巡检能力，并补充一套稳定的回归测试样本。
