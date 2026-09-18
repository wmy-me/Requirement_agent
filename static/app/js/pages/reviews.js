/* 审核中心：队列、审核详情、合并预演与历史记录。 */
'use strict';

function reviewMeta(detail) {
  return detail.metadata || {};
}

function reviewRisk(detail) {
  return reviewMeta(detail).risk || {};
}

function section(title, children) {
  return el('section', {}, [el('h3', { class: 'section-title', text: title }), children]);
}

function textBlock(value, empty = '未提供') {
  return el('div', { class: 'pre', text: value || empty });
}

function compactJson(value, empty = '未提供') {
  return textBlock(value ? JSON.stringify(value, null, 2) : empty);
}

function reviewRow(item, onOpen) {
  const risk = reviewRisk(item);
  const analysis = reviewMeta(item).analysis || {};
  return el('tr', { class: 'clickable', onclick: onOpen }, [
    el('td', {}, [el('div', { class: 'mono', text: String(item.source_id) }),
      el('div', { class: 'tiny', text: item.source_type || '未提供' })]),
    el('td', {}, [el('div', { text: fmt.cut(item.requester_name || '未提供', 18) }),
      el('div', { class: 'tiny', text: fmt.rel(item.submitted_at) })]),
    el('td', { text: fmt.cut(item.original_text || item.extracted_text || '未提供', 58) }),
    el('td', {}, [levelBadge(risk.quality_risk), el('span', { class: 'tiny', text: ` ${analysis.suggestion?.action || '待判定'}` })]),
    el('td', {}, [el('button', { class: 'btn', text: '审核', onclick: (event) => {
      event.stopPropagation(); onOpen();
    } })]),
  ]);
}

function queueTable(items, onOpen) {
  if (!items.length) return state.empty('当前没有待审核来源');
  const grid = el('table', { class: 'grid' }, [
    el('thead', {}, [el('tr', {}, ['来源', '提交人', '输入摘要', '风险 / 建议', ''].map((label) => el('th', { text: label })))]),
    el('tbody', {}, items.map((item) => reviewRow(item, () => onOpen(item)))),
  ]);
  return el('div', { class: 'table-wrap' }, [grid]);
}

async function showPreview(sourceId, targetKey, mode, host) {
  host.replaceChildren(state.loading('正在计算合并差异…'));
  try {
    const preview = await api.reviews.preview(sourceId, {
      target_requirement_key: targetKey,
      merge_mode: mode,
    });
    host.replaceChildren(renderPreview(preview));
  } catch (err) {
    host.replaceChildren(state.error(err, () => showPreview(sourceId, targetKey, mode, host)));
  }
}

function changeList(items, empty) {
  if (!items || !items.length) return el('span', { class: 'muted', text: empty });
  return el('ul', { class: 'change-list' }, items.map((item) => el('li', {
    text: fmt.cut(item.content || item.after?.content || item.before?.content || item.feature_key || '未提供', 140),
  })));
}

function renderPreview(preview) {
  const summary = preview.summary || {};
  return el('div', { class: 'preview' }, [
    el('div', { class: 'chips' }, [
      badge(`新增 ${summary.add || 0}`, 'ok'), badge(`修改 ${summary.modify || 0}`, 'warn'),
      badge(`删除 ${summary.delete || 0}`, summary.delete ? 'bad' : 'mute'),
      badge(`保留 ${summary.keep || 0}`, 'mute'), badge(`合并后 ${summary.active_after || 0}`, 'info'),
    ]),
    ...(preview.warnings || []).map((warning) => el('div', { class: 'notice notice-warn', text: warning })),
    ...(preview.groups || []).map((group) => el('div', { class: 'preview-group' }, [
      el('div', { class: 'preview-group-title', text: group.module_name || group.module_key || '未分组' }),
      el('div', { class: 'preview-grid' }, [
        el('div', {}, [el('strong', { text: '新增' }), changeList(group.added, '无')]),
        el('div', {}, [el('strong', { text: '修改' }), changeList(group.modified, '无')]),
        el('div', {}, [el('strong', { text: '删除' }), changeList(group.deleted, '无')]),
      ]),
    ])),
  ]);
}

