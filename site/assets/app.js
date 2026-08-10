const state = {
  data: null,
  filter: "all",
  query: "",
  sort: "recommended",
  timeMode: "all",
  timeValue: "",
};

const dom = {
  issueDate: document.querySelector("#issue-date"),
  statFocus: document.querySelector("#stat-focus"),
  statDiscovery: document.querySelector("#stat-discovery"),
  statTotal: document.querySelector("#stat-total"),
  statAi: document.querySelector("#stat-ai"),
  featured: document.querySelector("#featured-paper"),
  spectrum: document.querySelector("#topic-spectrum"),
  grid: document.querySelector("#paper-grid"),
  empty: document.querySelector("#empty-state"),
  count: document.querySelector("#result-count"),
  updated: document.querySelector("#last-updated"),
  search: document.querySelector("#paper-search"),
  sort: document.querySelector("#paper-sort"),
  periodPicker: document.querySelector("#period-picker"),
  periodPickerLabel: document.querySelector("#period-picker-label"),
  periodSelect: document.querySelector("#period-select"),
  archiveRange: document.querySelector("#archive-range"),
  dialog: document.querySelector("#paper-dialog"),
  dialogContent: document.querySelector("#dialog-content"),
  toast: document.querySelector("#toast"),
};

const el = (tag, className = "", text = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
};

const formatDate = (value, withTime = false) => {
  if (!value) return "日期未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
};

const localDateKey = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
};

const dateKey = (paper) => String(paper.published || "").slice(0, 10);
const monthKey = (paper) => dateKey(paper).slice(0, 7);

const formatMonth = (value) => {
  const [year, month] = String(value).split("-");
  return year && month ? `${year} 年 ${Number(month)} 月` : value;
};

function periodValues(mode) {
  const meta = state.data?.meta || {};
  if (mode === "day") {
    return meta.archive_dates?.length
      ? meta.archive_dates
      : [...new Set(state.data.papers.map(dateKey).filter(Boolean))].sort().reverse();
  }
  if (mode === "month") {
    return meta.archive_months?.length
      ? meta.archive_months
      : [...new Set(state.data.papers.map(monthKey).filter(Boolean))].sort().reverse();
  }
  return [];
}

function updatePeriodPicker(resetValue = false) {
  const values = periodValues(state.timeMode);
  dom.periodPicker.hidden = state.timeMode === "all";
  if (state.timeMode === "all") {
    state.timeValue = "";
    return;
  }
  if (resetValue || !values.includes(state.timeValue)) state.timeValue = values[0] || "";
  dom.periodPickerLabel.textContent = state.timeMode === "day" ? "选择日期" : "选择月份";
  dom.periodSelect.replaceChildren(
    ...values.map((value) => {
      const option = el("option", "", state.timeMode === "day" ? formatDate(value) : formatMonth(value));
      option.value = value;
      option.selected = value === state.timeValue;
      return option;
    }),
  );
}

function renderArchiveRange() {
  const meta = state.data.meta || {};
  if (!meta.archive_start || !meta.archive_end) {
    dom.archiveRange.textContent = "等待历史数据";
    return;
  }
  const aiCount = Number(meta.archive_ai_count || 0);
  const pending = Number(meta.archive_pending_count || 0);
  dom.archiveRange.textContent = `${formatDate(meta.archive_start)} — ${formatDate(meta.archive_end)} · ${state.data.papers.length} 篇 · ${aiCount} 篇 AI 解读${pending ? ` · ${pending} 篇待补` : ""}`;
}

const truncateAuthors = (authors = [], limit = 5) => {
  if (!authors.length) return "作者信息暂缺";
  const shown = authors.slice(0, limit).join(" · ");
  return authors.length > limit ? `${shown} · 等 ${authors.length} 人` : shown;
};

const currentIds = () => {
  const meta = state.data?.meta || {};
  return [...(meta.current_focus_ids || []), ...(meta.current_discovery_ids || [])];
};

