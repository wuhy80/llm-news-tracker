/* Verified source positions only; ambiguous and legacy media stay in an attachment section. */
(() => {
  const generations = new WeakMap();
  function httpUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url : null;
    } catch { return null; }
  }
  function figureFor(type, media) {
    const figure = document.createElement('figure');
    figure.className = 'reader-figure';
    let element;
    if (type === 'image') {
      const src = String(media.src || '');
      if (!/^data\/article-media\/[a-zA-Z0-9_./-]+$/.test(src) && !httpUrl(src)) return null;
      if (src.split('/').includes('..')) return null;
      element = document.createElement('img');
      element.src = src;
      element.alt = String(media.alt || '文章配图');
      element.loading = 'lazy';
      element.decoding = 'async';
      element.addEventListener('error', () => figure.remove(), {once: true});
    } else {
      const url = httpUrl(media.src);
      if (!url) return null;
      if (media.kind === 'embed') {
        const youtube = url.hostname === 'www.youtube-nocookie.com' && /^\/embed\/[\w-]{11}$/.test(url.pathname);
        const vimeo = url.hostname === 'player.vimeo.com' && /^\/video\/\d+$/.test(url.pathname);
        if (url.protocol !== 'https:' || (!youtube && !vimeo)) return null;
        url.search = '';
        element = document.createElement('iframe');
        element.title = String(media.title || '原文视频');
        element.loading = 'lazy';
        element.allow = 'encrypted-media; picture-in-picture; fullscreen';
        element.allowFullscreen = true;
        element.referrerPolicy = 'strict-origin-when-cross-origin';
      } else if (media.kind === 'video' && /\.(mp4|webm|ogg|ogv)$/i.test(url.pathname)) {
        element = document.createElement('video');
        element.controls = true;
        element.preload = 'none';
        element.playsInline = true;
        if (httpUrl(media.poster)) element.poster = media.poster;
      } else return null;
      element.src = url.href;
      figure.classList.add('reader-video');
    }
    figure.append(element);
    if (type === 'video' || media.alt) {
      const caption = document.createElement('figcaption');
      if (type === 'image') caption.textContent = String(media.alt);
      else {
        const link = document.createElement('a');
        link.href = httpUrl(media.originalUrl)?.href || element.src;
        link.textContent = '打开原视频 ↗';
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        caption.append(link);
      }
      figure.append(caption);
    }
    return figure;
  }
  async function render(item, body, container) {
    const generation = (generations.get(container) || 0) + 1;
    generations.set(container, generation);
    const resources = new Map();
    for (const [type, values] of [['image', item?.images], ['video', item?.videos]]) {
      (Array.isArray(values) ? values : []).forEach((media, index) => {
        if (media && typeof media === 'object') resources.set(media.id || `legacy-${type}-${index}`, {type, media});
      });
    }
    if (!resources.size) return;
    let verified = false;
    if (item?.mediaLayoutVersion === 1 && /^[a-f0-9]{64}$/.test(item.mediaLayoutBodyHash || '') && globalThis.crypto?.subtle) {
      try {
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body || ''));
        const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
        verified = hash === item.mediaLayoutBodyHash;
      } catch { /* Safe attachment fallback if digest is unavailable. */ }
    }
    if (generations.get(container) !== generation) return;
    const anchors = new Map(Array.from(container.querySelectorAll('[data-block-id]'), element => [element.dataset.blockId, element]));
    const cursors = new Map();
    const placed = new Set();
    const entries = verified && Array.isArray(item.mediaLayout) ? [...item.mediaLayout].filter(e => e && typeof e === "object").sort((a, b) => a.order - b.order) : [];
    for (const entry of entries) {
      if (!entry || !resources.has(entry.mediaId)) continue;
      const resource = resources.get(entry.mediaId);
      if (entry.type !== resource.type) continue;
      const anchor = entry.afterBlockId;
      if (anchor !== null && !anchors.has(anchor)) continue;
      const figure = figureFor(resource.type, resource.media);
      if (!figure) continue;
      const cursor = cursors.get(anchor);
      if (cursor) cursor.after(figure);
      else if (anchor === null) container.prepend(figure);
      else if (anchors.get(anchor).tagName === 'LI') anchors.get(anchor).append(figure);
      else anchors.get(anchor).after(figure);
      cursors.set(anchor, figure);
      placed.add(entry.mediaId);
    }
    const attachments = document.createElement('details');
    attachments.className = 'reader-media-attachments';
    const summary = document.createElement('summary');
    summary.textContent = '原文媒体附件（位置待恢复）';
    attachments.append(summary);
    // If one repeated occurrence is unresolved, preserve it here as well.
    const unresolved = new Set((Array.isArray(item?.mediaLayoutUnplaced) ? item.mediaLayoutUnplaced : []).map(e => e.mediaId));
    for (const [id, resource] of resources) {
      if (placed.has(id) && !unresolved.has(id)) continue;
      const figure = figureFor(resource.type, resource.media);
      if (figure) attachments.append(figure);
    }
    if (attachments.children.length > 1) container.append(attachments);
  }
  window.LLMMediaLayout = {render};
})();
