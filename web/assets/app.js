/* Saqua workspace — talks ONLY to the thin API (/api/*), which calls the
   existing backend. No business logic here: it sends the user's text and
   renders the curated conversation the server returns. All text is inserted
   via textContent (never innerHTML), so nothing typed can inject markup. */

// Every API call carries the Clerk session JWT (from auth.js). A 401 means the
// session ended — send the user back to sign in.
async function authedFetch(url, opts){
  opts = opts || {};
  const token = window.saquaAuth ? await window.saquaAuth.getToken() : null;
  const headers = Object.assign({}, opts.headers || {});
  if(token) headers['Authorization'] = 'Bearer ' + token;
  const r = await fetch(url, Object.assign({}, opts, {headers}));
  if(r.status === 401){ window.location.replace('/login.html'); throw new Error('Session expired.'); }
  return r;
}

const API = {
  async list(){ return (await authedFetch('/api/conversations')).json(); },
  async create(){ return (await authedFetch('/api/conversations', {method:'POST'})).json(); },
  async get(id){ const r = await authedFetch('/api/conversations/'+id); if(!r.ok) throw 0; return r.json(); },
  async del(id){ return authedFetch('/api/conversations/'+id, {method:'DELETE'}); },
  async rename(id, title){ return (await authedFetch('/api/conversations/'+id, {
    method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({title})})).json(); },
  async duplicate(id){ return (await authedFetch('/api/conversations/'+id+'/duplicate', {method:'POST'})).json(); },
  async send(id, text){
    const r = await authedFetch('/api/conversations/'+id+'/messages', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text})});
    const data = await r.json().catch(() => ({error:'Something went wrong.'}));
    if(!r.ok) throw new Error(data.error || 'Something went wrong.');
    return data;
  }
};

let activeId = null;
let sending = false;
let hasResearch = false;

// ---- tiny DOM helpers (text-safe) -------------------------------------
const $ = s => document.querySelector(s);
function el(tag, cls, text){ const n=document.createElement(tag); if(cls)n.className=cls; if(text!=null)n.textContent=text; return n; }
function icon(name){ const i=document.createElement('i'); i.setAttribute('data-lucide',name); return i; }
function icons(){ if(window.lucide) lucide.createIcons(); }
function scrollDown(){ const s=$('#stream'); s.scrollTop = s.scrollHeight; }
function relTime(ts){ if(!ts) return ''; const d=Math.max(0,Date.now()/1000-ts);
  if(d<60)return 'now'; if(d<3600)return Math.floor(d/60)+'m'; if(d<86400)return Math.floor(d/3600)+'h'; return Math.floor(d/86400)+'d'; }

// ---- rail --------------------------------------------------------------
async function loadRail(){
  let data; try{ data = await API.list(); }catch{ return; }
  const list = $('#threadList'); list.innerHTML='';
  (data.conversations||[]).forEach(c => {
    const row = el('div','thread'); row.dataset.id = c.id;
    if(c.id===activeId) row.classList.add('active');
    row.appendChild(icon('message-square'));
    row.appendChild(el('span','t-name', c.title || 'New chat'));
    row.appendChild(el('span','t-time', relTime(c.updated_at)));
    const menuBtn = el('button','thread-menu'); menuBtn.setAttribute('aria-label','Conversation actions');
    menuBtn.appendChild(icon('ellipsis'));
    menuBtn.addEventListener('click', e => { e.stopPropagation(); openThreadMenu(menuBtn, c); });
    row.appendChild(menuBtn);
    row.addEventListener('click', () => openConversation(c.id));
    list.appendChild(row);
  });
  icons();
}

// ---- render a conversation --------------------------------------------
function renderConversation(conv){
  activeId = conv.id;
  hasResearch = !!(conv.panel && conv.panel.has_research);
  $('#topTitle').textContent = conv.title || 'New chat';
  const inner = $('#streamInner'); inner.innerHTML='';
  (conv.messages||[]).forEach(m => inner.appendChild(renderMessage(m)));
  $('#input').placeholder = 'Ask a follow-up… e.g. “make it shorter” or “target the CTO”';
  renderPanel(conv.panel);
  document.querySelectorAll('.thread').forEach(t => t.classList.toggle('active', t.dataset.id===conv.id));
  icons(); scrollDown();
}

