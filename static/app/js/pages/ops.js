/* F9 运维诊断：只读健康、队列、Worker、模型和渠道配置。 */
'use strict';

function opsSection(title, result, render, retry) {
  return section(title, result.status === 'rejected' ? state.error(result.reason, retry) : render(result.value || {}));
}
function opsJson(data, empty = '后端没有提供数据。') {
  return data && Object.keys(data).length ? el('pre', { class: 'pre', text: JSON.stringify(data, null, 2) }) : state.empty(empty);
}

function renderOps() {
  const page = document.getElementById('page');
  const content = el('div');
  const loadAll = async () => {
    content.replaceChildren(state.loading('正在检查系统状态…'));
    const results = await Promise.allSettled([
      api.dashboard.health, api.dashboard.outbox(20), api.dashboard.worker(), api.dashboard.models({ limit: 20 }), api.dashboard.channels(),
    ].map((requester) => typeof requester === 'function' ? requester() : requester));
    const [health, outbox, worker, models, channels] = results;
    content.replaceChildren(
      el('div', { class: 'ops-grid' }, [
        opsSection('数据库健康', health.status === 'fulfilled' ? health.value[0] : health, (data) => opsJson(data), loadAll),
        opsSection('模型健康', health.status === 'fulfilled' ? health.value[1] : health, (data) => opsJson(data), loadAll),
        opsSection('Embedding 健康', health.status === 'fulfilled' ? health.value[2] : health, (data) => opsJson(data), loadAll),
      ]),
      el('div', { class: 'ops-grid' }, [
        opsSection('Outbox 与死信', outbox, (data) => opsJson(data, '没有队列数据。'), loadAll),
        opsSection('Worker 状态', worker, (data) => opsJson(data), loadAll),
      ]),
      opsSection('模型调用与路由诊断', models, (data) => opsJson(data, '没有模型调用记录。'), loadAll),
      opsSection('渠道配置状态', channels, (data) => opsJson(data, '没有渠道配置数据。'), loadAll),
    );
  };
  page.replaceChildren(head('系统运维', '查看服务健康、异步队列、Worker、模型路由和渠道配置。页面只读。'), content);
  loadAll();
}

function section(title, body) { return el('section', { class: 'archive-section' }, [el('h2', { class: 'section-title', text: title }), body]); }
document.addEventListener('DOMContentLoaded', renderOps);
