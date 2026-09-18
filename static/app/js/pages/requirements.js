/* F1/F2 需求列表与档案：只消费已注册的只读需求接口。 */
'use strict';

const LIST_PAGE_SIZE = 25;

function requirementStatus(value) {
  return badge(value === 'active' ? '有效' : value || '未提供', value === 'active' ? 'ok' : 'mute');
}

function unavailableCell(label = '后端暂未提供') {
  return el('span', { class: 'unavailable', title: label, text: '—' });
}

function valuesCell(values) {
  const items = Array.isArray(values) ? values.filter(Boolean) : [];
  return items.length ? el('span', { text: items.join('、') }) : unavailableCell('后端没有提供该值');
}

function requirementTable(items, open) {
  if (!items.length) return state.empty('没有符合筛选条件的需求', '可以调整筛选条件后重试。');
  const rows = items.map((item) => el('tr', { class: 'clickable', onclick: () => open(item) }, [
    el('td', {}, [el('div', { class: 'mono', text: String(item.requirement_key || '—') }), el('div', { class: 'tiny', text: item.requirement_name || '未命名需求' })]),
    el('td', {}, [el('div', { class: 'requirement-summary', text: item.final_requirement || '未提供需求描述' }), el('div', { class: 'tiny', text: (item.business_domains || []).join(' / ') || '业务域未提供' })]),
    el('td', { text: `V${item.current_version ?? '—'}` }),
    el('td', {}, [requirementStatus(item.status)]),
    el('td', {}, [valuesCell(item.source_types)]),
    el('td', {}, [valuesCell(item.requester_names)]),
    el('td', {}, [valuesCell(item.business_domains)]),
    el('td', { class: 'number-cell', text: item.feature_count == null ? '—' : String(item.feature_count) }),
    el('td', {}, [unavailableCell('需求列表响应没有来源总数')]),
    el('td', {}, [unavailableCell('需求列表响应没有需求级风险')]),
    el('td', {}, [unavailableCell('需求列表响应没有需求级冲突')]),
    el('td', {}, [el('span', { class: 'tiny', text: fmt.time(item.latest_source_submitted_at) }), el('div', { class: 'tiny', text: '最近来源时间' })]),
  ]));
  return el('div', { class: 'table-wrap requirements-table-wrap' }, [el('table', { class: 'grid requirements-table' }, [
    el('thead', {}, [el('tr', {}, ['编号 / 标题', '需求描述 / 领域', '版本', '状态', '来源渠道', '输入人', '业务域', '功能数', '来源数', '风险', '冲突', '最近来源'].map((label) => el('th', { text: label })))]),
    el('tbody', {}, rows),
  ])]);
}

function section(title, body) {
  return el('section', { class: 'archive-section' }, [el('h2', { class: 'section-title', text: title }), body]);
}

function archiveList(items, empty, render) {
  if (!items || !items.length) return state.empty(empty);
  return el('ul', { class: 'archive-list' }, items.map(render));
}

function openRequirement(item) {
  const key = String(item.requirement_key || '');
  if (key) window.location.assign(`/app/requirements/${encodeURIComponent(key)}`);
}

function resultSection(title, result, render) {
  if (result.status === 'rejected') return section(title, state.error(result.reason));
  return section(title, render(result.value || {}));
}

function featureRows(items) {
  if (!items.length) return state.empty('当前版本没有功能明细');
  return el('div', { class: 'feature-list' }, items.map((feature) => el('article', { class: 'feature-row' }, [
    el('div', { class: 'feature-key mono', text: String(feature.feature_key || '未提供') }),
    el('div', { class: 'feature-content', text: feature.content || '未提供功能描述' }),
    el('div', { class: 'feature-meta' }, [
      badge(feature.status === 'active' ? '生效' : feature.status || '未提供', feature.status === 'active' ? 'ok' : 'mute'),
      el('span', { text: feature.module_name || '未分组' }),
      feature.origin_version_no == null ? null : el('span', { text: `最初加入 V${feature.origin_version_no}` }),
    ]),
  ])));
}

function sourceGroups(versions) {
  const groups = (versions || []).filter((version) => Array.isArray(version.sources) && version.sources.length);
  if (!groups.length) return state.empty('没有可追溯的来源。回滚产生的版本没有来源是正常情况。');
  return el('div', { class: 'source-groups' }, groups.map((version) => el('section', { class: 'source-group' }, [
    el('h3', { text: `纳入 V${version.version_no || '未提供'} 的来源` }),
    ...version.sources.map((source) => el('article', { class: 'source-row' }, [
      el('div', { class: 'source-row-head' }, [
        el('span', { class: 'mono', text: String(source.source_id || '未提供') }),
        badge(source.source_type || '未提供'),
        el('span', { class: 'tiny', text: source.requester_name || '输入人未提供' }),
        el('span', { class: 'tiny', text: fmt.time(source.submitted_at) }),
      ]),
      el('p', { text: fmt.cut(source.original_text, 220) }),
      source.original_file_name ? el('div', { class: 'tiny', text: `原始文件：${source.original_file_name}` }) : null,
    ])),
  ])));
}