function decisionForm(detail, requirements, onComplete) {
  const target = el('select');
  target.appendChild(el('option', { value: '', text: '新建独立需求' }));
  requirements.forEach((item) => target.appendChild(el('option', {
    value: item.requirement_key,
    text: `${item.requirement_key} · ${fmt.cut(item.requirement_name, 42)}`,
  })));
  const mode = el('select', {}, [
    el('option', { value: 'union', text: '并集合并：保留目标既有功能' }),
    el('option', { value: 'replace', text: '替换合并：可能删除目标既有功能' }),
  ]);
  const reviewer = el('input', { type: 'text', placeholder: '审核人姓名（可选）' });
  const comment = el('textarea', { rows: 3, placeholder: '审核意见（可选）' });
  const previewHost = el('div');
  const feedback = el('div');
  const previewButton = el('button', { class: 'btn', text: '查看合并预演', onclick: () => {
    if (!target.value) {
      previewHost.replaceChildren(state.empty('选择目标需求后才能预演合并'));
      return;
    }
    showPreview(detail.source_id, target.value, mode.value, previewHost);
  } });

  const submit = async (decision) => {
    const action = decision === 'approved' ? '通过' : decision === 'rejected' ? '拒绝' : '退回';
    if (!window.confirm(`确认${action}这条来源？此操作会改变审核状态。`)) return;
    feedback.replaceChildren(state.loading('正在提交裁决…'));
    try {
      const result = await api.reviews.submit({
        source_id: String(detail.source_id), decision,
        target_requirement_key: decision === 'approved' ? target.value || null : null,
        merge_mode: mode.value, reviewer_name: reviewer.value, comment: comment.value,
      });
      feedback.replaceChildren(el('div', { class: 'notice notice-ok', text:
        `${action}已提交${result.requirement_key ? `，需求 ${result.requirement_key}` : ''}` }));
      onComplete();
    } catch (err) {
      feedback.replaceChildren(state.error(err));
    }
  };

  return section('审核裁决', [
    el('div', { class: 'form-grid' }, [
      el('label', {}, [el('span', { text: '合并目标' }), target]),
      el('label', {}, [el('span', { text: '合并策略' }), mode]),
      el('label', {}, [el('span', { text: '审核人' }), reviewer]),
      el('label', { class: 'form-wide' }, [el('span', { text: '审核意见' }), comment]),
    ]),
    el('div', { class: 'toolbar' }, [previewButton,
      el('button', { class: 'btn btn-primary', text: '通过', onclick: () => submit('approved') }),
      el('button', { class: 'btn', text: '退回', onclick: () => submit('returned') }),
      el('button', { class: 'btn btn-danger', text: '拒绝', onclick: () => submit('rejected') }),
    ]), previewHost, feedback,
  ]);
}

async function openReview(item, refresh) {
  openDrawer(`审核来源 ${item.source_id}`, async (body) => {
    try {
      const [detail, requirements] = await Promise.all([
        api.reviews.detail(item.source_id), api.requirements.list({ limit: 200 }),
      ]);
      const meta = reviewMeta(detail);
      const extracted = meta.extracted || {};
      const analysis = meta.analysis || {};
      const risk = meta.risk || {};
      body.replaceChildren(
        section('来源信息', kv([
          ['来源 ID', el('span', { class: 'mono', text: String(detail.source_id) })],
          ['提交人', detail.requester_name || '未提供'], ['渠道', detail.source_type || '未提供'],
          ['提交时间', fmt.time(detail.submitted_at)], ['当前状态', statusBadge(detail.processing_status)],
        ])),
        section('原始输入', textBlock(detail.original_text)),
        section('结构化需求', [
          kv([['标题', extracted.requirement_title || '未提供'], ['摘要', extracted.summary || '未提供'],
            ['业务对象', extracted.business_object || '未提供']]),
          textBlock(extracted.raw_text || detail.extracted_text),
        ]),
        section('分析与风险', [
          kv([['建议', analysis.suggestion?.action || '未提供'], ['建议目标', analysis.suggestion?.target_requirement_key || '未提供'],
            ['质量风险', levelBadge(risk.quality_risk)], ['变更风险', levelBadge(risk.change_risk)],
            ['技术风险', levelBadge(risk.technical_impact_risk)], ['置信度', fmt.num(risk.confidence)]]),
          textBlock(analysis.reasoning, '未提供分析理由'),
        ]),
        section('相似与冲突候选', compactJson({ duplicate: analysis.duplicate, related: analysis.related, conflict: analysis.conflict })),
        decisionForm(detail, requirements.items || [], () => { refresh(); closeDrawer(); }),
      );
    } catch (err) {
      body.replaceChildren(state.error(err, () => openReview(item, refresh)));
    }
  });
}

function historyTable(items) {
  if (!items.length) return state.empty('没有符合筛选条件的历史记录');
  return table([
    { key: 'source_id', label: '来源 ID', render: (row) => el('span', { class: 'mono', text: String(row.source_id) }) },
    { key: 'processing_status', label: '结论', render: (row) => statusBadge(row.processing_status) },
    { key: 'requester_name', label: '提交人', render: (row) => row.requester_name || '未提供' },
    { key: 'excerpt', label: '输入摘要', render: (row) => fmt.cut(row.excerpt || '未提供', 64) },
    { key: 'updated_at', label: '更新时间', render: (row) => fmt.time(row.updated_at) },
  ], items);
}

function renderReviews() {
  const page = document.getElementById('page');
  const queue = el('div');
  const history = el('div');
  const refresh = () => {
    load(queue, () => api.reviews.pending(), (data) => card('待审队列', [queueTable(data.items || [], (item) => openReview(item, refresh))]));
    load(history, () => api.reviews.history({ limit: 50 }), (data) => card('审核历史', [historyTable(data.items || [])]));
  };
  page.replaceChildren(head('审核中心', '核对来源、查看分析依据，并提交可追溯的审核裁决'), queue, history);
  refresh();
}

document.addEventListener('DOMContentLoaded', renderReviews);
