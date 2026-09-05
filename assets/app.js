const CATEGORY = {
  industry: { label: "行业落地", color: "var(--lime-deep)" },
  agent: { label: "Agent 技术", color: "var(--blue)" },
  release: { label: "模型发布", color: "var(--coral)" },
  benchmark: { label: "评测榜单", color: "var(--amber)" }
};
const PERIOD_LABEL = { day: "日", week: "周", month: "月" };
const IMPORTANCE_LABEL = {
  5: "必须关注",
  4: "值得精读",
  3: "建议浏览",
  2: "参考信息",
  1: "可以略过"
};
const PAGE_SIZE = 8;
const state = {
  items: [],
  sources: [],
  generatedAt: null,
  manifest: null,
  shardCache: new Map(),
  loadVersion: 0,
  period: "day",
  anchor: new Date(),
  aiMode: "curated",
  category: "all",
  importance: "all",
  source: "all",
  query: "",
  sort: "hot",
  translation: "all",
  translations: {},
  visible: PAGE_SIZE,
  saved: new Set(JSON.parse(localStorage.getItem("llm-pulse-saved") || "[]")),
  history: window.LLMReadingHistory.load()
};
const el = Object.fromEntries([
  "feedList", "feedItemTemplate", "itemCount", "signalCount", "levelDistribution", "orgCount", "topTopic", "topTopicMeta",
  "resultCount", "loadMore", "rangeLabel", "previousRange", "nextRange", "sortSelect", "importanceSelect", "translationSelect", "trendRadar",
  "trendList", "watchTags", "sourceList", "updateStatus", "sourceStatus", "searchPanel", "searchInput",
  "searchButton", "closeSearch", "mobileSearch", "themeButton", "githubLink", "historyList", "historySummary", "clearHistory"
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
  loadRangeData();
}
function resetVisible() {
  state.visible = PAGE_SIZE;
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
  const itemReview = review(item);
  if (Number.isFinite(itemReview?.importanceScore)) return itemReview.importanceScore;
  if (Number.isFinite(itemReview?.relevanceScore)) return itemReview.relevanceScore;
  return item.score || 0;
}
function levelForScore(score) {
  if (score >= 85) return 5;
  if (score >= 70) return 4;
  if (score >= 50) return 3;
  if (score >= 30) return 2;
  return 1;
}
function itemLevel(item) {
  return Number.isInteger(review(item)?.importanceLevel)
    ? Math.max(1, Math.min(5, review(item).importanceLevel))
    : levelForScore(itemScore(item));
}
function aiModeMatch(item) {
  return state.aiMode === "all" || (review(item)?.isRelevant !== false && itemLevel(item) >= 3);
}
function importanceMatch(item) {
  return state.importance === "all" || itemLevel(item) >= Number(state.importance);
}
function translationRecord(item) {
  return state.translations[item.id] || null;
}
function translationStatus(item) {
  const record = translationRecord(item);
  if (!record) return "none";
  return record.status === "complete" ? "complete" : "partial";
}
function translationMatch(item) {
  return state.translation === "all" || translationStatus(item) === state.translation;
}
function filteredItems() {
  const query = state.query.trim().toLocaleLowerCase();
  const filtered = rangeItems().filter((item) => {
    const categoryMatch = state.category === "all" || itemCategory(item) === state.category;
    const sourceMatch = state.source === "all" || item.source === state.source;
    const itemReview = review(item);
    const haystack = [item.title, itemSummary(item), item.source, itemReview?.reasonZh, ...itemTags(item)].join(" ").toLocaleLowerCase();
    return aiModeMatch(item) && importanceMatch(item) && translationMatch(item) && categoryMatch && sourceMatch && (!query || haystack.includes(query));
  });
  return filtered.sort((a, b) => state.sort === "latest"
    ? new Date(b.publishedAt) - new Date(a.publishedAt)
    : itemLevel(b) - itemLevel(a) || itemScore(b) - itemScore(a) || new Date(b.publishedAt) - new Date(a.publishedAt));
}
function saveBookmarks() {
  localStorage.setItem("llm-pulse-saved", JSON.stringify([...state.saved]));
}
function readingEntry(id) {
  return state.history.find((entry) => entry.id === id);
}
function recordReading(item, href) {
  const entry = window.LLMReadingHistory.record(item, { href });
  if (!entry) return null;
  state.history = [entry, ...state.history.filter((item) => item.id !== entry.id)];
  renderHistory();
  render();
  return entry;
}
function isInternalArticleHref(href) {
  return /^article\.html(?:\?|$)/.test(href || "");
}
function renderHistory() {
  const stats = window.LLMReadingHistory.stats(state.history);
  el.historySummary.textContent = stats.articles
    ? `已读 ${stats.articles} 篇 · 打开 ${stats.opens} 次`
    : "还没有阅读记录";
  el.historyList.replaceChildren();
  if (!state.history.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "打开一篇文章后，最近阅读会显示在这里。";
    el.historyList.append(empty);
    return;
  }
  state.history.slice(0, 6).forEach((entry) => {
    const href = entry.href || entry.url || "#";
    const link = document.createElement("a");
    link.className = "history-item";
    link.href = href;
    if (!isInternalArticleHref(href)) {
      link.target = "_blank";
      link.rel = "noreferrer";
    }
    const title = document.createElement("strong");
    title.className = "history-item-title";
    title.textContent = entry.title;
    const meta = document.createElement("span");
    meta.className = "history-item-meta";
    meta.textContent = `${entry.source} · ${relativeTime(entry.lastReadAt)} · ${entry.count} 次`;
    link.append(title, meta);
    el.historyList.append(link);
  });
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
    const level = itemLevel(item);
    article.dataset.category = category;
    article.dataset.level = level;
    article.style.setProperty("--index", index);
    const link = fragment.querySelector("h3 a");
    link.textContent = item.title;
    if (["community", "feed", "page", "reader"].includes(item.articleKind)) {
      const date = item.publishedAt.slice(0, 10);
      link.href = `article.html?id=${encodeURIComponent(item.id)}&date=${encodeURIComponent(date)}`;
    } else {
      const title = document.createElement("span");
      title.textContent = item.title;
      link.replaceWith(title);
    }
    fragment.querySelector(".category-label").textContent = CATEGORY[category]?.label || "行业动态";
    const time = fragment.querySelector("time");
    time.dateTime = item.publishedAt;
    time.textContent = relativeTime(item.publishedAt);
    const readStatus = fragment.querySelector(".read-status");
    const historyEntry = readingEntry(item.id);
    if (historyEntry) {
      readStatus.textContent = `已读 ${historyEntry.count} 次`;
      readStatus.hidden = false;
    }
    const badge = fragment.querySelector(".signal-badge");
    badge.textContent = level + "级 · " + IMPORTANCE_LABEL[level] + " · " + itemScore(item) + "分";
    if (itemReview?.reasonZh) {
      badge.title = itemReview.reasonZh;
      badge.setAttribute("aria-label", `${badge.textContent}：${itemReview.reasonZh}`);
    }
    badge.hidden = !badge.textContent;
    const translationBadge = fragment.querySelector(".translation-badge");
    const translation = translationRecord(item);
    if (translation?.status === "complete") {
      translationBadge.textContent = "中文已完成";
      translationBadge.classList.add("complete");
      translationBadge.hidden = false;
    } else if (translation) {
      translationBadge.textContent = `翻译中 ${translation.translatedBlocks || 0}/${translation.totalBlocks || 0}`;
      translationBadge.classList.add("partial");
      translationBadge.hidden = false;
    }
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
    const externalLink = fragment.querySelector(".external-link");
    externalLink.href = item.url;
    externalLink.addEventListener("click", () => recordReading(item, item.url));
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
  el.signalCount.textContent = items.filter((item) => itemLevel(item) === 5).length;
  el.levelDistribution.textContent = [5, 4, 3, 2, 1]
    .map((level) => level + "级 " + items.filter((item) => itemLevel(item) === level).length)
    .join(" · ");
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
  const currentItems = rangeItems();
  const counts = new Map();
  currentItems.forEach((item) => counts.set(item.source, (counts.get(item.source) || 0) + 1));
  const sourceMeta = new Map(state.sources.map((source) => [source.name, source]));
  const sourceNames = new Set(sourceMeta.keys());
  currentItems.forEach((item) => sourceNames.add(item.source));
  const sources = [...sourceNames]
    .map((name) => [name, counts.get(name) || 0])
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"));
  el.sourceList.replaceChildren();

  const allSources = document.createElement("button");
  allSources.type = "button";
  allSources.className = "source-row source-row-all";
  allSources.classList.toggle("active", state.source === "all");
  allSources.setAttribute("aria-pressed", state.source === "all");
  allSources.innerHTML = `<span class="source-all-mark" aria-hidden="true"></span><span>全部来源</span><small>${currentItems.length} 条</small>`;
  allSources.addEventListener("click", () => selectSource("all"));
  el.sourceList.append(allSources);

  sources.forEach(([name, count]) => {
    const item = currentItems.find((entry) => entry.source === name) || {
      source: name,
      sourceDomain: hostname(sourceMeta.get(name)?.url || ""),
      url: sourceMeta.get(name)?.url || "https://example.com"
    };
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
    loadRangeData();
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
  el.rangeLabel.addEventListener("click", () => { state.anchor = new Date(); resetVisible(); loadRangeData(); });
  el.sortSelect.addEventListener("change", () => { state.sort = el.sortSelect.value; resetVisible(); render(); });
  el.importanceSelect.addEventListener("change", () => { state.importance = el.importanceSelect.value; resetVisible(); render(); });
  el.translationSelect.addEventListener("change", () => { state.translation = el.translationSelect.value; resetVisible(); render(); });
  el.searchButton.addEventListener("click", openSearch);
  el.mobileSearch.addEventListener("click", openSearch);
  el.closeSearch.addEventListener("click", closeSearch);
  el.searchInput.addEventListener("input", () => { state.query = el.searchInput.value; resetVisible(); render(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement?.tagName !== "INPUT") { event.preventDefault(); openSearch(); }
    if (event.key === "Escape" && !el.searchPanel.hidden) closeSearch();
  });
  el.loadMore.addEventListener("click", () => { state.visible += PAGE_SIZE; renderFeed(filteredItems()); });
  el.clearHistory.addEventListener("click", () => {
    if (!state.history.length || !window.confirm("确定清空全部阅读历史吗？")) return;
    window.LLMReadingHistory.clear();
    state.history = [];
    renderHistory();
    render();
  });
  window.addEventListener("pageshow", () => {
    state.history = window.LLMReadingHistory.load();
    renderHistory();
    render();
  });
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

function utcDateKey(date) {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function shardDatesForRange() {
  const { start, end } = getRange();
  const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
  const last = Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate());
  const dates = [];
  while (cursor.getTime() <= last) {
    dates.push(utcDateKey(cursor));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}

async function loadShard(date) {
  if (state.shardCache.has(date)) return state.shardCache.get(date);
  const [year, month, day] = date.split("-");
  const response = await fetch(`data/news/${year}/${month}/${day}.json`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${date} HTTP ${response.status}`);
  const shard = await response.json();
  const items = Array.isArray(shard.items) ? shard.items : [];
  state.shardCache.set(date, items);
  return items;
}

async function loadRangeData() {
  updateRangeLabel();
  if (!state.manifest) {
    renderSources();
    render();
    return;
  }
  const version = ++state.loadVersion;
  const dates = shardDatesForRange().filter((date) => state.manifest.days?.[date]);
  try {
    const shards = await Promise.all(dates.map(loadShard));
    if (version !== state.loadVersion) return;
    state.items = shards.flat();
    renderSources();
    render();
  } catch (error) {
    if (version !== state.loadVersion) return;
    console.error("Unable to load daily news data", error);
    state.items = [];
    el.updateStatus.textContent = "当前时段数据暂时不可用";
    renderSources();
    render();
  }
}

async function loadData() {
  try {
    const [response, translationResponse] = await Promise.all([
      fetch("data/news.json", { cache: "no-store" }),
      fetch("data/translations/index.json", { cache: "no-store" }),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (translationResponse.ok) {
      const translationData = await translationResponse.json();
      state.translations = translationData.articles && typeof translationData.articles === "object"
        ? translationData.articles
        : {};
    }
    state.sources = Array.isArray(data.sources) ? data.sources : [];
    state.generatedAt = data.generatedAt;
    if (Array.isArray(data.items)) {
      state.items = data.items;
      if (state.items.length && !state.items.some((item) => inRange(item, getRange()))) {
        state.anchor = new Date(Math.max(...state.items.map((item) => new Date(item.publishedAt).getTime())));
      }
    } else {
      state.manifest = data;
      const available = shardDatesForRange().some((date) => data.days?.[date]);
      if (!available && /^\d{4}-\d{2}-\d{2}$/.test(data.latestDate || "")) {
        state.anchor = new Date(`${data.latestDate}T12:00:00`);
      }
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
  if (state.manifest) {
    await loadRangeData();
  } else {
    renderSources();
    render();
  }
}
(function init() {
  const savedTheme = localStorage.getItem("llm-pulse-theme");
  if (savedTheme) document.documentElement.dataset.theme = savedTheme;
  document.getElementById("footerYear").textContent = new Date().getFullYear();
  configureRepositoryLink();
  setupEvents();
  renderHistory();
  loadData();
})();
