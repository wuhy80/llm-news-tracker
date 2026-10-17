const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.text = '';
    const classes = new Set();
    this.classList = { add: (name) => classes.add(name), contains: (name) => classes.has(name) };
  }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  // decorateWordWise() and the plain fallback both assign textContent.
  set textContent(value) { this.children = []; this.text = String(value); }
  get textContent() {
    return this.children.length ? this.children.map((child) => child.textContent).join('') : this.text;
  }
  set className(value) { this.attributes.class = value; }
  get className() { return this.attributes.class; }
}

const context = {
  window: {},
  document: { createElement: (tag) => new Element(tag) },
  URL,
  readingState: { item: {} },
  decorateWordWise: (container, text) => { container.textContent = text; },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('assets/inline-markup.js', 'utf8'), context);

// Pull the real appendReadableText() out of article.js, the same way the media
// test pulls renderBody().
const article = fs.readFileSync('assets/article.js', 'utf8');
const start = article.indexOf('function appendReadableText(');
const end = article.indexOf('\nfunction applyReadingPreferences');
assert.ok(start > 0 && end > start, 'appendReadableText not found in article.js');
vm.runInContext(article.slice(start, end), context);

function render(sourceText, translatedText) {
  const block = new Element('p');
  const translations = translatedText === undefined ? new Map() : new Map([['b0001', translatedText]]);
  context.appendReadableText(block, sourceText, 'b0001', translations, [], new Set());
  return block;
}
const tagsOf = (element) => element.children.map((child) => child.tagName);

// 1. a translation carrying the source markers is rendered, not shown verbatim
let block = render('**Run effectively.** Use caching.', '**高效运行。** 使用缓存。');
const translation = block.children[1];
assert.equal(translation.className, 'reader-translation');
assert.equal(translation.lang, 'zh-CN');
assert.deepEqual(tagsOf(translation), ['STRONG', 'SPAN']);
assert.equal(translation.children[0].textContent, '高效运行。');
assert.equal(translation.textContent, '高效运行。 使用缓存。');
assert.ok(!translation.textContent.includes('**'));

// 2. the source side is rendered as well, so both surfaces agree
assert.deepEqual(tagsOf(block.children[0]), ['STRONG', 'SPAN']);

// 3. a translation without markers is placed exactly as before: no wrappers
block = render('plain source', '普通译文，没有标记。');
assert.deepEqual(tagsOf(block.children[1]), []);
assert.equal(block.children[1].textContent, '普通译文，没有标记。');

// 4. an unusable target inside a translation is never turned into an anchor
block = render('see [docs](https://example.com)', '见 [文档](javascript:alert(1))');
assert.deepEqual(tagsOf(block.children[1]), []);
assert.equal(block.children[1].textContent, '见 [文档](javascript:alert(1))');

// 5. a safe target inside a translation does become one
block = render('see [docs](https://example.com)', '见 [文档](https://example.com/g)');
assert.deepEqual(tagsOf(block.children[1]), ['SPAN', 'A']);
assert.equal(block.children[1].children[1].href, 'https://example.com/g');
assert.equal(block.children[1].children[1].rel, 'noopener noreferrer');

// 6. no translation means no translation span at all
const solo = render('source only');
assert.equal(solo.children.length, 1);
assert.equal(solo.children[0].className, 'reader-source-text');

console.log('Reader translation markup: markers rendered, plain text unchanged, unsafe targets preserved passed');
