const apiTokenInput = document.getElementById('api-token');
const ingestForm = document.getElementById('ingest-form');
const ingestResult = document.getElementById('ingest-result');
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatMessages = document.getElementById('chat-messages');
const pendingReviews = document.getElementById('pending-reviews');
const reviewForm = document.getElementById('review-form');
const reviewResult = document.getElementById('review-result');
const sourceTraceResult = document.getElementById('source-trace-result');
const versionList = document.getElementById('version-list');
const requirementTraceResult = document.getElementById('requirement-trace-result');
const auditEvents = document.getElementById('audit-events');
const dbStatus = document.getElementById('db-status');
const llmStatus = document.getElementById('llm-status');

let chatSessionId = null;
let lastRequirementText = '';
let lastArtifacts = null;

function getApiToken() {
  const token = apiTokenInput?.value.trim() || sessionStorage.getItem('requirement-agent-api-token') || '';
  if (token) {
    sessionStorage.setItem('requirement-agent-api-token', token);
  }
  return token;
}

async function fetchJson(url, options = {}) {
  const token = getApiToken();
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `请求失败: ${response.status}`);
  }
  return response.json();
}

function switchPanel(targetId) {
  document.querySelectorAll('.nav').forEach((button) => {
    button.classList.toggle('active', button.dataset.target === targetId);
  });
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === targetId);
  });
}

function clear(element) {
  element.replaceChildren();
}

function showJson(container, payload) {
  container.classList.remove('hidden');
  clear(container);
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(payload, null, 2);
  container.append(pre);
}

function getInputValue(id) {
  return document.getElementById(id)?.value.trim() || '';
}

function addCard(container, title, meta, body, onClick) {
  const card = document.createElement('button');
  card.type = 'button';
  card.className = 'item-card';
  if (onClick) {
    card.addEventListener('click', onClick);
  }

  const heading = document.createElement('h3');
  heading.textContent = title || '未命名';
  const metaNode = document.createElement('div');
  metaNode.className = 'requirement-meta';
  metaNode.textContent = meta || '';
  const bodyNode = document.createElement('p');
  bodyNode.textContent = body || '暂无内容';

  card.append(heading, metaNode, bodyNode);
  container.append(card);
}

