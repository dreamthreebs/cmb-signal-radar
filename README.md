# CMB Signal：自动文献雷达

一个无需日常维护的 GitHub Pages 文献站：每天自动抓取 arXiv 上的 CMB、宇宙学及相邻方向论文，进行规则筛选，并可调用 OpenAI 生成中文摘要、研究价值、关键点和精读提示。

## 它会自动做什么

- 每天北京时间 12:35 运行 GitHub Actions。
- 从 arXiv Atom API 抓取 `astro-ph.CO`、CMB 仪器方法及跨方向发现候选。
- 对同一 arXiv ID 去重，按 CMB 相关性、趣味度与时效性选出本期内容。
- 有 `OPENAI_API_KEY` 时，一次批量调用生成结构化中文解读；没有 Key 或接口失败时，仍会发布元数据和规则评分。
- 保存最近 120 天、最多 180 篇入选历史，并自动部署到 GitHub Pages。
- API Key 只存在于 GitHub Actions 的服务端环境，不会进入网页或数据文件。

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

### 3. 配置大模型（推荐）

进入 `Settings → Secrets and variables → Actions`：

- 在 `Secrets` 新增 `OPENAI_API_KEY`。
- 可在 `Variables` 新增 `OPENAI_MODEL`；默认使用 `gpt-5.6`。
- 可在 `Variables` 新增 `ARXIV_CONTACT_EMAIL`，让 arXiv 请求的 User-Agent 带有维护者联系方式。

如果暂时不配置 `OPENAI_API_KEY`，站点也会每天自动更新，只是显示“规则模式”；之后添加 Key，自动任务会逐步补全近期论文解读。

### 4. 触发第一次更新

进入 `Actions → Update papers and deploy Pages → Run workflow`。完成后，访问 `Settings → Pages` 中显示的网址。

仓库需要允许 Actions 写入内容，才能每天把历史数据提交回 `main`。如组织策略限制了写权限，请在 `Settings → Actions → General → Workflow permissions` 中允许 Read and write permissions。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/update_papers.py --no-ai --max-results 15
python -m http.server 8000 --directory site
```

然后打开 `http://localhost:8000`。如果想在本地测试 AI 解读：

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_MODEL="gpt-5.6"
python scripts/update_papers.py --max-results 15
```

不要把 `.env` 或 API Key 提交到仓库。

## 调整选题范围

编辑 [`config/radar.json`](config/radar.json)：

- `queries`：arXiv 搜索式及每次候选数量。
- `lookback_days`：本期候选时间窗口。
- `focus_limit` / `discovery_limit`：CMB 核心与跨域发现的每日上限。
- `analysis_limit`：每天最多交给大模型分析的论文数，用于控制成本。
- `history_days` / `max_history`：网页保留的历史范围和数量。

选题评分词表位于 [`scripts/update_papers.py`](scripts/update_papers.py)。前端位于 [`site`](site)。

## 数据与免责声明

Thank you to arXiv for use of its open access interoperability.

本项目与 arXiv 无隶属或背书关系。大模型只接收论文题目、摘要和分类；其输出适合做每日筛选，不替代全文阅读、同行评议或作者原意。

