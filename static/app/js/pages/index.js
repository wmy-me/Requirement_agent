/* 总览：一屏回答「有什么要处理、系统健不健康、数据长什么样」。
 *
 * 数据全部来自 `GET /api/v1/stats/overview`（聚合在后端做，前端不算 ——
 * 前端算的话 `limit` 一满就静默少算）。
 */
'use strict';

function renderOverview() {
  const page = document.getElementById('page');

  // ⚠️ **五块各自加载，不 await 串起来。** 串行的话一个慢接口会挡住整屏，
  //    而且任何一块失败都会让后面的卡片根本不渲染 —— 它们之间没有依赖关系。
  const todo = el('div', { class: 'metrics' });
  const health = el('div');
  const dist = el('div');
  const riskBox = el('div');
  const trendBox = el('div');

  page.replaceChildren(
    head('总览', '待办事项、系统健康与数据分布'),
    todo, health, dist, riskBox, trendBox,
  );

  load(todo, () => api.dashboard.overview(), (d) => [
    metric('待审核', d.pending_review, '等人工裁决的来源'),
    metric('高风险', d.high_risk, `基于已分析的 ${d.analysed_sources} 条`, d.high_risk ? 'bad' : null),
    metric('冲突', d.conflict, null, d.conflict ? 'warn' : null),
    metric('死信', d.dead_letter, '需要人工处理', d.dead_letter ? 'bad' : null),
    metric('需求总数', d.requirements_total, '已入库的主需求'),
    metric('来源总数', d.sources_total, '全部渠道输入'),
  ]);

  // ⚠️ 风险/冲突只是**已分析**来源里的数 —— 分母写在指标卡上，
  //    否则「高危 6 条」会被读成全库统计。

  load(dist, () => api.dashboard.overview(), (d) => [
    el('div', { class: 'cols cols-2' }, [
      card('审核漏斗（来源状态分布）', [distribution(d.source_status_counts, STATUS_LABEL)]),
      card('渠道占比', [distribution(d.channel_counts)]),
      card('业务域分布', [distribution(d.domain_counts)]),
      card('风险矩阵（质量 × 变更）', [
        d.risk_matrix.length
          ? table([
              { key: 'quality_risk', label: '质量风险', render: (r) => levelBadge(r.quality_risk) },
              { key: 'change_risk', label: '变更风险', render: (r) => levelBadge(r.change_risk) },
              { key: 'count', label: '条数' },
            ], d.risk_matrix)
          : state.empty('还没有评估过风险的来源'),
      ]),
    ]),
  ]);

  load(trendBox, () => api.dashboard.overview(), (d) => [
    card('提交趋势（按周）', [
      d.submission_trend.length ? trendChart(d.submission_trend) : state.empty('暂无数据'),
    ]),
  ]);

  load(health, () => api.dashboard.health(), (results) => {
    const [db, llm, emb] = results;
    const row = (label, r, render) => {
      if (r.status !== 'fulfilled') {
        return kvRow(label, badge('查询失败', 'bad'), r.reason && r.reason.message);
      }
      return render(r.value);
    };
    return [card('系统健康', [el('dl', { class: 'kv' }, [
      ...row('数据库', db, (v) => [
        el('dt', { text: '数据库' }),
        el('dd', {}, [v.database ? badge('连通', 'ok') : badge('不可用', 'bad')]),
      ]),
      ...row('模型', llm, (v) => [
        el('dt', { text: '模型' }),
        el('dd', {}, [
          v.configured ? badge('已配置', 'ok') : badge('未配置', 'warn'),
          el('span', { class: 'muted', text: ` ${v.provider} / ${v.model}` }),
        ]),
      ]),
      ...row('向量', emb, (v) => [
        el('dt', { text: '向量来源' }),
        el('dd', {}, v.trusted
          ? [badge('与当前模型一致', 'ok')]
          : [badge('不可信', 'bad'), el('div', { class: 'tiny', text: v.reason || '' }),
             v.action ? el('div', { class: 'mono', text: v.action }) : null]),
      ]),
    ])])];
  });
}

/** 极简柱状图。不做坐标轴 —— 这是「一眼看趋势」，不是图表组件。 */
function trendChart(points) {
  const max = Math.max(...points.map((p) => p.count), 1);
  return el('div', { class: 'trend' }, points.map((p) => el('div', { class: 'trend-col' }, [
    el('div', { class: 'trend-bar-wrap' }, [
      el('div', { class: 'trend-bar', style: `height:${(p.count / max) * 100}%`, title: `${p.period}: ${p.count}` }),
    ]),
    el('div', { class: 'trend-v', text: String(p.count) }),
    el('div', { class: 'trend-k', text: p.period.replace(/^\d\d/, '') }),
  ])));
}

document.addEventListener('DOMContentLoaded', renderOverview);
