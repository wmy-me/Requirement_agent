/* 工作台外壳：左侧导航 + 顶栏。
 *
 * **每个页面引它，它把导航渲染出来** —— 8 个页面各写一遍导航，改一处就要改八处，
 * 而「导航不一致」这种事要到用户点错才被发现。
 *
 * 页面通过 `<body data-page="reviews">` 声明自己是谁，导航据此高亮。
 */
'use strict';

const NAV = [
  { key: 'index', path: '/app', label: '总览', hint: '待办、健康、趋势' },
  { key: 'requirements', path: '/app/requirements', label: '需求工作台', hint: '需求库与档案' },
  { key: 'intake', path: '/app/intake', label: '输入中心', hint: '文本 / 文件 / 渠道' },
  { key: 'analysis', path: '/app/analysis', label: '智能分析', hint: 'Agent 运行与工具调用' },
  { key: 'reviews', path: '/app/reviews', label: '审核中心', hint: '待审、裁决、历史' },
  { key: 'versions', path: '/app/versions', label: '版本与变更', hint: '时间线、Diff、回滚' },
  { key: 'knowledge', path: '/app/knowledge', label: '来源与知识', hint: '来源、文档、能力' },
  { key: 'ops', path: '/app/ops', label: '系统运维', hint: '队列、Worker、模型、审计' },
];

function renderShell() {
  const current = document.body.dataset.page || 'index';
  const shell = document.getElementById('shell');
  if (!shell) return;

  const theme = document.body.dataset.title || '';

  shell.replaceChildren(
    el('aside', { class: 'rail' }, [
      el('div', { class: 'rail-brand' }, [
        el('div', { class: 'rail-title', text: '需求治理工作台' }),
        el('div', { class: 'rail-sub', text: theme }),
      ]),
      el('nav', { class: 'rail-nav' }, NAV.map((item) =>
        el('a', {
          class: 'nav-item' + (item.key === current ? ' active' : ''),
          href: item.path,
        }, [
          el('span', { class: 'nav-label', text: item.label }),
          el('span', { class: 'nav-hint', text: item.hint }),
        ]))),
      el('div', { class: 'rail-foot' }, [
        el('a', { class: 'nav-item nav-old', href: '/ui' }, [
          el('span', { class: 'nav-label', text: '对话助手（旧界面）' }),
          el('span', { class: 'nav-hint', text: '临时保留，逐页替换后删除' }),
        ]),
      ]),
    ]),
  );
}

document.addEventListener('DOMContentLoaded', renderShell);
