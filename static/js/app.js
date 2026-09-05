const submitForm = document.getElementById('submit-form');
const submitResult = document.getElementById('submit-result');
const requirementsList = document.getElementById('requirements-list');
const dbStatus = document.getElementById('db-status');
const llmStatus = document.getElementById('llm-status');
const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');
const apiTokenInput = document.getElementById('api-token');

async function fetchJson(url, options = {}) {
  const token = apiTokenInput?.value.trim() || sessionStorage.getItem('requirement-agent-api-token') || '';
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
    sessionStorage.setItem('requirement-agent-api-token', token);
  }
  const response = await fetch(url, {
    ...options,
    headers,
  });

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

function renderRequirements(items) {
  requirementsList.replaceChildren();
  if (!items || items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'requirement-item';
    empty.textContent = '暂无需求记录';
    requirementsList.append(empty);
    return;
  }

  items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'requirement-item';

    const title = document.createElement('h3');
    title.textContent = item.requirement_name || item.title || '未命名需求';

    const meta = document.createElement('div');
    meta.className = 'requirement-meta';
    meta.textContent = `状态: ${item.status || 'pending'} · 关键字: ${item.requirement_key || 'N/A'}`;

    const description = document.createElement('div');
    description.textContent = item.final_requirement || item.summary || item.description || '暂无描述';

    card.append(title, meta, description);
    requirementsList.append(card);
  });
}

async function loadRequirements(search = '') {
  const url = search ? `/api/v1/requirements/search?q=${encodeURIComponent(search)}&limit=10` : '/api/v1/requirements';
  try {
    const payload = await fetchJson(url);
    const items = payload.items || [];
    renderRequirements(items);
  } catch (error) {
    requirementsList.replaceChildren();
    const failure = document.createElement('div');
    failure.className = 'requirement-item';
    failure.textContent = `加载失败: ${error.message}`;
    requirementsList.append(failure);
  }
}

async function loadStatus() {
  try {
    const db = await fetchJson('/api/v1/health/db');
    dbStatus.textContent = db.database ? '正常' : '不可用';
    dbStatus.className = `metric ${db.database ? 'success' : 'danger'}`;
  } catch (error) {
    dbStatus.textContent = '异常';
    dbStatus.className = 'metric danger';
  }

  try {
    const llm = await fetchJson('/api/v1/health/llm');
    llmStatus.textContent = llm.configured ? `${llm.provider}/${llm.model}` : '未配置';
    llmStatus.className = `metric ${llm.configured ? 'success' : 'warning'}`;
  } catch (error) {
    llmStatus.textContent = '异常';
    llmStatus.className = 'metric danger';
  }
}

submitForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = {
    source_type: document.getElementById('source-type').value,
    requester_name: document.getElementById('requester-name').value || 'anonymous',
    original_text: document.getElementById('original-text').value,
    requester_id: document.getElementById('requester-name').value || 'anonymous',
    metadata: {}
  };

  if (!payload.original_text.trim()) {
    submitResult.classList.remove('hidden');
    submitResult.textContent = '请输入需求原文';
    return;
  }

  try {
    const result = await fetchJson('/api/v1/requirements/submit', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    submitResult.classList.remove('hidden');
    submitResult.textContent = `提交成功：${result.message || '已接受'}（${result.status}）`;
    submitForm.reset();
    await loadRequirements();
  } catch (error) {
    submitResult.classList.remove('hidden');
    submitResult.textContent = `提交失败：${error.message}`;
  }
});

document.querySelectorAll('.nav').forEach((button) => {
  button.addEventListener('click', () => switchPanel(button.dataset.target));
});

document.getElementById('load-list').addEventListener('click', () => loadRequirements());
searchBtn.addEventListener('click', () => loadRequirements(searchInput.value.trim()));
searchInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    loadRequirements(searchInput.value.trim());
  }
});

loadRequirements();
loadStatus();
