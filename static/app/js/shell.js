/* 工作台外壳：左侧导航 + 顶栏。
 *
 * **每个页面引它，它把导航渲染出来** —— 8 个页面各写一遍导航，改一处就要改八处，
 * 而「导航不一致」这种事要到用户点错才被发现。
 *
 * 页面通过 `<body data-page="reviews">` 声明自己是谁，导航据此高亮。
 * 导航表 `NAV` 在 core.js（对话页也要用同一份，见那里的说明）。
 */
'use strict';

function renderShell() {
  const current = document.body.dataset.page || 'index';
  const shell = document.getElementById('shell');
  if (!shell) return;

  const theme = document.body.dataset.title || '';

  shell.replaceChildren(
    el('aside', { class: 'rail' }, [
      el('div', { class: 'rail-brand' }, [
        el('a', { class: 'rail-title', href: '/app', text: '需求仓库' }),
        el('div', { class: 'rail-sub', text: '版本、证据与审核' }),
      ]),
      el('form', { class: 'shell-search', onsubmit: (event) => {
        event.preventDefault();
        const input = event.currentTarget.querySelector('input');
        const value = input.value.trim();
        if (value) window.location.assign(`/app/requirements?q=${encodeURIComponent(value)}`);
      } }, [el('input', { type: 'search', placeholder: '搜索需求编号或关键词', 'aria-label': '搜索需求编号或关键词' })]),
      el('nav', { class: 'rail-nav' }, NAV.map((item) =>
        el('a', {
          class: 'nav-item' + (item.key === current ? ' active' : ''), title: item.hint,
          href: item.path,
        }, [
          el('span', { class: 'nav-label', text: item.label }),
          el('span', { class: 'nav-hint', text: item.hint }),
        ]))),
      el('div', { class: 'rail-foot' }, [
        el('a', { class: 'btn btn-primary shell-create', href: '/app/intake', text: '录入需求' }),
      ]),
    ]),
  );
}

document.addEventListener('DOMContentLoaded', renderShell);
