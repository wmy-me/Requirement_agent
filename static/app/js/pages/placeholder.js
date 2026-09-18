/* 尚未交付模块的统一占位页。
 *
 * 这些页面已经有稳定的 URL 和导航入口，但业务界面尚未实现。必须渲染明确状态，
 * 不能因引用缺失脚本而留下空白页面，更不能放置没有后端能力支撑的操作按钮。
 */
'use strict';

const PLACEHOLDER_COPY = {
  intake: ['输入中心', '文本提交、文件导入与渠道配置将在此提供。截图输入和导入任务进度目前未提供。'],
  analysis: ['智能分析', 'Agent Run、节点事件、工具调用与模型调用视图尚未接入。'],
  versions: ['版本与变更', '版本时间线、Diff 与回滚工作区尚未接入。'],
  knowledge: ['来源与知识', '来源、文档与能力词表视图尚未接入；文档版本链目前未提供。'],
  ops: ['系统运维', '健康、队列、死信、模型、渠道与审计视图尚未接入。'],
};

function renderPlaceholder() {
  const page = document.getElementById('page');
  const key = document.body.dataset.page;
  const [title, description] = PLACEHOLDER_COPY[key] || ['工作台模块', '该模块尚未接入。'];
  page.replaceChildren(
    head(title, description),
    card('未提供', [state.empty('该模块正在实施，当前没有可执行操作')]),
  );
}

document.addEventListener('DOMContentLoaded', renderPlaceholder);
