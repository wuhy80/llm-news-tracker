const CATEGORY = {
  industry: { label: "行业落地", color: "var(--lime-deep)" },
  agent: { label: "Agent 技术", color: "var(--blue)" },
  release: { label: "模型发布", color: "var(--coral)" },
  benchmark: { label: "评测榜单", color: "var(--amber)" }
};
const PERIOD_LABEL = { day: "日", week: "周", month: "月" };
const PAGE_SIZE = 8;
const state = {
  items: [],
  sources: [],
  generatedAt: null,
  period: "day",
  anchor: new Date(),
  aiMode: "curated",
  category: "all",
  source: "all",
  query: "",
  sort: "hot",
  visible: Number.POSITIVE_INFINITY,
  saved: new Set(JSON.parse(localStorage.getItem("llm-pulse-saved") || "[]"))
};
const el = Object.fromEntries([
  "feedList", "feedItemTemplate", "itemCount", "signalCount", "orgCount", "topTopic", "topTopicMeta",
  "resultCount", "loadMore", "rangeLabel", "previousRange", "nextRange", "sortSelect", "trendRadar",
  "trendList", "watchTags", "sourceList", "updateStatus", "sourceStatus", "searchPanel", "searchInput",
  "searchButton", "closeSearch", "mobileSearch", "themeButton", "githubLink"
].map((id) => [id, document.getElementById(id)]));

