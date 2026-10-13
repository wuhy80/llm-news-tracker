const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = { window: {} };
vm.createContext(context);
vm.runInContext(fs.readFileSync('assets/date-picker.js', 'utf8'), context);
const { mondayOf, monthGrid, capFor, dateKey, sumCounts, buildDayCells, buildMonthCells, buildYearCells } = context.window.LLMDatePicker.internal;

// Monday-based weeks, matching getRange() in app.js (getDay() || 7).
assert.equal(dateKey(mondayOf(new Date(2026, 9, 5))), '2026-10-05');
assert.equal(dateKey(mondayOf(new Date(2026, 9, 7))), '2026-10-05');
assert.equal(dateKey(mondayOf(new Date(2026, 9, 11))), '2026-10-05');
assert.equal(dateKey(mondayOf(new Date(2025, 0, 1))), '2024-12-30');

// 42-cell month grid starting on Monday of the first week.
const grid = monthGrid(new Date(2026, 9, 15));
assert.equal(grid.length, 42);
assert.equal(dateKey(grid[0]), '2026-09-28');
assert.equal(dateKey(grid[41]), '2026-11-08');

// Cap: never beyond now, extended when published data is newer, junk ignored.
const now = new Date(2026, 9, 13, 15, 40);
assert.equal(dateKey(capFor('2026-10-10', now)), '2026-10-13');
assert.equal(dateKey(capFor('2026-10-20', now)), '2026-10-20');
assert.equal(dateKey(capFor('', now)), '2026-10-13');
assert.equal(dateKey(capFor('garbage', now)), '2026-10-13');
assert.equal(dateKey(capFor(null)), dateKey(new Date()));

// Per-month and per-year aggregation of the manifest day counts.
const days = { '2026-10-10': 50, '2026-10-09': 78, '2025-01-01': 3 };
assert.equal(sumCounts(days, 7).get('2026-10'), 128);
assert.equal(sumCounts(days, 4).get('2026'), 128);
assert.equal(sumCounts(days, 7).get('2025-01'), 3);
assert.equal(sumCounts(null, 7).size, 0);

// Day grid: counts, outside-month flag, future disabled, selected day.
const cap = capFor('2026-10-10', now);
const cells = buildDayCells({ view: new Date(2026, 9, 1), anchor: new Date(2026, 9, 10), cap, dayCounts: days });
assert.equal(cells.length, 42);
const oct10 = cells.find((cell) => cell.key === '2026-10-10');
assert.equal(oct10.count, 50);
assert.ok(oct10.selected);
assert.ok(!oct10.disabled);
const oct13 = cells.find((cell) => cell.key === '2026-10-13');
assert.ok(!oct13.disabled);
assert.equal(oct13.count, 0);
const oct14 = cells.find((cell) => cell.key === '2026-10-14');
assert.ok(oct14.disabled);
const sep28 = cells.find((cell) => cell.key === '2026-09-28');
assert.ok(sep28.outside);
assert.ok(!sep28.disabled);
const nov08 = cells.find((cell) => cell.key === '2026-11-08');
assert.ok(nov08.outside);
assert.ok(nov08.disabled);

// Month grid: monthly sums, selection by year+month, whole future months off.
const months = buildMonthCells({ view: new Date(2026, 0, 1), anchor: new Date(2026, 9, 10), cap, dayCounts: days });
assert.equal(months.length, 12);
assert.equal(months[9].label, '10月');
assert.equal(months[9].count, 128);
assert.ok(months[9].selected);
assert.ok(months[11].disabled);
assert.ok(!months[0].disabled);
assert.equal(months[0].count, 0);

// Year grid: spans from the earliest data year to the cap year.
const years = buildYearCells({ anchor: new Date(2026, 9, 10), cap, dayCounts: days });
assert.deepEqual([...years.map((cell) => cell.label)], ['2025', '2026']);
assert.equal(years[0].count, 3);
assert.equal(years[1].count, 128);
assert.ok(years[1].selected);
assert.ok(!years[1].disabled);
const sparseYears = buildYearCells({ anchor: new Date(2026, 9, 10), cap, dayCounts: null });
assert.deepEqual([...sparseYears.map((cell) => cell.label)], ['2026']);

console.log('Date picker weeks, 42-cell grid, future cap, manifest aggregation and selection state passed');
