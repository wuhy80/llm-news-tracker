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
  "articleTitle", "glossaryList", "originalLink", "readerNotice", "readerStatus", "readerSummary", "snapshotKind", "readCount", "themeButton",
  "translationControl", "translationProgress", "translationToggle", "translationToolbar", "wordWiseControl", "wordWiseToggle"
].map((id) => [id, document.getElementById(id)]));

const readingState = {
  body: "",
  item: null,
  location: "",
  translation: null,
  showTranslation: localStorage.getItem("llm-pulse-show-translation") !== "off",
  wordWise: localStorage.getItem("llm-pulse-word-wise") !== "off",
};

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

const CODE_LANGUAGE_ALIASES = {
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", tsx: "typescript",
  sh: "bash", shell: "bash", zsh: "bash", console: "bash",
  yml: "yaml", md: "markdown", py: "python", rb: "ruby",
};
const CODE_KEYWORDS = {
  python: new Set("and as assert async await break case class continue def del elif else except finally for from global if import in is lambda match nonlocal not or pass raise return try while with yield self True False None".split(" ")),
  javascript: new Set("as async await break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new null of return static super switch this throw try typeof var void while with yield true false undefined".split(" ")),
  typescript: new Set("as async await break case catch class const continue debugger declare default delete do else export extends finally for from function if implements import in infer instanceof interface keyof let namespace never new null of private protected public readonly return static super switch this throw try type typeof var void while with yield true false undefined".split(" ")),
  bash: new Set("if then else elif fi for while in do done case esac function select time coproc export local readonly set unset source alias unalias true false".split(" ")),
  ruby: new Set("BEGIN END alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield".split(" ")),
  sql: new Set("select from where and or as join left right inner outer on group by order having limit offset insert into values update set delete create alter drop table index null is not distinct union all case when then else end asc desc".split(" ")),
};
const CODE_LITERALS = new Set("true false null undefined None True False NaN Infinity".split(" "));

function escapeCodeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function canonicalCodeLanguage(language = "") {
  const normalized = String(language).trim().toLowerCase().replace(/^language-/, "");
  return CODE_LANGUAGE_ALIASES[normalized] || normalized;
}

