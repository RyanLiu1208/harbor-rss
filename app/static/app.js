'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {feeds: [], items: [], view: 'all', feed: null, folder: null, q: '', article: null, job: null, mode: 'original', listRequest: 0, articleRequest: 0, editingFeed: null};
let toastTimer, searchTimer, pollTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 5000); }
async function api(path, options = {}) {
  const response = await fetch('/api' + path, {...options, headers: {'Content-Type':'application/json', ...options.headers}});
  if (response.status === 401) { $('#login').classList.remove('hidden'); $('#app').classList.add('hidden'); throw Error('请先登录'); }
  if (!response.ok) { let message = '请求失败'; try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : '输入内容格式有误'; } catch { message = await response.text().catch(() => '请求失败'); } throw Error(message || '请求失败'); }
  return response.json();
}
const post = (path, body = {}) => api(path, {method:'POST',body:JSON.stringify(body)});
function date(value) { return new Date(value * 1000).toLocaleDateString('zh-CN', {month:'short',day:'numeric'}); }
async function start() {
  try { await loadFeeds(); $('#login').classList.add('hidden'); $('#app').classList.remove('hidden'); await loadArticles(); }
  catch(e) { if (!$('#login').classList.contains('hidden')) return; toast(e.message); }
}
async function loadFeeds() {
  state.feeds = await api('/feeds');
  $('#unread-count').textContent = state.feeds.reduce((n,f) => n + f.unread, 0) || '';
  const selected=state.feeds.find(f=>f.id===state.feed); $('#feed-notice').textContent=selected?.error || ''; $('#feed-notice').classList.toggle('hidden',!selected?.error);
  const groups = new Map();
  state.feeds.forEach(f => { if (!groups.has(f.folder)) groups.set(f.folder, []); groups.get(f.folder).push(f); });
  $('#feeds').innerHTML = state.feeds.length ? [...groups].map(([folder, feeds]) => `${folder ? `<div class="folder-label">⌄ ${esc(folder)}</div>` : ''}${feeds.map(f => `<button class="feed-row ${state.feed === f.id ? 'active' : ''}" data-feed="${f.id}" title="${esc(f.error || f.url)}"><span class="feed-avatar">${esc((f.title || '订').slice(0,1))}</span><span class="feed-name">${esc(f.title || f.url)}</span><small class="${f.error ? 'feed-error' : ''}">${f.error ? '!' : f.unread || ''}</small></button>`).join('')}`).join('') : '<div class="empty-list">还没有订阅<br>导入你的阅读世界 ↓</div>';
  $$('#feeds [data-feed]').forEach(el => el.onclick = () => selectView('all', Number(el.dataset.feed)));
}
async function selectView(view, feed = null) {
  state.view = view; state.feed = feed; state.folder = null;
  $('#view-title').textContent = feed ? state.feeds.find(f => f.id === feed)?.title || '订阅文章' : {all:'全部文章',unread:'未读文章',starred:'我的收藏'}[view];
  $$('#views button').forEach(b => b.classList.toggle('active', !feed && b.dataset.view === view));
  $$('#feeds [data-feed]').forEach(b => b.classList.toggle('active', Number(b.dataset.feed) === feed));
  await loadFeeds(); await loadArticles();
}
async function loadArticles(append = false) {
  const req = ++state.listRequest;
  const params = new URLSearchParams({view:state.view,q:state.q,offset:append ? state.items.length : 0});
  if (state.feed) params.set('feed_id', state.feed);
  const data = await api('/articles?' + params);
  if (req !== state.listRequest) return;
  state.items = append ? [...state.items, ...data.items] : data.items;
  $('#list-count').textContent = `${data.total} 篇文章`;
  $('#more').classList.toggle('hidden', state.items.length >= data.total);
  renderList();
}
function renderList() {
  $('#articles').innerHTML = state.items.length ? state.items.map(a => `<button class="article-card ${a.read ? 'read' : ''} ${state.article?.id === a.id ? 'active' : ''}" data-article="${a.id}"><div class="card-source"><span class="feed-avatar">${esc(a.feed_title.slice(0,1))}</span><span>${esc(a.feed_title.slice(0,22))}</span><time>${date(a.published)}</time></div><h3>${a.read ? '' : '<span class="unread-dot"></span>'}${esc(a.title)}${a.starred ? ' ☆' : ''}</h3><p>${esc(a.summary)}</p></button>`).join('') : '<div class="empty-list">这里还很安静。<br>添加订阅，或尝试其他筛选条件。<br>刚导入的订阅会在后台抓取。</div>';
  $$('[data-article]').forEach(b => b.onclick = () => openArticle(Number(b.dataset.article)).catch(e => toast(e.message)));
}
async function openArticle(id) {
  const req = ++state.articleRequest;
  const a = await api('/articles/' + id);
  if (req !== state.articleRequest) return;
  clearTimeout(pollTimer); state.article = a; state.job = null; state.mode = 'original';
  $('#app').classList.add('reading'); $('#reader-empty').classList.add('hidden'); $('#reader-body').classList.remove('hidden');
  $('#article-title').textContent = a.title; $('#article-meta').textContent = a.feed_title;
  $('#article-byline').textContent = `${a.author ? a.author + ' · ' : ''}${new Date(a.published * 1000).toLocaleString('zh-CN')} · ${Math.max(1, Math.ceil((a.full_content || a.content).length / 1000))} 分钟阅读`;
  if (a.url) $('#original').href=a.url; else $('#original').removeAttribute('href'); $('#content').innerHTML = a.full_content || a.content || '<p>订阅没有提供正文，请点击“抓取全文”或打开原文。</p>';
  $('#article-notice').classList.add('hidden');
  $('#translation').textContent = ''; $('#translation-status').classList.add('hidden'); $('#reading-tabs').classList.add('hidden');
  $('#star').textContent = a.starred ? '★' : '☆'; updateMode(); $('#reader-body').scrollTop = 0;
  await api('/articles/' + id, {method:'PATCH',body:JSON.stringify({read:true})});
  const local = state.items.find(a => a.id === id); if (local) local.read = 1; renderList(); await loadFeeds();
  if (state.article?.id === id && a.translation_job) { state.job = a.translation_job; pollJob(a.translation_job, id); }
  if (!a.full_content && (new DOMParser().parseFromString(a.content,'text/html').body.textContent || '').length<600) {
    $('#article-notice').textContent='正在获取原网页图文正文…'; $('#article-notice').classList.remove('hidden');
    try { const data=await post('/articles/'+id+'/extract'); if(state.article?.id!==id)return; if(data.source==='web')a.full_content=data.content; $('#content').innerHTML=data.content; $('#article-notice').textContent=data.warning || ''; $('#article-notice').classList.toggle('hidden',!data.warning); }
    catch(e) { if(state.article?.id===id) $('#article-notice').textContent='原网页未能提取：'+e.message+'。仍可阅读或翻译订阅中已有的内容。'; }
  }
}
function updateMode() {
  $('#content').classList.toggle('hidden', state.mode === 'translation');
  $('#translation').classList.toggle('hidden', state.mode === 'original');
  $('#content-grid').classList.toggle('both', state.mode === 'both');
  $$('#reading-tabs button').forEach(b => b.classList.toggle('active', b.dataset.mode === state.mode));
}
async function pollJob(jid, aid) {
  try {
    const job = await api('/jobs/' + jid);
    if (state.article?.id !== aid || state.job !== jid) return;
    $('#translation-status').classList.remove('hidden');
    $('#translation-status').textContent = {queued:'翻译任务已排队，可继续阅读其他文章。',running:`正在翻译全文 · ${job.completed} / ${job.total} 段`,done:`翻译完成 · ${job.total} 段 · ${job.language}`,failed:`翻译未完成：${job.error}（已保存 ${job.completed} 段）`}[job.status];
    if (job.translation_html) { $('#translation').innerHTML=job.translation_html; $('#translation').classList.add('rich'); } else { $('#translation').textContent=job.translation; $('#translation').classList.remove('rich'); }
    if (job.format==='html' && ['queued','running','failed'].includes(job.status)) $('#translation-status').textContent+=' · 未完成的段落暂时显示原文';
    if (job.source_warning) $('#translation-status').textContent+=' · '+job.source_warning;
    if (job.format==='text' && job.status==='done') $('#translation-status').textContent+=' · 这是旧版纯文本译文，点击「重新翻译」可保留图文。';
    if (job.translation) $('#reading-tabs').classList.remove('hidden');
    if (job.status === 'done') { state.mode = 'translation'; updateMode(); }
    if (job.status === 'failed') {
      const btn = document.createElement('button'); btn.textContent = '继续重试';
      btn.onclick = async () => { try { await post('/jobs/' + jid + '/retry'); pollJob(jid, aid); } catch(e) { toast(e.message); } };
      $('#translation-status').append(btn);
    }
    if (['queued','running'].includes(job.status)) pollTimer = setTimeout(() => pollJob(jid, aid), 2000);
  } catch(e) { toast(e.message); }
}
async function settings(tab = 'ai') {
  const cfg = await api('/settings'); const form = $('#ai-form');
  ['base_url','model','language'].forEach(k => form.elements[k].value = cfg[k]); form.elements.api_key.value = '';
  $('#key-state').textContent = cfg.has_key ? '✓ 已配置 Key；留空保留' : '尚未配置 API Key';
  changeTab(tab); $('#settings-dialog').showModal();
}
function changeTab(tab) {
  ['ai','migration','manage'].forEach(t => { $('#tab-' + t).classList.toggle('hidden', t !== tab); });
  $$('.settings-tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  if (tab === 'manage') renderManage();
}
function renderManage() {
  $('#manage-feeds').innerHTML = state.feeds.map(f => `<div class="manage-row"><div><strong>${esc(f.title || f.url)}</strong><small>${esc(f.url)}</small>${f.error ? `<small class="feed-error">${esc(f.error)}</small>` : ''}</div><button data-edit="${f.id}">编辑</button><button data-retry="${f.id}">重试</button><button data-delete="${f.id}">删除</button></div>`).join('') || '<p>还没有订阅</p>';
  $$('[data-edit]').forEach(b=>b.onclick=()=> { const f=state.feeds.find(f=>f.id===Number(b.dataset.edit)); state.editingFeed=f.id; $('#add-dialog h2').textContent='编辑订阅地址'; ['url','title','folder'].forEach(k=>$('#add-form').elements[k].value=f[k]); $('#add-dialog').showModal(); });
  $$('[data-retry]').forEach(b=>b.onclick=async()=>{try{await post('/feeds/'+b.dataset.retry+'/refresh');toast('正在重新抓取该订阅');}catch(e){toast(e.message);}});
  $$('[data-delete]').forEach(b => b.onclick = async () => {
    if (!confirm('确定删除这个订阅及其文章、收藏和译文？')) return;
    try { await api('/feeds/' + b.dataset.delete,{method:'DELETE'}); await loadFeeds(); renderManage(); await selectView('all'); } catch(e) { toast(e.message); }
  });
}
$('#login-form').onsubmit = async e => { e.preventDefault(); try { await post('/login',{token:$('#token').value}); $('#token').value = ''; $('#login-error').textContent = ''; await start(); } catch(e) { $('#login-error').textContent = e.message; } };
$('#add').onclick = () => { state.editingFeed=null; $('#add-form').reset(); $('#add-dialog h2').textContent='添加一份好内容'; $('#add-dialog').showModal(); };
$$('[data-close]').forEach(b => b.onclick = () => b.closest('dialog').close());
$$('#views button').forEach(b => b.onclick = () => selectView(b.dataset.view).catch(e => toast(e.message)));
$('#settings').onclick = () => settings().catch(e => toast(e.message));
['#empty-import','#import-short'].forEach(s => $(s).onclick = () => settings('migration').catch(e => toast(e.message)));
$$('.settings-tabs button').forEach(b => b.onclick = () => changeTab(b.dataset.tab));
$$('#reading-tabs button').forEach(b => b.onclick = () => { state.mode = b.dataset.mode; updateMode(); });
$('#add-form').onsubmit = async e => { e.preventDefault(); const b = e.submitter; b.disabled = true; try { const data = Object.fromEntries(new FormData(e.target)); if(state.editingFeed) await api('/feeds/'+state.editingFeed,{method:'PATCH',body:JSON.stringify(data)}); else await post('/feeds',data); if(state.editingFeed) await post('/feeds/'+state.editingFeed+'/refresh'); $('#add-dialog').close(); e.target.reset(); await loadFeeds(); toast('订阅已添加，正在后台获取文章'); setTimeout(() => loadArticles().catch(()=>{}),3000); } catch(e) { toast(e.message); } finally { b.disabled = false; } };
$('#ai-form').onsubmit = async e => { e.preventDefault(); try { await api('/settings',{method:'PUT',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))}); e.target.elements.api_key.value = ''; $('#key-state').textContent = '✓ 设置已保存'; toast('翻译设置已保存'); } catch(e) { toast(e.message); } };
$('#import-file').onchange = async e => { const file = e.target.files[0]; if (!file) return; if (file.size > 4 * 1024 * 1024) return toast('文件不能超过 4 MB'); $('#import-text').value = await file.text(); };
$('#import-submit').onclick = async () => { const b = $('#import-submit'); b.disabled = true; try { const result = await post('/import',{content:$('#import-text').value}); $('#import-result').textContent = `已导入 ${result.imported} 个订阅，跳过 ${result.duplicates} 个重复地址。文章将在后台抓取。`; await loadFeeds(); setTimeout(() => loadArticles().catch(()=>{}),3000); } catch(e) { toast(e.message); } finally { b.disabled = false; } };
$('#refresh').onclick = async () => { try { await post('/refresh'); toast('后台刷新已启动'); await loadFeeds(); await loadArticles(); } catch(e) { toast(e.message); } };
$('#more').onclick = () => loadArticles(true).catch(e => toast(e.message));
$('#search').oninput = e => { state.q = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(() => loadArticles().catch(e=>toast(e.message)),250); };
$('#back').onclick = () => $('#app').classList.remove('reading');
$('#star').onclick = async () => { if (!state.article) return; const a = state.article; try { await api('/articles/' + a.id,{method:'PATCH',body:JSON.stringify({starred:!a.starred})}); a.starred = !a.starred; $('#star').textContent = a.starred ? '★' : '☆'; await loadArticles(); } catch(e) { toast(e.message); } };
$('#extract').onclick = async () => { if (!state.article) return toast('请先选择文章'); const a = state.article, b = $('#extract'); b.disabled = true; b.textContent = '正在提取…'; try { const data = await post('/articles/' + a.id + '/extract'); if (state.article?.id === a.id) { if(data.source==='web') a.full_content=data.content; $('#content').innerHTML=data.content; $('#article-notice').textContent=data.warning || ''; $('#article-notice').classList.toggle('hidden',!data.warning); state.mode = 'original'; updateMode(); } toast(data.warning || '已提取图文正文，现在可以翻译全文'); } catch(e) { toast(e.message); } finally { b.disabled = false; b.textContent = '抓取全文'; } };
async function beginTranslation(force=false,useFeed=false) {
  if (!state.article) return toast('请先选择文章');
  const a = state.article, button = $('#translate'); button.disabled = true; button.textContent = '正在准备全文…';
  try {
    const cfg = await api('/settings');
    if (!cfg.has_key) { await settings(); return toast('请填写 DeepSeek 或兼容服务的 API Key'); }
    const result = await post('/articles/' + a.id + '/translate',{language:cfg.language,force,use_feed_content:useFeed});
    if (state.article?.id !== a.id) return;
    const updated = await api('/articles/' + a.id);
    if (state.article?.id !== a.id) return;
    if (updated.full_content) { a.full_content = updated.full_content; $('#content').innerHTML = a.full_content; }
    clearTimeout(pollTimer); state.job = result.job_id; pollJob(result.job_id,a.id);
  } catch(e) { toast(e.message); }
  finally { button.disabled = false; button.textContent = '✧ 全文翻译'; }
}
$('#translate').onclick=()=>beginTranslation();
$('#translate-feed').onclick=()=>beginTranslation(false,true);
$('#retranslate').onclick=()=>beginTranslation(true);
document.addEventListener('error',event=>{const img=event.target;if(img?.tagName!=='IMG'||!img.closest('.prose')||img.dataset.failed)return;img.dataset.failed='true';const original=new URL(img.src,location.href).searchParams.get('url');if(!original||!/^https?:/.test(original))return;const link=document.createElement('a');link.href=original;link.target='_blank';link.rel='noopener noreferrer';link.className='image-fallback';link.textContent='图片加载失败 · 打开原图';img.after(link);},true);
$('#logout').onclick = async () => { try { await post('/logout'); location.reload(); } catch(e) { toast(e.message); } };
setInterval(() => { if (!document.hidden && !$('#app').classList.contains('hidden') && !document.querySelector('dialog[open]')) { loadFeeds().catch(()=>{}); loadArticles().catch(()=>{}); } },30000);
start();
