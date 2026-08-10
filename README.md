# CMB Signal：自动文献雷达

一个无需日常维护的 GitHub Pages 文献站：每天自动检查 arXiv 上的 CMB、宇宙学及相邻方向论文，并调用 GPT 或第三方 OpenAI 兼容接口生成中文摘要、研究价值、关键点和精读提示。

## 它会自动做什么

- 每天北京时间 09:05 运行 GitHub Actions。
- 从 arXiv Atom API 抓取 `astro-ph.CO`、CMB 仪器方法及跨方向发现候选。
- 对同一 arXiv ID 去重，按 CMB 相关性、趣味度与时效性选出本期内容。
- 只有同时满足“API Key 已配置、接口调用成功、发现新论文”时，定时任务才更新数据并部署。
- Key 缺失、Key 失效、第三方接口报错或没有新论文时，保留上一版网站，不提交空更新。
- 保存最近 120 天、最多 180 篇入选历史，并自动部署到 GitHub Pages。
- API Key 只存在于 GitHub Actions 的服务端环境，不会进入网页或数据文件。
- 支持在 Actions 页面手动运行，并可强制重新分析当前精选。

## 一次性上线步骤

### 1. 创建并推送 GitHub 仓库

建议创建名为 `cmb-signal-radar` 的公开仓库，然后在本目录执行：

```bash
git init -b main
git add .
git commit -m "feat: launch CMB literature radar"
git remote add origin https://github.com/YOUR_USERNAME/cmb-signal-radar.git
git push -u origin main
```

GitHub Free 的项目 Pages 通常需要公开仓库；如果你的套餐支持，也可以使用私有仓库。

### 2. 启用 Pages

进入仓库：

1. `Settings → Pages`
2. 在 `Build and deployment → Source` 选择 `GitHub Actions`

工作流已经包含官方 Pages 部署所需的 `pages: write` 与 `id-token: write` 权限。

### 3. 配置 GPT API

进入 `Settings → Secrets and variables → Actions`：

| 名称 | 放置位置 | 用途 |
| --- | --- | --- |
| `GPT_API_KEY` | **Secret** | 必需。官方 OpenAI 或第三方接口的密钥 |
| `GPT_BASE_URL` | **Secret** 或 Variable | 第三方接口地址，例如 `https://provider.example/v1`；官方 OpenAI 留空 |
| `GPT_MODEL` | Variable | 模型名，默认 `gpt-5.6`；第三方需填写其模型标识 |
| `GPT_API_MODE` | Variable | `responses`（默认）或 `chat_completions` |
| `GPT_USER_AGENT` | Variable | 可选；第三方服务要求特定客户端标识时设置 |
| `GPT_BATCH_SIZE` | Variable | 可选；每次分析的论文数，慢速第三方接口建议设为 `3` |
| `GPT_MAX_RETRIES` | Variable | 可选；连接、超时、限流和服务端错误的最大自动重试次数，默认 `3` |
| `GPT_REASONING_EFFORT` | Variable | 可选；Responses API 的推理强度，如 `low` |
| `ARXIV_CONTACT_EMAIL` | Variable | 可选，让 arXiv User-Agent 带维护者联系方式 |

官方 OpenAI 推荐使用：

- `GPT_API_KEY`：你的 OpenAI API Key
- `GPT_BASE_URL`：不设置
- `GPT_MODEL`：`gpt-5.6`
- `GPT_API_MODE`：`responses`

第三方 OpenAI 兼容接口：

- 把 Key 存为 `GPT_API_KEY`，不要写入代码或仓库。
- 把兼容接口的 `/v1` 地址存为 `GPT_BASE_URL`。
- 如果服务支持 `/responses` 和结构化输出，使用 `responses`。
- 如果只支持 `/chat/completions`，使用 `chat_completions`；该服务还需要支持 JSON Object 输出。
- 如果服务商要求特定 `User-Agent`，将其存为 `GPT_USER_AGENT` Variable。

也可以使用命令行添加 Secret（输入内容不会写进仓库）：

```bash
gh secret set GPT_API_KEY --repo dreamthreebs/cmb-signal-radar
gh secret set GPT_BASE_URL --repo dreamthreebs/cmb-signal-radar
gh variable set GPT_MODEL --body "gpt-5.6" --repo dreamthreebs/cmb-signal-radar
gh variable set GPT_API_MODE --body "responses" --repo dreamthreebs/cmb-signal-radar
gh variable set GPT_USER_AGENT --body "provider-required-client-id" --repo dreamthreebs/cmb-signal-radar
gh variable set GPT_BATCH_SIZE --body "3" --repo dreamthreebs/cmb-signal-radar
gh variable set GPT_REASONING_EFFORT --body "low" --repo dreamthreebs/cmb-signal-radar
```

