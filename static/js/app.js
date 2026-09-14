/* ============ 需求治理 Agent · 对话式工作台 ============ */
'use strict';

/* ---------------- DOM refs ---------------- */
const $ = (id) => document.getElementById(id);
const msgs = $('messages');
const composerForm = $('composer');
const inputEl = $('input');
const sendBtn = $('send-btn');
const attachBtn = $('attach-btn');
const fileInput = $('file-input');
const sessionList = $('session-list');
const pendingCount = $('pending-count');
const modelChip = $('model-chip');
const dbPill = $('db-pill');
const llmPill = $('llm-pill');
const attachList = $('attach-list');

const STREAM_URL = '/api/v1/agent/chat/stream';
const STREAM_URL_FILES = '/api/v1/agent/chat/stream-with-files';
const SESSIONS_KEY = 'ra.sessions.v1';

const state = {
  sessionId: null,
  streaming: false,
  pendingFiles: [],
  convos: [],
};

/* ---------------- helpers ---------------- */
function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
function uuidv4() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
function firstLine(text, n) {
  const line = String(text || '').split('\n')[0].trim();
  return line.length > (n || 40) ? line.slice(0, n) + '…' : line;
}
function levelText(l) { return ({ low: '低', medium: '中', high: '高' })[l] || l; }
function levelClass(l) { return l === 'high' ? 'lvl-high' : l === 'medium' ? 'lvl-medium' : 'lvl-low'; }
function nowIso() { return new Date().toISOString(); }
function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ');
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtAgo(ts) {
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d === 1) return '昨天';
  return `${d} 天前`;
}

function toast(msg, kind) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast ' + (kind || 'ok');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 2600);
}

function scrollBottom() {
  msgs.scrollTop = msgs.scrollHeight;
}

async function apiJson(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    let detail = resp.statusText || '请求失败';
    try {
      const body = await resp.json();
      if (typeof body.detail === 'string') detail = body.detail;
      else if (body.message) detail = body.message;
    } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return resp.json();
}

/* minimal markdown-lite renderer (HTML-safe after escape) */
function md(s) {
  const raw = String(s == null ? '' : s);
  const lines = raw.split('\n');
  const out = [];
  let list = null;
  const flushList = () => { if (list) { out.push('<ul>' + list.join('') + '</ul>'); list = null; } };
  for (let line of lines) {
    const t = line.trim();
    const m = /^[-•*]\s+(.*)$/.exec(t);
    if (m) {
      list = list || [];
      list.push('<li>' + esc(m[1]) + '</li>');
      continue;
    }
    flushList();
    out.push(esc(line) || '<br/>');
  }
  flushList();
  return out.join('\n').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

/* ---------------- SSE parsing ---------------- */
async function* parseSSE(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = parseBlock(block);
      if (ev) yield ev;
    }
  }
  if (buf.trim()) {
    const ev = parseBlock(buf);
    if (ev) yield ev;
  }
}
function parseBlock(block) {
  let event = 'message';
  const datas = [];
  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) datas.push(line.slice(5).replace(/^ /, ''));
  }
  if (!datas.length) return null;
  return { event, data: datas.join('\n') };
}

/* ---------------- Chat: DOM builders ---------------- */
function appendUser(text, files) {
  const art = document.createElement('article');
  art.className = 'msg user';
  art.innerHTML = `
    <div class="msg-body"><div class="bubble"></div></div>
    <div class="avatar">我</div>`;
  const bubble = art.querySelector('.bubble');
  if (files && files.length) {
    bubble.innerHTML = `<div class="u-files"></div>` + (text ? `<div class="u-text"></div>` : '');
    if (text) bubble.querySelector('.u-text').innerHTML = md(text);
    const filesEl = bubble.querySelector('.u-files');
    files.forEach((f) => {
      const chip = document.createElement('span');
      chip.className = 'attach-chip' + (f.error ? ' err' : '');
      chip.innerHTML = `📎 <span class="a-name">${esc(f.name || f.title || '文件')}</span>${f.error ? '（读取失败）' : ''}`;
      filesEl.appendChild(chip);
    });
  } else {
    bubble.innerHTML = md(text);
  }
  msgs.appendChild(art);
  return art;
}

function appendSystem(text) {
  const art = document.createElement('article');
  art.className = 'msg system';
  art.innerHTML = `<div class="msg-body"><div class="bubble"></div></div>`;
  art.querySelector('.bubble').textContent = text;
  msgs.appendChild(art);
  scrollBottom();
  return art;
}

/* —— 分析消息：展示进行中的步骤与结构化分析卡片（不含最终结论文字）—— */
function appendAnalysisMessage() {
  const art = document.createElement('article');
  art.className = 'msg assistant';
  art.innerHTML = `
    <div class="avatar">✦</div>
    <div class="msg-body">
      <div class="bubble steps-holder">
        <div class="steps-line"><span class="steps-dot"></span><span class="steps-text">正在准备…</span></div>
      </div>
      <div class="artifact"></div>
    </div>`;
  msgs.appendChild(art);
  scrollBottom();

  const holder = art.querySelector('.steps-holder');
  const stepsText = art.querySelector('.steps-text');
  const artifactBox = art.querySelector('.artifact');
  let finalized = false;

  const hideSteps = () => {
    if (!finalized) { finalized = true; holder.style.display = 'none'; }
  };

  const api = {
    setStep(label) { if (!finalized) { stepsText.textContent = label; scrollBottom(); } },
    /* 卡片就绪：收起步骤行，只保留结构化分析卡片 */
    addArtifacts(pipeline) {
      if (!pipeline || typeof pipeline !== 'object') return;
      hideSteps();
      const frag = buildArtifact(pipeline);
      if (frag) { artifactBox.appendChild(frag); scrollBottom(); }
    },
    /* 无卡片时手动收尾步骤行 */
    completeAnalysis() { hideSteps(); scrollBottom(); },
    error(message) {
      hideSteps();
      const box = document.createElement('div');
      box.className = 'bubble';
      box.style.cssText = 'background:var(--danger-soft);color:var(--danger);border:0;margin-top:8px;';
      box.textContent = '⚠ ' + (message || '请求失败，请重试');
      artifactBox.appendChild(box);
      scrollBottom();
    },
    art: art,
  };
  return api;
}