const isLatestAddition = (paper) => {
  const generatedDate = localDateKey(state.data?.meta?.generated_at);
  return Boolean(generatedDate && localDateKey(paper.first_selected_at) === generatedDate);
};

const getAnalysis = (paper) => paper.analysis || {};
const titleFor = (paper) => paper.title || getAnalysis(paper).title_zh || "Untitled";

function trackChip(paper) {
  const discovery = paper.track === "discovery";
  return el(
    "span",
    `track-chip${discovery ? " track-chip--discovery" : ""}`,
    discovery ? "COSMIC DISCOVERY" : "CMB FOCUS",
  );
}

function actionLink(label, href, accent = false) {
  const link = el("a", `text-action${accent ? " text-action--accent" : ""}`, label);
  link.href = href;
  link.target = "_blank";
  link.rel = "noreferrer";
  const arrow = el("span", "", "↗");
  arrow.setAttribute("aria-hidden", "true");
  link.append(arrow);
  return link;
}

function detailButton(paper, label = "展开解读") {
  const button = el("button", "text-action", label);
  button.type = "button";
  button.append(el("span", "", "＋"));
  button.addEventListener("click", () => openDialog(paper));
  return button;
}

function scoreRow(label, score, color = "var(--mint)") {
  const row = el("div", "score-row");
  row.append(el("span", "", label));
  const bar = el("i");
  bar.style.setProperty("--score", `${Math.max(0, Math.min(100, score || 0))}%`);
  bar.style.setProperty("--score-color", color);
  row.append(bar, el("strong", "", String(Math.round(score || 0))));
  return row;
}

function tagList(tags = []) {
  const list = el("div", "tag-list");
  tags.slice(0, 5).forEach((tag) => list.append(el("span", "tag", `# ${tag}`)));
  return list;
}

function renderStatus() {
  const meta = state.data.meta || {};
  const focusCount = (meta.current_focus_ids || []).length;
  const discoveryCount = (meta.current_discovery_ids || []).length;
  dom.statFocus.textContent = String(focusCount).padStart(2, "0");
  dom.statDiscovery.textContent = String(discoveryCount).padStart(2, "0");
  dom.statTotal.textContent = String(state.data.papers.length).padStart(2, "0");
  dom.statAi.textContent =
    meta.analysis_status === "openai" ? "AI 在线" : meta.analysis_status === "mixed" ? "混合" : "规则模式";
  dom.issueDate.textContent = meta.generated_at ? formatDate(meta.generated_at) : "等待首次更新";
  dom.updated.textContent = meta.generated_at
    ? `最后更新 ${formatDate(meta.generated_at, true)}`
    : "尚未执行自动更新";
}

function featuredEmpty() {
  dom.featured.className = "featured-paper";
  dom.featured.replaceChildren();
  const content = el("div", "featured-paper__content");
  content.style.gridColumn = "1 / -1";
  content.append(
    el("p", "paper-kicker", "WAITING FOR FIRST SCAN"),
    el("h3", "", "雷达已就绪，等待第一次自动抓取。"),
    el("p", "featured-summary", "在 GitHub Actions 中手动运行一次工作流，或在本地执行更新脚本，即可生成今日信号。"),
  );
  dom.featured.append(content);
}

