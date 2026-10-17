(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LLMReadingHistory = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const STORAGE_KEY = "llm-pulse-reading-history-v1";
  const MAX_ENTRIES = 500;
  const ID_PATTERN = /^[0-9a-f]{12}$/i;

  function storage() {
    try {
      return typeof globalThis !== "undefined" && globalThis.localStorage ? globalThis.localStorage : null;
    } catch {
      return null;
    }
  }

  function text(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function normalizeEntry(value) {
    if (!value || !ID_PATTERN.test(value.id)) return null;
    const count = Number.isFinite(value.count) && value.count > 0 ? Math.floor(value.count) : 1;
    const firstReadAt = text(value.firstReadAt) || text(value.lastReadAt);
    const lastReadAt = text(value.lastReadAt) || firstReadAt;
    if (!firstReadAt || !lastReadAt) return null;
    return {
      id: value.id.toLowerCase(),
      title: text(value.title) || "未命名文章",
      source: text(value.source) || "未知来源",
      publishedAt: text(value.publishedAt),
      url: text(value.url),
      href: text(value.href),
      count,
      firstReadAt,
      lastReadAt
    };
  }

  function sortEntries(entries) {
    return entries.sort((a, b) => new Date(b.lastReadAt) - new Date(a.lastReadAt));
  }

  function load() {
    const currentStorage = storage();
    if (!currentStorage) return [];
    try {
      const parsed = JSON.parse(currentStorage.getItem(STORAGE_KEY) || "[]");
      if (!Array.isArray(parsed)) return [];
      return sortEntries(parsed.map(normalizeEntry).filter(Boolean)).slice(0, MAX_ENTRIES);
    } catch {
      return [];
    }
  }

  function save(entries) {
    const currentStorage = storage();
    if (!currentStorage) return;
    try {
      currentStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)));
    } catch {
      // Storage can be disabled or full; reading history is optional.
    }
  }

  function record(article, options = {}) {
    if (!article || !ID_PATTERN.test(article.id || "")) return null;
    const now = new Date().toISOString();
    const entries = load();
    const previous = entries.find((entry) => entry.id === article.id.toLowerCase());
    const entry = {
      id: article.id.toLowerCase(),
      title: text(article.title) || previous?.title || "未命名文章",
      source: text(article.source) || previous?.source || "未知来源",
      publishedAt: text(article.publishedAt) || previous?.publishedAt || "",
      url: text(article.url) || previous?.url || "",
      href: text(options.href) || previous?.href || text(article.href),
      count: (previous?.count || 0) + 1,
      firstReadAt: previous?.firstReadAt || now,
      lastReadAt: now
    };
    save(sortEntries([entry, ...entries.filter((item) => item.id !== entry.id)]));
    return entry;
  }

  function clear() {
    const currentStorage = storage();
    if (!currentStorage) return;
    try { currentStorage.removeItem(STORAGE_KEY); } catch { /* Optional feature. */ }
  }

  function stats(entries = load()) {
    const validEntries = Array.isArray(entries) ? entries.map(normalizeEntry).filter(Boolean) : [];
    return {
      articles: validEntries.length,
      opens: validEntries.reduce((total, entry) => total + entry.count, 0)
    };
  }

  return { load, record, clear, stats };
});
