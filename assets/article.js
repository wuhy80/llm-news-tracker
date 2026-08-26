const CATEGORY_LABEL = {
  industry: "行业落地",
  agent: "Agent 技术",
  release: "模型发布",
  benchmark: "评测榜单"
};
const SNAPSHOT_LABEL = {
  community: "社区正文",
  feed: "Feed 正文",
  page: "网页快照",
  reader: "Reader 正文",
  summary: "摘要回退"
};

const elements = Object.fromEntries([
  "articleBody", "articleCategory", "articleSource", "articleSourceIcon", "articleSummary", "articleTime",
  "articleTitle", "originalLink", "readerNotice", "readerStatus", "readerSummary", "snapshotKind", "themeButton"
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

function renderArticle(item, snapshot) {
  const kind = snapshot?.contentKind || "summary";
  const body = snapshot?.body || item.summary || "";
  const category = item.aiReview?.category || item.category;
  document.title = `${item.title} · 模型脉动`;
  elements.articleTitle.textContent = item.title;
  elements.articleCategory.textContent = CATEGORY_LABEL[category] || "行业动态";
  elements.articleTime.dateTime = item.publishedAt;
  elements.articleTime.textContent = formatDate(item.publishedAt);
  elements.articleSource.textContent = item.source;
  elements.articleSourceIcon.src = favicon(item);
  elements.articleSourceIcon.addEventListener("error", () => { elements.articleSourceIcon.hidden = true; }, { once: true });
  elements.snapshotKind.textContent = SNAPSHOT_LABEL[kind] || "内部快照";
  elements.articleSummary.textContent = snapshot?.summaryZh || item.aiReview?.summaryZh || fallbackSummary(item);
  elements.originalLink.href = item.url;
  elements.readerStatus.textContent = kind === "summary" ? "正文快照暂未取得" : "内部阅读已就绪";
  elements.readerNotice.hidden = kind !== "summary";
  elements.readerNotice.textContent = "该站点正文暂未成功下载，当前显示聚合摘要。自动任务会继续尝试补齐，也可直接查看原文。";
  renderBody(body);
}

function renderFailure(message) {
  elements.articleTitle.textContent = "文章暂时无法读取";
  elements.readerStatus.textContent = "加载失败";
  elements.readerNotice.hidden = false;
  elements.readerNotice.textContent = message;
  renderBody("");
  elements.originalLink.hidden = true;
}

async function loadArticle() {
  const articleId = new URLSearchParams(window.location.search).get("id") || "";
  if (!/^[0-9a-f]{12}$/.test(articleId)) {
    renderFailure("文章链接无效，请返回情报流重新选择。");
    return;
  }
  try {
    const newsResponse = await fetch("data/news.json", { cache: "no-store" });
    if (!newsResponse.ok) throw new Error(`news HTTP ${newsResponse.status}`);
    const news = await newsResponse.json();
    const item = news.items?.find((entry) => entry.id === articleId);
    if (!item) throw new Error("未找到该文章");
    let snapshot = null;
    try {
      const snapshotResponse = await fetch(`data/articles/${articleId}.json`, { cache: "no-store" });
      if (snapshotResponse.ok) snapshot = await snapshotResponse.json();
    } catch (error) {
      console.warn("Article snapshot unavailable", error);
    }
    renderArticle(item, snapshot);
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