function renderMessage(m){
  if(m.kind==='email') return emailArtifact(m.data||{});
  if(m.kind==='research') return researchNote(m.data||{});
  const wrap = el('div', 'msg ' + (m.role==='user'?'user':'ai') + (m.kind==='notice'?' warn':''));
  const av = el('div','av'); av.appendChild(icon(m.role==='user'?'user': (m.kind==='notice'?'triangle-alert':'sparkles')));
  wrap.appendChild(av);
  const body = el('div','body');
  if(m.role==='user') body.textContent = m.content||'';
  else renderRichText(body, m.content||'');
  wrap.appendChild(body);
  return wrap;
}

// ---- minimal, text-only markdown for assistant replies -----------------
// Claude's replies use plain-text conventions (**bold**, `code`, "- "/"1." lists,
// "#" headings, "---" rules, blank-line paragraphs). Rendered as real DOM nodes
// built from textContent only — never innerHTML — so nothing in a reply can
// inject markup.
function renderInline(parent, text){
  // Split on [label](url) links, **bold**, and `code`, keeping the delimiters.
  const parts = String(text).split(/(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*|`[^`]+`)/g);
  parts.forEach(part => {
    let m;
    if(part.startsWith('**') && part.endsWith('**') && part.length > 4){
      parent.appendChild(el('strong', null, part.slice(2, -2)));
    } else if(part.startsWith('`') && part.endsWith('`') && part.length > 2){
      parent.appendChild(el('code', 'body-code', part.slice(1, -1)));
    } else if((m = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/))){
      const url = m[2].trim();
      // Only http(s) links become anchors; anything else stays plain text so a
      // reply can never inject javascript:/data: URLs. href is set, not innerHTML.
      if(/^https?:\/\//i.test(url)){
        const a = el('a', 'body-link', m[1]);
        a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        parent.appendChild(a);
      } else {
        parent.appendChild(document.createTextNode(part));
      }
    } else if(part){
      parent.appendChild(document.createTextNode(part));
    }
  });
}
const _BULLET = /^[-*]\s+/, _ORDERED = /^\d+\.\s+/, _HEADING = /^#{1,6}\s+/, _RULE = /^(-{3,}|\*{3,}|_{3,})$/;
// Line-oriented parser: walks the reply line by line and groups consecutive
// lines of the same kind (list run, paragraph run) so a heading immediately
// followed by bullets — no blank line between — still renders correctly.
function renderRichText(container, text){
  const lines = String(text || '').split('\n');
  let para = [], list = null;
  const flushPara = () => {
    if(!para.length) return;
    const p = el('p');
    para.forEach((ln, i) => { if(i) p.appendChild(el('br')); renderInline(p, ln); });
    container.appendChild(p); para = [];
  };
  const flushList = () => { if(list){ container.appendChild(list.node); list = null; } };
  lines.forEach(raw => {
    const ln = raw.trim();
    if(!ln){ flushPara(); flushList(); return; }               // blank = block break
    if(_RULE.test(ln)){ flushPara(); flushList(); container.appendChild(el('hr','body-rule')); return; }
    if(_HEADING.test(ln)){ flushPara(); flushList();
      const h = el('div','body-h'); renderInline(h, ln.replace(_HEADING, '')); container.appendChild(h); return; }
    const ordered = _ORDERED.test(ln), bulleted = _BULLET.test(ln);
    if(ordered || bulleted){
      flushPara();
      const tag = ordered ? 'ol' : 'ul';
      if(!list || list.tag !== tag){ flushList(); list = {tag, node: el(tag, 'body-list')}; }
      const li = el('li'); renderInline(li, ln.replace(ordered ? _ORDERED : _BULLET, '')); list.node.appendChild(li);
      return;
    }
    flushList(); para.push(ln);
  });
  flushPara(); flushList();
}

function researchNote(d){
  const wrap = el('div','msg ai');
  const av = el('div','av'); av.appendChild(icon('circle-check')); wrap.appendChild(av);
  const body = el('div','body');
  const score = (typeof d.research_score==='number') ? ` · ${d.research_score}/100 confidence` : '';
  body.appendChild(el('div', null, `Researched ${d.company||'the company'}${score}.`));
  wrap.appendChild(body);
  return wrap;
}

function emailArtifact(d){
  const wrap = el('div','msg ai'); const av = el('div','av'); av.appendChild(icon('sparkles')); wrap.appendChild(av);
  const body = el('div','body'); body.style.width='100%';
  const art = el('div','artifact');
  const bar = el('div','art-bar');
  const nm = el('div','art-name'); nm.appendChild(icon('file-text')); nm.appendChild(document.createTextNode(' ' + (d.label || 'Draft email')));
  const acts = el('div','art-actions');
  const subjEl = el('div','art-subject', d.subject||'');
  const bodyEl = el('div'); (d.body||'').split(/\n{2,}/).forEach(p => bodyEl.appendChild(el('p',null,p)));

  const copyBtn = el('button','art-btn'); copyBtn.appendChild(icon('copy')); copyBtn.appendChild(document.createTextNode(' Copy'));
  copyBtn.addEventListener('click', () => {
    navigator.clipboard && navigator.clipboard.writeText('Subject: '+(subjEl.textContent)+'\n\n'+(d.body||''));
    copyBtn.textContent='Copied'; setTimeout(()=>{copyBtn.innerHTML=''; copyBtn.appendChild(icon('copy')); copyBtn.appendChild(document.createTextNode(' Copy')); icons();},1400);
  });
  const editBtn = el('button','art-btn'); editBtn.appendChild(icon('pencil')); editBtn.appendChild(document.createTextNode(' Edit'));
  editBtn.addEventListener('click', () => {
    const on = subjEl.isContentEditable;
    subjEl.contentEditable = !on;
    bodyEl.contentEditable = !on;
    bodyEl.classList.toggle('art-editing', !on);
    editBtn.lastChild.textContent = on ? ' Edit' : ' Done';
    if(!on){ subjEl.focus(); toast('Editing — your changes stay in this draft.','pencil'); }
  });
  const regenBtn = el('button','art-btn'); regenBtn.appendChild(icon('refresh-cw')); regenBtn.appendChild(document.createTextNode(' Regenerate'));
  regenBtn.addEventListener('click', () => submit('Regenerate the email with a different angle.'));
  acts.append(copyBtn, editBtn, regenBtn);
  bar.append(nm, acts);
  const ab = el('div','art-body'); ab.append(subjEl, bodyEl);
  art.append(bar, ab); body.appendChild(art); wrap.appendChild(body);
  return wrap;
}

// ---- right panel -------------------------------------------------------
function panelBlock(iconName, title){
  const b = el('div','side-block'); const h = el('div','sb-h'); h.appendChild(icon(iconName));
  h.appendChild(document.createTextNode(' '+title)); b.appendChild(h); return b;
}
function renderPanel(panel){
  const side = $('#side'); side.innerHTML='';
  const inner = el('div','side-inner');
  if(!panel || !panel.has_research){
    const e = el('div','side-empty-wrap'); e.appendChild(icon('sparkles'));
    e.appendChild(el('p','', 'Company research, evidence, and contact will appear here once you research a company.'));
    inner.appendChild(e); side.appendChild(inner); icons(); return;
  }
  if(panel.summary){ const b=panelBlock('building-2','Company summary'); b.appendChild(el('p','sb-desc',panel.summary)); inner.appendChild(b); }
  if(typeof panel.confidence==='number'){
    const b=panelBlock('gauge','Research confidence');
    const track=el('div','conf'); const fill=el('div','conf-fill'); fill.style.width=Math.max(4,Math.min(100,panel.confidence))+'%'; track.appendChild(fill);
    b.appendChild(track); b.appendChild(el('div','conf-val tnum', panel.confidence+' / 100 · enough to personalize')); inner.appendChild(b);
  }
  if(panel.evidence && panel.evidence.length){
    const b=panelBlock('quote','Evidence'); const ev=el('div','evidence');
    panel.evidence.forEach(t=>ev.appendChild(el('div','ev',t))); b.appendChild(ev); inner.appendChild(b);
  }
  if(panel.sources && panel.sources.length){
    const b=panelBlock('link','Sources'); const s=el('div','sources');
    panel.sources.forEach(src=>{ const a=el('a','src',src.mark); a.href=src.url; a.target='_blank'; a.rel='noopener noreferrer'; a.title=src.domain; s.appendChild(a); });
    if(panel.source_count>panel.sources.length){ s.appendChild(el('span','src','+'+(panel.source_count-panel.sources.length))); }
    b.appendChild(s); inner.appendChild(b);
  }
  if(panel.contact){
    const b=panelBlock('user-round','Contact'); const row=el('div','contact-row');
    row.appendChild(el('div','c-av',panel.contact.initials));
    const info=el('div'); info.appendChild(el('div','c-name',panel.contact.name)); info.appendChild(el('div','c-role',panel.contact.role||'')); row.appendChild(info);
    const li=el('a','c-in','in'); li.href=panel.contact.linkedin; li.target='_blank'; li.rel='noopener noreferrer'; li.title='Find on LinkedIn'; row.appendChild(li);
    b.appendChild(row); inner.appendChild(b);
  }
  side.appendChild(inner); icons();
}

// ---- empty state -------------------------------------------------------
function showEmpty(){
  activeId = null; hasResearch = false;
  $('#topTitle').textContent = 'New chat';
  $('#input').placeholder = 'Enter a company name or website…';
  const inner=$('#streamInner'); inner.innerHTML='';
  const e=el('div','empty');
  const mark=el('div','empty-mark'); mark.appendChild(icon('sparkles')); e.appendChild(mark);
  e.appendChild(el('h2','','Research a company'));
  e.appendChild(el('p','','Enter a company name or website. Saqua reads their site, finds what matters, and drafts a personal email — grounded only in what it actually found.'));
  const chips=el('div','empty-chips');
  ['Stripe','Notion','Linear','Vercel'].forEach(n=>{ const c=el('button','chip'); c.appendChild(icon('search')); c.appendChild(document.createTextNode(n));
    c.addEventListener('click',()=>submit(n)); chips.appendChild(c); });
  e.appendChild(chips); inner.appendChild(e);
  renderPanel(null);
  document.querySelectorAll('.thread').forEach(t=>t.classList.remove('active'));
  icons();
}

// ---- send flow ---------------------------------------------------------
// One subtle indicator for any wait — a normal chat reply, or a longer research
// turn. No fabricated "steps"; the real response replaces it when it lands.
function thinkingIndicator(){
  const wrap=el('div','msg ai'); const av=el('div','av'); av.appendChild(icon('sparkles')); wrap.appendChild(av);
  const body=el('div','body');
  const t=el('div','typing'); t.append(el('span'), el('span'), el('span'));
  body.appendChild(t); wrap.appendChild(body); $('#streamInner').appendChild(wrap);
  icons(); scrollDown();
  return wrap;
}

async function submit(text){
  text = (text||'').trim();
  if(!text || sending) return;
  sending = true; setSendEnabled();
  const input=$('#input'); input.value=''; input.style.height='auto';

  // If we were on the empty state, drop it before echoing.
  if(!activeId) $('#streamInner').innerHTML='';

  // Optimistic user bubble.
  const u=el('div','msg user'); const av=el('div','av'); av.appendChild(icon('user')); u.appendChild(av); u.appendChild(el('div','body',text));
  $('#streamInner').appendChild(u); icons(); scrollDown();

  const loader = thinkingIndicator();

  try{
    if(!activeId){ const c = await API.create(); activeId = c.id; }
    const conv = await API.send(activeId, text);
    loader.remove();
    renderConversation(conv);
    loadRail();
  }catch(err){
    loader.remove();
    const n=el('div','msg ai warn'); const a=el('div','av'); a.appendChild(icon('triangle-alert')); n.appendChild(a);
    n.appendChild(el('div','body', (err && err.message) ? err.message : 'Something went wrong. Please try again.'));
    $('#streamInner').appendChild(n); icons(); scrollDown();
  }finally{
    sending = false; setSendEnabled();
  }
}

function setSendEnabled(){ const has = $('#input').value.trim().length>0 && !sending; $('#send').disabled = !has; }

// ---- boot --------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  // Wait until Clerk confirms a session (auth.js). If signed out, auth.js has
  // already redirected to /login.html and this promise never resolves.
  if(window.saquaAuthReady){ try{ await window.saquaAuthReady; }catch(e){} }
  icons();
  const input=$('#input');
  input.addEventListener('input', () => { input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,180)+'px'; setSendEnabled(); });
  input.addEventListener('keydown', e => { if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); submit(input.value); } });
  $('#send').addEventListener('click', () => submit(input.value));
  $('#newChat').addEventListener('click', () => { showEmpty(); closeRail(); });
  $('#shareBtn').addEventListener('click', () => toast('Link sharing is coming soon.','share'));
  $('#moreBtn').addEventListener('click', e => activeId ? openWorkspaceMenu(e.currentTarget) : toast('Start a conversation first.','info'));
  $('#toolsBtn').addEventListener('click', () => toast('Send email, prospecting and LinkedIn are coming soon.','sliders-horizontal'));
  $('#attachBtn').addEventListener('click', () => toast('Attachments are coming soon.','paperclip'));
  $('#menuBtn') && $('#menuBtn').addEventListener('click', openRail);
  $('#scrim') && $('#scrim').addEventListener('click', closeRail);
  await loadRail();
  showEmpty();
});

function openRail(){ $('#rail').classList.add('open'); $('#scrim').classList.add('show'); }
function closeRail(){ $('#rail').classList.remove('open'); $('#scrim').classList.remove('show'); }

// ---- Toast (proper component, stacks + auto-dismisses) -----------------
function toast(msg, iconName){
  let wrap = document.querySelector('.toast-wrap');
  if(!wrap){ wrap = el('div','toast-wrap'); document.body.appendChild(wrap); }
  const t = el('div','toast'); if(iconName) t.appendChild(icon(iconName));
  t.appendChild(el('span',null,msg)); wrap.appendChild(t); icons();
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 240); }, 2400);
}

// ---- Modal (confirm) ---------------------------------------------------
function confirmModal({title, body, confirmLabel='Confirm', danger=false, onConfirm}){
  const scrim = el('div','modal-scrim');
  const modal = el('div','modal');
  modal.appendChild(el('h3',null,title));
  if(body) modal.appendChild(el('p',null,body));
  const actions = el('div','modal-actions');
  const cancel = el('button','btn btn-ghost','Cancel');
  const ok = el('button','btn '+(danger?'btn-danger':'btn-primary'),confirmLabel);
  const close = () => scrim.remove();
  cancel.addEventListener('click', close);
  ok.addEventListener('click', () => { close(); onConfirm && onConfirm(); });
  scrim.addEventListener('click', e => { if(e.target===scrim) close(); });
  document.addEventListener('keydown', function esc(e){ if(e.key==='Escape'){ close(); document.removeEventListener('keydown',esc); } });
  actions.append(cancel, ok); modal.appendChild(actions); scrim.appendChild(modal);
  document.body.appendChild(scrim); ok.focus();
}

function confirmDeleteId(id, title){
  confirmModal({
    title:'Delete this conversation?',
    body:'This permanently removes “'+(title||'this thread')+'” and its research. This can’t be undone.',
    confirmLabel:'Delete', danger:true,
    onConfirm: async () => {
      try{ await API.del(id); }catch{ toast('Could not delete.','triangle-alert'); return; }
      if(id===activeId){ activeId=null; showEmpty(); }
      await loadRail(); toast('Conversation deleted','check');
    }
  });
}
function confirmDelete(){ if(activeId) confirmDeleteId(activeId, $('#topTitle').textContent); }

// ---- Dropdown menu (sidebar row + workspace) ---------------------------
// The outside-click listener is tracked explicitly (not `{once:true}`) so
// closing a menu via an item click (which stops propagation) always
// unregisters it too — otherwise a stale listener lingers and silently
// closes the NEXT menu the instant it opens.
let _menuOutsideHandler = null;
function closeMenus(){
  document.querySelectorAll('.menu').forEach(m => m.remove());
  if(_menuOutsideHandler){ document.removeEventListener('click', _menuOutsideHandler); _menuOutsideHandler = null; }
}
function contextMenu(anchor, items){
  closeMenus();
  const menu = el('div','menu');
  items.forEach(it => {
    if(it.divider){ menu.appendChild(el('div','menu-div')); return; }
    const b = el('button','menu-item'+(it.danger?' danger':''));
    b.appendChild(icon(it.icon)); b.appendChild(el('span',null,it.label));
    b.addEventListener('click', e => { e.stopPropagation(); closeMenus(); it.onClick && it.onClick(); });
    menu.appendChild(b);
  });
  document.body.appendChild(menu); icons();
  const r = anchor.getBoundingClientRect();
  menu.style.top = Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 12) + 'px';
  menu.style.left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 12) + 'px';
  _menuOutsideHandler = e => { if(!menu.contains(e.target)) closeMenus(); };
  setTimeout(() => document.addEventListener('click', _menuOutsideHandler), 0);
}
function openThreadMenu(anchor, c){
  contextMenu(anchor, [
    {label:'Rename', icon:'pencil', onClick:() => renameConversation(c.id, c.title)},
    {label:'Duplicate', icon:'copy', onClick:() => duplicateConversation(c.id)},
    {divider:true},
    {label:'Delete', icon:'trash-2', danger:true, onClick:() => confirmDeleteId(c.id, c.title)},
  ]);
}
function openWorkspaceMenu(anchor){
  const items = [];
  if(activeId){
    items.push({label:'Rename', icon:'pencil', onClick:() => renameConversation(activeId, $('#topTitle').textContent)});
    items.push({label:'Export', icon:'download', onClick:exportConversation});
    items.push({label:'Delete', icon:'trash-2', danger:true, onClick:() => confirmDeleteId(activeId, $('#topTitle').textContent)});
    items.push({divider:true});
  }
  items.push({label:'Keyboard shortcuts', icon:'command', onClick:shortcutsModal});
  items.push({label:'Help', icon:'life-buoy', onClick:() => window.open('/contact.html','_blank','noopener')});
  contextMenu(anchor, items);
}

// ---- Rename (modal) + Duplicate + Export + Shortcuts -------------------
function promptModal({title, value, confirmLabel, onSave}){
  const scrim = el('div','modal-scrim'); const m = el('div','modal');
  m.appendChild(el('h3',null,title));
  const input = el('input','input'); input.value = value || ''; input.style.marginTop='14px'; m.appendChild(input);
  const a = el('div','modal-actions');
  const cancel = el('button','btn btn-ghost','Cancel'); const ok = el('button','btn btn-primary',confirmLabel||'Save');
  const close = () => scrim.remove();
  const save = () => { const v = input.value.trim(); close(); if(v) onSave(v); };
  cancel.onclick = close; ok.onclick = save;
  input.addEventListener('keydown', e => { if(e.key==='Enter'){ e.preventDefault(); save(); } if(e.key==='Escape') close(); });
  scrim.addEventListener('click', e => { if(e.target===scrim) close(); });
  a.append(cancel, ok); m.appendChild(a); scrim.appendChild(m); document.body.appendChild(scrim);
  input.focus(); input.select();
}
function renameConversation(id, current){
  promptModal({ title:'Rename conversation', value:current, confirmLabel:'Save', onSave: async (v) => {
    try{ await API.rename(id, v); if(id===activeId) $('#topTitle').textContent = v; await loadRail(); }
    catch{ toast('Could not rename.','triangle-alert'); }
  }});
}
async function duplicateConversation(id){
  try{ const dup = await API.duplicate(id); await loadRail(); openConversation(dup.id); toast('Conversation duplicated.'); }
  catch{ toast('Could not duplicate.','triangle-alert'); }
}
async function exportConversation(){
  if(!activeId) return;
  try{
    const conv = await API.get(activeId);
    let out = '# ' + (conv.title || 'Conversation') + '\n\n';
    (conv.messages||[]).forEach(m => {
      if(m.kind==='email' && m.data){ out += '## '+(m.data.label||'Draft email')+'\n**Subject:** '+(m.data.subject||'')+'\n\n'+(m.data.body||'')+'\n\n'; }
      else if(m.kind==='research' && m.data){ out += '_Researched '+(m.data.company||'the company')+'._\n\n'; }
      else if(m.content){ out += (m.role==='user'?'**You:** ':'**Saqua:** ')+m.content+'\n\n'; }
    });
    const blob = new Blob([out], {type:'text/markdown'});
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = (conv.title || 'saqua-conversation').replace(/[^a-z0-9]+/gi,'-').toLowerCase() + '.md';
    a.click(); URL.revokeObjectURL(a.href); toast('Conversation exported.');
  }catch{ toast('Could not export.','triangle-alert'); }
}
function shortcutsModal(){
  const scrim = el('div','modal-scrim'); const m = el('div','modal');
  m.appendChild(el('h3',null,'Keyboard shortcuts'));
  const list = el('div','shortcuts');
  [['Enter','Send message'],['Shift + Enter','New line'],['Esc','Close menus & dialogs']].forEach(([k,d]) => {
    const row = el('div','shortcut-row'); row.appendChild(el('kbd',null,k)); row.appendChild(el('span',null,d)); list.appendChild(row);
  });
  m.appendChild(list);
  const a = el('div','modal-actions'); const ok = el('button','btn btn-primary','Got it'); ok.onclick = () => scrim.remove();
  a.appendChild(ok); m.appendChild(a);
  scrim.addEventListener('click', e => { if(e.target===scrim) scrim.remove(); });
  scrim.appendChild(m); document.body.appendChild(scrim); ok.focus();
}

async function openConversation(id){
  try{ const conv = await API.get(id); renderConversation(conv); closeRail(); }
  catch{ toast('Could not open that conversation.','triangle-alert'); }
}
