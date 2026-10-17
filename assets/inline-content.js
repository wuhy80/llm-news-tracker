/* Render only validated text spans; never insert publisher HTML. */
(() => {
  function safeHref(value) {
    try {const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;} catch{return null;}
  }
  function append(container,text,annotation,decorate) {
    if(!annotation || annotation.source!==text) return false;
    const chars=Array.from(text);
    const spans=(annotation.spans||[]).filter(s=>Number.isInteger(s.start)&&Number.isInteger(s.end)&&s.start>=0&&s.end>s.start&&s.end<=chars.length&&['a','code','strong','em'].includes(s.kind)&&(s.kind!=='a'||safeHref(s.href)));
    const cuts=[...new Set([0,chars.length,...spans.flatMap(s=>[s.start,s.end])])].sort((a,b)=>a-b);
    for(let i=0;i<cuts.length-1;i++) {
      const start=cuts[i],end=cuts[i+1];let target=container;const kinds=new Set();
      for(const span of spans.filter(s=>s.start<=start&&s.end>=end).sort((a,b)=>a.start-b.start||b.end-a.end)) {
        if(kinds.has(span.kind))continue;kinds.add(span.kind);
        const e=document.createElement(span.kind);
        if(span.kind==='a'){e.href=safeHref(span.href);e.target='_blank';e.rel='noopener noreferrer';}
        target.append(e);target=e;
      }
      const leaf=document.createElement('span');const value=chars.slice(start,end).join('');
      if(kinds.has('code'))leaf.textContent=value;else decorate(leaf,value);
      target.append(leaf);
    }
    return true;
  }
  function tables(container,annotations) {
    const blocks=new Map([...container.querySelectorAll('[data-block-id]')].map(e=>[e.dataset.blockId,e]));const groups=new Map();
    for(const a of annotations||[]) {
      const b=blocks.get(a.id);if(!a.table||!b)continue;
      const source=b.querySelector('.reader-source-text')?.cloneNode(true);
      source?.querySelectorAll('rt').forEach(e=>e.remove());
      if(source?.textContent!==a.source)continue;
      if(!groups.has(a.table.id))groups.set(a.table.id,[]);groups.get(a.table.id).push([a,b]);
    }
    for(const cells of groups.values()) {
      const wrapper=document.createElement('div');wrapper.className='reader-table-scroll';
      const table=document.createElement('table'),body=document.createElement('tbody');table.append(body);wrapper.append(table);cells[0][1].before(wrapper);
      const rows=new Map();
      for(const [a,b] of cells) {
        if(!rows.has(a.table.row)){const row=document.createElement('tr');rows.set(a.table.row,row);body.append(row);}
        const cell=document.createElement(a.table.header?'th':'td');cell.dataset.blockId=a.id;
        cell.colSpan=Math.max(1,Math.min(50,Number(a.table.colspan)||1));cell.rowSpan=Math.max(1,Math.min(50,Number(a.table.rowspan)||1));
        if(b.classList.contains('has-translation'))cell.classList.add('has-translation');
        cell.append(...b.childNodes);b.remove();rows.get(a.table.row).append(cell);
      }
    }
  }
  window.LLMInlineContent={append,tables,safeHref};
})();