function relationRows(items) {
  if (!items.length) return state.empty('没有已记录的关联、冲突、依赖或重复关系。');
  const labels = { related: '关联', conflict: '冲突', depends: '依赖', duplicates_of: '疑似重复' };
  return el('div', { class: 'relation-list' }, items.map((relation) => el('article', { class: 'relation-row' }, [
    badge(labels[relation.relation_type] || relation.relation_type || '未提供', relation.relation_type === 'conflict' ? 'bad' : relation.relation_type === 'duplicates_of' ? 'warn' : 'mute'),
    el('div', {}, [
      el('a', { class: 'mono', href: `/app/requirements/${encodeURIComponent(String(relation.other_requirement_key || ''))}`, text: String(relation.other_requirement_key || '未提供') }),
      el('div', { class: 'tiny', text: relation.other_requirement_name || '需求名称未提供' }),
    ]),
    el('div', { class: 'relation-reason', text: relation.reason || '未提供判定依据' }),
    statusBadge(relation.status),
  ])));
}

function capabilityRows(data) {
  const capabilities = Array.isArray(data.capabilities) ? data.capabilities : [];
  const constraints = Array.isArray(data.constraints) ? data.constraints : [];
  if (!capabilities.length && !constraints.length) return state.empty('没有能力或条件记录。');
  return el('div', { class: 'capability-layout' }, [
    el('div', {}, [el('h3', { text: '能力匹配' }), archiveList(capabilities, '没有能力匹配记录', (item) => el('li', {}, [
      el('span', { text: item.display_name || item.action || '未提供' }), ' ', statusBadge(item.review_status),
    ]))]),
    el('div', {}, [el('h3', { text: '条件' }), archiveList(constraints, '没有条件记录', (item) => el('li', {}, [
      el('span', { text: item.raw || item.constraint_key || '未提供' }), ' ', badge(item.matched ? '已匹配' : '待确认', item.matched ? 'ok' : 'warn'),
    ]))]),
  ]);
}

function renderRequirementDetail(key) {
  const page = document.getElementById('page');
  const content = el('div', { class: 'detail-content' });
  const back = el('a', { class: 'btn', href: '/app/requirements', text: '返回需求列表' });
  page.replaceChildren(head('需求档案', '正式需求的当前事实、来源追溯和待确认分析项'), el('div', { class: 'page-toolbar' }, [back]), content);
  content.replaceChildren(state.loading('正在加载需求档案…'));

  Promise.allSettled([
    api.requirements.trace(key), api.requirements.versions(key), api.requirements.features(key),
    api.requirements.relations(key), api.requirements.capabilities(key), api.requirements.titles(key),
  ]).then((results) => {
    const [trace, versions, features, relations, capabilityData, titles] = results;
    if (trace.status === 'rejected') {
      content.replaceChildren(state.error(trace.reason, () => renderRequirementDetail(key)));
      return;
    }
    const requirement = trace.value.requirement || {};
    const versionItems = versions.status === 'fulfilled' ? (versions.value.items || []) : [];
    const current = versionItems.find((version) => version.status === 'current') || null;
    const confirmedTitles = titles.status === 'fulfilled' ? (titles.value.items || []).filter((title) => title.review_status === 'confirmed') : [];
    content.replaceChildren(
      el('section', { class: 'detail-hero' }, [
        el('div', {}, [
          el('div', { class: 'mono detail-key', text: String(requirement.requirement_key || key) }),
          el('h2', { text: requirement.requirement_name || '未命名需求' }),
          el('p', { text: requirement.final_requirement || '后端没有提供最终需求描述。' }),
        ]),
        el('div', { class: 'detail-status' }, [
          requirementStatus(requirement.status),
          el('span', { class: 'detail-version', text: `当前 V${requirement.current_version ?? '未提供'}` }),
        ]),
      ]),
      el('div', { class: 'detail-grid' }, [
        section('档案事实', kv([
          ['需求编号', el('span', { class: 'mono', text: String(requirement.requirement_key || key) })],
          ['当前状态', requirementStatus(requirement.status)],
          ['当前版本', `V${requirement.current_version ?? '未提供'}`],
          ['乐观锁版本', requirement.lock_version == null ? '未提供' : String(requirement.lock_version)],
          ['当前版本提交时间', current ? fmt.time(current.created_at) : '后端未提供'],
          ['当前版本提交人', current ? (current.created_by || '未提供') : '后端未提供'],
        ])),
        section('正式名称', confirmedTitles.length ? archiveList(confirmedTitles, '', (title) => el('li', { text: title.title || '未提供' })) : state.empty('没有人工确认的正式标题。机器候选标题不会在这里作为正式名称展示。')),
      ]),
      resultSection('当前功能', features, (data) => featureRows(Array.isArray(data.items) ? data.items : [])),
      resultSection('来源追溯', trace, (data) => sourceGroups(data.versions)),
      resultSection('需求关系', relations, (data) => relationRows(Array.isArray(data.items) ? data.items : [])),
      resultSection('AI 分析待确认项', capabilityData, capabilityRows),
      section('版本与 Diff', el('div', { class: 'detail-next-step' }, [
        el('p', { text: '版本时间线和 Diff 已可用。回滚将在后续批次接入。' }),
        el('a', { class: 'btn', href: `/app/versions?requirement_key=${encodeURIComponent(key)}`, text: '查看版本时间线' }),
      ])),
    );
  });
}

