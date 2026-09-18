/* F7 AI 分析结果：运行状态、风险、冲突和关联仅作为 AI 结果展示。 */
'use strict';

function analysisSection(title, body) {
  return el('section', { class: 'archive-section' }, [el('h2', { class: 'section-title', text: title }), body]);
}

function runStatus(value) {
  const kind = value === 'failed' || value === 'cancelled' ? 'bad' : value === 'completed' ? 'ok' : value === 'running' ? 'warn' : 'mute';
  return badge(value || '未提供', kind);
}

function runTable(items, open) {
  if (!items.length) return state.empty('没有分析运行记录。');
  return el('div', { class: 'run-list' }, items.map((run) => el('button', { class: 'run-row', onclick: () => open(run) }, [
    el('div', { class: 'run-row-main' }, [el('span', { class: 'mono', text: String(run.run_id || '未提供') }), runStatus(run.status), el('span', { class: 'tiny', text: run.run_type || '未提供' })]),
    el('div', { class: 'tiny', text: `${run.source_id == null ? '未关联来源' : `来源 ${String(run.source_id)}`} · ${fmt.time(run.created_at || run.started_at)}` }),
    el('div', { class: 'run-stage', text: run.stage || run.current_node || '未提供运行阶段' }),
  ])));
}

function riskPanel(detail) {
  const metadata = detail.metadata || {};
  const risk = metadata.risk || {};
  const analysis = metadata.analysis || {};
  const degradation = metadata.degradation;
  return el('div', { class: 'analysis-result' }, [
    degradation ? el('div', { class: 'notice notice-warn', text: `分析降级：${degradation.reason || '模型链路降级'}${degradation.fields?.length ? `；影响 ${degradation.fields.join('、')}` : ''}` }) : null,
    kv([['质量风险', levelBadge(risk.quality_risk)], ['变更风险', levelBadge(risk.change_risk)], ['技术影响风险', levelBadge(risk.technical_impact_risk)], ['置信度', fmt.num(risk.confidence)]]),
    analysisSection('冲突候选', analysis.conflict ? compactValue(analysis.conflict) : state.empty('没有冲突结论。')),
    analysisSection('关联与重复候选', el('div', { class: 'candidate-columns' }, [
      el('div', {}, [el('h3', { text: '重复候选' }), analysisCandidates(analysis.duplicate || analysis.candidates, 'duplicate')]),
      el('div', {}, [el('h3', { text: '关联候选' }), analysisCandidates(analysis.related || analysis.candidates, 'related')]),
    ])),
    analysisSection('AI 判断依据', el('p', { class: 'analysis-reasoning', text: analysis.reasoning || '后端没有提供分析理由。' })),
  ]);
}

function compactValue(value) { return el('pre', { class: 'pre', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }); }
function analysisCandidates(value, kind) {
  const items = Array.isArray(value) ? value.filter((item) => !kind || !item.relation_type || item.relation_type === kind) : [];
  return items.length ? el('ul', { class: 'candidate-list' }, items.map((item) => el('li', {}, [
    el('span', { class: 'mono', text: String(item.requirement_key || item.target_requirement_key || item.key || '未提供') }),
    el('span', { text: item.reason || item.explanation || (item.similarity == null ? '未提供依据' : `相似度 ${item.similarity}`) }),
  ]))) : state.empty('没有候选。');
}

function renderRunDetail(run, host) {
  const runId = String(run.run_id);
  host.replaceChildren(state.loading('正在加载运行详情…'));
  Promise.allSettled([
    api.dashboard.run(runId), api.dashboard.runEvents(runId, { limit: 100 }), api.dashboard.runInvocations(runId),
    run.source_id == null ? Promise.resolve(null) : api.reviews.detail(String(run.source_id)),
  ]).then(([detail, events, invocations, source]) => {
    if (detail.status === 'rejected') { host.replaceChildren(state.error(detail.reason, () => renderRunDetail(run, host))); return; }
    const current = detail.value;
    host.replaceChildren(
      analysisSection('运行概况', kv([['Run ID', el('span', { class: 'mono', text: runId })], ['状态', runStatus(current.status)], ['类型', current.run_type || '未提供'], ['来源 ID', current.source_id == null ? '未关联' : el('span', { class: 'mono', text: String(current.source_id) })], ['阶段', current.stage || current.current_node || '未提供'], ['创建时间', fmt.time(current.created_at)]])),
      source.status === 'fulfilled' && source.value ? analysisSection('来源的 AI 风险与关系', riskPanel(source.value)) : analysisSection('来源的 AI 风险与关系', state.empty('该运行未关联可读取的来源分析结果。')),
      analysisSection('运行事件', events.status === 'fulfilled' && events.value.items?.length ? el('ol', { class: 'event-list' }, events.value.items.map((event) => el('li', {}, [el('span', { class: 'mono', text: String(event.sequence ?? event.id ?? '') }), el('span', { text: event.event_type || event.type || '事件' }), el('span', { class: 'tiny', text: event.message || event.label || JSON.stringify(event.data || '') })]))) : state.empty('没有运行事件。')),
      analysisSection('工具调用', invocations.status === 'fulfilled' && invocations.value.tools?.length ? compactValue(invocations.value.tools) : state.empty('没有工具调用记录。模型调用明细由后端暂未提供。')),
    );
  });
}

function renderAnalysis() {
  const page = document.getElementById('page');
  const list = el('div');
  const detail = el('div');
  const loadRuns = () => load(list, () => api.dashboard.runs({ run_type: 'analysis', limit: 100 }), (data) => card('最近分析运行', runTable(data.items || [], (run) => { history.replaceState(null, '', `/app/analysis/runs/${encodeURIComponent(String(run.run_id))}`); renderRunDetail(run, detail); })));
  page.replaceChildren(head('智能分析', '查看 AI 运行状态、风险判断、冲突候选和关联候选'), el('div', { class: 'analysis-layout' }, [list, detail]));
  loadRuns();
  const runId = window.location.pathname.match(/^\/app\/analysis\/runs\/([^/]+)$/)?.[1];
  if (runId) api.dashboard.run(decodeURIComponent(runId)).then((run) => renderRunDetail(run, detail)).catch((error) => detail.replaceChildren(state.error(error)));
}

document.addEventListener('DOMContentLoaded', renderAnalysis);