function addChatMessage(role, content, artifacts = null) {
  const message = document.createElement('div');
  message.className = `chat-message ${role}`;

  const text = document.createElement('div');
  text.textContent = content;
  message.append(text);

  if (artifacts) {
    message.append(buildArtifactPanel(artifacts));
  }

  chatMessages.append(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function buildArtifactPanel(artifacts) {
  const extracted = artifacts.extracted || {};
  const analysis = artifacts.analysis || {};
  const risk = artifacts.risk || {};
  const panel = document.createElement('div');
  panel.className = 'artifact-panel';

  const title = document.createElement('strong');
  title.textContent = extracted.requirement_title || '已识别需求';
  const summary = document.createElement('p');
  summary.textContent = extracted.summary || extracted.raw_text || '暂无摘要';

  const meta = document.createElement('div');
  meta.className = 'artifact-meta';
  meta.textContent = [
    `业务域：${extracted.business_domain || 'general'}`,
    `优先级：${extracted.priority || 'medium'}`,
    `重复：${analysis.duplicate ? '是' : '否'}`,
    `冲突：${analysis.conflict ? '是' : '否'}`,
    `质量风险：${risk.quality_risk || '-'}`,
    `变更风险：${risk.change_risk || '-'}`,
  ].join(' · ');

  const actions = document.createElement('div');
  actions.className = 'actions compact-actions';
  const submit = document.createElement('button');
  submit.type = 'button';
  submit.className = 'secondary';
  submit.textContent = '提交这条需求进入审核';
  submit.addEventListener('click', async () => {
    try {
      await submitRequirementFromChat(extracted.raw_text || lastRequirementText);
    } catch (error) {
      addChatMessage('assistant', `提交失败：${error.message}`);
    }
  });
  actions.append(submit);

  panel.append(title, summary, meta, actions);
  return panel;
}

function buildIngestMetadata() {
  const metadata = {};
  const department = getInputValue('ingest-department');
  const businessDomain = getInputValue('ingest-business-domain');
  const sensitivityLevel = getInputValue('ingest-sensitivity-level');
  if (department) {
    metadata.department = department;
  }
  if (businessDomain) {
    metadata.business_domain = businessDomain;
  }
  if (sensitivityLevel) {
    metadata.sensitivity_level = sensitivityLevel;
  }
  return metadata;
}

async function submitRequirementFromChat(text) {
  const requirementText = (text || lastRequirementText || chatInput.value || '').trim();
  if (!requirementText) {
    addChatMessage('assistant', '还没有可提交的需求。请先用一句话描述你的需求。');
    return;
  }

  const requester = getInputValue('agent-requester-name') || 'anonymous';
  const result = await fetchJson('/api/v1/requirements/submit', {
    method: 'POST',
    body: JSON.stringify({
      source_type: getInputValue('agent-source-type') || 'web',
      requester_id: requester,
      requester_name: requester,
      original_text: requirementText,
      metadata: {
        chat_session_id: chatSessionId,
        extracted: lastArtifacts?.extracted || {},
        analysis: lastArtifacts?.analysis || {},
        risk: lastArtifacts?.risk || {},
      },
    }),
  });
  addChatMessage('assistant', `已提交进入审核，Source ID：${result.source_id}`);
  document.getElementById('source-trace-id').value = result.source_id || '';
  await loadPendingReviews();
}

async function submitIngest(event) {
  event.preventDefault();
  const fileInput = document.getElementById('ingest-file');
  const file = fileInput.files[0];
  const originalText = getInputValue('ingest-original-text');
  if (!originalText && !file) {
    ingestResult.classList.remove('hidden');
    ingestResult.textContent = '请填写需求文本或选择附件';
    return;
  }

  const form = new FormData();
  form.append('source_type', getInputValue('ingest-source-type') || 'web');
  form.append('metadata', JSON.stringify(buildIngestMetadata()));
  [
    ['requester_id', 'ingest-requester-id'],
    ['requester_name', 'ingest-requester-name'],
    ['source_event_id', 'ingest-source-event-id'],
    ['original_text', 'ingest-original-text'],
  ].forEach(([field, id]) => {
    const value = getInputValue(id);
    if (value) {
      form.append(field, value);
    }
  });
  if (file) {
    form.append('file', file);
  }

  const result = await fetchJson('/api/v1/requirements/ingest', {
    method: 'POST',
    body: form,
  });
  showJson(ingestResult, result);
  document.getElementById('source-trace-id').value = result.source_id || '';
  await loadPendingReviews();
}

async function loadPendingReviews() {
  clear(pendingReviews);
  try {
    const payload = await fetchJson('/api/v1/reviews/pending?limit=20');
    const items = payload.items || [];
    if (!items.length) {
      addCard(pendingReviews, '暂无待审核需求', '', '提交需求后会出现在这里');
      return;
    }
    items.forEach((item) => {
      const extracted = item.metadata?.extracted || {};
      const filterMeta = item.metadata?.retrieval_filters || {};
      addCard(
        pendingReviews,
        extracted.requirement_title || `Source #${item.source_id}`,
        `source_id=${item.source_id} · ${item.source_type} · ${filterMeta.department || '未标部门'} · ${item.processing_status}`,
        extracted.summary || item.extracted_text || item.original_text,
        () => selectReviewItem(item),
      );
    });
  } catch (error) {
    addCard(pendingReviews, '待审核加载失败', '', error.message);
  }
}

function selectReviewItem(item) {
  document.getElementById('review-source-id').value = item.source_id;
  document.getElementById('source-trace-id').value = item.source_id;
  document.getElementById('review-edited-requirement').value =
    item.metadata?.extracted?.summary || item.extracted_text || item.original_text || '';
  showJson(reviewResult, item);
}

async function submitReviewDecision(event) {
  event.preventDefault();
  const sourceId = Number(document.getElementById('review-source-id').value);
  if (!sourceId) {
    reviewResult.classList.remove('hidden');
    reviewResult.textContent = '请先选择或填写 Source ID';
    return;
  }
  const payload = {
    source_id: sourceId,
    decision: document.getElementById('review-decision').value,
    reviewer_name: '前端审核人',
    comment: getInputValue('review-comment') || null,
    edited_requirement: getInputValue('review-edited-requirement') || null,
  };
  const result = await fetchJson('/api/v1/reviews/submit', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  showJson(reviewResult, result);
  if (result.requirement_key) {
    document.getElementById('version-key').value = result.requirement_key;
  }
  await loadPendingReviews();
  await loadAuditEvents();
}

async function loadSourceTrace() {
  const sourceId = Number(document.getElementById('source-trace-id').value);
  if (!sourceId) {
    sourceTraceResult.classList.remove('hidden');
    sourceTraceResult.textContent = '请输入 Source ID';
    return;
  }
  const payload = await fetchJson(`/api/v1/sources/${sourceId}/trace`);
  showJson(sourceTraceResult, payload);
}

async function loadVersions() {
  clear(versionList);
  const key = getInputValue('version-key');
  if (!key) {
    addCard(versionList, '请输入 requirement_key', '', '例如 REQ-000001');
    return;
  }
  try {
    const payload = await fetchJson(`/api/v1/requirements/${encodeURIComponent(key)}/versions`);
    const items = payload.items || [];
    if (!items.length) {
      addCard(versionList, '暂无版本记录', key, '审核通过后会生成版本快照');
      return;
    }
    items.forEach((item) => {
      addCard(
        versionList,
        `V${item.version_no} · ${item.version_title}`,
        `${item.change_type} · ${item.created_by} · ${item.created_at || ''}`,
        item.requirement_snapshot,
      );
    });
    requirementTraceResult.classList.add('hidden');
  } catch (error) {
    addCard(versionList, '版本加载失败', key, error.message);
  }
}

async function loadRequirementTrace() {
  const key = getInputValue('version-key');
  if (!key) {
    requirementTraceResult.classList.remove('hidden');
    requirementTraceResult.textContent = '请输入 requirement_key';
    return;
  }
  const payload = await fetchJson(`/api/v1/requirements/${encodeURIComponent(key)}/trace`);
  showJson(requirementTraceResult, payload);
}

async function loadAuditEvents() {
  clear(auditEvents);
  try {
    const payload = await fetchJson('/api/v1/audit/events?limit=50');
    const items = payload.items || [];
    if (!items.length) {
      addCard(auditEvents, '暂无审计日志', '', '审核或提交后会产生审计记录');
      return;
    }
    items.forEach((item) => {
      addCard(
        auditEvents,
        item.event_type,
        `${item.aggregate_type}:${item.aggregate_id || '-'} · ${item.result_status}`,
        `${item.actor_id || 'system'} · ${item.created_at || ''}`,
      );
    });
  } catch (error) {
    addCard(auditEvents, '审计日志加载失败', '', error.message);
  }
}

async function loadStatus() {
  try {
    const db = await fetchJson('/api/v1/health/db');
    dbStatus.textContent = db.database ? '正常' : '不可用';
    dbStatus.className = `metric ${db.database ? 'success' : 'danger'}`;
  } catch (error) {
    dbStatus.textContent = `异常：${error.message}`;
    dbStatus.className = 'metric danger';
  }

  try {
    const llm = await fetchJson('/api/v1/health/llm');
    llmStatus.textContent = llm.configured ? `${llm.provider}/${llm.model}` : '未配置';
    llmStatus.className = `metric ${llm.configured ? 'success' : 'warning'}`;
  } catch (error) {
    llmStatus.textContent = `异常：${error.message}`;
    llmStatus.className = 'metric danger';
  }
}

chatForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) {
    return;
  }
  addChatMessage('user', message);
  chatInput.value = '';
  lastRequirementText = message;
  try {
    const payload = await fetchJson('/api/v1/agent/chat', {
      method: 'POST',
      body: JSON.stringify({
        session_id: chatSessionId,
        message,
        requirement_text: lastRequirementText,
        source_type: getInputValue('agent-source-type') || 'web',
        requester_name: getInputValue('agent-requester-name') || null,
      }),
    });
    chatSessionId = payload.session_id;
    lastArtifacts = payload.message?.artifacts || null;
    addChatMessage(payload.message?.role || 'assistant', payload.message?.content || '已理解需求', lastArtifacts);
  } catch (error) {
    addChatMessage('assistant', `理解失败：${error.message}`);
  }
});