/* —— 最终结论消息：卡片之后独立的普通 AI 气泡，支持逐字流式 —— */
function createFinalBubble() {
  const art = document.createElement('article');
  art.className = 'msg assistant';
  art.innerHTML = `
    <div class="avatar">✦</div>
    <div class="msg-body"><div class="bubble"></div></div>`;
  msgs.appendChild(art);
  scrollBottom();

  const bubble = art.querySelector('.bubble');
  let textNode = document.createTextNode('');
  const caret = document.createElement('span');
  caret.className = 'caret';
  bubble.appendChild(textNode);
  bubble.appendChild(caret);
  let buffer = '';
  let done = false;

  const api = {
    token(t) {
      if (done) return;
      textNode.appendData(t);
      buffer += t;
      scrollBottom();
    },
    finish() {
      if (done) return;
      done = true;
      textNode.remove();
      caret.remove();
      bubble.innerHTML = md(buffer);
      scrollBottom();
    },
    error(message) {
      if (done) return;
      done = true;
      caret.remove();
      bubble.style.cssText = 'background:var(--danger-soft);color:var(--danger);border:0;';
      bubble.textContent = '⚠ ' + (message || '请求失败，请重试');
      scrollBottom();
    },
    art: art,
  };
  return api;
}

/* ---------------- Artifacts cards ---------------- */
function card(title, emoji, openDefault) {
  const box = document.createElement('div');
  box.className = 'acard' + (openDefault ? ' open' : '');
  box.innerHTML = `
    <div class="acard-head">
      <span class="acard-title"><span class="emoji">${emoji}</span>${esc(title)}</span>
      <span class="acard-caret">▶</span>
    </div>
    <div class="acard-body"></div>`;
  box.querySelector('.acard-head').addEventListener('click', () => {
    box.classList.toggle('open');
  });
  return box;
}

function chipHtml(cls, text) { return `<span class="chip ${cls}">${esc(text)}</span>`; }

function buildArtifact(p) {
  const frag = document.createDocumentFragment();
  const extracted = p.extracted || {};
  const analysis = p.analysis || {};
  const risk = p.risk || {};
  const extCands = analysis.candidates || [];
  const topCands = Array.isArray(p.candidates) ? p.candidates : [];

  // 1) understood requirement
  const c1 = card('已理解的需求', '🔎', true);
  const b1 = c1.querySelector('.acard-body');
  const fields = [];
  if (extracted.requirement_title) fields.push(`<div class="field"><span class="k">标题</span><strong>${esc(extracted.requirement_title)}</strong></div>`);
  if (extracted.business_domain) fields.push(`<div class="field"><span class="k">领域</span>${chipHtml('tagged', extracted.business_domain)}</div>`);
  if (extracted.priority) fields.push(`<div class="field"><span class="k">优先级</span>${chipHtml('pri-' + extracted.priority, levelText(extracted.priority) + '优先级')}</div>`);
  if (extracted.source_type) fields.push(`<div class="field"><span class="k">来源</span>${esc(extracted.source_type)}</div>`);
  if (extracted.requester_name) fields.push(`<div class="field"><span class="k">发起人</span>${esc(extracted.requester_name)}</div>`);
  if (extracted.tags && extracted.tags.length) fields.push(`<div class="field"><span class="k">标签</span>${extracted.tags.map((t) => chipHtml('tagged', t)).join(' ')}</div>`);
  b1.innerHTML = `<div class="kv">${fields.join('') || '—'}</div>`;
  if (extracted.summary) b1.insertAdjacentHTML('beforeend', `<p style="margin:2px 0 0;font-size:13px;color:var(--text-2)">${esc(extracted.summary)}</p>`);
  if (extracted.requirements && extracted.requirements.length) {
    b1.insertAdjacentHTML('beforeend', `<div class="section-label">要点拆解</div><ul class="list-plain">${extracted.requirements.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`);
  }
  if (extracted.raw_text) {
    const orig = document.createElement('details');
    orig.style.cssText = 'margin-top:10px;font-size:12px;color:var(--text-3);';
    orig.innerHTML = `<summary style="cursor:pointer">查看来源原文</summary><pre style="white-space:pre-wrap;background:var(--surface-2);border-radius:8px;padding:8px;margin:6px 0 0;color:var(--text-2);font-family:inherit">${esc(extracted.raw_text)}</pre>`;
    b1.appendChild(orig);
  }
  frag.appendChild(c1);

  // 2) similar / related
  const cands = extCands.length ? extCands : topCands.map((c) => ({
    requirement_key: c.requirement_key, title: c.requirement_name || c.title, similarity: c.score, reason: '检索命中（' + (c.match_type || '') + '）',
  }));
  const c2 = card('相似 / 关联需求', '🔗', false);
  const b2 = c2.querySelector('.acard-body');
  if (cands.length) {
    b2.innerHTML = cands.map((c) => `
      <div class="cand">
        <div class="cand-top"><span class="cand-key">${esc(c.requirement_key || '')}</span><span style="font-size:12px;color:var(--text-2)">${Math.round((c.similarity || 0) * 100)}%</span></div>
        <div class="cand-title">${esc(c.title || '历史需求')}</div>
        <div class="score"><span class="track"><span class="fill" style="width:${Math.max(2, Math.round((c.similarity || 0) * 100))}%"></span></span></div>
        <div class="reason">${esc(c.reason || '')}</div>
        ${Array.isArray(c.evidence) && c.evidence.length ? `<div class="reason">证据：${c.evidence.map((e) => `<span class="chip">${esc(e)}</span>`).join(' ')}</div>` : ''}
      </div>`).join('');
  } else {
    b2.innerHTML = `<div class="empty-hint">未检索到存量相似需求</div>`;
  }
  frag.appendChild(c2);

  // 3) conflict / duplicate verdicts
  const c3 = card('冲突与重复分析', '🧭', false);
  const b3 = c3.querySelector('.acard-body');
  const verdicts = [
    { k: 'duplicate', yes: analysis.duplicate, label: '重复', cls: 'yes' },
    { k: 'related', yes: analysis.related, label: '关联', cls: 'related' },
    { k: 'conflict', yes: analysis.conflict, label: '冲突', cls: 'yes' },
    { k: 'independent', yes: analysis.independent, label: '独立', cls: 'no' },
  ];
  b3.innerHTML = `<div class="verdict-row">${verdicts.map((v) => `<span class="verdict ${v.yes ? v.cls : ''}">${v.label} ${v.yes ? '是' : '否'}</span>`).join('')}</div>`;
  if (analysis.reasoning) b3.insertAdjacentHTML('beforeend', `<p style="margin:8px 0 2px;font-size:13px;color:var(--text-2)">${esc(analysis.reasoning)}</p>`);
  frag.appendChild(c3);

  // 4) risk
  const c4 = card('风险评估', '🛡', false);
  const b4 = c4.querySelector('.acard-body');
  const risks = [['质量风险', risk.quality_risk], ['变更风险', risk.change_risk], ['技术影响', risk.technical_impact_risk]];
  b4.innerHTML = `<div class="risk-line">${risks.map(([label, lvl]) => `<span class="lvl ${levelClass(lvl)}"><span class="bar"></span>${esc(label)} · ${levelText(lvl)}</span>`).join('')}</div>`;
  if (risk.confidence != null) b4.insertAdjacentHTML('beforeend', `<div style="font-size:12px;color:var(--text-3);margin-top:6px">模型置信度 ${Math.round(risk.confidence * 100)}%</div>`);
  if (p.analysis_mode) b4.insertAdjacentHTML('beforeend', `<div style="font-size:12px;color:var(--text-3);margin-top:4px">审核模式：${esc(p.analysis_mode)}</div>`);
  frag.appendChild(c4);

  // 5) actions
  const c5 = document.createElement('div');
  c5.className = 'acard';
  c5.innerHTML = `<div class="acard-body" style="display:block;border:0;padding:10px 14px"><div class="act-row"></div></div>`;
  const actRow = c5.querySelector('.act-row');
  const submitBtn = document.createElement('button');
  submitBtn.className = 'btn primary';
  submitBtn.textContent = '提交到待办审核';
  const againBtn = document.createElement('button');
  againBtn.className = 'btn ghost';
  againBtn.textContent = '重新分析';
  actRow.appendChild(submitBtn);
  actRow.appendChild(againBtn);
  submitBtn.addEventListener('click', async () => {
    if (state.streaming) return;
    submitBtn.disabled = true;
    submitBtn.textContent = '正在写入…';
    try {
      const res = await doSubmitRequirement(p);
      appendSystem(`已提交待办审核（来源 #${res.source_id || ''}）。可到右侧「待办」进行审核。`);
      refreshPending();
      setRailTab('pending');
    } catch (e) {
      toast('提交失败：' + e.message, 'err');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = '提交到待办审核';
    }
  });
  againBtn.addEventListener('click', () => {
    const raw = (p.extracted && p.extracted.raw_text) || '';
    if (raw) runChat(raw);
  });
  frag.appendChild(c5);

  return frag;
}

