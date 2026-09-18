/* F3 版本时间线：只读 versions、trace、features?at_version，不接入 Diff 或回滚写操作。 */
'use strict';

function section(title, body) {
  return el('section', { class: 'archive-section' }, [el('h2', { class: 'section-title', text: title }), body]);
}

function versionFeatureRows(items) {
  if (!items.length) return state.empty('该版本没有功能。');
  return el('div', { class: 'feature-list' }, items.map((feature) => el('article', { class: 'feature-row' }, [
    el('div', { class: 'feature-key mono', text: String(feature.feature_key || '未提供') }),
    el('div', { class: 'feature-content', text: feature.content || '未提供功能描述' }),
    el('div', { class: 'feature-meta' }, [
      badge(feature.status === 'active' ? '生效' : feature.status || '未提供', feature.status === 'active' ? 'ok' : 'mute'),
      el('span', { text: feature.module_name || '未分组' }),
    ]),
  ])));
}

function versionKind(version) {
  const type = version.change_type || '未提供';
  const labels = { create: '新增', modify: '修改', delete: '删除', revert: '回滚' };
  return badge(labels[type] || type, type === 'delete' ? 'bad' : type === 'modify' || type === 'revert' ? 'warn' : 'ok');
}

function versionSourceCount(traceVersions, versionNo) {
  const item = (traceVersions || []).find((version) => String(version.version_no) === String(versionNo));
  return Array.isArray(item?.sources) ? item.sources.length : 0;
}

function versionTimeline(items, traceVersions, selected, onSelect) {
  if (!items.length) return state.empty('此需求没有版本记录。');
  const ordered = [...items].sort((a, b) => Number(b.version_no) - Number(a.version_no));
  return el('ol', { class: 'version-timeline' }, ordered.map((version) => {
    const isCurrent = version.status === 'current';
    const isSelected = String(version.version_no) === String(selected);
    return el('li', { class: 'version-node' + (isCurrent ? ' current' : '') + (isSelected ? ' selected' : '') }, [
      el('button', { class: 'version-node-button', onclick: () => onSelect(version.version_no) }, [
        el('div', { class: 'version-node-title' }, [
          el('span', { class: 'mono', text: `V${version.version_no}` }),
          isCurrent ? badge('当前', 'ok') : null,
          versionKind(version),
        ]),
        el('div', { class: 'version-node-summary', text: version.version_title || version.change_summary || '未提供变更说明' }),
        el('div', { class: 'tiny', text: `${version.created_by || '提交人未提供'}，${fmt.time(version.created_at)}，${versionSourceCount(traceVersions, version.version_no)} 条来源` }),
        el('div', { class: 'tiny', text: version.parent_version_no == null ? '起始版本' : `父版本 V${version.parent_version_no}` }),
      ]),
    ]);
  }));
}

function changeRows(changes) {
  if (!Array.isArray(changes) || !changes.length) return state.empty('此版本没有提供功能变更摘要。');
  const labels = { add: '新增', modify: '修改', delete: '删除', remove: '删除' };
  return el('ul', { class: 'change-list timeline-change-list' }, changes.map((change) => el('li', {}, [
    badge(labels[change.op] || change.op || '变更', change.op === 'delete' || change.op === 'remove' ? 'bad' : change.op === 'modify' ? 'warn' : 'ok'),
    ' ', el('span', { class: 'mono', text: String(change.feature_key || '功能未提供') }), ' ',
    el('span', { text: change.content || change.after || change.before || '未提供内容' }),
  ])));
}

function snapshotFields(snapshot) {
  if (typeof snapshot === 'string') {
    try { snapshot = JSON.parse(snapshot); } catch (error) { return el('pre', { class: 'pre', text: snapshot }); }
  }
  if (!snapshot || typeof snapshot !== 'object') return state.empty('后端没有提供该版本的需求快照。');
  const description = snapshot.final_requirement || snapshot.requirement_content || snapshot.content || snapshot.description;
  const pairs = Object.entries(snapshot).filter(([key]) => !['final_requirement', 'requirement_content', 'content', 'description'].includes(key));
  return el('div', { class: 'snapshot-content' }, [
    description ? el('p', { text: String(description) }) : null,
    pairs.length ? kv(pairs.map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value ?? '未提供')])) : null,
  ]);
}

function diffGroup(title, items, kind, render) {
  if (!Array.isArray(items) || !items.length) return el('section', { class: `diff-group diff-${kind}` }, [el('h3', { text: `${title}（0）` }), state.empty('没有记录')]);
  return el('section', { class: `diff-group diff-${kind}` }, [
    el('h3', { text: `${title}（${items.length}）` }),
    el('ul', { class: 'diff-list' }, items.map(render)),
  ]);
}

function renderDiff(data) {
  const modified = Array.isArray(data.modified) ? data.modified : [];
  const added = Array.isArray(data.added) ? data.added : [];
  const removed = Array.isArray(data.removed) ? data.removed : [];
  return el('div', { class: 'diff-content' }, [
    el('div', { class: 'diff-summary' }, [
      metric('新增', added.length, `V${data.from_version ?? '?'} → V${data.to_version ?? '?'}`, 'ok'),
      metric('修改', modified.length, '需要对照前后内容', 'warn'),
      metric('删除', removed.length, '从目标版本移除', 'bad'),
      metric('未变化', data.unchanged == null ? '未提供' : data.unchanged, '后端返回的数量', 'mute'),
    ]),
    el('div', { class: 'diff-groups' }, [
      diffGroup('新增功能', added, 'added', (item) => el('li', {}, [el('span', { class: 'mono', text: String(item.feature_key || '功能未提供') }), el('span', { text: item.content || '未提供内容' })])),
      diffGroup('修改功能', modified, 'modified', (item) => el('li', {}, [
        el('span', { class: 'mono', text: String(item.feature_key || '功能未提供') }),
        el('div', { class: 'diff-before', text: `之前：${item.before || '未提供'}` }),
        el('div', { class: 'diff-after', text: `之后：${item.after || '未提供'}` }),
      ])),
      diffGroup('删除功能', removed, 'removed', (item) => el('li', {}, [el('span', { class: 'mono', text: String(item.feature_key || '功能未提供') }), el('span', { text: item.content || '未提供内容' })])),
    ]),
  ]);
}