function renderFeatured() {
  const papersById = new Map(state.data.papers.map((paper) => [paper.id, paper]));
  const currentPapers = currentIds().map((id) => papersById.get(id)).filter(Boolean);
  const firstId = currentPapers.find(isLatestAddition)?.id || currentIds()[0];
  const paper = papersById.get(firstId) || state.data.papers[0];
  if (!paper) {
    featuredEmpty();
    return;
  }

  const analysis = getAnalysis(paper);
  dom.featured.className = "featured-paper";
  dom.featured.replaceChildren();
  dom.featured.append(el("div", "featured-paper__number", "01"));

  const content = el("article", "featured-paper__content");
  const kicker = el("div", "paper-kicker");
  kicker.append(trackChip(paper));
  if (isLatestAddition(paper)) {
    kicker.append(el("span", "intake-chip", `今日收录 ${formatDate(paper.first_selected_at)}`));
  }
  kicker.append(el("span", "", `arXiv 提交 ${formatDate(paper.published)}`), el("span", "", `arXiv:${paper.id}`));
  const title = el("h3", "", titleFor(paper));
  title.id = "featured-title";
  content.append(kicker, title);
  if (analysis.title_zh) content.append(el("p", "paper-title-zh", analysis.title_zh));
  content.append(
    el("p", "paper-authors", truncateAuthors(paper.authors)),
    el("p", "featured-summary", analysis.summary_zh || paper.abstract),
  );
  const actions = el("div", "paper-actions");
  actions.append(detailButton(paper, "查看完整解读"), actionLink("摘要页", paper.abs_url), actionLink("PDF", paper.pdf_url, true));
  content.append(actions);

  const aside = el("aside", "featured-paper__aside");
  aside.append(
    el("h4", "", "WHY IT MATTERS"),
    el("p", "", analysis.why_it_matters_zh || "等待分析引擎给出研究价值判断。"),
  );
  const scores = el("div", "score-stack");
  scores.append(
    scoreRow("CMB", paper.scores?.cmb || 0, "var(--mint)"),
    scoreRow("趣味度", paper.scores?.interest || 0, "var(--amber)"),
    scoreRow("新颖度", (analysis.novelty_score || 0) * 10, "var(--acid)"),
  );
  aside.append(scores, tagList(paper.tags));
  dom.featured.append(content, aside);
}

function renderSpectrum() {
  dom.spectrum.replaceChildren();
  const activeIds = new Set(currentIds());
  const source = activeIds.size
    ? state.data.papers.filter((paper) => activeIds.has(paper.id))
    : state.data.papers.slice(0, 18);
  const counts = new Map();
  source.forEach((paper) =>
    (paper.tags || []).forEach((tag) => counts.set(tag, (counts.get(tag) || 0) + 1)),
  );
  const topics = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  if (!topics.length) {
    dom.spectrum.append(el("p", "section-note", "首次抓取后，这里会显示当天最活跃的研究主题。"));
    return;
  }
  const max = Math.max(...topics.map(([, count]) => count));
  topics.forEach(([topic, count]) => {
    const bar = el("div", "spectrum-bar");
    const fill = el("div", "spectrum-bar__fill");
    fill.style.setProperty("--bar-height", `${38 + (count / max) * 104}px`);
    bar.append(fill, el("strong", "", topic), el("small", "", `${count} PAPERS`));
    dom.spectrum.append(bar);
  });
}

const topicFilterKeys = new Set([
  "polarization",
  "lensing-lss",
  "early-universe",
  "instruments",
  "foregrounds-methods",
  "dark-sector",
  "gravity",
  "surveys",
  "ai-computation",
]);

function matchesFilter(paper) {
  if (state.filter === "focus" || state.filter === "discovery") return paper.track === state.filter;
  if (state.filter === "deep") {
    const analysis = getAnalysis(paper);
    return String(analysis.audience || "").includes("精读") || (analysis.novelty_score || 0) >= 8;
  }
  if (topicFilterKeys.has(state.filter)) return (paper.topics || []).includes(state.filter);
  return true;
}

function matchesTime(paper) {
  if (state.timeMode === "day") return dateKey(paper) === state.timeValue;
  if (state.timeMode === "month") return monthKey(paper) === state.timeValue;
  return true;
}

function matchesQuery(paper) {
  if (!state.query) return true;
  const analysis = getAnalysis(paper);
  const haystack = [
    paper.title,
    analysis.title_zh,
    analysis.summary_zh,
    ...(paper.authors || []),
    ...(paper.tags || []),
  ]
    .join(" ")
    .toLocaleLowerCase();
  return haystack.includes(state.query.toLocaleLowerCase());
}