/* ---------------- Submit from chat ---------------- */
async function doSubmitRequirement(p) {
  const extracted = p.extracted || {};
  const body = {
    source_type: p.source_type || 'web',
    requester_id: 'chat',
    requester_name: extracted.requester_name || '需求方',
    original_text: extracted.raw_text || '',
    metadata: {
      chat_session_id: state.sessionId,
      extracted: p.extracted || {},
      analysis: p.analysis || {},
      risk: p.risk || {},
    },
  };
  return apiJson('/api/v1/requirements/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/* ---------------- Streaming chat ---------------- */
function setSendEnabled() {
  sendBtn.disabled = state.streaming || (!inputEl.value.trim() && !state.pendingFiles.length);
}

function resizeInput() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(160, inputEl.scrollHeight) + 'px';
}

async function runChat(text, files) {
  const msg = String(text || '').trim();
  const attached = Array.isArray(files) ? files : [];
  if ((!msg && !attached.length) || state.streaming) return;

  state.streaming = true;
  setSendEnabled();
  const needRecord = !state.sessionId;
  const clientId = uuidv4();

  appendUser(msg, attached.map((f) => ({ name: f.name, size: f.size })));
  // 一条“分析消息”（步骤 + 结构化卡片），最终结论另起一条独立气泡
  const analysis = appendAnalysisMessage();
  let final = null;

  try {
    let resp;
    if (attached.length) {
      const form = new FormData();
      form.append('message', msg);
      form.append('session_id', state.sessionId || '');
      form.append('client_message_id', clientId);
      form.append('requester_name', '我');
      form.append('analysis_mode', 'strict');
      attached.forEach((f) => form.append('files', f.file || f, f.name));
      resp = await fetch(STREAM_URL_FILES, { method: 'POST', body: form });
    } else {
      const payload = {
        message: msg,
        session_id: state.sessionId || null,
        client_message_id: clientId,
        source_type: 'web',
        requester_name: '我',
        analysis_mode: 'strict',
      };
      resp = await fetch(STREAM_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }
    if (!resp.ok) {
      let detail = resp.statusText;
      try { const b = await resp.json(); detail = b.detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    for await (const ev of parseSSE(resp)) {
      let data = {};
      try { data = ev.data ? JSON.parse(ev.data) : {}; } catch (e) { /* ignore */ }
      if (ev.event === 'session') state.sessionId = data.session_id || state.sessionId;
      else if (ev.event === 'step') analysis.setStep(data.label || '');
      else if (ev.event === 'artifacts') analysis.addArtifacts(data.artifacts);
      else if (ev.event === 'narrative') {
        if (data.start) { if (!final) final = createFinalBubble(); }
        else if (data.t) { if (!final) final = createFinalBubble(); final.token(data.t); }
      } else if (ev.event === 'error') (final || analysis).error(data.message);
      else if (ev.event === 'done') { if (final) final.finish(); else analysis.completeAnalysis(); }
    }
    if (final) final.finish();
    else analysis.completeAnalysis();
  } catch (err) {
    (final || analysis).error(err && err.message ? err.message : '网络异常，请重试');
  } finally {
    state.streaming = false;
    state.pendingFiles = [];
    renderAttachList();
    setSendEnabled();
    if (needRecord && state.sessionId) {
      // 首轮对话结束：触发 finalize（LLM 一句话摘要）作为会话默认名，不阻塞 UI
      const sid = state.sessionId;
      apiJson('/api/v1/conversations/' + encodeURIComponent(sid) + '/finalize', { method: 'POST' })
        .catch(() => { /* 摘要失败不影响本次对话 */ })
        .then(() => loadConversations());
    }
    loadConversations();
    scrollBottom();
    refreshPending();
  }
}

/* ---------------- History replay ---------------- */
function renderHistory(history) {
  clearChatInner();
  (history || []).forEach((m) => {
    if (m.role === 'user') {
      const files = (m.meta && Array.isArray(m.meta.files)) ? m.meta.files : null;
      appendUser(m.content || '', files);
    } else if (m.role === 'assistant') {
      // 回放顺序与实时一致：先卡片，再最终结论气泡
      const analysis = appendAnalysisMessage();
      if (m.artifacts && typeof m.artifacts === 'object' && Object.keys(m.artifacts).length) {
        analysis.addArtifacts(m.artifacts);
      } else {
        analysis.completeAnalysis();
      }
      const final = createFinalBubble();
      if (m.content) final.token(m.content);
      final.finish();
    }
  });
  scrollBottom();
}

async function loadSession(id) {
  if (state.streaming) return;
  state.sessionId = id;
  renderSessions();
  try {
    const res = await apiJson('/api/v1/agent/chat/' + encodeURIComponent(id));
    const history = res.history || [];
    if (!history.length) {
      clearChatInner();
      appendSystem('该会话在服务端已失效（服务可能刚重启），已为你重置为新对话。');
      state.sessionId = null;
      renderSessions();
      return;
    }
    renderHistory(history);
  } catch (e) {
    toast('加载会话失败：' + e.message, 'err');
  }
}

function clearChatInner() {
  msgs.innerHTML = '';
}

/* ---------------- Sessions (database-backed, localStorage as cache) ---------------- */
function getSessions() {
  try { return JSON.parse(localStorage.getItem(SESSIONS_KEY)) || []; } catch (e) { return []; }
}
function saveSessions(list) {
  try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(list.slice(0, 40))); } catch (e) { /* ignore */ }
}
function convTitle(c) {
  // 默认名优先用 LLM 摘要；用户改过名（title 不再是「新对话」）则显示 title，摘要不会覆盖。
  return (c && c.title && c.title !== '新对话') ? c.title : ((c && c.summary) || '新对话');
}
function isoToTs(iso) {
  const t = iso ? new Date(iso).getTime() : NaN;
  return Number.isNaN(t) ? Date.now() : t;
}
async function loadConversations() {
  try {
    const res = await apiJson('/api/v1/conversations?limit=100');
    state.convos = res.items || [];
    saveSessions(state.convos.map((c) => ({ id: c.id, title: convTitle(c), ts: isoToTs(c.updated_at) })));
  } catch (e) {
    // 后端不可达时回退 localStorage 缓存，保证至少能看到本地会话
    state.convos = getSessions().map((s) => ({ id: s.id, title: s.title, summary: null, updated_at: new Date(s.ts).toISOString() }));
  }
  renderSessions();
}
function removeSession(id) {
  apiJson('/api/v1/conversations/' + encodeURIComponent(id), { method: 'DELETE' })
    .then(loadConversations)
    .catch(() => {
      saveSessions(getSessions().filter((s) => s.id !== id));
      renderSessions();
    });
  if (state.sessionId === id) newChat();
}
async function renameSession(id) {
  const conv = state.convos.find((c) => c.id === id);
  const current = convTitle(conv);
  const name = window.prompt('给这个会话重命名：', current);
  if (name == null) return; // 取消
  const trimmed = name.trim();
  if (!trimmed) return;
  try {
    await apiJson('/api/v1/conversations/' + encodeURIComponent(id), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: trimmed }),
    });
    await loadConversations();
  } catch (e) {
    toast('重命名失败：' + e.message, 'err');
  }
}
function renderSessions() {
  sessionList.innerHTML = '';
  const sessions = state.convos;
  if (!sessions.length) {
    const li = document.createElement('li');
    li.className = '';
    li.innerHTML = `<div style="padding:6px 2px;color:var(--text-3);font-size:12px">还没有历史会话，直接开始一段对话吧</div>`;
    sessionList.appendChild(li);
    return;
  }
  sessions.forEach((c) => {
    const li = document.createElement('li');
    if (state.sessionId === c.id) li.classList.add('active');
    li.innerHTML = `<div class="s-title"></div><div class="s-time"></div><button class="s-rename" type="button" title="重命名">✏️</button><button class="s-del" type="button" title="删除">×</button>`;
    li.querySelector('.s-title').textContent = convTitle(c);
    li.querySelector('.s-time').textContent = fmtAgo(isoToTs(c.updated_at));
    li.querySelector('.s-rename').addEventListener('click', (e) => { e.stopPropagation(); renameSession(c.id); });
    li.querySelector('.s-del').addEventListener('click', (e) => { e.stopPropagation(); removeSession(c.id); });
    li.addEventListener('click', () => loadSession(c.id));
    sessionList.appendChild(li);
  });
}
function newChat() {
  if (state.streaming) return;
  // 收尾上一个会话：后台 finalize（一句话摘要 + 沉淀长期记忆），完成后刷新列表显示摘要名
  if (state.sessionId) {
    apiJson('/api/v1/conversations/' + encodeURIComponent(state.sessionId) + '/finalize', { method: 'POST' })
      .catch(() => { /* 记忆抽取失败不影响开新会话 */ })
      .then(() => loadConversations());
  }
  state.sessionId = null;
  clearChatInner();
  appendSystem('新对话已开启。直接说出业务需求，我会提取要点、检索相似需求并评估风险。');
  renderSessions();
  inputEl.focus();
}

