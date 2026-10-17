const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.text = ''; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  // decorateWordWise() assigns textContent, which replaces whatever is there.
  set textContent(value) { this.children = []; this.text = String(value); }
  get textContent() {
    return this.children.length ? this.children.map((child) => child.textContent).join('') : this.text;
  }
}

const context = { window: {}, document: { createElement: (tag) => new Element(tag) }, URL };
vm.createContext(context);
vm.runInContext(fs.readFileSync('assets/inline-markup.js', 'utf8'), context);
const render = context.window.LLMInlineMarkup.render;

// Mirrors decorateWordWise() with no glossary terms: it replaces the target's text.
const decorate = (target, value) => { target.textContent = value; };

function run(text) {
  const host = new Element('span');
  render(host, text, decorate);
  return host;
}
const tags = (host) => host.children.map((child) => child.tagName);

// 1. bold, and the markers are gone from the rendered text
let host = run('**Run effectively in production.** Use caching and compaction.');
assert.deepEqual(tags(host), ['STRONG', 'SPAN']);
assert.equal(host.children[0].textContent, 'Run effectively in production.');
assert.equal(host.textContent, 'Run effectively in production. Use caching and compaction.');
assert.ok(!host.textContent.includes('*'));

// 2. italic
host = run('a *little* emphasis');
assert.deepEqual(tags(host), ['SPAN', 'EM', 'SPAN']);
assert.equal(host.children[1].textContent, 'little');

// 3. inline code keeps its text verbatim
host = run('call `normalizeProseMarkup(value)` first');
assert.deepEqual(tags(host), ['SPAN', 'CODE', 'SPAN']);
assert.equal(host.children[1].textContent, 'normalizeProseMarkup(value)');

// 4. markdown link becomes a safe anchor
host = run('see [the docs](https://example.com/guide)');
assert.deepEqual(tags(host), ['SPAN', 'A']);
assert.equal(host.children[1].href, 'https://example.com/guide');
assert.equal(host.children[1].target, '_blank');
assert.equal(host.children[1].rel, 'noopener noreferrer');
assert.equal(host.children[1].textContent, 'the docs');

// 5. an unusable target must not swallow the text
host = run('[click](javascript:alert(1))');
assert.deepEqual(tags(host), []);
assert.equal(host.textContent, '[click](javascript:alert(1))');

// 6. two spans on one line must not merge into one greedy match
host = run('**a** and **b**');
assert.deepEqual(tags(host), ['STRONG', 'SPAN', 'STRONG']);
assert.equal(host.children[0].textContent, 'a');
assert.equal(host.children[2].textContent, 'b');

// 7. plain text is handed to decorate untouched, exactly as before
host = run('plain sentence with no markers');
assert.deepEqual(tags(host), []);
assert.equal(host.textContent, 'plain sentence with no markers');

// 8. stray asterisks and multiplication are not emphasis
for (const text of ['2 * 3 = 6', '* item without emphasis', 'a ** b', '*']) {
  host = run(text);
  assert.deepEqual(tags(host), [], text);
  assert.equal(host.textContent, text);
}

// 9. underscore emphasis is deliberately not supported
host = run('snake_case_name and __dunder__ stay literal');
assert.deepEqual(tags(host), []);
assert.equal(host.textContent, 'snake_case_name and __dunder__ stay literal');

// 10. every non-code leaf goes through decorate, so word-wise highlighting still
//     applies; code is set directly, exactly as inline-content.js does.
const seen = [];
const host10 = new Element('span');
render(host10, '**bold** and `code` and plain', (target, value) => { seen.push(value); target.textContent = value; });
assert.deepEqual(seen, ['bold', ' and ', ' and plain']);
assert.deepEqual(tags(host10), ['STRONG', 'SPAN', 'CODE', 'SPAN']);
assert.equal(host10.children[2].textContent, 'code');

// 11. a missing decorate callback still renders
const host11 = new Element('span');
render(host11, '**x**', undefined);
assert.equal(host11.textContent, 'x');

console.log('Inline markdown: emphasis, code, links, unsafe targets, greedy spans, literal asterisks and underscore policy passed');
