/** 任务记录列表与详情（方案 §4.7 / §4.8）。
 *
 * 两条口径必须守住：
 *  1. **`failed` + `meta.outcome=incomplete` 是「未完成/预算耗尽」，不是普通失败**，
 *     更不能当成功。原始状态照常保留供诊断。
 *  2. **控制按钮只在该状态真的支持时出现**。`/retry` 只接受分析类 run（会话 run
 *     会被后端拒绝），所以只对 analysis 显示重试。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Ban, CircleSlash, Clock, PauseCircle, PlayCircle, RotateCcw } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { ApiError, ListResponse, RunEvent, RunInvocations, RunRow } from "../../contracts/api";
import { api } from "../../lib/http";
import { date } from "../../lib/format";

const metaOf = (run: RunRow): Record<string, unknown> => {
  if (!run.meta) return {};
  if (typeof run.meta === "string") { try { return JSON.parse(run.meta) as Record<string, unknown>; } catch { return {}; } }
  return run.meta;
};

/** 任务的**可读名**：m 优先、其次类型 + 编号。不显示 token/租约。 */
const nameOf = (run: RunRow): string => {
  const meta = metaOf(run);
  for (const key of ["title", "summary", "message", "last_user_message"]) {
    const value = meta[key];
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, 60);
  }
  return `${run.run_type === "conversation" ? "对话" : run.run_type === "analysis" ? "需求分析" : "任务"} ${run.run_id.slice(0, 8)}`;
};

/** 状态显示：`failed` 且 outcome=incomplete → 未完成（预算/超时），这是独立于失败的口径。 */
export function statusView(run: RunRow): { label: string; tone: "" | "warn" | "bad"; raw: string } {
  const outcome = metaOf(run).outcome;
  if (outcome === "incomplete") return { label: "未完成（预算或超时耗尽）", tone: "warn", raw: run.status };
  const label: Record<string, string> = {
    queued: "排队中", running: "执行中", paused: "已暂停", waiting_clarification: "等待补充",
    waiting_review: "待审核", completed: "已完成", failed: "失败", cancelled: "已取消", retrying: "重试中",
  };
  const tone: "" | "warn" | "bad" = run.status === "failed" ? "bad" : run.status === "cancelled" ? "" : ["queued", "running", "paused", "retrying", "waiting_clarification", "waiting_review"].includes(run.status) ? "warn" : "";
  return { label: label[run.status] ?? run.status, tone, raw: run.status };
}

