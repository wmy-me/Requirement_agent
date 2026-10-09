/* 对话式 Agent 入口：真实接入 agent chat stream，不伪造需求结果。 */
'use strict';

const chatState = { sessionId: null, busy: false, files: [] };
const chatToken = document.querySelector('meta[name="ra-ui-token"]')?.content || '';
const chatHeaders = () => ({ Accept: 'text/event-stream', ...(chatToken ? { Authorization: `Bearer ${chatToken}` } : {}) });

function chatNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addMessage(role, text) {
  const host = document.getElementById('agent-messages');
  /* 欢迎页只描述「还没有对话」这一种状态。原来没人把它摘掉，
     于是第一条消息发出去之后，「把需求交给我」那块大标题还杵在对话上方，
     正文从半屏处才开始。 */
  const welcome = host.querySelector('.agent-welcome');
  if (welcome) welcome.remove();
  const row = chatNode('article', `agent-message agent-message-${role}`);
  row.append(chatNode('div', 'agent-message-role', role === 'user' ? '我' : '需求助手'));
  const body = chatNode('div', 'agent-message-body', text || '');
  row.append(body);
  host.append(row);
  row.scrollIntoView({ block: 'end' });
  return body;
}

function parseEventBlock(block) {
  const lines = block.split('\n');
  const event = (lines.find((line) => line.startsWith('event:')) || '').slice(6).trim() || 'message';
  const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
  try { return { event, data: data ? JSON.parse(data) : {} }; } catch (error) { return { event, data: { t: data } }; }
}

