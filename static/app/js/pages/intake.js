/* F8 输入中心：文本提交与真实 multipart 导入。 */
'use strict';

function intakeResult(target, result) {
  target.replaceChildren(el('div', { class: 'notice notice-ok' }, [
    el('strong', { text: '已接收。' }), el('span', { text: `状态：${result.status || '未提供'}；来源 ID：${String(result.source_id || '未提供')}` }),
    result.queued ? el('div', { class: 'tiny', text: '分析任务已进入队列，可在审核中心查看。' }) : null,
  ]));
}

function renderIntake() {
  const page = document.getElementById('page');
  const feedback = el('div');
  const requester = el('input', { type: 'text', placeholder: '输入人姓名或 ID' });
  const sourceType = el('select', {}, [['web', 'Web'], ['manual', '手工'], ['email', '邮件'], ['meeting', '会议']].map(([value, text]) => el('option', { value, text })));
  const text = el('textarea', { rows: 10, placeholder: '粘贴需求原文。提交后进入待审核流程。' });
  const submit = el('button', { class: 'btn btn-primary', text: '提交文本需求' });
  submit.addEventListener('click', async () => {
    if (!text.value.trim()) { feedback.replaceChildren(state.empty('请输入需求原文。')); return; }
    submit.disabled = true; feedback.replaceChildren(state.loading('正在提交…'));
    try { intakeResult(feedback, await api.intake.submit({ source_type: sourceType.value, requester_name: requester.value.trim() || null, original_text: text.value.trim(), metadata: {} })); text.value = ''; }
    catch (error) { feedback.replaceChildren(state.error(error, () => submit.click())); }
    finally { submit.disabled = false; }
  });

  const file = el('input', { type: 'file', accept: '.txt,.md,.docx,.pdf' });
  const fileText = el('textarea', { rows: 4, placeholder: '可选：补充文件之外的说明。' });
  const ingest = el('button', { class: 'btn btn-primary', text: '导入文件' });
  ingest.addEventListener('click', async () => {
    if (!file.files?.[0]) { feedback.replaceChildren(state.empty('请选择文件。')); return; }
    const form = new FormData(); form.append('file', file.files[0]); form.append('source_type', sourceType.value); form.append('requester_name', requester.value.trim()); form.append('original_text', fileText.value.trim());
    ingest.disabled = true; feedback.replaceChildren(state.loading('正在上传并解析…'));
    try { intakeResult(feedback, await api.intake.ingest(form)); file.value = ''; fileText.value = ''; }
    catch (error) { feedback.replaceChildren(state.error(error, () => ingest.click())); }
    finally { ingest.disabled = false; }
  });

  page.replaceChildren(head('输入中心', '从文本或文档创建待审核来源。提交后不会直接成为正式需求。'), el('div', { class: 'intake-grid' }, [
    card('文本输入', [el('div', { class: 'form-grid' }, [el('label', {}, [el('span', { text: '来源渠道' }), sourceType]), el('label', {}, [el('span', { text: '输入人' }), requester])]), text, submit]),
    card('文档导入', [el('p', { class: 'tiny', text: '后端当前提供文本、Markdown、DOCX、PDF 的 multipart 导入；截图识别接口暂未提供。' }), file, fileText, ingest]),
  ]), feedback);
}

document.addEventListener('DOMContentLoaded', renderIntake);