/* ---------------- Right rail: tabs ---------------- */
// 「需求库」不再占用右栏窄栏，改为打开全宽面板（见下方 library panel 一节），
// 所以这里记下最后一个普通标签，收起面板时好还回去。
let libraryLastTab = 'pending';

function setRailTab(name) {
  if (name === 'library') { openLibrary(); return; }
  libraryLastTab = name;
  document.querySelectorAll('.wb-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.wb-pane').forEach((p) => p.classList.remove('active'));
  const pane = $('tab-' + name);
  if (pane) pane.classList.add('active');
}

function setPendingBadge(n) {
  if (n > 0) { pendingCount.textContent = n > 99 ? '99+' : n; pendingCount.classList.remove('hidden'); }
  else pendingCount.classList.add('hidden');
}

/* ---------------- Workbench: pending review ---------------- */
async function loadPendingReviews() {
  const listEl = $('pending-list');
  try {
    const res = await apiJson('/api/v1/reviews/pending?limit=30');
    const items = res.items || [];
    setPendingBadge(items.length);
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = `<div class="empty-hint">没有待审核的需求 🎉</div>`;
      return;
    }
    items.forEach((item) => listEl.appendChild(buildPendingItem(item)));
  } catch (e) {
    listEl.innerHTML = `<div class="empty-hint">加载失败：${esc(e.message)}</div>`;
  }
}
function refreshPending() { loadPendingReviews(); }

