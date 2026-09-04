const submitForm = document.getElementById('submit-form');
const submitResult = document.getElementById('submit-result');
const requirementsList = document.getElementById('requirements-list');
const dbStatus = document.getElementById('db-status');
const llmStatus = document.getElementById('llm-status');
const searchInput = document.getElementById('search-input');
const searchBtn = document.getElementById('search-btn');

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
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
  if (!items || items.length === 0) {
    requirementsList.innerHTML = '<div class="requirement-item">暂无需求记录</div>';
    return;
  }

  requirementsList.innerHTML = items.map((item) => `
    <div class="requirement-item">
      <h3>${item.requirement_name || item.title || '未命名需求'}</h3>
      <div class="requirement-meta">状态: ${item.status || 'pending'} · 关键字: ${item.requirement_key || 'N/A'}</div>
      <div>${item.final_requirement || item.summary || item.description || '暂无描述'}</div>
    </div>
  `).join('');
}

async function loadRequirements(search = '') {
  const url = search ? `/api/v1/requirements/search?q=${encodeURIComponent(search)}&limit=10` : '/api/v1/requirements';
  try {
    const payload = await fetchJson(url);
    const items = payload.items || [];
    renderRequirements(items);
  } catch (error) {
    requirementsList.innerHTML = `<div class="requirement-item">加载失败: ${error.message}</div>`;
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
