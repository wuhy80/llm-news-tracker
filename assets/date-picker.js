// Calendar popover for the briefing date control. Picking a range reuses the
// same anchor + loadRangeData() path as the previous/next arrows in app.js.
// Pure builders are exposed via LLMDatePicker.internal for tests.
(function () {
  "use strict";
  const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];
  const TODAY_LABEL = { day: "今天", week: "本周", month: "本月", year: "今年" };

  function startOfDay(value) {
    const date = new Date(value);
    date.setHours(0, 0, 0, 0);
    return date;
  }
  function addDays(date, days) {
    const next = new Date(date);
    next.setDate(next.getDate() + days);
    return next;
  }
  function addMonths(date, months) {
    return new Date(date.getFullYear(), date.getMonth() + months, 1);
  }
  // Monday-based week start, matching getRange() in app.js.
  function mondayOf(date) {
    const day = date.getDay() || 7;
    return addDays(startOfDay(date), 1 - day);
  }
  function dateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }
  function sameDay(a, b) {
    return Boolean(a && b) && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  // Selection is capped at the newer of "now" and the latest published data so
  // the calendar can never land on a guaranteed-empty future range.
  function capFor(latestDate, now) {
    const today = startOfDay(now || new Date());
    if (!/^\d{4}-\d{2}-\d{2}$/.test(latestDate || "")) return today;
    const latest = startOfDay(new Date(`${latestDate}T12:00:00`));
    return latest.getTime() > today.getTime() ? latest : today;
  }
  function monthGrid(view) {
    const start = mondayOf(new Date(view.getFullYear(), view.getMonth(), 1));
    const cells = [];
    for (let index = 0; index < 42; index += 1) cells.push(addDays(start, index));
    return cells;
  }
  function sumCounts(dayCounts, prefixLength) {
    const sums = new Map();
    if (!dayCounts) return sums;
    Object.entries(dayCounts).forEach(([key, count]) => {
      const prefix = key.slice(0, prefixLength);
      sums.set(prefix, (sums.get(prefix) || 0) + count);
    });
    return sums;
  }
  function buildDayCells({ view, anchor, cap, dayCounts }) {
    const counts = dayCounts || {};
    return monthGrid(view).map((date) => {
      const key = dateKey(date);
      return {
        date,
        key,
        count: counts[key] || 0,
        outside: date.getMonth() !== view.getMonth(),
        disabled: date.getTime() > cap.getTime(),
        selected: sameDay(date, anchor)
      };
    });
  }
  function buildMonthCells({ view, anchor, cap, dayCounts }) {
    const sums = sumCounts(dayCounts, 7);
    const cells = [];
    for (let month = 0; month < 12; month += 1) {
      const date = new Date(view.getFullYear(), month, 1);
      cells.push({
        date,
        label: `${month + 1}月`,
        count: sums.get(dateKey(date).slice(0, 7)) || 0,
        disabled: date.getTime() > cap.getTime(),
        selected: anchor.getFullYear() === date.getFullYear() && anchor.getMonth() === month
      });
    }
    return cells;
  }
  function buildYearCells({ anchor, cap, dayCounts }) {
    const sums = sumCounts(dayCounts, 4);
    let min = anchor.getFullYear();
    if (dayCounts) {
      Object.keys(dayCounts).forEach((key) => {
        min = Math.min(min, Number(key.slice(0, 4)));
      });
    }
    const max = Math.max(cap.getFullYear(), anchor.getFullYear());
    const cells = [];
    for (let year = min; year <= max; year += 1) {
      cells.push({
        date: new Date(year, 0, 1),
        label: String(year),
        count: sums.get(String(year)) || 0,
        disabled: new Date(year, 0, 1).getTime() > cap.getTime(),
        selected: anchor.getFullYear() === year
      });
    }
    return cells;
  }

  function create({ toggle, popover, getPeriod, getAnchor, getDayCounts, getLatestDate, onSelect, onToday }) {
    if (!toggle || !popover || typeof document === "undefined") return null;
    let isOpen = false;
    let view = startOfDay(new Date());

    function el(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text != null) node.textContent = text;
      return node;
    }
    function navButton(direction, title, step) {
      const button = el("button", "dp-nav");
      button.type = "button";
      button.setAttribute("aria-label", title);
      button.innerHTML = direction < 0
        ? '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"></path></svg>'
        : '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"></path></svg>';
      button.addEventListener("click", () => {
        view = addMonths(view, direction * step);
        render();
      });
      return button;
    }
    function attachArrowKeys(grid, columns) {
      grid.addEventListener("keydown", (event) => {
        const deltas = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns };
        const delta = deltas[event.key];
        if (!delta) return;
        event.preventDefault();
        const cells = [...grid.querySelectorAll("button:not(:disabled)")];
        const index = cells.indexOf(document.activeElement);
        if (index === -1) {
          cells[0]?.focus();
          return;
        }
        cells[(index + delta + cells.length) % cells.length]?.focus();
      });
    }
    function buildHeader(period) {
      const header = el("div", "dp-header");
      if (period !== "year") header.append(navButton(-1, period === "month" ? "上一年" : "上一个月", period === "month" ? 12 : 1));
      const title = el("div", "dp-title");
      title.textContent = period === "year" ? "选择年份"
        : period === "month" ? `${view.getFullYear()}年`
        : `${view.getFullYear()}年${view.getMonth() + 1}月`;
      header.append(title);
      if (period !== "year") header.append(navButton(1, period === "month" ? "下一年" : "下一个月", period === "month" ? 12 : 1));
      const today = el("button", "dp-today", TODAY_LABEL[period] || "今天");
      today.type = "button";
      today.addEventListener("click", () => {
        close({ restoreFocus: false });
        onToday();
      });
      header.append(today);
      return header;
    }
    function buildWeekdayRow() {
      const row = el("div", "dp-weekdays");
      WEEKDAY_LABELS.forEach((label) => row.append(el("span", null, label)));
      return row;
    }
    function buildDayGrid(period, anchor) {
      const capDate = capFor(getLatestDate?.(), new Date());
      const cells = buildDayCells({ view, anchor, cap: capDate, dayCounts: getDayCounts?.() || null });
      const selectedWeek = mondayOf(anchor).getTime();
      const grid = el("div", "dp-grid");
      cells.forEach((cell) => {
        const button = el("button", "dp-cell");
        button.type = "button";
        const inWeek = period === "week" && mondayOf(cell.date).getTime() === selectedWeek;
        if (cell.outside) button.classList.add("outside");
        if (!cell.count) button.classList.add("empty");
        if (cell.selected && period === "day") button.classList.add("selected");
        if (inWeek) button.classList.add("inrange");
        button.disabled = cell.disabled;
        button.append(el("span", "dp-day", String(cell.date.getDate())));
        if (cell.count) button.append(el("small", "dp-count", cell.count.toLocaleString("zh-CN")));
        const labels = [`${cell.date.getMonth() + 1}月${cell.date.getDate()}日`, cell.count ? `${cell.count} 条动态` : "暂无动态"];
        if (cell.selected && period === "day") labels.push("当前选中");
        if (inWeek) labels.push("当前选中周");
        if (cell.disabled) labels.push("不可选");
        button.setAttribute("aria-label", labels.join("，"));
        button.addEventListener("click", () => {
          close({ restoreFocus: false });
          onSelect(period === "week" ? mondayOf(cell.date) : cell.date);
        });
        grid.append(button);
      });
      attachArrowKeys(grid, 7);
      return grid;
    }
    function buildChoiceGrid(period, anchor) {
      const capDate = capFor(getLatestDate?.(), new Date());
      const cells = period === "month"
        ? buildMonthCells({ view, anchor, cap: capDate, dayCounts: getDayCounts?.() || null })
        : buildYearCells({ anchor, cap: capDate, dayCounts: getDayCounts?.() || null });
      const grid = el("div", period === "month" ? "dp-grid months" : "dp-grid years");
      cells.forEach((cell) => {
        const button = el("button", "dp-cell");
        button.type = "button";
        if (!cell.count) button.classList.add("empty");
        if (cell.selected) button.classList.add("selected");
        button.disabled = cell.disabled;
        button.append(el("span", "dp-day", cell.label));
        if (cell.count) button.append(el("small", "dp-count", cell.count.toLocaleString("zh-CN")));
        const labels = [period === "month" ? `${view.getFullYear()}年${cell.label}` : `${cell.label}年`, cell.count ? `${cell.count} 条动态` : "暂无动态"];
        if (cell.selected) labels.push("当前选中");
        if (cell.disabled) labels.push("不可选");
        button.setAttribute("aria-label", labels.join("，"));
        button.addEventListener("click", () => {
          close({ restoreFocus: false });
          onSelect(cell.date);
        });
        grid.append(button);
      });
      attachArrowKeys(grid, period === "month" ? 3 : 4);
      return grid;
    }
    function render() {
      const period = getPeriod();
      const anchor = startOfDay(getAnchor());
      popover.replaceChildren();
      popover.append(buildHeader(period));
      if (period === "day" || period === "week") {
        popover.append(buildWeekdayRow(), buildDayGrid(period, anchor));
      } else {
        popover.append(buildChoiceGrid(period, anchor));
      }
    }
    function onDocumentPointerDown(event) {
      if (popover.contains(event.target) || toggle.contains(event.target)) return;
      close({ restoreFocus: false });
    }
    function onDocumentKeydown(event) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close();
    }
    function openPopover() {
      syncView();
      render();
      isOpen = true;
      popover.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      document.addEventListener("pointerdown", onDocumentPointerDown, true);
      document.addEventListener("keydown", onDocumentKeydown, true);
      popover.focus();
    }
    function close({ restoreFocus = true } = {}) {
      if (!isOpen) return;
      isOpen = false;
      popover.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", onDocumentPointerDown, true);
      document.removeEventListener("keydown", onDocumentKeydown, true);
      if (restoreFocus && (popover.contains(document.activeElement) || document.activeElement === toggle)) toggle.focus();
    }
    function syncView() {
      const anchor = startOfDay(getAnchor());
      view = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    }
    toggle.addEventListener("click", () => (isOpen ? close() : openPopover()));
    popover.tabIndex = -1;
    return { open: openPopover, close, render };
  }

  window.LLMDatePicker = {
    create,
    internal: { mondayOf, monthGrid, capFor, dateKey, sumCounts, buildDayCells, buildMonthCells, buildYearCells }
  };
})();
