/* 工作台外壳：左侧导航 + 顶栏。
 *
 * **每个页面引它，它把导航渲染出来** —— 8 个页面各写一遍导航，改一处就要改八处，
 * 而「导航不一致」这种事要到用户点错才被发现。
 *
 * 页面通过 `<body data-page="reviews">` 声明自己是谁，导航据此高亮。
 */
'use strict';

const NAV = [
  { key: 'index', path: '/app', label: '全局视图', hint: '状态与流动' },
  { key: 'requirements', path: '/app/requirements', label: '需求', hint: '正式需求' },
  { key: 'versions', path: '/app/versions', label: '版本图谱', hint: '提交与 Diff' },
  { key: 'knowledge', path: '/app/knowledge', label: '来源', hint: '证据与文档' },
  { key: 'reviews', path: '/app/reviews', label: '审核', hint: '人工确认' },
  { key: 'analysis', path: '/app/analysis', label: '分析', hint: '风险与关联' },
  { key: 'intake', path: '/app/intake', label: '输入', hint: '文本与文件' },
  { key: 'ops', path: '/app/ops', label: '运行', hint: '系统诊断' },
];

function renderShell() {
  const current = document.body.dataset.page || 'index';
  const shell = document.getElementById('shell');
  if (!shell) return;

  const theme = document.body.dataset.title || '';

  shell.replaceChildren(
    el('aside', { class: 'rail' }, [
      el('div', { class: 'rail-brand' }, [
        el('a', { class: 'rail-title', href: '/app', text: '需求演进' }),
        el('div', { class: 'rail-sub', text: '需求、证据与版本' }),
      ]),
      el('nav', { class: 'rail-nav' }, NAV.map((item) =>
        el('a', {
          class: 'nav-item' + (item.key === current ? ' active' : ''), title: item.hint,
          href: item.path,
        }, [
          el('span', { class: 'nav-label', text: item.label }),
          el('span', { class: 'nav-hint', text: item.hint }),
        ]))),
      el('div', { class: 'rail-foot' }, [
        el('a', { class: 'nav-item nav-old', href: '/ui' }, [
          el('span', { class: 'nav-label', text: '旧版对话' }),
          el('span', { class: 'nav-hint', text: '保留访问' }),
        ]),
      ]),
    ]),
  );
}

document.addEventListener('DOMContentLoaded', renderShell);
