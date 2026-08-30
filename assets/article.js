const CATEGORY_LABEL = {
  industry: "行业落地",
  agent: "Agent 技术",
  release: "模型发布",
  benchmark: "评测榜单"
};
const IMPORTANCE_LABEL = {
  5: "必须关注",
  4: "值得精读",
  3: "建议浏览",
  2: "参考信息",
  1: "可以略过"
};
const SNAPSHOT_LABEL = {
  community: "社区正文",
  feed: "Feed 正文",
  page: "网页快照",
  reader: "Reader 正文",
  summary: "摘要回退"
};
const GISCUS_CONFIG = {
  repo: "wuhy80/llm-news-tracker",
  repoId: "R_kgDOUCQp2Q",
  category: "General",
  categoryId: "DIC_kwDOUCQp2c4DEen1"
};

const elements = Object.fromEntries([
  "articleBody", "articleCategory", "articleGlossary", "articleSource", "articleSourceIcon", "articleSummary", "articleTime",
  "articleImportance", "articleReason", "articleReview",
  "articleTitle", "glossaryList", "originalLink", "readerNotice", "readerStatus", "readerSummary", "snapshotKind", "readCount", "themeButton"
].map((id) => [id, document.getElementById(id)]));

function favicon(item) {
  const domain = item.sourceDomain || new URL(item.url).hostname;
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
}

function formatDate(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false
  }).format(date);
}