function startOfDay(value) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  return date;
}
function endOfDay(value) {
  const date = new Date(value);
  date.setHours(23, 59, 59, 999);
  return date;
}
function getRange() {
  const anchor = startOfDay(state.anchor);
  if (state.period === "day") return { start: anchor, end: endOfDay(anchor) };
  if (state.period === "week") {
    const day = anchor.getDay() || 7;
    const start = new Date(anchor);
    start.setDate(anchor.getDate() - day + 1);
    const end = endOfDay(start);
    end.setDate(start.getDate() + 6);
    return { start, end };
  }
  return {
    start: new Date(anchor.getFullYear(), anchor.getMonth(), 1),
    end: new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0, 23, 59, 59, 999)
  };
}
function formatDate(date, withYear = false) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {})
  }).format(date);
}
function updateRangeLabel() {
  const { start, end } = getRange();
  const today = startOfDay(new Date());
  if (state.period === "day") {
    el.rangeLabel.textContent = start.getTime() === today.getTime() ? "今天" : formatDate(start, start.getFullYear() !== today.getFullYear());
  } else if (state.period === "week") {
    el.rangeLabel.textContent = `${formatDate(start)} – ${formatDate(end, start.getFullYear() !== end.getFullYear())}`;
  } else {
    el.rangeLabel.textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long" }).format(start);
  }
}
function shiftRange(direction) {
  const next = new Date(state.anchor);
  if (state.period === "day") next.setDate(next.getDate() + direction);
  if (state.period === "week") next.setDate(next.getDate() + direction * 7);
  if (state.period === "month") next.setMonth(next.getMonth() + direction);
  state.anchor = next;
  resetVisible();
  render();
}
function resetVisible() {
  state.visible = state.category === "all" && state.source === "all" && !state.query.trim()
    ? Number.POSITIVE_INFINITY
    : PAGE_SIZE;
}
function relativeTime(value) {
  const date = new Date(value);
  const delta = Date.now() - date.getTime();
  const hours = Math.floor(delta / 3600000);
  if (hours < 1) return "刚刚";
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return formatDate(date, date.getFullYear() !== new Date().getFullYear());
}
function hostname(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}
function favicon(item) {
  const domain = item.sourceDomain || hostname(item.url);
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
}
function inRange(item, range) {
  const published = new Date(item.publishedAt);
  return published >= range.start && published <= range.end;
}
function rangeItems() {
  const range = getRange();
  return state.items.filter((item) => inRange(item, range));
}
function review(item) {
  return item.aiReview && typeof item.aiReview === "object" ? item.aiReview : null;
}
function itemCategory(item) {
  return review(item)?.category || item.category;
}
function itemTags(item) {
  const tags = review(item)?.tags;
  return Array.isArray(tags) && tags.length ? tags : (item.tags || []);
}
function itemSummary(item) {
  return review(item)?.summaryZh || item.summary;
}
function itemScore(item) {
  return Number.isFinite(review(item)?.relevanceScore) ? review(item).relevanceScore : (item.score || 0);
}
function aiModeMatch(item) {
  return state.aiMode === "all" || review(item)?.isRelevant !== false;
}
function filteredItems() {
  const query = state.query.trim().toLocaleLowerCase();
  const filtered = rangeItems().filter((item) => {
    const categoryMatch = state.category === "all" || itemCategory(item) === state.category;
    const sourceMatch = state.source === "all" || item.source === state.source;
    const itemReview = review(item);
    const haystack = [item.title, itemSummary(item), item.source, itemReview?.reasonZh, ...itemTags(item)].join(" ").toLocaleLowerCase();
    return aiModeMatch(item) && categoryMatch && sourceMatch && (!query || haystack.includes(query));
  });
  return filtered.sort((a, b) => state.sort === "latest"
    ? new Date(b.publishedAt) - new Date(a.publishedAt)
    : itemScore(b) - itemScore(a) || new Date(b.publishedAt) - new Date(a.publishedAt));
}
function saveBookmarks() {
  localStorage.setItem("llm-pulse-saved", JSON.stringify([...state.saved]));
}
function escapeSelector(value) {
  return window.CSS?.escape ? CSS.escape(value) : value.replace(/["\\]/g, "\\$&");
}
function renderFeed(items) {
  el.feedList.replaceChildren();
  el.resultCount.textContent = `${items.length} 条`;
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<strong>当前范围没有匹配动态</strong><br><span>可以切换时间维度、分类或搜索词</span>";
    el.feedList.append(empty);
    el.loadMore.hidden = true;
    return;
  }
  items.slice(0, state.visible).forEach((item, index) => {
    const fragment = el.feedItemTemplate.content.cloneNode(true);
    const article = fragment.querySelector(".feed-item");
    const category = itemCategory(item);
    const itemReview = review(item);
    article.dataset.category = category;
    article.style.setProperty("--index", index);
    const link = fragment.querySelector("h3 a");
    link.textContent = item.title;
    if (["community", "feed", "page", "reader"].includes(item.articleKind)) {
      link.href = `article.html?id=${encodeURIComponent(item.id)}`;
    } else {
      const title = document.createElement("span");
      title.textContent = item.title;
      link.replaceWith(title);
    }
    fragment.querySelector(".category-label").textContent = CATEGORY[category]?.label || "行业动态";
    const time = fragment.querySelector("time");
    time.dateTime = item.publishedAt;
    time.textContent = relativeTime(item.publishedAt);
    const badge = fragment.querySelector(".signal-badge");
    badge.textContent = itemReview
      ? (itemReview.isRelevant ? `AI ${itemScore(item)}` : "AI 低相关")
      : item.signal === "high" ? "高信号" : item.signal === "medium" ? "关注" : "";
    if (itemReview?.reasonZh) {
      badge.title = itemReview.reasonZh;
      badge.setAttribute("aria-label", `${badge.textContent}：${itemReview.reasonZh}`);
    }
    badge.hidden = !badge.textContent;
    fragment.querySelector(".item-summary").textContent = itemSummary(item) || "打开原文查看详情。";
    const sourceImg = fragment.querySelector(".source-identity img");
    sourceImg.src = favicon(item);
    sourceImg.alt = `${item.source} 图标`;
    sourceImg.addEventListener("error", () => { sourceImg.hidden = true; }, { once: true });
    fragment.querySelector(".source-identity span").textContent = item.source;
    const tags = fragment.querySelector(".item-tags");
    itemTags(item).slice(0, 4).forEach((tag) => {
      const chip = document.createElement("span");
      chip.textContent = tag;
      tags.append(chip);
    });
    fragment.querySelector(".external-link").href = item.url;
    const bookmark = fragment.querySelector(".bookmark");
    bookmark.classList.toggle("saved", state.saved.has(item.id));
    bookmark.setAttribute("aria-label", state.saved.has(item.id) ? "取消收藏" : "收藏");
    bookmark.addEventListener("click", () => {
      state.saved.has(item.id) ? state.saved.delete(item.id) : state.saved.add(item.id);
      saveBookmarks();
      bookmark.classList.toggle("saved", state.saved.has(item.id));
      bookmark.setAttribute("aria-label", state.saved.has(item.id) ? "取消收藏" : "收藏");
    });
    el.feedList.append(fragment);
  });
  el.loadMore.hidden = state.visible >= items.length;
}
function topicCounts(items) {
  const counts = new Map();
  items.forEach((item) => itemTags(item).forEach((tag) => {
    if (tag.length > 1) counts.set(tag, (counts.get(tag) || 0) + 1);
  }));
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}
function renderMetrics(items) {
  const topics = topicCounts(items);
  el.itemCount.textContent = items.length;
  el.signalCount.textContent = items.filter((item) => itemScore(item) >= 82).length;
  el.orgCount.textContent = new Set(items.map((item) => item.source)).size;
  el.topTopic.textContent = topics[0]?.[0] || "暂无";
  el.topTopicMeta.textContent = topics[0] ? `出现 ${topics[0][1]} 次 · ${PERIOD_LABEL[state.period]}度信号` : "等待更多数据";
}
function renderTrends(items) {
  const counts = Object.keys(CATEGORY).map((category) => ({
    category,
    count: items.filter((item) => itemCategory(item) === category).length
  }));
  const max = Math.max(1, ...counts.map((entry) => entry.count));
  el.trendRadar.replaceChildren();
  counts.forEach(({ category, count }) => {
    const bar = document.createElement("div");
    bar.className = "radar-bar";
    bar.style.setProperty("--bar", CATEGORY[category].color);
    bar.innerHTML = `<i style="height:${Math.max(5, count / max * 100)}%"></i><span>${CATEGORY[category].label.slice(0, 2)}</span>`;
    el.trendRadar.append(bar);
  });
  const topics = topicCounts(items);
  el.trendList.replaceChildren();
  topics.slice(0, 5).forEach(([topic, count], index) => {
    const row = document.createElement("div");
    row.className = "trend-row";
    row.innerHTML = `<b>${String(index + 1).padStart(2, "0")}</b><div><strong></strong><small>${count} 条相关动态</small></div><span>↑</span>`;
    row.querySelector("strong").textContent = topic;
    el.trendList.append(row);
  });
  el.watchTags.replaceChildren();
  topics.slice(0, 10).forEach(([topic]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = topic;
    button.addEventListener("click", () => {
      el.searchPanel.hidden = false;
      el.searchInput.value = topic;
      state.query = topic;
      resetVisible();
      render();
      window.scrollTo({ top: el.searchPanel.offsetTop - 110, behavior: "smooth" });
    });
    el.watchTags.append(button);
  });
}
function renderSources() {
  const counts = new Map();
  state.items.forEach((item) => counts.set(item.source, (counts.get(item.source) || 0) + 1));
  const sources = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7);
  el.sourceList.replaceChildren();

  const allSources = document.createElement("button");
  allSources.type = "button";
  allSources.className = "source-row source-row-all";
  allSources.classList.toggle("active", state.source === "all");
  allSources.setAttribute("aria-pressed", state.source === "all");
  allSources.innerHTML = `<span class="source-all-mark" aria-hidden="true"></span><span>全部来源</span><small>${state.items.length} 条</small>`;
  allSources.addEventListener("click", () => selectSource("all"));
  el.sourceList.append(allSources);

  sources.forEach(([name, count]) => {
    const item = state.items.find((entry) => entry.source === name);
    const row = document.createElement("button");
    row.type = "button";
    row.className = "source-row";
    row.classList.toggle("active", state.source === name);
    row.setAttribute("aria-pressed", state.source === name);
    row.innerHTML = `<img alt="" width="20" height="20"><span></span><small>${count} 条</small>`;
    row.querySelector("img").src = favicon(item);
    row.querySelector("img").addEventListener("error", (event) => { event.currentTarget.hidden = true; }, { once: true });
    row.querySelector("span").textContent = name;
    row.addEventListener("click", () => selectSource(state.source === name ? "all" : name));
    el.sourceList.append(row);
  });
}
function selectSource(source) {
  state.source = source;
  resetVisible();
  renderSources();
  render();
  document.querySelector(".feed").scrollIntoView({ behavior: "smooth", block: "start" });
}
function render() {
  updateRangeLabel();
  const items = filteredItems();
  renderFeed(items);
  renderMetrics(items);
  renderTrends(rangeItems().filter(aiModeMatch));
}
function openSearch() {
  el.searchPanel.hidden = false;
  el.searchInput.focus();
  el.searchPanel.scrollIntoView({ behavior: "smooth", block: "center" });
}
function closeSearch() {
  state.query = "";
  el.searchInput.value = "";
  el.searchPanel.hidden = true;
  resetVisible();
  render();
}
function setupEvents() {
  document.querySelectorAll("[data-ai-mode]").forEach((button) => button.addEventListener("click", () => {
    state.aiMode = button.dataset.aiMode;
    resetVisible();
    document.querySelectorAll("[data-ai-mode]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", active);
    });
    render();
  }));
  document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => {
    state.period = button.dataset.period;
    resetVisible();
    document.querySelectorAll("[data-period]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", active);
    });
    render();
  }));
  document.querySelectorAll("[data-category]").forEach((button) => button.addEventListener("click", () => {
    state.category = button.dataset.category;
    if (state.category === "all") {
      state.query = "";
      el.searchInput.value = "";
      el.searchPanel.hidden = true;
    }
    resetVisible();
    document.querySelectorAll("[data-category]").forEach((item) => item.classList.toggle("active", item === button));
    render();
  }));
  el.previousRange.addEventListener("click", () => shiftRange(-1));
  el.nextRange.addEventListener("click", () => shiftRange(1));
  el.rangeLabel.addEventListener("click", () => { state.anchor = new Date(); resetVisible(); render(); });
  el.sortSelect.addEventListener("change", () => { state.sort = el.sortSelect.value; resetVisible(); render(); });
  el.searchButton.addEventListener("click", openSearch);
  el.mobileSearch.addEventListener("click", openSearch);
  el.closeSearch.addEventListener("click", closeSearch);
  el.searchInput.addEventListener("input", () => { state.query = el.searchInput.value; resetVisible(); render(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement?.tagName !== "INPUT") { event.preventDefault(); openSearch(); }
    if (event.key === "Escape" && !el.searchPanel.hidden) closeSearch();
  });
  el.loadMore.addEventListener("click", () => { state.visible += PAGE_SIZE; renderFeed(filteredItems()); });
  el.themeButton.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("llm-pulse-theme", dark ? "dark" : "light");
    el.themeButton.setAttribute("aria-label", dark ? "切换浅色模式" : "切换深色模式");
  });
}
function configureRepositoryLink() {
  const host = window.location.hostname;
  if (!host.endsWith(".github.io")) return;
  const owner = host.split(".")[0];
  const repo = window.location.pathname.split("/").filter(Boolean)[0] || `${owner}.github.io`;
  el.githubLink.href = `https://github.com/${owner}/${repo}`;
}
async function loadData() {
  try {
    const response = await fetch("data/news.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.items = Array.isArray(data.items) ? data.items : [];
    state.sources = Array.isArray(data.sources) ? data.sources : [];
    state.generatedAt = data.generatedAt;
    if (state.items.length && !state.items.some((item) => inRange(item, getRange()))) {
      state.anchor = new Date(Math.max(...state.items.map((item) => new Date(item.publishedAt).getTime())));
    }
    const updated = state.generatedAt ? new Date(state.generatedAt) : null;
    el.updateStatus.textContent = updated && !Number.isNaN(updated.getTime())
      ? `更新于 ${new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(updated)}`
      : "已载入本地数据";
    el.sourceStatus.textContent = `${state.sources.length || new Set(state.items.map((item) => item.source)).size} 个来源`;
  } catch (error) {
    console.error("Unable to load news data", error);
    el.updateStatus.textContent = "数据暂时不可用";
  }
  renderSources();
  render();
}
(function init() {
  const savedTheme = localStorage.getItem("llm-pulse-theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  document.getElementById("footerYear").textContent = new Date().getFullYear();
  configureRepositoryLink();
  setupEvents();
  loadData();
})();
