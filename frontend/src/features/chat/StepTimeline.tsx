/** 执行摘要：默认折叠成「已查询 N 个工具」，展开显示公开动作。
 * 只展示后端真实发来的 `step`（label / reason / tool）——没有隐含思维链，
 * 也不把原始工具 payload 当聊天内容。 */
import { ChevronDown, Wrench } from "lucide-react";
import { useState } from "react";
import type { ToolStep } from "./reducer";

export function StepTimeline({ steps, live }: { steps: ToolStep[]; live: boolean }) {
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;
  const toolCount = steps.filter((s) => s.step === "tool" || s.tool).length;
  // `step.label` 是事件到达时的进行时文案；终态继续显示“正在查询”会让已完成的
  // 回答看起来仍在跑。只在 live 时保留原始进行时，收尾后给出明确完成态。
  const summary = toolCount > 0 ? `已查询 ${toolCount} 个工具` : live ? steps[steps.length - 1].label : "已完成本轮查询";
  return (
    <div className="steps">
      <button className="steps-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <ChevronDown size={14} className={open ? "rot" : ""} />
        <span>{summary}</span>
        {live && <span className="steps-live">进行中</span>}
      </button>
      {open && (
        <ol className="steps-list">
          {steps.map((s, i) => (
            <li key={`${s.label}-${i}`}>
              {s.tool ? <Wrench size={13} /> : null}
              <span>{s.label}</span>
              {s.reason && <em>{s.reason}</em>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