document.getElementById('submit-requirement').addEventListener('click', async () => {
  try {
    await submitRequirementFromChat();
  } catch (error) {
    addChatMessage('assistant', `提交失败：${error.message}`);
  }
});

document.getElementById('clear-chat').addEventListener('click', () => {
  clear(chatMessages);
  chatSessionId = null;
  lastRequirementText = '';
  lastArtifacts = null;
});

ingestForm.addEventListener('submit', async (event) => {
  try {
    await submitIngest(event);
  } catch (error) {
    ingestResult.classList.remove('hidden');
    ingestResult.textContent = `上传失败：${error.message}`;
  }
});

reviewForm.addEventListener('submit', submitReviewDecision);
document.getElementById('load-pending-reviews').addEventListener('click', loadPendingReviews);
document.getElementById('refresh-after-upload').addEventListener('click', loadPendingReviews);
document.getElementById('load-source-trace').addEventListener('click', async () => {
  try {
    await loadSourceTrace();
  } catch (error) {
    sourceTraceResult.classList.remove('hidden');
    sourceTraceResult.textContent = `回放加载失败：${error.message}`;
  }
});
document.getElementById('load-versions').addEventListener('click', loadVersions);
document.getElementById('load-requirement-trace').addEventListener('click', async () => {
  try {
    await loadRequirementTrace();
  } catch (error) {
    requirementTraceResult.classList.remove('hidden');
    requirementTraceResult.textContent = `追踪加载失败：${error.message}`;
  }
});
document.getElementById('load-audit-events').addEventListener('click', loadAuditEvents);
document.querySelectorAll('.nav').forEach((button) => {
  button.addEventListener('click', () => switchPanel(button.dataset.target));
});

apiTokenInput.value = sessionStorage.getItem('requirement-agent-api-token') || '';
addChatMessage('assistant', '你好，我是需求助手。直接告诉我你的业务想法，我会自动抽取需求、分析风险和相似项。');
loadPendingReviews();
loadAuditEvents();
loadStatus();
