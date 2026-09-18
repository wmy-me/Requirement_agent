/* F5 来源追踪：真实来源列表 + source trace，只读。 */
'use strict';

function sourceStatus(value) {
  const kind = value === 'failed' ? 'bad' : value === 'pending_review' ? 'warn' : value === 'committed' ? 'ok' : 'mute';
  return badge(value || '未提供', kind);
}

function sourceRow(source, open) {
  const key = String(source.source_id || '');
  return el('article', { class: 'source-center-row', onclick: () => open(key) }, [
    el('div', { class: 'source-center-head' }, [
      el('span', { class: 'mono', text: key || '来源编号未提供' }), badge(source.source_type || '渠道未提供'), sourceStatus(source.processing_status),
    ]),
    el('div', { class: 'source-center-summary', text: source.original_text || '没有来源摘要' }),
    el('div', { class: 'tiny', text: `${source.requester_name || '输入人未提供'} · ${fmt.time(source.submitted_at)} · ${source.linked_requirement_key || '尚未关联正式需求'}` }),
  ]);
}

function traceValue(value) {
  if (value === null || value === undefined) return '未提供';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

function renderSourceTrace(sourceId, content, reload) {
  content.replaceChildren(state.loading('正在加载来源链…'));
  api.sources.trace(sourceId).then((data) => {
    const source = data.source || data;
    const links = Array.isArray(data.links) ? data.links : Array.isArray(data.versions) ? data.versions : [];
    content.replaceChildren(
      el('div', { class: 'source-trace-head' }, [
        el('div', { class: 'mono', text: String(source.source_id || sourceId) }),
        sourceStatus(source.processing_status || source.status),
        source.linked_requirement_key ? el('a', { class: 'btn', href: `/app/requirements/${encodeURIComponent(String(source.linked_requirement_key))}`, text: `打开 ${source.linked_requirement_key}` }) : null,
      ]),
      section('来源事实', kv(Object.entries(source).filter(([key]) => !['metadata', 'original_text', 'source_id'].includes(key)).slice(0, 14).map(([key, value]) => [key, traceValue(value)]))),
      section('原始内容', el('pre', { class: 'pre source-original', text: source.original_text || '后端没有提供原始内容' })),
      section('来源链路', links.length ? el('ol', { class: 'trace-links' }, links.map((link) => el('li', { text: traceValue(link) }))) : state.empty('后端没有提供额外链路节点。')),
      source.metadata ? section('分析元数据', el('pre', { class: 'pre', text: JSON.stringify(source.metadata, null, 2) })) : null,
    );
  }).catch((error) => content.replaceChildren(state.error(error, () => renderSourceTrace(sourceId, content, reload))));
}

function section(title, body) {
  return el('section', { class: 'archive-section' }, [el('h2', { class: 'section-title', text: title }), body]);
}

function renderKnowledge() {
  const page = document.getElementById('page');
  const content = el('div');
  const sourceType = el('input', { type: 'text', placeholder: '来源渠道，例如 web / feishu', 'aria-label': '来源渠道' });
  const requester = el('input', { type: 'text', placeholder: '输入人', 'aria-label': '输入人' });
  const list = el('div');
  const open = (sourceId) => { history.replaceState(null, '', `/app/knowledge?source_id=${encodeURIComponent(sourceId)}`); renderSourceTrace(sourceId, content); };
  const loadList = () => load(list, () => api.sources.list(queryParams({ source_type: sourceType.value.trim(), requester: requester.value.trim(), limit: 100 })), (data) => {
    const items = Array.isArray(data.items) ? data.items : [];
    return card('最近来源', items.length ? el('div', { class: 'source-center-list' }, items.map((item) => sourceRow(item, open))) : state.empty('没有符合条件的来源。'));
  });
  const apply = el('button', { class: 'btn btn-primary', text: '筛选', onclick: loadList });
  const detailId = new URLSearchParams(window.location.search).get('source_id');
  page.replaceChildren(head('来源与知识', '追踪来源渠道、输入人、原始内容及其进入需求主线的路径'), el('div', { class: 'filter-panel' }, [el('div', { class: 'source-filters' }, [el('label', {}, [el('span', { text: '来源渠道' }), sourceType]), el('label', {}, [el('span', { text: '输入人' }), requester]), apply])]), detailId ? content : list);
  if (detailId) renderSourceTrace(String(detailId), content); else loadList();
}

document.addEventListener('DOMContentLoaded', renderKnowledge);