function parseTags(text) {
  const match = text.match(/^(?:tags?|标签)\s*[:：]\s*(.+)$/i);
  if (!match) return [];
  return match[1]
    .split(/\s*[,，]\s*/)
    .map((tag) => tag.replace(/^#+/, "").trim())
    .filter(Boolean)
    .slice(0, 12);
}

function renderTags(tags) {
  const section = document.createElement("section");
  section.className = "reader-tags";
  section.setAttribute("aria-label", "文章相关标签");

  const label = document.createElement("strong");
  label.className = "reader-tags-label";
  label.textContent = "相关标签";

  const list = document.createElement("div");
  list.className = "reader-tag-list";
  tags.forEach((tag) => {
    const chip = document.createElement("span");
    chip.textContent = tag;
    list.append(chip);
  });
  section.append(label, list);
  return section;
}

function renderBody(body) {
  elements.articleBody.replaceChildren();
  const paragraphs = String(body || "").split(/\n\s*\n/).map((text) => text.trim()).filter(Boolean);
  if (!paragraphs.length) {
    const empty = document.createElement("p");
    empty.className = "reader-empty";
    empty.textContent = "暂无可用的内部正文，请查看原文。";
    elements.articleBody.append(empty);
    return;
  }
  paragraphs.forEach((text) => {
    const tags = parseTags(text);
    if (tags.length) {
      elements.articleBody.append(renderTags(tags));
      return;
    }
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    elements.articleBody.append(paragraph);
  });
}

function fallbackSummary(item) {
  const category = CATEGORY_LABEL[item.aiReview?.category || item.category] || "大模型行业动态";
  const topics = (item.aiReview?.tags || item.tags || []).slice(0, 3).join("、");
  const topicText = topics ? `，重点涉及${topics}` : "";
  return `本文来自${item.source}，围绕“${item.title}”介绍${category}方面的最新进展${topicText}。以下为系统保存的原文正文。`;
}

function importanceScore(item) {
  const itemReview = item.aiReview;
  if (Number.isFinite(itemReview?.importanceScore)) return itemReview.importanceScore;
  if (Number.isFinite(itemReview?.relevanceScore)) return itemReview.relevanceScore;
  return item.score || 0;
}

function importanceLevel(item) {
  if (Number.isInteger(item.aiReview?.importanceLevel)) return Math.max(1, Math.min(5, item.aiReview.importanceLevel));
  const score = importanceScore(item);
  if (score >= 85) return 5;
  if (score >= 70) return 4;
  if (score >= 50) return 3;
  if (score >= 30) return 2;
  return 1;
}

function renderGlossary(item, level) {
  elements.glossaryList.replaceChildren();
  const entries = level >= 4 && Array.isArray(item.aiReview?.glossary)
    ? item.aiReview.glossary.filter((entry) => entry?.term && entry?.explanationZh).slice(0, 8)
    : [];
  entries.forEach((entry) => {
    const term = document.createElement("dt");
    term.textContent = entry.term;
    const explanation = document.createElement("dd");
    explanation.textContent = entry.explanationZh;
    elements.glossaryList.append(term, explanation);
  });
  elements.articleGlossary.hidden = entries.length === 0;
}

function renderArticle(item, snapshot, historyEntry) {
  const kind = snapshot?.contentKind || "summary";
  const body = snapshot?.body || item.summary || "";
  const category = item.aiReview?.category || item.category;
  elements.articleTitle.closest(".reader-article").dataset.category = category;
  document.title = `${item.title} · 模型脉动`;
  elements.articleTitle.textContent = item.title;
  elements.articleCategory.textContent = CATEGORY_LABEL[category] || "行业动态";
  elements.articleTime.dateTime = item.publishedAt;
  elements.articleTime.textContent = formatDate(item.publishedAt);
  elements.articleSource.textContent = item.source;
  elements.articleSourceIcon.src = favicon(item);
  elements.articleSourceIcon.addEventListener("error", () => { elements.articleSourceIcon.hidden = true; }, { once: true });
  elements.snapshotKind.textContent = SNAPSHOT_LABEL[kind] || "内部快照";
  elements.readCount.textContent = `已读 ${historyEntry?.count || 1} 次`;
  elements.readCount.hidden = false;
  const level = importanceLevel(item);
  elements.articleImportance.textContent = level + "级 · " + IMPORTANCE_LABEL[level] + " · " + importanceScore(item) + "分";
  elements.articleImportance.dataset.level = level;
  elements.articleReason.textContent = item.aiReview?.reasonZh || "";
  elements.articleReview.hidden = !item.aiReview;
  elements.articleSummary.textContent = snapshot?.summaryZh || item.aiReview?.summaryZh || fallbackSummary(item);
  renderGlossary(item, level);
  elements.originalLink.href = item.url;
  elements.readerStatus.textContent = kind === "summary" ? "正文快照暂未取得" : "内部阅读已就绪";
  elements.readerNotice.hidden = kind !== "summary";
  elements.readerNotice.textContent = "该站点正文暂未成功下载，当前显示聚合摘要。自动任务会继续尝试补齐，也可直接查看原文。";
  renderBody(body);
}

function loadComments(articleId) {
  const container = document.getElementById("articleComments");
  if (!container || container.dataset.loaded === "true") return;
  const script = document.createElement("script");
  script.src = "https://giscus.app/client.js";
  script.dataset.repo = GISCUS_CONFIG.repo;
  script.dataset.repoId = GISCUS_CONFIG.repoId;
  script.dataset.category = GISCUS_CONFIG.category;
  script.dataset.categoryId = GISCUS_CONFIG.categoryId;
  script.dataset.mapping = "specific";
  script.dataset.term = `article:${articleId}`;
  script.dataset.strict = "1";
  script.dataset.reactionsEnabled = "1";
  script.dataset.inputPosition = "top";
  script.dataset.theme = "preferred_color_scheme";
  script.dataset.lang = "zh-CN";
  script.dataset.loading = "lazy";
  script.crossOrigin = "anonymous";
  script.async = true;
  container.dataset.loaded = "true";
  container.append(script);
}

function renderFailure(message) {
  elements.articleTitle.textContent = "文章暂时无法读取";
  elements.readerStatus.textContent = "加载失败";
  elements.readerNotice.hidden = false;
  elements.readerNotice.textContent = message;
  renderBody("");
  elements.originalLink.hidden = true;
}

function articlePath(articleId, location) {
  if (!/^[0-9a-f]{12}$/.test(articleId) || !/^\d{4}\/\d{2}\/\d{2}$/.test(location)) {
    throw new Error("文章位置无效");
  }
  return `data/articles/${location}/${articleId}.json`;
}

async function fetchRecord(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${path} HTTP ${response.status}`);
  return response.json();
}

async function loadArticle() {
  const params = new URLSearchParams(window.location.search);
  const articleId = params.get("id") || "";
  if (!/^[0-9a-f]{12}$/.test(articleId)) {
    renderFailure("文章链接无效，请返回情报流重新选择。");
    return;
  }
  try {
    const date = params.get("date") || "";
    let record = null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      record = await fetchRecord(articlePath(articleId, date.replaceAll("-", "/")));
    }
    if (!record) {
      const locator = await fetchRecord(`data/article-index/${articleId.slice(0, 2)}.json`);
      const location = locator?.[articleId];
      if (location) record = await fetchRecord(articlePath(articleId, location));
    }
    if (!record || record.id !== articleId) throw new Error("未找到该文章");
    const historyEntry = window.LLMReadingHistory.record(record, {
      href: `article.html${window.location.search}`
    });
    renderArticle(record, record, historyEntry);
    loadComments(articleId);
  } catch (error) {
    console.error("Unable to load article", error);
    renderFailure("无法加载该文章的本地记录，请稍后重试。");
  }
}

(function init() {
  const savedTheme = localStorage.getItem("llm-pulse-theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  elements.themeButton.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("llm-pulse-theme", dark ? "dark" : "light");
  });
  loadArticle();
})();