function inferCodeLanguage(code, requested = "") {
  const explicit = canonicalCodeLanguage(requested);
  if (explicit) return explicit;
  const text = String(code || "");
  if (/^\s*(?:#!.*\b(?:ba|z|fi)?sh\b|(?:cd|npm|npx|pip|curl|wget|docker|git|aws)\s+)/m.test(text)) return "bash";
  if (/^\s*(?:def\s+\w+\s*\(|from\s+\w[\w.]*\s+import\s+|import\s+\w[\w.]*|class\s+\w+\s*[:(]|async\s+def\s+)/m.test(text)) return "python";
  if (/^\s*(?:const|let|var|function|interface|type|export|import\s+.+\s+from)\b/m.test(text) || /=>/.test(text)) return "javascript";
  if (/^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b/im.test(text)) return "sql";
  if (/^\s*[A-Za-z_][\w-]*:\s*[^:=]/m.test(text) && !/[;{}]\s*$/.test(text)) return "yaml";
  try {
    if (/^\s*[\[{]/.test(text)) {
      JSON.parse(text);
      return "json";
    }
  } catch (_error) {
    // Not JSON; continue with plain rendering.
  }
  return "";
}

function highlightCode(code, requestedLanguage = "") {
  const source = String(code || "");
  const language = inferCodeLanguage(source, requestedLanguage);
  const keywords = CODE_KEYWORDS[language] || CODE_KEYWORDS.javascript;
  const supportsHashComments = ["python", "bash", "ruby", "yaml", "shell"].includes(language);
  const tokenPattern = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\/[^\n]*|\/\*[\s\S]*?\*\/|#[^\n]*|\b\d+(?:\.\d+)?\b|\b[A-Za-z_$][\w$-]*\b)/g;
  let output = "";
  let cursor = 0;
  let match;
  while ((match = tokenPattern.exec(source))) {
    output += escapeCodeHtml(source.slice(cursor, match.index));
    const token = match[0];
    const next = source.slice(match.index + token.length);
    let tokenClass = "";
    if (/^(?:\/\/|\/\*)/.test(token) || (supportsHashComments && token.startsWith("#"))) {
      tokenClass = "tok-comment";
    } else if (/^[\"'`]/.test(token)) {
      const property = language === "json" && /^\s*:/.test(next);
      tokenClass = property ? "tok-property" : "tok-string";
    } else if (/^\d/.test(token)) {
      tokenClass = "tok-number";
    } else if (CODE_LITERALS.has(token) || (language === "python" && ["True", "False", "None"].includes(token))) {
      tokenClass = "tok-literal";
    } else if (keywords.has(token)) {
      tokenClass = "tok-keyword";
    } else if (/^\s*\(/.test(next)) {
      tokenClass = "tok-function";
    }
    output += tokenClass ? `<span class="${tokenClass}">${escapeCodeHtml(token)}</span>` : escapeCodeHtml(token);
    cursor = match.index + token.length;
  }
  output += escapeCodeHtml(source.slice(cursor));
  return { html: output, language };
}

function renderCode(code, language = "") {
  const pre = document.createElement("pre");
  pre.className = "reader-code";
  const highlighted = highlightCode(code, language);
  if (highlighted.language) pre.dataset.language = highlighted.language;
  const element = document.createElement("code");
  if (highlighted.language) element.className = `language-${highlighted.language}`;
  element.innerHTML = highlighted.html;
  pre.append(element);
  return pre;
}

function normalizeBodyFences(value) {
  let text = String(value || "").replace(/\r\n?/g, "\n");
  text = text.replace(/```([^\n`]*)[ \t]+([\s\S]*?)[ \t]+```/g, "\n```$1\n$2\n```\n");
  text = text.replace(/([^\n])```[ \t]*(?=\n|$)/g, "$1\n```\n");
  return text;
}

const LATEX_CONTENT_COMMANDS = [
  "textbf", "textit", "texttt", "textrm", "textsf", "textsc", "textup", "textsl",
  "emph", "textsuperscript", "textsubscript", "mathrm", "mathbf", "mathit", "mathsf",
  "mathtt", "operatorname", "underline", "overline", "text", "url", "cite", "ref"
];
const LATEX_SYMBOLS = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", theta: "θ", lambda: "λ",
  mu: "μ", pi: "π", sigma: "σ", phi: "φ", omega: "ω", times: "×", cdot: "·", pm: "±",
  leq: "≤", geq: "≥", neq: "≠", infty: "∞"
};

function normalizeProseMarkup(value) {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = String(value || "");
  let text = textarea.value;
  for (let pass = 0; pass < 5; pass += 1) {
    const before = text;
    LATEX_CONTENT_COMMANDS.forEach((command) => {
      text = text.replace(new RegExp(`\\\\${command}\\s*\\{([^{}]*)\\}`, "g"), "$1");
    });
    text = text.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "($1)/($2)");
    text = text.replace(/\\sqrt\s*\{([^{}]*)\}/g, "sqrt($1)");
    if (text === before) break;
  }
  Object.entries(LATEX_SYMBOLS).forEach(([command, symbol]) => {
    text = text.replace(new RegExp(`\\\\${command}\\b`, "g"), symbol);
  });
  text = text.replace(/\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}/g, "$1");
  text = text.replace(/\\(?:left|right)\s*/g, "");
  text = text.replace(/\\(?:quad|qquad|enspace|hspace\s*\{[^{}]*\}|[,;:!])/g, " ");
  text = text.replace(/\\([%&_#$])/g, "$1");
  text = text.replace(/\${1,2}/g, "");
  text = text.replace(/(?<!\\)(?:\^|_)\s*\{([^{}]*)\}/g, "$1");
  text = text.replace(/(?<!\\)\^\s*([A-Za-z0-9+\-=()])/g, "$1");
  text = text.replace(/\\(?:begin|end)\s*\{[^{}]*\}/g, "");
  text = text.replace(/\\[A-Za-z]+/g, "");
  text = text.replace(/(?<!\\)[{}]/g, "");
  return text.replace(/[ \t]+/g, " ").trim();
}

function wordWiseEntries() {
  const entries = [];
  const seen = new Set();
  const candidates = [
    ...(Array.isArray(readingState.translation?.wordWise) ? readingState.translation.wordWise : []),
    ...(Array.isArray(readingState.item?.aiReview?.glossary) ? readingState.item.aiReview.glossary : []),
  ];
  candidates.forEach((entry) => {
    const term = String(entry?.term || "").trim();
    const key = term.toLowerCase();
    const explanation = String(entry?.explanationZh || "").trim();
    const brief = String(entry?.briefZh || explanation.split(/[，。；：]/)[0] || "").trim().slice(0, 12);
    if (!/[A-Za-z]/.test(term) || !brief || seen.has(key)) return;
    seen.add(key);
    entries.push({ term, briefZh: brief, explanationZh: explanation || brief });
  });
  return entries.slice(0, 24);
}

function decorateWordWise(container, text, entries, usedTerms) {
  const terms = entries
    .filter((entry) => !usedTerms.has(entry.term.toLowerCase()))
    .sort((a, b) => b.term.length - a.term.length);
  if (!terms.length) {
    container.textContent = text;
    return;
  }
  const lower = text.toLowerCase();
  let cursor = 0;
  while (cursor < text.length) {
    let selected = null;
    let selectedIndex = -1;
    terms.forEach((entry) => {
      if (usedTerms.has(entry.term.toLowerCase())) return;
      const index = lower.indexOf(entry.term.toLowerCase(), cursor);
      if (index >= 0 && (selectedIndex < 0 || index < selectedIndex || (index === selectedIndex && entry.term.length > selected.term.length))) {
        selected = entry;
        selectedIndex = index;
      }
    });
    if (!selected) {
      container.append(document.createTextNode(text.slice(cursor)));
      break;
    }
    if (selectedIndex > cursor) container.append(document.createTextNode(text.slice(cursor, selectedIndex)));
    const ruby = document.createElement("ruby");
    ruby.className = "word-wise";
    ruby.tabIndex = 0;
    ruby.dataset.explanation = selected.explanationZh;
    ruby.title = selected.explanationZh;
    ruby.setAttribute("aria-label", `${text.slice(selectedIndex, selectedIndex + selected.term.length)}：${selected.explanationZh}`);
    const source = document.createElement("rb");
    source.textContent = text.slice(selectedIndex, selectedIndex + selected.term.length);
    const hint = document.createElement("rt");
    hint.textContent = selected.briefZh;
    ruby.append(source, hint);
    container.append(ruby);
    usedTerms.add(selected.term.toLowerCase());
    cursor = selectedIndex + selected.term.length;
  }
}

function translatedBlocks() {
  return new Map(
    (Array.isArray(readingState.translation?.blocks) ? readingState.translation.blocks : [])
      .filter((block) => block?.id && block?.translationZh)
      .map((block) => [block.id, block.translationZh])
  );
}

function appendReadableText(element, text, blockId, translations, wiseEntries, usedTerms) {
  element.dataset.blockId = blockId;
  const source = document.createElement("span");
  source.className = "reader-source-text";
  decorateWordWise(source, text, wiseEntries, usedTerms);
  element.append(source);
  const translated = translations.get(blockId);
  if (translated) {
    element.classList.add("has-translation");
    const translation = document.createElement("span");
    translation.className = "reader-translation";
    translation.lang = "zh-CN";
    translation.textContent = translated;
    element.append(translation);
  }
}

function applyReadingPreferences() {
  elements.articleBody.dataset.showTranslation = readingState.showTranslation ? "on" : "off";
  elements.articleBody.dataset.wordWise = readingState.wordWise ? "on" : "off";
  if (elements.translationToggle) elements.translationToggle.checked = readingState.showTranslation;
  if (elements.wordWiseToggle) elements.wordWiseToggle.checked = readingState.wordWise;
}

function updateTranslationToolbar() {
  const translations = translatedBlocks();
  const wordWise = wordWiseEntries();
  const available = translations.size > 0 || wordWise.length > 0;
  elements.translationToolbar.hidden = !available;
  elements.translationControl.hidden = translations.size === 0;
  elements.wordWiseControl.hidden = wordWise.length === 0;
  if (readingState.translation) {
    const complete = readingState.translation.status === "complete";
    const translated = Number(readingState.translation.translatedBlocks || translations.size);
    const total = Number(readingState.translation.totalBlocks || translated);
    elements.translationProgress.textContent = complete ? `译文完成 · ${total} 段` : `翻译进行中 · ${translated}/${total} 段`;
  } else {
    elements.translationProgress.textContent = wordWise.length ? "Word Wise 词汇提示" : "译文准备中";
  }
  applyReadingPreferences();
}

function translationPath(articleId, location) {
  if (!/^[0-9a-f]{12}$/.test(articleId) || !/^\d{4}\/\d{2}\/\d{2}$/.test(location)) {
    throw new Error("翻译位置无效");
  }
  return `data/translations/zh-CN/${location}/${articleId}.json`;
}

async function loadTranslation(articleId, location) {
  const record = await fetchRecord(translationPath(articleId, location));
  if (!record) return null;
  if (record.articleId !== articleId || record.targetLanguage !== "zh-CN") return null;
  if (!Array.isArray(record.blocks)) return null;
  return record;
}

function renderBody(body) {
  elements.articleBody.replaceChildren();
  const proseLines = [];
  let codeLines = null;
  let codeLanguage = "";
  let rendered = false;
  let blockNumber = 0;
  const translations = translatedBlocks();
  const wiseEntries = wordWiseEntries();
  const usedTerms = new Set();
  const nextBlockId = () => `b${String(++blockNumber).padStart(4, "0")}`;
  const appendParagraph = (lines) => {
    const text = lines.join(" ").trim();
    if (!text) return;
    const blockId = nextBlockId();
    const tags = parseTags(text);
    if (tags.length) elements.articleBody.append(renderTags(tags));
    else {
      const paragraph = document.createElement("p");
      appendReadableText(paragraph, text, blockId, translations, wiseEntries, usedTerms);
      elements.articleBody.append(paragraph);
    }
    rendered = true;
  };
  const flushProse = () => {
    const lines = proseLines.splice(0).map((line) => line.trim()).filter(Boolean);
    if (!lines.length) return;
    let paragraph = [];
    let list = null;
    const flushParagraph = () => {
      appendParagraph(paragraph);
      paragraph = [];
    };
    const flushList = () => {
      if (!list) return;
      elements.articleBody.append(list);
      list = null;
      rendered = true;
    };
    lines.forEach((line) => {
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      const unordered = line.match(/^[-*+]\s+(.+)$/);
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      const quote = line.match(/^>\s?(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        const element = document.createElement(`h${heading[1].length}`);
        appendReadableText(element, heading[2].trim(), nextBlockId(), translations, wiseEntries, usedTerms);
        elements.articleBody.append(element);
        rendered = true;
      } else if (unordered || ordered) {
        flushParagraph();
        const type = unordered ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== type) {
          flushList();
          list = document.createElement(type);
        }
        const item = document.createElement("li");
        appendReadableText(item, (unordered || ordered)[1].trim(), nextBlockId(), translations, wiseEntries, usedTerms);
        list.append(item);
      } else if (quote) {
        flushParagraph();
        flushList();
        const element = document.createElement("blockquote");
        appendReadableText(element, quote[1].trim(), nextBlockId(), translations, wiseEntries, usedTerms);
        elements.articleBody.append(element);
        rendered = true;
      } else {
        flushList();
        paragraph.push(line);
      }
    });
    flushParagraph();
    flushList();
  };
  normalizeBodyFences(body).split("\n").forEach((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      if (codeLines === null) {
        flushProse();
        codeLines = [];
        codeLanguage = trimmed.slice(3).trim();
      } else {
        elements.articleBody.append(renderCode(codeLines.join("\n"), codeLanguage));
        codeLines = null;
        codeLanguage = "";
        rendered = true;
      }
      return;
    }
    if (codeLines !== null) {
      codeLines.push(line);
      return;
    }
    if (!trimmed) {
      flushProse();
      return;
    }
    proseLines.push(normalizeProseMarkup(trimmed));
  });
  if (codeLines !== null) {
    elements.articleBody.append(renderCode(codeLines.join("\n"), codeLanguage));
    elements.articleBody.append(document.createTextNode(""));
    rendered = true;
  }
  flushProse();
  if (!rendered) {
    const empty = document.createElement("p");
    empty.className = "reader-empty";
    empty.textContent = "暂无可用的内部正文，请查看原文。";
    elements.articleBody.append(empty);
  }
  applyReadingPreferences();
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
  readingState.item = item;
  readingState.body = body;
  readingState.translation = null;
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
  readingState.item = null;
  readingState.body = "";
  readingState.translation = null;
  elements.translationToolbar.hidden = true;
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
    let location = /^\d{4}-\d{2}-\d{2}$/.test(date) ? date.replaceAll("-", "/") : "";
    let record = null;
    if (location) {
      record = await fetchRecord(articlePath(articleId, location));
    }
    if (!record) {
      const locator = await fetchRecord(`data/article-index/${articleId.slice(0, 2)}.json`);
      location = locator?.[articleId] || "";
      if (location) record = await fetchRecord(articlePath(articleId, location));
    }
    if (!record || record.id !== articleId) throw new Error("未找到该文章");
    if (!location) throw new Error("未找到该文章日期目录");
    const historyEntry = window.LLMReadingHistory.record(record, {
      href: `article.html${window.location.search}`
    });
    renderArticle(record, record, historyEntry);
    readingState.location = location;
    try {
      readingState.translation = await loadTranslation(articleId, location);
    } catch (translationError) {
      if (!String(translationError?.message || "").includes("HTTP 404")) {
        console.warn("Unable to load article translation", translationError);
      }
    }
    renderBody(readingState.body);
    updateTranslationToolbar();
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
  elements.translationToggle?.addEventListener("change", (event) => {
    readingState.showTranslation = event.target.checked;
    localStorage.setItem("llm-pulse-show-translation", readingState.showTranslation ? "on" : "off");
    applyReadingPreferences();
  });
  elements.wordWiseToggle?.addEventListener("change", (event) => {
    readingState.wordWise = event.target.checked;
    localStorage.setItem("llm-pulse-word-wise", readingState.wordWise ? "on" : "off");
    elements.articleBody.dataset.wordWise = readingState.wordWise ? "on" : "off";
  });
  loadArticle();
})();
