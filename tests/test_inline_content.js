const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this._text='';}
 append(...items){this.children.push(...items);}
 set textContent(value){this._text=value;this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
}
const context={URL,window:{},document:{createElement:t=>new Element(t)}};
vm.createContext(context);vm.runInContext(fs.readFileSync('assets/inline-content.js','utf8'),context);
const {append,safeHref}=context.window.LLMInlineContent,text='🤗 Read dataset now',root=new Element('span');
assert.equal(append(root,text,{source:text,spans:[{kind:'a',start:7,end:14,href:'https://huggingface.co/datasets/test'},{kind:'strong',start:7,end:14}]},(e,s)=>e.textContent=s),true);
assert.equal(root.textContent,text);assert.equal(root.children[1].tagName,'a');assert.equal(root.children[1].textContent,'dataset');assert.equal(root.children[1].children[0].tagName,'strong');
assert.equal(append(root,text,{source:'stale',spans:[]},()=>{}),false);assert.equal(safeHref('javascript:alert(1)'),null);assert.equal(safeHref('https://user:password@example.com'),null);
console.log('Inline content: Unicode offsets, nesting, stale annotations and URL safety passed');
