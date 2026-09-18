/* 需求工作台：可筛选的需求库与按需加载的需求档案。 */
'use strict';

function requirementStatus(value) {
  return badge(value === 'active' ? '有效' : value || '未提供', value === 'active' ? 'ok' : 'mute');
}

function requirementTable(items, open) {
  if (!items.length) return state.empty('没有符合筛选条件的需求');
  const rows = items.map((item) => el('tr', { class: 'clickable', onclick: () => open(item) }, [
    el('td', {}, [el('div', { class: 'mono', text: item.requirement_key }),
      el('div', { class: 'tiny', text: (item.business_domains || []).join(' / ') || '未标注' })]),
    el('td', {}, [el('div', { text: item.requirement_name || '未提供' }),
      el('div', { class: 'tiny', text: fmt.cut(item.final_requirement, 72) })]),
    el('td', {}, [requirementStatus(item.status)]),
    el('td', { text: `v${item.current_version || 0}` }),
    el('td', { text: String(item.feature_count || 0) }),
    el('td', { text: fmt.time(item.latest_source_submitted_at) }),
  ]));
  return el('div', { class: 'table-wrap' }, [el('table', { class: 'grid' }, [
    el('thead', {}, [el('tr', {}, ['编号 / 领域', '需求摘要', '状态', '版本', '功能', '最新来源'].map((x) => el('th', { text: x })))]),
    el('tbody', {}, rows),
  ])]);
}

function archiveSection(title, result, render) {
  if (result.status === 'rejected') return section(title, state.error(result.reason));
  return section(title, render(result.value));
}

function section(title, body) {
  return el('section', {}, [el('h3', { class: 'section-title', text: title }), body]);
}

function archiveList(items, empty, render) {
  if (!items || !items.length) return state.empty(empty);
  return el('ul', { class: 'archive-list' }, items.map(render));
}

function openRequirement(item) {
  const key = item.requirement_key;
  history.replaceState(null, '', `/app/requirements/${encodeURIComponent(key)}`);
  openDrawer(`需求档案 · ${key}`, async (body) => {
    const results = await Promise.allSettled([
      api.requirements.trace(key), api.requirements.versions(key), api.requirements.features(key),
      api.requirements.relations(key), api.requirements.capabilities(key), api.requirements.titles(key),
    ]);
    const [trace, versions, features, relations, capabilityData, titles] = results;
    body.replaceChildren(
      section('概览', kv([
        ['编号', el('span', { class: 'mono', text: key })], ['名称', item.requirement_name || '未提供'],
        ['状态', requirementStatus(item.status)], ['当前版本', `v${item.current_version || 0}`],
        ['最终需求描述', item.final_requirement || '未提供'],
      ])),
      archiveSection('功能明细', features, (data) => archiveList(data.items, '当前没有功能明细', (feature) =>
        el('li', {}, [el('span', { class: 'mono', text: `${feature.feature_key} ` }),
          el('span', { text: feature.content || '未提供' }), el('span', { class: 'tiny', text: ` · ${feature.module_name || '未分组'}` })]))),
      archiveSection('版本时间线', versions, (data) => archiveList(data.items, '没有版本记录', (version) =>
        el('li', { text: `v${version.version_no} · ${version.version_title || version.change_type || '未命名'} · ${fmt.time(version.created_at)}` }))),
      archiveSection('来源链', trace, (data) => {
        const sourceRows = (data.versions || []).flatMap((version) => version.sources || []);
        return archiveList(sourceRows, '未提供来源链', (source) => el('li', { text:
          `${source.source_id || '—'} · ${source.requester_name || '未提供'} · ${fmt.cut(source.original_text, 90)}` }));
      }),
      archiveSection('关系', relations, (data) => archiveList(data.items, '没有已记录的需求关系', (relation) =>
        el('li', { text: `${relation.direction || ''} ${relation.relation_type || '关联'} · ${relation.other_requirement_key || '未提供'} · ${relation.status || 'proposed'}` }))),
      archiveSection('能力与条件', capabilityData, (data) => archiveList(data.capabilities, '没有能力匹配记录', (capability) =>
        el('li', { text: `${capability.display_name || capability.action || '未提供'} · ${capability.review_status || 'proposed'}` }))),
      archiveSection('候选标题', titles, (data) => archiveList(data.items, '没有候选标题', (title) =>
        el('li', { text: `${title.title || '未提供'} · ${title.review_status || 'proposed'}` }))),
      section('影响范围', state.empty('后端未提供影响分析')),
    );
  });
}

function renderRequirements() {
  const page = document.getElementById('page');
  const query = el('input', { type: 'search', placeholder: '搜索编号、名称或正文' });
  const status = el('select', {}, [el('option', { value: '', text: '全部状态' }), el('option', { value: 'active', text: '有效' })]);
  const results = el('div');
  const refresh = () => load(results, () => api.requirements.list({ q: query.value, status: status.value, limit: 200 }),
    (data) => card('需求库', [requirementTable(data.items || [], openRequirement)]));
  page.replaceChildren(head('需求工作台', '检索需求主线，查看版本、功能、来源与关系'),
    el('div', { class: 'toolbar' }, [query, status, el('button', { class: 'btn btn-primary', text: '查询', onclick: refresh })]), results);
  query.addEventListener('keydown', (event) => { if (event.key === 'Enter') refresh(); });
  refresh();
}

document.addEventListener('DOMContentLoaded', renderRequirements);