function renderVersionWorkbench(initialKey) {
  const page = document.getElementById('page');
  const keyInput = el('input', { type: 'text', placeholder: '输入 REQ 编号，例如 REQ-000015', value: initialKey || '', 'aria-label': '需求编号' });
  const query = () => keyInput.value.trim();
  const content = el('div', { class: 'version-workbench' });
  const open = () => {
    const key = query();
    if (!key) { content.replaceChildren(state.empty('请输入需求编号。')); return; }
    history.replaceState(null, '', `/app/versions?requirement_key=${encodeURIComponent(key)}`);
    loadTimeline(key);
  };
  const loadTimeline = async (key) => {
    content.replaceChildren(state.loading('正在加载版本时间线…'));
    const [versions, trace] = await Promise.allSettled([api.requirements.versions(key), api.requirements.trace(key)]);
    if (versions.status === 'rejected') { content.replaceChildren(state.error(versions.reason, () => loadTimeline(key))); return; }
    const items = versions.value.items || [];
    const selected = (items.find((item) => item.status === 'current') || items[items.length - 1] || {}).version_no;
    renderTimelineContent(key, items, trace.status === 'fulfilled' ? trace.value.versions || [] : [], selected, content, loadTimeline);
  };
  page.replaceChildren(
    head('版本与变更', '按需求主线查看版本提交、父版本、来源和可复原的功能快照'),
    el('div', { class: 'version-query' }, [keyInput, el('button', { class: 'btn btn-primary', text: '查看时间线', onclick: open })]), content,
  );
  keyInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
  if (initialKey) open();
}

function renderTimelineContent(key, items, traceVersions, selected, container, reload) {
  const selectedVersion = items.find((item) => String(item.version_no) === String(selected)) || items[0];
  const featuresPanel = el('div');
  const choose = (versionNo) => {
    const next = items.find((item) => String(item.version_no) === String(versionNo));
    if (!next) return;
    renderTimelineContent(key, items, traceVersions, next.version_no, container, reload);
  };
  const diffPanel = el('div');
  const versionOptions = items.slice().sort((a, b) => Number(a.version_no) - Number(b.version_no)).map((item) => [String(item.version_no), `V${item.version_no}`]);
  const fromSelect = el('select', { 'aria-label': 'Diff 起始版本' }, versionOptions.map(([value, label]) => el('option', { value, text: label })));
  const toSelect = el('select', { 'aria-label': 'Diff 目标版本' }, versionOptions.map(([value, label]) => el('option', { value, text: label })));
  const selectedIndex = items.findIndex((item) => String(item.version_no) === String(selectedVersion.version_no));
  const defaultFrom = items[Math.max(0, selectedIndex - 1)] || selectedVersion;
  fromSelect.value = String(defaultFrom.version_no);
  toSelect.value = String(selectedVersion.version_no);
  const loadDiff = () => {
    const from = fromSelect.value;
    const to = toSelect.value;
    if (from === to) { diffPanel.replaceChildren(state.empty('请选择两个不同版本。')); return; }
    load(diffPanel, () => api.requirements.diff(key, { from_version: from, to_version: to }), renderDiff);
  };
  const diffSection = section('版本 Diff', [
    el('div', { class: 'diff-controls' }, [
      el('label', {}, [el('span', { text: '从' }), fromSelect]),
      el('span', { class: 'diff-arrow', text: '到' }),
      el('label', {}, [el('span', { text: '到' }), toSelect]),
      el('button', { class: 'btn btn-primary', text: '查看 Diff', onclick: loadDiff }),
    ]),
    diffPanel,
  ]);
  container.replaceChildren(el('div', { class: 'version-layout' }, [
    section('版本时间线', versionTimeline(items, traceVersions, selectedVersion.version_no, choose)),
    el('div', { class: 'version-main' }, [
      section(`V${selectedVersion.version_no} 版本快照`, snapshotFields(selectedVersion.requirement_snapshot)),
      section('本次功能变更', changeRows(selectedVersion.feature_changes)),
      section(`V${selectedVersion.version_no} 的功能清单`, featuresPanel),
      diffSection,
    ]),
    section('版本元数据', kv([
      ['版本', `V${selectedVersion.version_no}`], ['状态', selectedVersion.status === 'current' ? badge('当前版本', 'ok') : badge('历史版本')],
      ['提交人', selectedVersion.created_by || '未提供'], ['审核人', selectedVersion.reviewed_by || '未提供'],
      ['提交时间', fmt.time(selectedVersion.created_at)], ['父版本', selectedVersion.parent_version_no == null ? '起始版本' : `V${selectedVersion.parent_version_no}`],
      ['来源数量', String(versionSourceCount(traceVersions, selectedVersion.version_no))],
    ])),
  ]));
  load(featuresPanel, () => api.requirements.features(key, { at_version: selectedVersion.version_no }), (data) => {
    const rows = Array.isArray(data.items) ? data.items : [];
    return versionFeatureRows(rows);
  });
}

document.addEventListener('DOMContentLoaded', () => renderVersionWorkbench(new URLSearchParams(window.location.search).get('requirement_key') || ''));
