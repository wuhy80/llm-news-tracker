/* Inline markdown fallback for sources archived as plain text.
 *
 * renderBody() already understands block markup (headings, lists, quotes and
 * fenced code), and inline-content.js renders validated inline spans whenever
 * the archive supplies them. A source archived without those spans keeps its
 * emphasis, code and link markers as literal text, which is what a reader sees
 * as stray asterisks. This fills that gap using the same element vocabulary:
 * strong, em, code and a.
 *
 * Only asterisk emphasis is recognised. Underscore emphasis is deliberately
 * skipped: snake_case identifiers and __dunder__ names are common in archived
 * technical writing and would turn into stray emphasis instead.
 *
 * Every leaf is decorated through the caller's callback, so word-wise term
 * highlighting and the block-id contract keep working exactly as before.
 */
(() => {
  const TOKEN = /(\*\*[^\s*](?:[^*\n]*[^\s*])?\*\*)|(\*[^\s*](?:[^*\n]*[^\s*])?\*)|(`[^`\n]+`)|(\[[^\]\n]+\]\([^()\s]+\))/g;
  const LINK = /^\[([^\]\n]+)\]\(([^()\s]+)\)$/;

  function safeHref(value) {
    const shared = window.LLMInlineContent?.safeHref;
    if (typeof shared === 'function') return shared(value);
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  function leaf(container, decorate, value) {
    // decorateWordWise() replaces the container's content when it has no terms,
    // so every run needs its own element rather than sharing the container.
    const span = document.createElement('span');
    container.append(span);
    decorate(span, value);
  }

  function render(container, text, decorate) {
    const value = String(text ?? '');
    const apply = typeof decorate === 'function'
      ? decorate
      : (target, run) => { target.textContent = run; };
    if (!value) return true;

    const pattern = new RegExp(TOKEN.source, 'g');
    let cursor = 0;
    let match;
    let found = false;
    while ((match = pattern.exec(value)) !== null) {
      if (!match[0]) { pattern.lastIndex += 1; continue; }
      if (match.index > cursor) leaf(container, apply, value.slice(cursor, match.index));
      const [raw, bold, italic, code, link] = match;
      if (bold || italic) {
        const element = document.createElement(bold ? 'strong' : 'em');
        element.dataset.inlineMarkup = bold ? 'strong' : 'em';
        container.append(element);
        const inner = (bold || italic).slice(bold ? 2 : 1, bold ? -2 : -1);
        leaf(element, apply, inner);
      } else if (code) {
        const element = document.createElement('code');
        element.textContent = code.slice(1, -1);
        container.append(element);
      } else {
        const parts = LINK.exec(link);
        const href = parts ? safeHref(parts[2]) : null;
        if (!href) {
          // An unusable target must not swallow the text: show it verbatim.
          leaf(container, apply, raw);
        } else {
          const element = document.createElement('a');
          element.href = href;
          element.target = '_blank';
          element.rel = 'noopener noreferrer';
          container.append(element);
          leaf(element, apply, parts[1]);
        }
      }
      cursor = match.index + raw.length;
      found = true;
    }

    if (!found) { apply(container, value); return true; }
    if (cursor < value.length) leaf(container, apply, value.slice(cursor));
    return true;
  }

  window.LLMInlineMarkup = { render };
})();