async function sendMessage(message) {
  if (!message || chatState.busy) return;
  chatState.busy = true;
  const send = document.getElementById('agent-send');
  const status = document.getElementById('agent-status');
  send.disabled = true; status.textContent = '正在分析…';
  addMessage('user', message);
  const assistant = addMessage('assistant', '');
  assistant.classList.add('agent-thinking');
  try {
    const clientId = uuidv4();
    let response;
    if (chatState.files.length) {
      const formData = new FormData();
      formData.append('message', message); formData.append('session_id', chatState.sessionId || ''); formData.append('client_message_id', clientId); formData.append('source_type', 'web'); formData.append('requester_name', '我'); formData.append('analysis_mode', 'strict');
      chatState.files.forEach((file) => formData.append('files', file, file.name));
      response = await fetch('/api/v1/agent/chat/stream-with-files', { method: 'POST', headers: chatHeaders(), body: formData });
    } else {
      response = await fetch('/api/v1/agent/chat/stream', { method: 'POST', headers: { ...chatHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ message, session_id: chatState.sessionId || null, client_message_id: clientId, source_type: 'web', requester_name: '我', analysis_mode: 'strict' }) });
    }
    if (!response.ok) throw new Error(`对话请求失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      const blocks = buffer.split('\n\n'); buffer = blocks.pop() || '';
      blocks.forEach((block) => {
        const packet = parseEventBlock(block);
        if (packet.event === 'session') chatState.sessionId = packet.data.session_id || chatState.sessionId;
        if (packet.event === 'step') { status.textContent = packet.data.label || '正在分析…'; }
        if (packet.event === 'narrative' && packet.data.t) assistant.textContent += packet.data.t;
        if (packet.event === 'artifacts' && Array.isArray(packet.data.artifacts)) renderArtifacts(packet.data.artifacts);
        if (packet.event === 'error') assistant.textContent += `\n${packet.data.message || '分析失败'}`;
      });
    }
    if (!assistant.textContent) assistant.textContent = 'Agent 没有返回可展示内容，请重试。';
  } catch (error) {
    assistant.textContent = error.message || '网络异常，请重试。';
  } finally {
    assistant.classList.remove('agent-thinking'); chatState.files = []; renderAttachments(); chatState.busy = false; send.disabled = false; status.textContent = '在线，可继续对话';
  }
}

function renderArtifacts(artifacts) {
  const context = document.getElementById('agent-recent');
  const block = chatNode('div', 'agent-artifacts');
  block.append(chatNode('div', 'agent-context-title', '本轮产生的工作产物'));
  artifacts.slice(0, 5).forEach((item) => block.append(chatNode('div', 'agent-artifact', item.title || item.type || '分析结果')));
  context.prepend(block);
}

/** 工作区里的一条记录：主标题一行，来源与时间一行。 */
function contextItem(text, meta, href) {
  const node = chatNode(href ? 'a' : 'div', 'agent-context-item', text);
  if (href) node.href = href;
  if (meta) node.append(chatNode('span', 'agent-context-meta', meta));
  return node;
}

/**
 * 待审来源的标题。**顶层没有 requirement_name / requirement_key** ——
 * `/api/v1/reviews/pending` 返回的是来源级对象，标题埋在
 * `metadata.extracted.requirement_title` 里（契约见 docs/api-contract.md §reviews/pending）。
 * 照顶层字段读的后果是整列渲染成同一个兜底字面量，看着像「有五条待审」，
 * 其实一个字都没读出来。
 */
function sourceTitle(item) {
  const extracted = (item.metadata && item.metadata.extracted) || {};
  return extracted.requirement_title || extracted.summary
    || fmt.cut(item.original_text, 40) || '未命名来源';
}

function metaLine(...parts) {
  return parts.filter(Boolean).join(' · ');
}

async function loadContext() {
  const pending = document.getElementById('agent-pending');
  const recent = document.getElementById('agent-recent');
  try {
    const data = await api.dashboard.pending(5);
    const items = data.items || [];
    pending.replaceChildren(...(items.length ? items.map((item) => contextItem(
      sourceTitle(item),
      metaLine(item.source_id, item.requester_name, fmt.rel(item.submitted_at)),
      `/app/reviews/detail?source_id=${encodeURIComponent(String(item.source_id))}`,
    )) : [chatNode('div', 'agent-muted', '暂无待审核需求')]));
  } catch (error) { pending.replaceChildren(chatNode('div', 'agent-muted', '待审核暂不可用')); }
  try {
    const data = await api.requirements.list({ limit: 5 });
    const items = data.items || [];
    recent.replaceChildren(...(items.length ? items.map((item) => contextItem(
      item.requirement_name || item.requirement_key || '未命名需求',
      metaLine(item.requirement_key, item.current_version ? `V${item.current_version}` : null, fmt.rel(item.latest_source_submitted_at)),
      `/app/requirements/${encodeURIComponent(String(item.requirement_key || ''))}`,
    )) : [chatNode('div', 'agent-muted', '暂无正式需求')]));
  } catch (error) { recent.replaceChildren(chatNode('div', 'agent-muted', '需求库暂不可用')); }
  try {
    const data = await request('/api/v1/documents', { params: { limit: 8 } });
    const items = data.items || [];
    const target = document.getElementById('agent-documents');
    target.replaceChildren(...(items.length ? items.map((item) => contextItem(
      item.file_name || item.title || '未命名文档',
      metaLine(item.content_type, item.size_bytes ? fmt.size(item.size_bytes) : null),
    )) : [chatNode('div', 'agent-muted', '暂无文档证据')]));
  } catch (error) { document.getElementById('agent-documents').replaceChildren(chatNode('div', 'agent-muted', '文档暂不可用')); }
  try {
    /* `/ops/outbox` 返回的是 {counts, dead_letters[], consumer}，**没有 items**。
       原来照 `data.items` 读，所以这一栏无论后台积了多少死信都显示「暂无运行异常」。 */
    const data = await api.dashboard.outbox(8);
    const dead = data.dead_letters || [];
    const counts = data.counts || {};
    const target = document.getElementById('agent-ops');
    target.replaceChildren(...(dead.length ? dead.map((item) => contextItem(
      item.last_error || item.event_type || '运行事件',
      metaLine(item.event_type, `重试 ${item.retries ?? 0} 次`, fmt.rel(item.created_at)),
    )) : [chatNode('div', 'agent-muted',
      counts.completed ? `暂无死信（已完成 ${counts.completed} 条）` : '暂无运行异常')]));
  } catch (error) { document.getElementById('agent-ops').replaceChildren(chatNode('div', 'agent-muted', '运行状态暂不可用')); }
}

async function loadSessions() {
  const list = document.getElementById('session-list');
  try {
    const data = await request('/api/v1/conversations', { params: { limit: 30 } });
    const items = data.items || data.conversations || [];
    if (!items.length) return;
    list.replaceChildren(...items.map((item) => {
      const id = String(item.session_id || item.id || '');
      const row = chatNode('div', 'agent-session-row');
      const button = chatNode('button', 'agent-session-item', item.title && item.title !== '新对话' ? item.title : (item.summary || '新对话'));
      button.type = 'button'; button.title = button.textContent; button.addEventListener('click', () => loadSession(id));
      const actions = chatNode('span', 'agent-session-actions');
      const rename = chatNode('button', 'agent-session-action', '改名'); rename.type = 'button'; rename.title = '重命名会话'; rename.onclick = (event) => { event.stopPropagation(); renameSession(id, item); };
      const remove = chatNode('button', 'agent-session-action agent-session-delete', '删'); remove.type = 'button'; remove.title = '删除会话'; remove.onclick = (event) => { event.stopPropagation(); deleteSession(id); };
      actions.append(rename, remove); row.append(button, actions); return row;
    }));
  } catch (error) { list.replaceChildren(chatNode('div', 'agent-muted', '会话列表暂不可用')); }
}

async function renameSession(id, item) {
  const current = item.title && item.title !== '新对话' ? item.title : (item.summary || '新对话');
  const next = window.prompt('请输入新的会话名称', current);
  if (!next || !next.trim()) return;
  try { await request(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', body: { title: next.trim() } }); await loadSessions(); }
  catch (error) { window.alert(`重命名失败：${error.message}`); }
}

async function deleteSession(id) {
  if (!window.confirm('删除后将无法从历史会话中恢复，确认删除吗？')) return;
  try { await request(`/api/v1/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }); if (chatState.sessionId === id) { chatState.sessionId = null; document.getElementById('agent-messages').replaceChildren(); } await loadSessions(); }
  catch (error) { window.alert(`删除失败：${error.message}`); }
}

