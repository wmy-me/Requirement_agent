/* F1 总览：只消费后端真实聚合与只读列表接口。 */
'use strict';

function renderOverview() {
  const page = document.getElementById('page');
  const overview = el('div');
  const health = el('div');
  const pending = el('div');
  const changes = el('div');
  const incidents = el('div');

  page.replaceChildren(
    el('div', { class: 'page-toolbar' }, [
      head('总览', '需求治理工作台的待办、风险、输入与运行健康'),
      el('span', { class: 'data-note', text: '数据来自实时接口' }),
    ]),
    overview,
    el('div', { class: 'overview-grid' }, [health, pending, changes, incidents]),
  );

  load(overview, () => api.dashboard.overview({ trend_periods: 12 }), renderOverviewStats);
  load(health, () => api.dashboard.health(), renderHealth);
  load(pending, () => api.dashboard.pending(5), (data) => renderPending(data.items || []));
  load(changes, () => Promise.resolve(null), () => unsupportedCard('最近需求变更', '后端暂未提供按版本变更时间排序的总览接口。'));
  load(incidents, async () => {
    const [failedRuns, outbox] = await Promise.all([api.dashboard.failedRuns(5), api.dashboard.outbox(5)]);
    return { failedRuns, outbox };
  }, renderIncidents);
}

function valueOrUnavailable(value) {
  return value === null || value === undefined ? '后端暂未提供' : String(value);
}

function renderOverviewStats(data) {
  const cards = [
    metric('需求总数', valueOrUnavailable(data.requirements_total), '已入库需求主线'),
    metric('待审核', valueOrUnavailable(data.pending_review), '等待人工裁决', data.pending_review ? 'warn' : null),
    metric('高风险', valueOrUnavailable(data.high_risk), data.analysed_sources == null ? '分析分母未提供' : `基于已分析 ${data.analysed_sources} 条`, data.high_risk ? 'bad' : null),
    metric('冲突', valueOrUnavailable(data.conflict), '已分析来源中的冲突', data.conflict ? 'warn' : null),
    metric('来源总数', valueOrUnavailable(data.sources_total), '全部渠道输入'),
    metric('死信', valueOrUnavailable(data.dead_letter), '需要运维处理', data.dead_letter ? 'bad' : null),
  ];
  const distributionGrid = el('div', { class: 'overview-distributions' }, [
    distributionCard('审核漏斗', data.source_status_counts, STATUS_LABEL),
    distributionCard('输入渠道', data.channel_counts),
    distributionCard('业务领域', data.domain_counts),
    riskMatrix(data.risk_matrix, data.analysed_sources),
  ]);
  const trend = data.submission_trend && data.submission_trend.length
    ? trendChart(data.submission_trend)
    : state.empty('暂无提交趋势', '有新的来源输入后，这里会显示按周趋势。');
  return [el('div', { class: 'metrics metrics-overview' }, cards), distributionGrid, card('提交趋势', [trend])];
}

function distributionCard(title, counts, labels = {}) {
  const entries = Object.entries(counts || {});
  return card(title, [entries.length ? distribution(entries, labels) : state.empty('暂无数据')]);
}

function distribution(entries, labels = {}) {
  const max = Math.max(...entries.map(([, value]) => Number(value) || 0), 1);
  return el('div', { class: 'distribution' }, entries.map(([key, value]) => el('div', { class: 'dist-row' }, [
    el('span', { class: 'dist-k', text: labels[key] || key }),
    el('span', { class: 'dist-bar' }, [el('span', { class: 'dist-fill', style: `width:${((Number(value) || 0) / max) * 100}%` })]),
    el('span', { class: 'dist-v', text: String(value) }),
  ])));
}

function riskMatrix(rows, denominator) {
  if (!rows || !rows.length) return card('风险矩阵', [state.empty('暂无风险矩阵', '后端尚未提供已分析来源的风险分布。')]);
  return card('风险矩阵', [el('div', { class: 'card-note', text: denominator == null ? '分母：后端暂未提供' : `统计范围：已分析 ${denominator} 条来源` }), table([
    { key: 'quality_risk', label: '质量风险', render: (row) => levelBadge(row.quality_risk) },
    { key: 'change_risk', label: '变更风险', render: (row) => levelBadge(row.change_risk) },
    { key: 'count', label: '条数' },
  ], rows)]);
}

function renderHealth(results) {
  const [db, llm, embedding] = results;
  const row = (label, result, render) => result.status === 'fulfilled' ? kvRow(label, render(result.value)) : kvRow(label, badge('查询失败', 'bad'), result.reason?.message || '请重试');
  return card('系统健康', [el('dl', { class: 'kv health-kv' }, [
    ...row('数据库', db, (value) => value.database ? badge('连通', 'ok') : badge('不可用', 'bad')),
    ...row('模型', llm, (value) => [badge(value.configured ? '已配置' : '未配置', value.configured ? 'ok' : 'warn'), el('span', { class: 'muted', text: ` ${value.provider || '—'} / ${value.model || '—'}` })]),
    ...row('向量', embedding, (value) => value.trusted ? badge('可信', 'ok') : [badge('不可信', 'bad'), el('span', { class: 'tiny', text: ` ${value.reason || '未提供原因'}` })]),
  ])]);
}

function renderPending(items) {
  if (!items.length) return card('最近待审核', [state.empty('暂无待审核来源', '新的来源完成分析后会出现在这里。')]);
  return card('最近待审核', [el('div', { class: 'overview-list' }, items.map((item) => el('a', {
    class: 'overview-list-item', href: `/app/reviews/detail?source_id=${encodeURIComponent(String(item.source_id))}`,
  }, [el('span', { class: 'mono', text: String(item.source_id) }), el('strong', { text: fmt.cut(item.original_text || item.extracted_text || '未提供输入', 64) }), el('span', { class: 'tiny', text: `${item.source_type || '未提供渠道'} · ${item.requester_name || '未提供输入人'} · ${fmt.rel(item.submitted_at)}` })]))) ]);
}

function renderIncidents(data) {
  const failed = data.failedRuns?.items || [];
  const outbox = data.outbox || null;
  const rows = failed.map((run) => el('li', {}, [badge('运行失败', 'bad'), el('span', { class: 'mono', text: String(run.run_id || run.id || '—') }), el('span', { text: run.error || run.current_node || '未提供错误信息' })]));
  (outbox?.dead_letters || []).forEach((item) => rows.push(el('li', {}, [badge('死信', 'bad'), el('span', { class: 'mono', text: String(item.id) }), el('span', { text: item.last_error || item.event_type || '未提供错误信息' })])));
  return card('最近运行异常', [rows.length ? el('ul', { class: 'incident-list' }, rows) : state.empty('暂无运行异常')]);
}

function unsupportedCard(title, message) {
  return card(title, [state.empty('后端暂未提供', message)]);
}

function trendChart(points) {
  const max = Math.max(...points.map((p) => Number(p.count) || 0), 1);
  return el('div', { class: 'trend' }, points.map((point) => el('div', { class: 'trend-col' }, [
    el('div', { class: 'trend-bar-wrap' }, [el('div', { class: 'trend-bar', style: `height:${((Number(point.count) || 0) / max) * 100}%`, title: `${point.period}: ${point.count}` })]),
    el('div', { class: 'trend-v', text: String(point.count) }), el('div', { class: 'trend-k', text: String(point.period || '').replace(/^\d\d/, '') }),
  ])));
}

document.addEventListener('DOMContentLoaded', renderOverview);
