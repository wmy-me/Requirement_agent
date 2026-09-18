/* 工作台公共层：请求、格式化、DOM 构建、通用区块。
 *
 * **一页一个 HTML，各自引这一个文件。** 没有构建步骤、没有框架 —— 这是
 * 「先出静态版」的选择，React 方案另存在 docs/方案_前端工作台.md。
 *
 * 三条贯穿全文件的约定：
 *
 * 1. **不用 innerHTML 塞数据。** 所有来自后端或用户的内容一律走 `textContent`
 *    或 `el()` 的文本参数。需求正文、审核意见都是用户输入，拼 HTML 就是 XSS。
 * 2. **token 由服务端注入**（`meta[name="ra-ui-token"]`），见 api/app.py 的 `_render_page`。
 *    这里只负责带上它，**不做「让用户填」那一套** —— 内网共用工作台，
 *    token 是服务入口凭证不是个人凭证。
 * 3. **错误要说人话。** 401/403/404/409 各有含义，直接抛「请求失败」等于没写。
 */
'use strict';

/* ── 请求 ────────────────────────────────────────────────────────────── */

const TOKEN = document.querySelector('meta[name="ra-ui-token"]')?.content || '';

/** 带鉴权头的 fetch 包装。**所有请求都要经过它**（否则就是那一页 401）。 */
async function request(path, { method = 'GET', body, params } = {}) {
  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v === undefined || v === null || v === '') return;
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else qs.append(k, v);
    });
    const s = qs.toString();
    if (s) url += '?' + s;
  }
  const headers = {};
  if (TOKEN) headers['Authorization'] = 'Bearer ' + TOKEN;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const resp = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!resp.ok) throw new Error(await describeError(resp));
  if (resp.status === 204) return null;
  return resp.json();
}

/** 把 HTTP 错误翻成人话。**按状态码分支，不解析 detail 文本判断类型** ——
 *  detail 是中文，会改；状态码是契约冻过的（api-contract §1.2）。 */
async function describeError(resp) {
  let detail = '';
  try {
    const body = await resp.json();
    if (typeof body.detail === 'string') detail = body.detail;
    else if (body.detail && body.detail.message) detail = body.detail.message;
  } catch (e) { /* 响应不是 JSON，用状态码兜底 */ }

  if (resp.status === 401) {
    return '未通过鉴权（401）—— 页面里没拿到 API token。这是服务端配置问题，' +
           '检查 .env 的 API_AUTH_TOKEN。';
  }
  if (resp.status === 403) {
    return '权限不足（403）—— ' + (detail || '当前 token 的角色没有这一档权限。') +
           ' 这不是登录失效，换 token 也没用。';
  }
  if (resp.status === 404) return '找不到（404）—— ' + (detail || '对象不存在或已被删除。');
  if (resp.status === 409) return '状态冲突（409）—— ' + (detail || '别处已经改过它了，刷新后再试。');
  if (resp.status === 422) return '参数不合法（422）—— ' + (detail || '检查筛选条件。');
  return `请求失败（${resp.status}）` + (detail ? '：' + detail : '');
}

const api = {
  dashboard: {
    overview: () => request('/api/v1/stats/overview'),
    health: () => Promise.allSettled([
      request('/api/v1/health/db'),
      request('/api/v1/health/llm'),
      request('/api/v1/health/embedding'),
    ]),
  },
  requirements: {
    list: (params) => request('/api/v1/requirements', { params }),
    versions: (key) => request(`/api/v1/requirements/${encodeURIComponent(key)}/versions`),
    features: (key, params) => request(`/api/v1/requirements/${encodeURIComponent(key)}/features`, { params }),
    trace: (key) => request(`/api/v1/requirements/${encodeURIComponent(key)}/trace`),
    relations: (key) => request(`/api/v1/requirements/${encodeURIComponent(key)}/relations`),
    capabilities: (key) => request(`/api/v1/requirements/${encodeURIComponent(key)}/capabilities`),
    titles: (key) => request(`/api/v1/requirements/${encodeURIComponent(key)}/titles`),
  },
  reviews: {
    pending: (limit = 50) => request('/api/v1/reviews/pending', { params: { limit } }),
    detail: (sourceId) => request(`/api/v1/reviews/${encodeURIComponent(sourceId)}/detail`),
    history: (params) => request('/api/v1/reviews/history', { params }),
    preview: (sourceId, params) => request(
      `/api/v1/reviews/${encodeURIComponent(sourceId)}/merge-preview`, { params },
    ),
    submit: (payload) => request('/api/v1/reviews/submit', { method: 'POST', body: payload }),
  },
};