未配置 Key 时，定时工作流会立即安全结束；已经发布的网站仍保持可访问。

当前仓库的实际放置位置：

- Key：`Settings → Secrets and variables → Actions → Secrets → GPT_API_KEY`
- 第三方 URL：`Settings → Secrets and variables → Actions → Variables → GPT_BASE_URL`
- 模型与协议：同一页面的 Variables 中修改 `GPT_MODEL` 与 `GPT_API_MODE`

更新 Key 时也可以执行 `gh secret set GPT_API_KEY --repo dreamthreebs/cmb-signal-radar`，命令会安全提示输入新值；不要把 Key 作为命令参数或写入文件。

### 4. 触发第一次更新

进入 `Actions → Update papers and deploy Pages → Run workflow`。`force_refresh` 默认为开启，因此即使当天没有新论文，也会重新分析当前精选；关闭后则使用与定时任务相同的“无新论文即跳过”规则。

仓库需要允许 Actions 写入内容，才能每天把历史数据提交回 `main`。如组织策略限制了写权限，请在 `Settings → Actions → General → Workflow permissions` 中允许 Read and write permissions。

### 5. 回填历史文献

手动运行工作流时，把 `backfill_days` 设为 `90`，即可建立过去三个月的档案。`astro-ph.CO` 会完整保留元数据，邻近分类仍按月筛选；`analysis_cap` 控制单次最多生成多少篇 AI 解读。重复运行相同的 90 天回填会从上次未完成的位置继续，不会重新分析已经完成的论文。

网页文献库提供“按日 / 按月 / 全部历史”三种时间视图，并可继续叠加主题、关键词和排序条件。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/update_papers.py --no-ai --max-results 15
python -m http.server 8000 --directory site
```

然后打开 `http://localhost:8000`。如果想在本地测试官方 OpenAI：

```bash
export GPT_API_KEY="your-key"
export GPT_MODEL="gpt-5.6"
export GPT_API_MODE="responses"
python scripts/update_papers.py --max-results 15 --require-ai --force-ai
```

第三方接口再设置 `GPT_BASE_URL`，并按其兼容能力选择 `GPT_API_MODE`。

不要把 `.env` 或 API Key 提交到仓库。

## 自动运行规则

- `schedule`：每天北京时间 09:05 检查一次。无新论文时不调用 GPT、不提交、不部署。
- `workflow_dispatch`：在 Actions 页面手动启动；可选择强制刷新。
- `push`：代码或页面发生修改时，只运行测试并部署已有数据，不调用 GPT。
- GPT Key 缺失、鉴权失败、超时、返回结构不完整或 arXiv 暂时不可用时，任务不覆盖 `papers.json`、不部署新页面，并以失败状态结束。
- GPT 遇到短暂的连接错误、超时、限流或服务端错误时会先自动退避重试 3 次；只有最终仍失败才进入告警流程。
- 故障时工作流会自动创建并指派 `⚠️ CMB Signal 自动更新异常` Issue；连续故障只更新同一条，API 恢复且成功更新后自动关闭。

## 调整选题范围

编辑 [`config/radar.json`](config/radar.json)：

- `queries`：arXiv 搜索式、排序方式及每次候选数量。`astro-ph.CO` 同时按投稿时间和更新时间抓取，以覆盖新投稿、交叉投稿与修订。
- `complete_category`：不经过推荐限额、始终完整写入档案的 arXiv 分类。
- `lookback_days`：本期候选时间窗口。
- `focus_limit` / `discovery_limit`：CMB 核心与跨域发现的 GPT 精选上限，不再决定 `astro-ph.CO` 是否进入档案。
- `analysis_limit`：每天最多交给大模型分析的论文数，用于控制成本。
- `history_days` / `max_history`：网页保留的历史范围和数量。

选题评分词表位于 [`scripts/update_papers.py`](scripts/update_papers.py)。前端位于 [`site`](site)。

## 数据与免责声明

Thank you to arXiv for use of its open access interoperability.

本项目与 arXiv 无隶属或背书关系。大模型只接收论文题目、摘要和分类；其输出适合做每日筛选，不替代全文阅读、同行评议或作者原意。
