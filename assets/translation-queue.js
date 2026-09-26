(() => {
  let queuePromise;
  const repo = 'https://github.com/wuhy80/llm-news-tracker';
  function queue() {
    if (!queuePromise) queuePromise = fetch('data/translations/queue.json', {cache: 'no-store'})
      .then(response => response.ok ? response.json() : {})
      .then(data => data.articles || {}).catch(() => ({}));
    return queuePromise;
  }
  function mount(link, item, complete = false) {
    if (!link || !item || !/^[0-9a-f]{12}$/.test(item.id)) return;
    link.dataset.articleId = item.id;
    link.hidden = complete;
    if (complete) return;
    const url = new URL(repo + '/issues/new');
    url.searchParams.set('title', `[优先翻译] ${item.id}`);
    url.searchParams.set('body', `请优先翻译这篇文章：\n\n${String(item.title || '')}\n\nhttps://wuhy80.github.io/llm-news-tracker/article.html?id=${item.id}\n\n提交此 Issue 后即保存翻译请求，后台将按手动优先、最新请求优先处理。\n仅仓库有写入权限的维护者可触发；仍遵守每日额度和失败重试间隔。关闭此 Issue 可取消请求（已运行中的任务可能继续至本批结束）。`);
    link.href = url.href;
    link.textContent = '优先翻译 ↗';
    link.title = '在 GitHub 确认提交后优先入队（需仓库写入权限）；不是立即完成翻译';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    queue().then(records => {
      if (link.hidden || link.dataset.articleId !== item.id) return;
      const record = records[item.id];
      if (!record || !Number.isInteger(record.issueNumber)) return;
      // Queue JSON is a last-run snapshot, not a live runner status.
      if (record.status === 'complete') return;
      link.href = `${repo}/issues/${record.issueNumber}`;
      const labels = {queued: '已请求优先翻译', partial: '优先翻译 · 部分完成', retry_wait: '优先翻译 · 等待重试', awaiting_body: '已请求 · 等待正文', not_required: '不适用当前翻译规则', not_found: '请求文章未找到'};
      link.textContent = `${labels[record.status] || '查看翻译请求'} ↗`;
      link.title = '查看 GitHub 翻译请求；状态为最近一次任务同步结果。关闭后重新打开请求可再次触发任务。';
    });
  }
  window.LLMTranslationQueue = {mount};
})();