function buildPendingItem(item) {
  const meta = item.metadata || {};
  const extracted = meta.extracted || {};
  const analysis = meta.analysis || {};
  const risk = meta.risk || {};
  const title = extracted.requirement_title || firstLine(item.original_text, 36) || '待审核需求';
  const summary = extracted.summary || item.original_text || '';

  const box = document.createElement('div');
  box.className = 'wb-item';
  box.innerHTML = `
    <div class="w-title"></div>
    <div class="w-meta">
      <span class="src-tag">#${item.source_id} · ${esc(item.source_type || 'web')}</span>
      <span class="status-tag pending_review">待审核</span>
      <span style="margin-left:auto">${esc(fmtTime(item.submitted_at || item.updated_at))}</span>
    </div>
    <div class="w-ext">
      <div class="w-section"><div class="k">摘要</div><div class="w-summary"></div></div>
      <div class="w-section"><div class="k">来源原文</div><div class="orig-text"></div></div>
      <div class="w-section"><div class="k">分析结论</div><div class="verdict-row"></div></div>
      <div class="w-section"><div class="k">风险</div><div class="risk-line"></div></div>
      <div class="w-actions">
        <textarea class="review-note" placeholder="审核意见（可选）"></textarea>
        <button class="btn primary" data-d="approved" type="button">✓ 通过并生成需求</button>
        <button class="btn" data-d="rejected" type="button">✕ 退回</button>
      </div>
    </div>`;
  box.querySelector('.w-title').textContent = title;
  box.querySelector('.w-summary').textContent = summary;
  box.querySelector('.orig-text').textContent = item.original_text || '';

  const verdictRow = box.querySelector('.verdict-row');
  verdictRow.innerHTML = [
    ['重复', analysis.duplicate],
    ['关联', analysis.related],
    ['冲突', analysis.conflict],
  ].map(([label, v]) => `<span class="verdict ${v ? 'yes' : ''}">${label} ${v ? '是' : '否'}</span>`).join('');

  const riskLine = box.querySelector('.risk-line');
  const risks = [['质量', risk.quality_risk], ['变更', risk.change_risk], ['技术影响', risk.technical_impact_risk]];
  riskLine.innerHTML = risks.map(([label, lvl]) => `<span class="lvl ${levelClass(lvl)}"><span class="bar"></span>${esc(label)}·${levelText(lvl)}</span>`).join('');

  box.addEventListener('click', (e) => {
    if (e.target.closest('.w-actions, .review-note, .orig-text')) return;
    box.classList.toggle('open');
  });

  const approveBtn = box.querySelector('[data-d="approved"]');
  const rejectBtn = box.querySelector('[data-d="rejected"]');
  approveBtn.addEventListener('click', () => decideReview(item.source_id, 'approved', box));
  rejectBtn.addEventListener('click', () => decideReview(item.source_id, 'rejected', box));
  return box;
}

async function decideReview(sourceId, decision, boxEl) {
  const noteEl = boxEl.querySelector('.review-note');
  const comment = (noteEl && noteEl.value.trim()) || undefined;
  const buttons = boxEl.querySelectorAll('.btn');
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const res = await apiJson('/api/v1/reviews/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_id: sourceId, decision, reviewer_name: '需求负责人', comment }),
    });
    let line;
    if (decision === 'approved') {
      line = `✅ 来源 #${sourceId} 已通过审核 → 生成正式需求 ${res.requirement_key || ''}（v${res.version_no || 1}），已入库。`;
      toast('已通过并生成正式需求', 'ok');
    } else {
      line = `↩️ 来源 #${sourceId} 已退回${comment ? '，意见：' + comment : ''}。`;
      toast('已退回该需求', 'ok');
    }
    appendSystem(line);
    refreshPending();
    loadAuditEvents();
  } catch (e) {
    toast('审核提交失败：' + e.message, 'err');
    buttons.forEach((b) => { b.disabled = false; });
  }
}

/* ---------------- Workbench: library ---------------- */
/* ---------------- 需求库：全宽面板 ---------------- */
const LIB_TABLE_COLS = 9;
const LIB_PAGE_LIMIT = 300;

// 需求关系类型 → 中文标签（对应后端 requirement_relation.relation_type 的枚举）
const RELATION_LABEL = { duplicates_of: '重复', related: '关联', conflict: '冲突', depends: '依赖' };

function openLibrary() {
  $('library-panel').classList.remove('hidden');
  document.querySelector('.workspace').classList.add('library-open');
  document.querySelectorAll('.wb-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === 'library'));
  loadLibraryTable();
}

function closeLibrary() {
  $('library-panel').classList.add('hidden');
  document.querySelector('.workspace').classList.remove('library-open');
  setRailTab(libraryLastTab === 'library' ? 'pending' : libraryLastTab);
}

function libraryQuery() {
  const parts = [];
  const put = (k, v) => { if (v) parts.push(k + '=' + encodeURIComponent(v)); };
  put('q', $('lib-filter-q').value.trim());
  put('channel', $('lib-filter-channel').value);
  put('status', $('lib-filter-status').value);
  put('business_domain', $('lib-filter-domain').value);
  put('department', $('lib-filter-department').value);
  put('submitted_from', $('lib-filter-from').value);
  // 「至」当天要含进去：日期控件给的是 YYYY-MM-DD，若原样传给后端会被当成当天 00:00，
  // 于是当天提交的需求全被排除。补到 23:59:59 才是用户理解的「截至今天」。
  const to = $('lib-filter-to').value;
  if (to) put('submitted_to', to + 'T23:59:59');
  return parts.join('&');
}

function resetLibraryFilters() {
  ['lib-filter-q', 'lib-filter-channel', 'lib-filter-status', 'lib-filter-domain',
   'lib-filter-department', 'lib-filter-from', 'lib-filter-to'].forEach((id) => { $(id).value = ''; });
  $('lib-panel-detail').classList.add('hidden');
}

async function loadLibraryTable() {
  const body = $('lib-table-body');
  body.innerHTML = `<tr><td colspan="${LIB_TABLE_COLS}" class="empty-hint">加载中…</td></tr>`;
  const qs = libraryQuery();
  try {
    const res = await apiJson('/api/v1/requirements?limit=' + LIB_PAGE_LIMIT + (qs ? '&' + qs : ''));
    const items = res.items || [];
    // 筛选下拉的候选项只在「无筛选」时刷新：否则一筛选，选项就只剩当前命中的值，
    // 用户再也切不回上一个条件。（没有 facets 接口，这是最省事且不误导的做法。）
    if (!qs) syncFilterOptions(items);
    renderLibraryRows(items);
    $('lib-count').textContent = items.length + ' 条';
  } catch (e) {
    body.innerHTML = `<tr><td colspan="${LIB_TABLE_COLS}" class="empty-hint">加载失败：${esc(e.message)}</td></tr>`;
  }
}

function renderLibraryRows(items) {
  const body = $('lib-table-body');
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="${LIB_TABLE_COLS}" class="empty-hint">没有符合条件的需求</td></tr>`;
    return;
  }
  body.innerHTML = '';
  items.forEach((it) => {
    const name = it.requirement_name || it.final_requirement || it.requirement_key || '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="mono">${esc(it.requirement_key || '')}</td>
      <td class="lib-name"></td>
      <td><span class="status-tag ${it.status === 'active' ? 'active' : ''}">${esc(it.status || '')}</span></td>
      <td>${esc((it.business_domains || []).join('、'))}</td>
      <td>V${esc(it.current_version == null ? '' : it.current_version)}</td>
      <td>${esc(it.feature_count == null ? 0 : it.feature_count)}</td>
      <td>${esc((it.source_types || []).join('、'))}</td>
      <td>${esc((it.requester_names || []).join('、'))}</td>
      <td>${esc(it.latest_source_submitted_at || '')}</td>`;
    tr.querySelector('.lib-name').textContent = name;
    tr.addEventListener('click', () => showRequirementDetail(it.requirement_key, name, 'lib-panel-detail'));
    body.appendChild(tr);
  });
}