function renderAttachments() {
  const host = document.getElementById('agent-attachments');
  host.replaceChildren(...chatState.files.map((file, index) => { const item = chatNode('span', 'agent-attachment', file.name); const remove = chatNode('button', '', '×'); remove.type = 'button'; remove.onclick = () => { chatState.files.splice(index, 1); renderAttachments(); }; item.append(remove); return item; }));
}

async function loadSession(sessionId) {
  if (!sessionId || chatState.busy) return;
  try {
    const data = await request(`/api/v1/agent/chat/${encodeURIComponent(sessionId)}`);
    chatState.sessionId = sessionId;
    const messages = data.history || data.messages || data.items || [];
    const host = document.getElementById('agent-messages'); host.replaceChildren();
    messages.forEach((item) => addMessage(item.role === 'user' ? 'user' : 'assistant', item.content || item.message || ''));
  } catch (error) { addMessage('assistant', `会话加载失败：${error.message}`); }
}

/** 切换工作区面板。`aria-selected` 是唯一的状态来源 —— CSS 也按它选样式，
 *  两处各记一份「哪个是当前页签」迟早会不一致。 */
function selectContextTab(button) {
  document.querySelectorAll('[data-context-tab]').forEach((item) => {
    const on = item === button;
    item.classList.toggle('active', on);
    item.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('[data-context-pane]').forEach((pane) => {
    pane.classList.toggle('active', pane.dataset.contextPane === button.dataset.contextTab);
  });
}

/** 小屏（≤1080px）工作区是覆盖式抽屉：面板本身还在，只是需要先打开。 */
function setContextOpen(open) {
  const panel = document.getElementById('agent-context');
  const scrim = document.getElementById('agent-context-scrim');
  const toggle = document.getElementById('agent-context-toggle');
  if (!panel || !scrim || !toggle) return;
  panel.classList.toggle('open', open);
  scrim.classList.toggle('open', open);
  toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

/** 侧栏底部的完整导航。对话页没有左侧导航栏，这是它到达其余页面的通路。 */
function renderNavFoot() {
  const foot = document.querySelector('.agent-session-foot');
  foot.replaceChildren(...NAV.map((item) => {
    const link = chatNode('a', '', item.label);
    link.href = item.path;
    link.title = item.hint;
    return link;
  }));
}

/** 欢迎页里的示例提问。`cloneNode` 不复制事件，所以重新插入后要再绑一次。 */
function bindPrompts(root, input) {
  root.querySelectorAll('[data-prompt]').forEach((button) => button.addEventListener('click', () => {
    input.value = button.dataset.prompt;
    input.focus();
  }));
}

function initChat() {
  const input = document.getElementById('agent-input');
  const form = document.getElementById('agent-composer');
  const welcome = document.querySelector('.agent-welcome');
  form.addEventListener('submit', (event) => { event.preventDefault(); const text = input.value.trim(); if (!text) return; input.value = ''; sendMessage(text); });
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); } });
  bindPrompts(document, input);
  document.getElementById('agent-attach').addEventListener('click', () => document.getElementById('agent-file').click());
  document.getElementById('agent-file').addEventListener('change', (event) => { chatState.files.push(...Array.from(event.target.files || [])); event.target.value = ''; renderAttachments(); });
  document.querySelectorAll('[data-context-tab]').forEach((button) => button.addEventListener('click', () => selectContextTab(button)));
  document.getElementById('agent-context-toggle').addEventListener('click', () => {
    setContextOpen(!document.getElementById('agent-context').classList.contains('open'));
  });
  document.getElementById('agent-context-scrim').addEventListener('click', () => setContextOpen(false));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setContextOpen(false); });
  document.getElementById('new-chat').addEventListener('click', () => {
    chatState.sessionId = null;
    const host = document.getElementById('agent-messages');
    /* 清空后要把欢迎页放回来，否则点「新建对话」只会得到一片空白。 */
    host.replaceChildren(welcome ? welcome.cloneNode(true) : '');
    bindPrompts(host, input);
    setContextOpen(false);
    input.focus();
  });
  renderNavFoot();
  loadContext();
  loadSessions();
}

document.addEventListener('DOMContentLoaded', initChat);