const durationOf = (run: RunRow): string => {
  if (!run.started_at || !run.ended_at) return "未提供";
  const ms = new Date(run.ended_at).valueOf() - new Date(run.started_at).valueOf();
  if (Number.isNaN(ms) || ms < 0) return "未提供";
  return ms < 1000 ? `${ms} 毫秒` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} 秒` : `${Math.round(ms / 60_000)} 分钟`;
};

function ErrorView({ error }: { error: unknown }) {
  const e = error as ApiError;
  return <div className="state error">无法加载数据。{e?.detail ?? (error instanceof Error ? error.message : "")}</div>;
}

export function RunsListPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const runType = params.get("type") ?? "";
  const query = new URLSearchParams({ limit: "50" });
  if (status) query.set("status", status);
  if (runType) query.set("run_type", runType);
  const result = useQuery({ queryKey: ["runs", status, runType], queryFn: () => api<ListResponse<RunRow>>(`/api/v1/agent/runs?${query.toString()}`) });
  const rows = result.data?.items ?? [];
  const set = (key: string, value: string) => setParams((p) => { value ? p.set(key, value) : p.delete(key); return p; });
  return (
    <section className="page">
      <header className="page-heading"><div><span className="eyebrow">系统诊断</span><h1>任务记录</h1><p>对话与分析任务的执行记录</p></div></header>
      <div className="run-filters">
        <label>类型<select value={runType} onChange={(e) => set("type", e.target.value)}><option value="">全部</option><option value="conversation">对话</option><option value="analysis">分析</option></select></label>
        <label>状态<select value={status} onChange={(e) => set("status", e.target.value)}><option value="">全部</option>{["queued", "running", "paused", "waiting_clarification", "waiting_review", "completed", "failed", "cancelled", "retrying"].map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
      </div>
      {result.isLoading ? <div className="state">正在加载…</div> : result.isError ? <ErrorView error={result.error} /> : (
        <div className="data-table">
          <div className="table-row runs"><span>任务</span><span>类型</span><span>状态</span><span>开始</span><span>耗时</span></div>
          {rows.map((run) => {
            const view = statusView(run);
            return (
              <Link className="table-row runs" key={run.run_id} to={`/runs/${encodeURIComponent(run.run_id)}`}>
                <span className="name">{nameOf(run)}</span>
                <span>{run.run_type === "conversation" ? "对话" : run.run_type === "analysis" ? "分析" : "未提供"}</span>
                <span><span className={`status ${view.tone}`}>{view.label}</span>{view.raw !== view.label && <em className="run-raw">{view.raw}</em>}</span>
                <time>{date(run.created_at)}</time>
                <span>{durationOf(run)}</span>
              </Link>
            );
          })}
          {!rows.length && <div className="state">没有匹配的任务记录。</div>}
        </div>
      )}
    </section>
  );
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api<RunRow>(`/api/v1/agent/runs/${encodeURIComponent(runId)}`) });
  const events = useQuery({ queryKey: ["run-events", runId], queryFn: () => api<{ items: RunEvent[] }>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/events?after_seq=0`) });
  const invocations = useQuery({ queryKey: ["run-invocations", runId], queryFn: () => api<RunInvocations>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/invocations`) });

  const control = useMutation({
    mutationFn: ({ action }: { action: "pause" | "cancel" | "retry" }) =>
      api<Record<string, unknown>>(`/api/v1/agent/runs/${encodeURIComponent(runId)}/${action}`, { method: "POST" }),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      if (variables.action === "retry") navigate("/runs");
    },
  });

  if (run.isLoading) return <section className="page"><div className="state">正在加载…</div></section>;
  if (run.isError || !run.data) return <section className="page"><ErrorView error={run.error} /></section>;
  const data = run.data;
  const view = statusView(data);
  const tone = statusView(data).tone;
  const incomplete = metaOf(data).outcome === "incomplete";
  const tools = invocations.data?.tools ?? [];

  return (
    <section className="page">
      <Link className="back" to="/runs"><ArrowLeft size={14} /> 返回任务记录</Link>
      <header className="page-heading">
        <div><span className="eyebrow">{data.run_type === "conversation" ? "对话任务" : data.run_type === "analysis" ? "分析任务" : "任务"} · <span className="mono">{data.run_id.slice(0, 8)}</span></span><h1>{nameOf(data)}</h1><p>开始 {date(data.created_at)} · 耗时 {durationOf(data)}</p></div>
        <div className="run-controls">
          {data.status === "running" && <button className="button" disabled={control.isPending} onClick={() => control.mutate({ action: "pause" })}><PauseCircle size={14} />暂停</button>}
          {["queued", "running", "paused"].includes(data.status) && <button className="button" disabled={control.isPending} onClick={() => control.mutate({ action: "cancel" })}><Ban size={14} />取消</button>}
          {/* 重试只对分析类有效 —— 后端对会话 run 会直接拒绝，所以只在这里出现。 */}
          {data.status === "failed" && data.run_type === "analysis" && <button className="button primary" disabled={control.isPending} onClick={() => control.mutate({ action: "retry" })}><RotateCcw size={14} />重试</button>}
          {/* 恢复必须回到该 run 所属的会话 —— 暂停的 run 只能从对话页继续。 */}
          {data.status === "paused" && data.conversation_id && <Link className="button primary" to={`/chat/${encodeURIComponent(data.conversation_id)}`}><PlayCircle size={14} />去对话页继续</Link>}
          {data.status === "paused" && !data.conversation_id && <span className="hint">该运行不是会话任务，无法在对话页继续。</span>}
        </div>
      </header>

      {control.isError && <p className="hint bad">操作失败。{(control.error as ApiError)?.detail ?? ""}</p>}

      <div className={`chat-banner ${tone === "bad" ? "bad" : tone === "warn" ? "warn" : ""}`}>
        {incomplete ? <Clock size={15} /> : data.status === "cancelled" ? <CircleSlash size={15} /> : data.status === "failed" ? <Ban size={15} /> : <Clock size={15} />}
        <div>
          <strong>{view.label}</strong>
          {incomplete && <p>本轮因预算或超时耗尽而结束。已产出的部分仍可回看，但它不算一次成功。</p>}
          {data.error && <p>{data.error}</p>}
          {!incomplete && !data.error && <p>当前状态 {view.raw}{data.current_node ? ` · 最后节点 ${data.current_node}` : ""}</p>}
        </div>
      </div>

      <div className="detail-grid runs-detail">
        <article className="panel"><h2>公开动作时间线</h2>
          {events.isLoading ? <div className="state">正在加载…</div> : events.isError ? <ErrorView error={events.error} /> : (
            <ol className="run-timeline">
              {(events.data?.items ?? []).map((event) => (
                <li key={event.id}>
                  <span className="mono">#{event.sequence}</span>
                  <div><strong>{event.event_type}</strong>{event.node && <em>{event.node}</em>}{typeof event.payload?.status === "string" && <span> {event.payload.status}</span>}</div>
                  <time>{date(event.created_at)}</time>
                </li>
              ))}
              {!(events.data?.items ?? []).length && <li className="hint">该任务没有已落库的事件。</li>}
            </ol>
          )}
        </article>

        <article className="panel"><h2>工具调用（{tools.length}）</h2>
          {invocations.isLoading ? <div className="state">正在加载…</div> : invocations.isError ? <ErrorView error={invocations.error} /> : (
            <>
              <ul className="tool-list">
                {tools.map((tool) => (
                  <li key={tool.id}>
                    <span className="mono">{tool.tool_name}</span>
                    {tool.status && <span className="status">{tool.status}</span>}
                    {tool.elapsed_ms != null && <em>{tool.elapsed_ms} ms</em>}
                    {tool.error_message && <em className="bad-text">{tool.error_message}</em>}
                  </li>
                ))}
                {!tools.length && <li className="hint">本次运行没有工具调用记录。</li>}
              </ul>
              {/* 模型调用暂无接口返回内容：`models` 恒为空数组，不假装有数据。 */}
              <p className="hint"><AlertTriangle size={13} /> 模型调用明细当前不由该接口提供（`models` 恒为空），所以这里只展示工具调用。</p>
            </>
          )}
        </article>
      </div>
    </section>
  );
}