function sortedPapers() {
  const ranked = new Map(currentIds().map((id, index) => [id, index]));
  const papers = state.data.papers.filter(matchesTime).filter(matchesFilter).filter(matchesQuery);
  return papers.sort((a, b) => {
    if (state.sort === "date") return String(b.published).localeCompare(String(a.published));
    if (state.sort === "cmb") return (b.scores?.cmb || 0) - (a.scores?.cmb || 0);
    if (state.sort === "novelty") {
      return (getAnalysis(b).novelty_score || 0) - (getAnalysis(a).novelty_score || 0);
    }
    const aLatest = isLatestAddition(a);
    const bLatest = isLatestAddition(b);
    if (aLatest !== bLatest) return aLatest ? -1 : 1;
    const aRank = ranked.has(a.id) ? ranked.get(a.id) : 999;
    const bRank = ranked.has(b.id) ? ranked.get(b.id) : 999;
    if (aRank !== bRank) return aRank - bRank;
    return (b.scores?.editorial || 0) - (a.scores?.editorial || 0);
  });
}

function paperCard(paper, index) {
  const analysis = getAnalysis(paper);
  const card = el("article", "paper-card");
  const top = el("div", "paper-card__top");
  top.append(el("span", "paper-card__number", String(index + 1).padStart(2, "0")), trackChip(paper));
  card.append(top);

  const kicker = el("div", "paper-kicker");
  if (isLatestAddition(paper)) {
    kicker.append(el("span", "intake-chip", `今日收录 ${formatDate(paper.first_selected_at)}`));
  }
  kicker.append(el("span", "", `arXiv 提交 ${formatDate(paper.published)}`), el("span", "", `arXiv:${paper.id}`));
  card.append(kicker);

  const title = el("h3", "", titleFor(paper));
  card.append(title);
  if (analysis.title_zh) card.append(el("p", "paper-title-zh", analysis.title_zh));
  card.append(el("p", "paper-authors", truncateAuthors(paper.authors, 4)));

  const insight = el("div", "paper-insight");
  insight.append(
    el("h4", "", analysis.provider === "openai" ? "AI ABSTRACT NOTE" : "ABSTRACT NOTE"),
    el("p", "", analysis.summary_zh || paper.abstract),
  );
  card.append(insight, tagList(paper.tags));
  const actions = el("div", "paper-actions");
  actions.append(detailButton(paper), actionLink("PDF", paper.pdf_url));
  card.append(actions);
  return card;
}

function renderGrid() {
  const papers = sortedPapers();
  dom.count.textContent = papers.length;
  dom.grid.replaceChildren(...papers.map(paperCard));
  dom.grid.setAttribute("aria-busy", "false");
  dom.grid.hidden = papers.length === 0;
  dom.empty.hidden = papers.length !== 0;
}

function dialogSection(title, content, className = "") {
  const section = el("section", `dialog-section${className ? ` ${className}` : ""}`);
  section.append(el("h4", "", title));
  if (Array.isArray(content)) {
    const list = el("ul");
    content.forEach((item) => list.append(el("li", "", item)));
    section.append(list);
  } else {
    section.append(el("p", "", content || "摘要中未明确说明。"));
  }
  return section;
}