/* ── 格式化 ──────────────────────────────────────────────────────────── */

const fmt = {
  /** ISO → `MM-DD HH:mm`。空值给 `—`，不给空字符串（表格里空着看不出是没值还是没渲染）。 */
  time(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    const p = (x) => String(x).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  },
  /** 相对时间。列表里比绝对时间好读。 */
  rel(iso) {
    if (!iso) return '—';
    const ms = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(ms)) return '—';
    const m = Math.floor(ms / 60000);
    if (m < 1) return '刚刚';
    if (m < 60) return `${m} 分钟前`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} 小时前`;
    return `${Math.floor(h / 24)} 天前`;
  },
  num(v) { return v === null || v === undefined ? '—' : String(v); },
  /** 截断长文本。列表里不渲染全文。 */
  cut(s, n = 60) {
    const t = String(s ?? '');
    return t.length > n ? t.slice(0, n) + '…' : t;
  },
};

/* ── DOM 构建（**安全的那一种**）─────────────────────────────────────── */

/** 建元素。`text` 走 textContent，`children` 递归 —— 全程不碰 innerHTML。 */
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'text') node.textContent = String(v);
    else if (k === 'class') node.className = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, String(v));
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

/** 状态标签。`kind` 决定配色：ok / warn / bad / mute。 */
function badge(text, kind = 'mute') {
  return el('span', { class: 'badge badge-' + kind, text: String(text ?? '') });
}

/* ── 通用区块 ────────────────────────────────────────────────────────── */

/** 卡片：标题 + 内容。每个页面都由若干个卡片组成。 */
function card(title, children, extra) {
  return el('section', { class: 'card' }, [
    title ? el('header', { class: 'card-head' }, [
      el('h2', { text: title }),
      extra || null,
    ]) : null,
    el('div', { class: 'card-body' }, children),
  ]);
}

/**
 * 表格。`columns` 是 `{key, label, render?, width?}`。
 *
 * `render(row)` 返回字符串（走 textContent）或一个元素。**不要在这里返回 HTML 字符串**
 * —— 见文件头第 1 条。
 */
function table(columns, rows) {
  const thead = el('thead', {}, [
    el('tr', {}, columns.map((c) => el('th', { text: c.label, style: c.width ? `width:${c.width}` : null }))),
  ]);
  const tbody = el('tbody', {}, rows.map((row) => el('tr', {}, columns.map((c) => {
    const value = c.render ? c.render(row) : row[c.key];
    return el('td', {}, [typeof value === 'string' || typeof value === 'number'
      ? String(value) : value]);
  }))));
  return el('table', { class: 'grid' }, [thead, tbody]);
}

/** 空态 / 错误 / 加载。三态分开：**「没有数据」和「查询失败」不是一回事**。 */
const state = {
  loading: (msg = '加载中…') => el('div', { class: 'state state-loading', text: msg }),
  empty: (msg = '没有数据', hint) => el('div', { class: 'state state-empty' }, [
    el('div', { text: msg }),
    hint ? el('div', { class: 'state-hint', text: hint }) : null,
  ]),
  error: (err, onRetry) => el('div', { class: 'state state-error' }, [
    el('div', { class: 'state-title', text: '加载失败' }),
    el('div', { class: 'state-msg', text: err && err.message ? err.message : String(err) }),
    onRetry ? el('button', { class: 'btn', text: '重试', onclick: onRetry }) : null,
  ]),
};

/**
 * 取数并渲染，带三态。**每个页面的骨架都长这样**：
 *
 *   load(container, () => api.get('/api/v1/...'), (data) => card(...))
 *
 * 失败时保留「重试」按钮 —— 内部工具最常见的故障是网关抖了一下，
 * 让人自己刷页面不如给个按钮。
 */
async function load(container, fetcher, render) {
  container.replaceChildren(state.loading());
  const retry = () => load(container, fetcher, render);
  try {
    const data = await fetcher();
    const out = render(data);
    container.replaceChildren(...(Array.isArray(out) ? out : [out]));
  } catch (err) {
    container.replaceChildren(state.error(err, retry));
  }
}

/* ── 跨页面共用件 ────────────────────────────────────────────────────── */

/** 来源处理状态的中文名与配色。**8 个页面都会用到，放这里一处定义** ——
 *  各页自己写一份的话，「pending_review 在 A 页叫待审、B 页叫待处理」迟早发生。 */
const STATUS_LABEL = {
  received: '已接收', extracting: '抽取中', analyzing: '分析中',
  pending_review: '待审核', approved: '已通过', rejected: '已拒绝',
  returned: '已退回', committed: '已入库', failed: '失败',
};

function statusKind(s) {
  if (s === 'pending_review' || s === 'returned') return 'warn';
  if (s === 'failed' || s === 'rejected') return 'bad';
  if (s === 'committed' || s === 'approved') return 'ok';
  return 'mute';
}

function statusBadge(s) {
  return badge(STATUS_LABEL[s] || s || '—', statusKind(s));
}

/** 风险等级徽标。 */
function levelBadge(level) {
  const kind = level === 'high' ? 'bad' : level === 'medium' ? 'warn' : level === 'low' ? 'ok' : 'mute';
  const text = { high: '高', medium: '中', low: '低' }[level] || level || '—';
  return badge(text, kind);
}

/** 页面标题。 */
function head(title, desc) {
  return el('div', { class: 'page-head' }, [
    el('h1', { text: title }),
    el('p', { text: desc || '' }),
  ]);
}

function kvRow(label, node, hint) {
  return [el('dt', { text: label }),
          el('dd', {}, [node, hint ? el('div', { class: 'tiny', text: hint }) : null])];
}

/** 详情抽屉。点列表某行打开，点关闭或按 Esc 收起。 */
function openDrawer(title, bodyBuilder) {
  closeDrawer();
  const body = el('div', { class: 'drawer-body' });
  body.appendChild(state.loading());
  const drawer = el('div', { class: 'drawer' }, [
    el('div', { class: 'drawer-head' }, [
      el('h2', { text: title }),
      el('button', { class: 'btn', text: '关闭', onclick: closeDrawer }),
    ]),
    body,
  ]);
  document.body.appendChild(drawer);
  const onKey = (e) => { if (e.key === 'Escape') closeDrawer(); };
  document.addEventListener('keydown', onKey);
  drawer._onKey = onKey;
  bodyBuilder(body);
}

function closeDrawer() {
  const old = document.querySelector('.drawer');
  if (old) {
    if (old._onKey) document.removeEventListener('keydown', old._onKey);
    old.remove();
  }
}

/* ── 几个常用渲染件 ──────────────────────────────────────────────────── */

/** 键值对展示（详情面板用）。 */
function kv(pairs) {
  return el('dl', { class: 'kv' }, pairs.flatMap(([k, v]) => [
    el('dt', { text: k }),
    el('dd', {}, [typeof v === 'string' || typeof v === 'number' ? String(v) : v]),
  ]));
}

/** 计数条：若干「标签 + 数字」，用于状态分布这类聚合。 */
function countChips(counts) {
  const entries = Object.entries(counts || {});
  if (!entries.length) return state.empty('无数据');
  return el('div', { class: 'chips' }, entries.map(([k, v]) =>
    el('span', { class: 'chip' }, [
      el('span', { class: 'chip-k', text: k }),
      el('span', { class: 'chip-v', text: String(v) }),
    ])));
}

/** 指标块：一个大数字 + 说明。总览页用。 */
function metric(label, value, hint, kind) {
  return el('div', { class: 'metric' + (kind ? ' metric-' + kind : '') }, [
    el('div', { class: 'metric-v', text: String(value) }),
    el('div', { class: 'metric-l', text: label }),
    hint ? el('div', { class: 'metric-h', text: hint }) : null,
  ]);
}