function fillSelect(id, values, placeholder) {
  const el = $(id);
  const current = el.value;
  el.innerHTML = `<option value="">${esc(placeholder)}</option>` +
    values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (values.includes(current)) el.value = current;
}

function syncFilterOptions(items) {
  const fromArrays = (key) => {
    const set = new Set();
    items.forEach((it) => (it[key] || []).forEach((v) => { if (v) set.add(v); }));
    return [...set].sort();
  };
  const fromScalar = (key) => {
    const set = new Set();
    items.forEach((it) => { if (it[key]) set.add(it[key]); });
    return [...set].sort();
  };
  fillSelect('lib-filter-channel', fromArrays('source_types'), '全部渠道');
  fillSelect('lib-filter-status', fromScalar('status'), '全部状态');
  fillSelect('lib-filter-domain', fromArrays('business_domains'), '全部领域');
  fillSelect('lib-filter-department', fromArrays('departments'), '全部部门');
}

function exportLibraryCsv() {
  // 不能用 apiJson —— 它写死了 resp.json()。这里让浏览器按 Content-Disposition 直接下载。
  const qs = libraryQuery();
  const a = document.createElement('a');
  a.href = '/api/v1/requirements/export?limit=5000' + (qs ? '&' + qs : '');
  a.download = 'requirements.csv';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function showRequirementDetail(key, name, targetId = 'lib-panel-detail') {
  const detailEl = $(targetId);
  if (!detailEl) return;
  detailEl.classList.remove('hidden');
  detailEl.innerHTML = `<div class="empty-hint">加载版本与溯源…</div>`;
  try {
    const [vers, trace, features, diff, relationRes] = await Promise.all([
      apiJson('/api/v1/requirements/' + encodeURIComponent(key) + '/versions'),
      apiJson('/api/v1/requirements/' + encodeURIComponent(key) + '/trace'),
      apiJson('/api/v1/requirements/' + encodeURIComponent(key) + '/features'),
      apiJson('/api/v1/requirements/' + encodeURIComponent(key) + '/diff'),
      apiJson('/api/v1/requirements/' + encodeURIComponent(key) + '/relations'),
    ]);
    const req = (trace.requirement || {});
    const featureItems = features.items || [];
    const diffItems = diff || {};
    let html = `<div class="d-title"></div>`;
    if (req.final_requirement) html += `<p style="font-size:12px;color:var(--text-2);margin:2px 0 8px"></p>`;
    html += `<div class="section-label">当前功能明细</div><ul class="timeline feature-lines"></ul>`;
    html += `<div class="section-label">版本 Diff</div><ul class="timeline diff-lines"></ul>`;
    html += `<div class="section-label">版本历史</div><ul class="timeline version-lines"></ul>`;
    html += `<div class="section-label">需求关系</div><ul class="timeline relation-lines"></ul>`;
    detailEl.innerHTML = html;
    detailEl.querySelector('.d-title').textContent = `${key} · ${name || ''}`;
    if (req.final_requirement) detailEl.querySelector('p').textContent = req.final_requirement;
    const featureLine = detailEl.querySelector('.feature-lines');
    const diffLine = detailEl.querySelector('.diff-lines');
    const versionLine = detailEl.querySelector('.version-lines');
    const relationLine = detailEl.querySelector('.relation-lines');
    if (!featureItems.length) {
      featureLine.innerHTML = `<li style="border:0;padding-left:0"><span class="empty-hint">无功能条目</span></li>`;
    }
    featureItems.forEach((f) => {
      const li = document.createElement('li');
      li.innerHTML = `<div class="t-head">${esc(f.feature_key || '')}</div><div class="t-sub">引入 V${esc(f.origin_version_no || '')}${f.origin_source_id ? ` · source #${esc(f.origin_source_id)}` : ''}</div><pre>${esc(f.content || '')}</pre>`;
      featureLine.appendChild(li);
    });
    const diffHtml = [];
    (diffItems.added || []).forEach((item) => diffHtml.push(`<li><div class="t-head">新增 ${esc(item.feature_key || '')}</div><pre>${esc(item.content || '')}</pre></li>`));
    (diffItems.modified || []).forEach((item) => diffHtml.push(`<li><div class="t-head">修改 ${esc(item.feature_key || '')}</div><div class="t-sub">Before → After</div><pre>${esc(item.before || '')}\n---\n${esc(item.after || '')}</pre></li>`));
    (diffItems.removed || []).forEach((item) => diffHtml.push(`<li><div class="t-head">删除 ${esc(item.feature_key || '')}</div><pre>${esc(item.content || '')}</pre></li>`));
    diffLine.innerHTML = diffHtml.length ? diffHtml.join('') : `<li style="border:0;padding-left:0"><span class="empty-hint">当前相邻版本无差异</span></li>`;
    const versions = vers.items || trace.versions || [];
    if (!versions.length) versionLine.innerHTML = `<li style="border:0;padding-left:0"><span class="empty-hint">无版本记录</span></li>`;
    versions.forEach((v) => {
      const li = document.createElement('li');
      const head = document.createElement('div');
      head.className = 't-head';
      head.textContent = `V${v.version_no} · ${v.change_type || ''}`;
      const sub = document.createElement('div');
      sub.className = 't-sub';
      sub.textContent = `${v.change_summary || v.version_title || ''}（${v.created_by || ''} · ${fmtTime(v.created_at) || ''}）`;
      li.appendChild(head);
      li.appendChild(sub);
      if (v.requirement_snapshot) {
        const pre = document.createElement('pre');
        pre.textContent = v.requirement_snapshot;
        li.appendChild(pre);
      }
      versionLine.appendChild(li);
    });

    // —— 需求关系：分析阶段算出、审核通过时落库的 REQ↔REQ 边；proposed 的等待人工裁决 ——
    const relations = relationRes.items || [];
    if (!relations.length) {
      relationLine.innerHTML = `<li style="border:0;padding-left:0"><span class="empty-hint">暂无关联需求</span></li>`;
    }
    relations.forEach((rel) => {
      const li = document.createElement('li');
      const head = document.createElement('div');
      head.className = 't-head';
      head.textContent = `${rel.other_requirement_key || ''} · ${rel.other_requirement_name || ''}`;
      const sub = document.createElement('div');
      sub.className = 't-sub';
      const arrow = rel.direction === 'outgoing' ? '本需求 → 对方' : '对方 → 本需求';
      const sim = rel.similarity == null ? '' : ` · 相似度 ${Math.round(rel.similarity * 100)}%`;
      sub.textContent = `${arrow} · ${RELATION_LABEL[rel.relation_type] || rel.relation_type}${sim}`;
      li.appendChild(head);
      li.appendChild(sub);
      if (rel.reason) {
        const pre = document.createElement('pre');
        pre.textContent = rel.reason;
        li.appendChild(pre);
      }
      if (rel.status === 'proposed') {
        const row = document.createElement('div');
        row.className = 'doc-actions';
        ['确认', '驳回'].forEach((label) => {
          const btn = document.createElement('button');
          btn.className = 'btn ghost';
          btn.textContent = label;
          btn.addEventListener('click', async () => {
            btn.disabled = true;
            try {
              await apiJson('/api/v1/requirements/relations/' + rel.id, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: label === '确认' ? 'confirmed' : 'dismissed' }),
              });
              showRequirementDetail(key, name, targetId);
            } catch (err) {
              toast('裁决失败：' + err.message, 'err');
              btn.disabled = false;
            }
          });
          row.appendChild(btn);
        });
        li.appendChild(row);
      } else {
        const tag = document.createElement('span');
        tag.className = 'status-tag';
        tag.textContent = rel.status === 'confirmed' ? '已确认' : '已驳回';
        li.appendChild(tag);
      }
      relationLine.appendChild(li);
    });
  } catch (e) {
    detailEl.innerHTML = `<div class="empty-hint">加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------------- Workbench: documents ---------------- */
async function loadDocuments(query = '') {
  const listEl = $('document-list');
  try {
    const res = await apiJson('/api/v1/documents?limit=50');
    let items = res.items || [];
    const q = (query || '').trim().toLowerCase();
    if (q) {
      items = items.filter((doc) => {
        const hay = `${doc.file_name || ''} ${doc.extracted_text || ''} ${doc.original_text || ''}`.toLowerCase();
        return hay.includes(q);
      });
    }
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = '<div class="empty-hint">暂无文档记录</div>';
      return;
    }
    items.forEach((doc) => listEl.appendChild(buildDocumentItem(doc)));
  } catch (e) {
    listEl.innerHTML = `<div class="empty-hint">加载失败：${esc(e.message)}</div>`;
  }
}

function buildDocumentItem(doc) {
  const box = document.createElement('div');
  box.className = 'wb-item';
  box.innerHTML = `
    <div class="w-title"></div>
    <div class="w-meta">
      <span class="src-tag">#${doc.id}</span>
      <span class="status-tag active">${esc(doc.content_type || 'file')}</span>
      <span style="margin-left:auto">${esc(doc.size_bytes ? (doc.size_bytes / 1024).toFixed(1) + ' KB' : '0 KB')}</span>
    </div>
    <div class="w-ext">
      <div class="w-section"><div class="k">存储</div><div class="orig-text"></div></div>
      <div class="w-section"><div class="k">摘要</div><div class="w-summary"></div></div>
    </div>`;
  box.querySelector('.w-title').textContent = doc.file_name || '未命名文档';
  box.querySelector('.orig-text').textContent = doc.storage_uri || '';
  box.querySelector('.w-summary').textContent = doc.extracted_text ? String(doc.extracted_text).slice(0, 180) : '已保留原始文件与固定切片索引。';
  box.addEventListener('click', () => showDocumentDetail(doc.id));
  return box;
}

async function showDocumentDetail(documentId) {
  const detailEl = $('document-detail');
  detailEl.classList.remove('hidden');
  detailEl.innerHTML = '<div class="empty-hint">加载文档详情…</div>';
  try {
    const [doc, chunks] = await Promise.all([
      apiJson('/api/v1/documents/' + documentId),
      apiJson('/api/v1/documents/' + documentId + '/chunks?limit=20'),
    ]);
    const hits = chunks.items || [];
    const metadata = doc.metadata || {};
    const fields = metadata.normalized_fields ? Object.entries(metadata.normalized_fields) : [];
    const html = `
      <div class="d-title">${esc(doc.file_name || '文档')}</div>
      <div class="doc-meta-row">
        <span class="src-tag">${esc(doc.content_type || 'file')}</span>
        <span class="status-tag">${esc(doc.source_type || 'web')}</span>
        <span class="status-tag active">${esc(doc.size_bytes ? (doc.size_bytes / 1024).toFixed(1) + 'KB' : '0KB')}</span>
      </div>
      <div class="doc-actions">
        <button type="button" class="btn ghost" data-action="reindex-doc">重建分片索引</button>
        <button type="button" class="btn" data-action="open-source">查看来源</button>
      </div>
      <div class="section-label">字段归一</div>
      ${fields.length ? `<div class="kv">${fields.map(([k, v]) => `<span class="field"><span class="k">${esc(k)}</span><span class="chip tagged">${esc(v)}</span></span>`).join('')}</div>` : '<div class="empty-hint">无标准字段</div>'}
      <div class="section-label">来源地址</div>
      <div class="orig-text" style="white-space:pre-wrap">${esc(doc.storage_uri || '')}</div>
      <div class="section-label">证据切片</div>
      <ul class="timeline">
        ${hits.length ? hits.map((h) => `<li><div class="t-head">切片 #${h.chunk_index}</div><div class="t-sub">${h.metadata && h.metadata.source ? esc(h.metadata.source) : 'fixed-slice'} · ${h.chunk_text ? '长度 ' + h.chunk_text.length + ' 字' : ''}</div><pre>${esc(h.chunk_text || '')}</pre></li>`).join('') : '<li><span class="empty-hint">暂无切片命中</span></li>'}
      </ul>`;
    detailEl.innerHTML = html;
    const reindexBtn = detailEl.querySelector('[data-action="reindex-doc"]');
    const openBtn = detailEl.querySelector('[data-action="open-source"]');
    if (reindexBtn) {
      reindexBtn.addEventListener('click', async () => {
        try {
          const res = await apiJson('/api/v1/documents/' + documentId + '/reindex', { method: 'POST' });
          toast('已重建文档切片索引：' + (res.result || 'queued'), 'ok');
          await showDocumentDetail(documentId);
        } catch (e) {
          toast('重建索引失败：' + e.message, 'err');
        }
      });
    }
    if (openBtn) {
      openBtn.addEventListener('click', () => {
        const uri = doc.storage_uri || '';
        if (!uri) {
          toast('该文档未保存有效来源地址', 'err');
          return;
        }
        if (uri.startsWith('file://')) {
          toast('本地文件地址无法直接在浏览器中打开：' + uri, 'ok');
          return;
        }
        window.open(uri, '_blank', 'noopener');
      });
    }
  } catch (e) {
    detailEl.innerHTML = `<div class="empty-hint">加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------------- Workbench: audit & status ---------------- */
async function loadAuditEvents() {
  const listEl = $('audit-list');
  try {
    const res = await apiJson('/api/v1/audit/events?limit=15');
    const items = res.items || [];
    listEl.innerHTML = '';
    if (!items.length) { listEl.innerHTML = `<div class="empty-hint">暂无审计事件</div>`; return; }
    items.forEach((a) => {
      const row = document.createElement('div');
      row.className = 'audit-item';
      row.innerHTML = `<span class="a-time"></span><div style="min-width:0"><div class="a-type"></div><div class="a-sub"></div></div>`;
      row.querySelector('.a-time').textContent = fmtTime(a.created_at);
      row.querySelector('.a-type').textContent = a.event_type || '';
      row.querySelector('.a-sub').textContent = `${a.aggregate_type || ''} ${a.aggregate_id || ''} · ${a.actor_id || a.actor_type || ''} · ${a.result_status || ''}`.trim();
      listEl.appendChild(row);
    });
  } catch (e) {
    listEl.innerHTML = `<div class="empty-hint">加载失败：${esc(e.message)}</div>`;
  }
}

async function loadStatus() {
  const setPill = (pill, ok, label) => {
    pill.classList.remove('ok', 'bad');
    pill.classList.add(ok ? 'ok' : 'bad');
    pill.title = label;
  };
  try {
    const db = await apiJson('/api/v1/health/db');
    setPill(dbPill, !!db.database, '数据库 ' + (db.status || ''));
  } catch (e) { setPill(dbPill, false, '数据库不可达'); }
  try {
    const llm = await apiJson('/api/v1/health/llm');
    const ok = !!llm.configured;
    setPill(llmPill, ok, `${llm.provider} ${llm.model}`);
    const text = `${llm.provider || ''} · ${llm.model || ''}`;
    modelChip.textContent = text || '未配置模型';
    const tag = $('audit-model');
    tag.textContent = '模型 ' + text;
  } catch (e) { setPill(llmPill, false, 'LLM 不可达'); }
}

/* ---------------- Wire events ---------------- */
composerForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if ((!text && !state.pendingFiles.length) || state.streaming) return;
  const files = state.pendingFiles.splice(0);
  inputEl.value = '';
  resizeInput();
  renderAttachList();
  setSendEnabled();
  runChat(text, files);
});

inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    composerForm.requestSubmit();
  }
});
inputEl.addEventListener('input', () => { setSendEnabled(); resizeInput(); });

attachBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  const selected = Array.from(fileInput.files || []);
  selected.forEach((f) => {
    const key = f.name + '|' + f.size;
    if (!state.pendingFiles.some((p) => (p.name + '|' + p.size) === key)) {
      state.pendingFiles.push({ file: f, name: f.name, size: f.size });
    }
  });
  fileInput.value = '';
  renderAttachList();
  setSendEnabled();
});

function fmtFileSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function renderAttachList() {
  attachList.innerHTML = '';
  if (!state.pendingFiles.length) {
    attachList.classList.add('hidden');
    return;
  }
  attachList.classList.remove('hidden');
  state.pendingFiles.forEach((p, idx) => {
    const chip = document.createElement('span');
    chip.className = 'attach-chip';
    chip.innerHTML = `📎 <span class="a-name" title="${esc(p.name)}">${esc(p.name)}</span><span class="a-size">${fmtFileSize(p.size)}</span><button type="button" class="a-remove" title="移除">×</button>`;
    chip.querySelector('.a-remove').addEventListener('click', () => {
      state.pendingFiles.splice(idx, 1);
      renderAttachList();
      setSendEnabled();
    });
    attachList.appendChild(chip);
  });
}

