const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
class Element {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.classList={add:()=>{}}; }
  append(...nodes) { for(const n of nodes){n.remove?.();n.parent=this;this.children.push(n);} }
  prepend(n) { n.remove?.(); n.parent=this;this.children.unshift(n); }
  after(n) {const p=this.parent;n.remove?.();n.parent=p;p.children.splice(p.children.indexOf(this)+1,0,n);}
  remove() {if(this.parent){const p=this.parent;p.children.splice(p.children.indexOf(this),1);this.parent=null;}}
  replaceChildren(){for(const c of this.children)c.parent=null;this.children=[];}
  addEventListener() {}
  querySelectorAll() {const all=[];function visit(n){for(const c of n.children){if(c.dataset.blockId)all.push(c);visit(c);}}visit(this);return all;}
}
const document={createElement:t=>new Element(t),createTextNode:()=>new Element('text')};
const context={document,URL,TextEncoder,Uint8Array,crypto:webcrypto,window:{}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('assets/media-layout.js','utf8'),context);
const render=context.window.LLMMediaLayout.render;
const digest=body=>createHash('sha256').update(body).digest('hex');
function fixture(){const root=new Element('article');for(const id of ['b0001','c0001','b0002']){const p=new Element(id[0]==='c'?'pre':'p');p.dataset.blockId=id;root.append(p);}return root;}
const item={images:[{id:'a',src:'https://example.com/a.png'}],videos:[{id:'v',kind:'video',src:'https://example.com/v.mp4'}],mediaLayoutVersion:1,mediaLayoutBodyHash:digest('body'),mediaLayout:[{mediaId:'a',type:'image',afterBlockId:'b0001',order:0},{mediaId:'v',type:'video',afterBlockId:'c0001',order:1},{mediaId:'a',type:'image',afterBlockId:'c0001',order:2}]};
(async()=>{
 let root=fixture();await render(item,'body',root);assert.deepEqual(root.children.map(n=>n.tagName),['P','FIGURE','PRE','FIGURE','FIGURE','P']);
 assert.equal(root.children[3].children[0].controls,true);
 root=fixture();await render(item,'changed body',root);assert.equal(root.children.at(-1).tagName,'DETAILS');assert.equal(root.children.filter(n=>n.tagName==='FIGURE').length,0);
 root=fixture();const stale=render(item,'body',root);root.replaceChildren();await render({},'',root);await stale;assert.equal(root.children.length,0);
 root=fixture();await render({...item,mediaLayout:[{mediaId:'a',type:'image',afterBlockId:null,order:0}]},'body',root);assert.equal(root.children[0].tagName,'FIGURE');
 root=fixture();await render({images:[{src:'javascript:alert(1)'},{src:'data/article-media/../../x'}],videos:[{kind:'embed',src:'https://evil.example/embed/123'}]},'',root);assert.equal(root.children.length,3);
 const article=fs.readFileSync('assets/article.js','utf8');
 const fn=article.slice(article.indexOf('function renderBody(body)'),article.indexOf('function fallbackSummary'));
 Object.assign(context,{elements:{articleBody:new Element('article')},readingState:{item:{}},translatedBlocks:()=>new Map(),wordWiseEntries:()=>[],parseTags:()=>[],renderTags:()=>new Element('div'),appendReadableText:(e,text,id)=>{e.dataset.blockId=id;},renderCode:()=>new Element('pre'),normalizeBodyFences:s=>s,normalizeProseMarkup:s=>s,applyReadingPreferences:()=>{}});
 vm.runInContext(fn,context);
 context.renderBody('First paragraph\n\n## Heading\n\n- Item one\n- Item two\n\n```js\nconsole.log(1)\n```\n\n> Quote\n\nLast paragraph');
 assert.deepEqual(context.elements.articleBody.querySelectorAll().map(e=>e.dataset.blockId),['b0001','b0002','b0003','b0004','c0001','b0005','b0006']);
 console.log('Media DOM ordering, repeated media, hash guard, render race, URL safety and real reader block ids passed');
})().catch(error=>{console.error(error);process.exit(1);});