function openDialog(paper) {
  const analysis = getAnalysis(paper);
  dom.dialogContent.replaceChildren();
  const kicker = el("div", "paper-kicker");
  kicker.append(trackChip(paper));
  if (isLatestAddition(paper)) {
    kicker.append(el("span", "intake-chip", `今日收录 ${formatDate(paper.first_selected_at)}`));
  }
  kicker.append(el("span", "", `arXiv 提交 ${formatDate(paper.published)}`), el("span", "", `arXiv:${paper.id}`));
  const title = el("h2", "dialog-paper-title", titleFor(paper));
  title.id = "dialog-title";
  dom.dialogContent.append(kicker, title);
  if (analysis.title_zh) dom.dialogContent.append(el("p", "paper-title-zh", analysis.title_zh));
  dom.dialogContent.append(el("p", "dialog-meta", truncateAuthors(paper.authors, 12)));
  dom.dialogContent.append(dialogSection("一句话概要", analysis.summary_zh || paper.abstract));

  const grid = el("div", "dialog-grid");
  grid.append(
    dialogSection("为什么值得关注", analysis.why_it_matters_zh),
    dialogSection("精读提示", analysis.reading_note_zh),
  );
  dom.dialogContent.append(grid);
  if (analysis.key_points?.length) dom.dialogContent.append(dialogSection("关键点", analysis.key_points));
  if (analysis.methods?.length) dom.dialogContent.append(dialogSection("摘要明确提到的方法 / 数据", analysis.methods));
  dom.dialogContent.append(dialogSection("原始摘要", paper.abstract, "dialog-section--abstract"));

  const actions = el("div", "paper-actions");
  actions.append(actionLink("打开 arXiv 摘要页", paper.abs_url), actionLink("阅读 PDF", paper.pdf_url, true));
  dom.dialogContent.append(actions);
  const provider = analysis.provider === "openai" ? `OpenAI ${analysis.model || "model"}` : "本地规则";
  dom.dialogContent.append(
    el(
      "p",
      "analysis-disclaimer",
      `解读来源：${provider}；依据：${analysis.basis || "题目与摘要"}。AI 文字可能遗漏限制条件，请以论文原文为准。`,
    ),
  );
  dom.dialog.showModal();
}

let toastTimer;
function showToast(message) {
  window.clearTimeout(toastTimer);
  dom.toast.textContent = message;
  dom.toast.classList.add("is-visible");
  toastTimer = window.setTimeout(() => dom.toast.classList.remove("is-visible"), 4200);
}

function bindControls() {
  document.querySelectorAll(".scope-tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.timeMode = button.dataset.timeMode;
      document.querySelectorAll(".scope-tab").forEach((item) => item.classList.toggle("is-active", item === button));
      updatePeriodPicker(true);
      renderGrid();
    });
  });
  document.querySelectorAll(".filter-pill").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      document.querySelectorAll(".filter-pill").forEach((item) => item.classList.toggle("is-active", item === button));
      renderGrid();
    });
  });
  dom.search.addEventListener("input", () => {
    state.query = dom.search.value.trim();
    renderGrid();
  });
  dom.sort.addEventListener("change", () => {
    state.sort = dom.sort.value;
    renderGrid();
  });
  dom.periodSelect.addEventListener("change", () => {
    state.timeValue = dom.periodSelect.value;
    renderGrid();
  });
  document.querySelector(".dialog-close").addEventListener("click", () => dom.dialog.close());
  dom.dialog.addEventListener("click", (event) => {
    if (event.target === dom.dialog) dom.dialog.close();
  });
}

async function loadData() {
  try {
    const response = await fetch("./data/papers.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.data = { meta: data.meta || {}, papers: Array.isArray(data.papers) ? data.papers : [] };
    updatePeriodPicker(true);
    renderArchiveRange();
    renderStatus();
    renderFeatured();
    renderSpectrum();
    renderGrid();
    if (state.data.meta.fetch_status === "stale") showToast("本次抓取失败，正在展示上一次成功更新的数据。");
  } catch (error) {
    console.error(error);
    state.data = { meta: {}, papers: [] };
    updatePeriodPicker(true);
    renderArchiveRange();
    renderStatus();
    featuredEmpty();
    renderSpectrum();
    renderGrid();
    showToast("数据文件暂时无法读取，请稍后刷新。");
  }
}

bindControls();
loadData();