$('new-chat').addEventListener('click', newChat);
$('toggle-left').addEventListener('click', () => toggleRail('rail-sessions', 'toggle-left'));
$('toggle-right').addEventListener('click', () => toggleRail('rail-workbench', 'toggle-right'));

document.querySelectorAll('.wb-tab').forEach((b) => b.addEventListener('click', () => setRailTab(b.dataset.tab)));
$('refresh-pending').addEventListener('click', refreshPending);
$('refresh-documents').addEventListener('click', loadDocuments);
$('refresh-audit').addEventListener('click', loadAuditEvents);
$('lib-close').addEventListener('click', closeLibrary);
$('lib-apply').addEventListener('click', loadLibraryTable);
$('lib-reset').addEventListener('click', () => { resetLibraryFilters(); loadLibraryTable(); });
$('lib-export').addEventListener('click', exportLibraryCsv);
$('lib-filter-q').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); loadLibraryTable(); }
});
$('document-search').addEventListener('submit', (e) => {
  e.preventDefault();
  loadDocuments($('document-q').value.trim());
});

function toggleRail(id, btnId) {
  const rail = $(id);
  const btn = $(btnId);
  const collapsed = rail.classList.toggle('collapsed');
  btn.classList.toggle('active', !collapsed);
}

/* ---------------- Init ---------------- */
async function init() {
  setSendEnabled();
  newChat();
  loadConversations();
  loadPendingReviews();
  loadDocuments();
  loadAuditEvents();
  loadStatus();
  setInterval(() => { if (!state.streaming) loadStatus(); }, 30000);
}
init();