function filterInput(label, id, type = 'text', placeholder = '') {
  const input = el('input', { id, type, placeholder, 'aria-label': label });
  return el('label', { class: 'filter-field' }, [el('span', { text: label }), input]);
}

function filterSelect(label, id, options) {
  const select = el('select', { id, 'aria-label': label }, options.map(([value, text]) => el('option', { value, text })));
  return el('label', { class: 'filter-field' }, [el('span', { text: label }), select]);
}

function renderRequirements() {
  const page = document.getElementById('page');
  const query = filterInput('编号或关键词', 'req-q', 'search', '搜索需求编号、标题或正文');
  const channel = filterInput('来源渠道', 'req-channel', 'text', '如 web / manual');
  const requester = filterInput('输入人', 'req-requester', 'text', '姓名或 requester_id');
  const department = filterInput('部门', 'req-department', 'text', '后端字段');
  const domain = filterInput('业务域', 'req-domain', 'text', '后端字段');
  const version = filterInput('版本 ≥', 'req-version', 'number', '1');
  const from = filterInput('提交开始', 'req-from', 'date');
  const to = filterInput('提交结束', 'req-to', 'date');
  const status = filterSelect('状态', 'req-status', [['', '全部状态'], ['active', '有效'], ['archived', '已归档'], ['deleted', '已删除']]);
  const risk = filterSelect('风险等级', 'req-risk', [['', '风险等级（需要后端支持）']]);
  risk.querySelector('select').disabled = true;
  const results = el('div');
  let currentPage = 1;
  let currentItems = [];

  const filters = () => queryParams({
    q: query.querySelector('input').value.trim(), channel: channel.querySelector('input').value.trim(),
    requester: requester.querySelector('input').value.trim(), department: department.querySelector('input').value.trim(),
    business_domain: domain.querySelector('input').value.trim(), has_version_ge: version.querySelector('input').value.trim(),
    status: status.querySelector('select').value, submitted_from: from.querySelector('input').value,
    submitted_to: to.querySelector('input').value ? `${to.querySelector('input').value}T23:59:59` : '',
  });

  const render = (data) => {
    currentItems = Array.isArray(data.items) ? data.items : [];
    const model = paginate(currentItems, currentPage, LIST_PAGE_SIZE);
    currentPage = model.current;
    const pagination = paginationBar(model, (nextPage) => { currentPage = nextPage; renderList(); });
    return [card('需求列表', [el('div', { class: 'list-meta' }, [el('span', { text: `${model.total} 条结果` }), el('span', { class: 'tiny', text: '列表接口最多读取 500 条；来源数、风险和冲突由后端暂未提供' })]), requirementTable(model.items, openRequirement), pagination])];
  };
  const renderList = () => load(results, () => api.requirements.list({ ...filters(), limit: 500 }), render);
  const apply = el('button', { class: 'btn btn-primary', text: '查询', onclick: () => { currentPage = 1; renderList(); } });
  const reset = el('button', { class: 'btn', text: '重置', onclick: () => { page.querySelectorAll('.filter-field input').forEach((input) => { input.value = ''; }); status.querySelector('select').value = ''; currentPage = 1; renderList(); } });
  [query, channel, requester, department, domain, version, from, to].forEach((field) => field.querySelector('input').addEventListener('keydown', (event) => { if (event.key === 'Enter') { currentPage = 1; renderList(); } }));
  status.querySelector('select').addEventListener('change', () => { currentPage = 1; renderList(); });

  page.replaceChildren(head('需求列表', '按编号、来源、输入人和版本条件检索正式需求主线'), el('div', { class: 'filter-panel' }, [el('div', { class: 'filter-grid' }, [query, channel, requester, department, domain, version, status, risk, from, to]), el('div', { class: 'toolbar filter-actions' }, [apply, reset])]), results);
  renderList();
}

document.addEventListener('DOMContentLoaded', () => {
  const match = window.location.pathname.match(/^\/app\/requirements\/([^/]+)$/);
  if (match) renderRequirementDetail(decodeURIComponent(match[1]));
  else renderRequirements();
});
