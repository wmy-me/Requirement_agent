/** 审核列表与详情（方案 §4.5 / §4.6）。
 *
 * 三条硬约束：
 *  1. **复用现有服务**，不发明第二套审批。裁决走 `POST /api/v1/reviews/submit`，
 *     键是 `source_id`（不是 proposal_id —— 独立 Proposal 并不存在）。
 *  2. **预演失败不得继续**。预演拿不到结果时批准按钮不可用 —— 拿空结果当「没有变更」
 *    去批准，正是方案点名禁止的。
 *  3. **任何 409 都不自动重试**。乐观锁冲突意味着「你看到的已经不是最新版本」，
 *     必须重新加载再决定，照着旧预览点第二下会产生基于陈旧功能集的新版本。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, FileText, RotateCcw, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { ApiError, ListResponse, MergePreview, Requirement, ReviewDecision, ReviewDetail, ReviewSourceRow } from "../../contracts/api";
import { api } from "../../lib/http";
import { date, text } from "../../lib/format";

const meta = (row: { metadata?: Record<string, unknown> }): Record<string, unknown> => (row.metadata ?? {}) as Record<string, unknown>;
const titleOf = (row: ReviewSourceRow): string => {
  const extracted = meta(row).extracted as Record<string, unknown> | undefined;
  const title = extracted && typeof extracted.requirement_title === "string" ? extracted.requirement_title : "";
  return title || (row.original_text ?? "").trim().split("\n")[0]?.slice(0, 60) || "（无标题）";
};
const suggestionOf = (row: ReviewSourceRow): string => {
  const analysis = meta(row).analysis as Record<string, unknown> | undefined;
  const suggestion = analysis?.suggestion as Record<string, unknown> | undefined;
  return typeof suggestion?.action === "string" ? suggestion.action : "未提供";
};

function ErrorView({ error }: { error: unknown }) {
  const e = error as ApiError;
  return <div className="state error">无法加载数据。{e?.detail ?? (error instanceof Error ? error.message : "")}{e?.status === 409 ? "（状态已变化，请重新加载）" : ""}</div>;
}

export function ReviewsListPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "history" ? "history" : "pending";
  const endpoint = tab === "pending" ? "/api/v1/reviews/pending?limit=50" : "/api/v1/reviews/history?limit=50";
  const result = useQuery({ queryKey: ["reviews", tab], queryFn: () => api<ListResponse<ReviewSourceRow>>(endpoint) });
  const rows = result.data?.items ?? [];
  return (
    <section className="page">
      <header className="page-heading"><div><span className="eyebrow">人工确认</span><h1>审核</h1><p>核对来源、查看分析依据，并提交可追溯的裁决</p></div></header>
      <div className="tabs" role="tablist">
        <button className={tab === "pending" ? "selected" : ""} onClick={() => setTab("pending")}>待审核</button>
        <button className={tab === "history" ? "selected" : ""} onClick={() => setTab("history")}>审核历史</button>
      </div>
      {tab === "history" && <p className="window-note">已裁决的来源，默认取 approved / rejected / committed；退回修改不是终态，不在其中。</p>}
      {result.isLoading ? <div className="state">正在加载…</div> : result.isError ? <ErrorView error={result.error} /> : (
        <div className="data-table">
          <div className="table-row header reviews"><span>材料</span><span>来源</span><span>建议动作</span><span>状态</span><span>更新时间</span></div>
          {rows.map((row) => (
            <Link className="table-row reviews" key={row.source_id} to={`/reviews/${encodeURIComponent(row.source_id)}`}>
              <span className="name"><FileText size={16} />{titleOf(row)}</span>
              <span>{text(row.source_type, "未提供")} · {text(row.requester_name, "提交人未提供")}</span>
              <span>{suggestionOf(row)}</span>
              <span><span className={`status ${row.processing_status.includes("reject") || row.processing_status.includes("fail") ? "bad" : row.processing_status === "pending_review" ? "warn" : ""}`}>{text(row.processing_status, "未知")}</span></span>
              <time>{date(row.updated_at ?? row.submitted_at)}</time>
            </Link>
          ))}
          {!rows.length && <div className="state">{tab === "pending" ? "当前没有待审核的来源。" : "没有已裁决的记录。"}</div>}
        </div>
      )}
    </section>
  );

  function setTab(next: string) { setParams((p) => { p.set("tab", next); return p; }); }
}

export function ReviewDetailPage() {
  const { sourceId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [targetKey, setTargetKey] = useState("");
  const [mergeMode, setMergeMode] = useState<"union" | "replace">("union");
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);

  const detail = useQuery({ queryKey: ["review", sourceId], queryFn: () => api<ReviewDetail>(`/api/v1/reviews/${encodeURIComponent(sourceId)}/detail`) });
  const requirements = useQuery({ queryKey: ["reviews-targets"], queryFn: () => api<ListResponse<Requirement>>("/api/v1/requirements?limit=200") });
  // 预演只在**选定了目标**时才发 —— 没有目标就是「新建独立需求」，没有可比对的基线。
  const preview = useQuery({
    queryKey: ["merge-preview", sourceId, targetKey, mergeMode],
    queryFn: () => api<MergePreview>(`/api/v1/reviews/${encodeURIComponent(sourceId)}/merge-preview?target_requirement_key=${encodeURIComponent(targetKey)}&merge_mode=${mergeMode}`),
    enabled: Boolean(targetKey),
    retry: false,
  });

  const submit = useMutation({
    mutationFn: (decision: ReviewDecision) => api<{ requirement_key?: string }>("/api/v1/reviews/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_id: Number(sourceId), decision,
        target_requirement_key: targetKey || null,
        merge_mode: mergeMode,
        reviewer_name: reviewer || null,
        comment: comment || null,
        feature_overrides: decision === "approved" && preview.data ? preview.data.overrides : [],
      }),
    }),
    onSuccess: (result, decision) => {
      queryClient.invalidateQueries({ queryKey: ["reviews"] });
      if (decision === "approved" && result?.requirement_key) navigate(`/library/requirements/${encodeURIComponent(result.requirement_key)}`);
      else navigate("/reviews");
    },
    onError: (error) => {
      // 不自动重试：冲突意味着数据已变，必须重新加载。
      const e = error as ApiError;
      setConflict(e.status === 409 ? (typeof e.detail === "string" ? e.detail : "该条已被处理或目标需求已变更，请重新加载。") : null);
      if (e.status === 409) { queryClient.invalidateQueries({ queryKey: ["review", sourceId] }); queryClient.invalidateQueries({ queryKey: ["merge-preview"] }); }
    },
  });

  if (detail.isLoading) return <section className="page"><div className="state">正在加载…</div></section>;
  if (detail.isError || !detail.data) return <section className="page"><ErrorView error={detail.error} /></section>;
  const row = detail.data;
  const m = meta(row);
  const extracted = (m.extracted ?? {}) as Record<string, unknown>;
  const analysis = (m.analysis ?? {}) as Record<string, unknown>;
  const risk = (m.risk ?? {}) as Record<string, unknown>;
  const degradation = (m.degradation ?? null) as Record<string, unknown> | null;
  const previewFailed = Boolean(targetKey) && preview.isError;
  const canApprove = Boolean(targetKey) ? preview.isSuccess : true;

  return (
    <section className="page">
      <Link className="back" to="/reviews"><ArrowLeft size={14} /> 返回审核</Link>
      <header className="page-heading">
        <div><span className="eyebrow">{text(row.source_type, "来源")} · 待审核</span><h1>{titleOf(row)}</h1><p>来源 {row.source_id} · 提交人 {text(row.requester_name, "未提供")} · {date(row.submitted_at)}</p></div>
      </header>

      {conflict && <div className="chat-banner bad"><AlertTriangle size={15} /><div><strong>这条审核已经不能按当前页面提交</strong><p>{conflict}</p></div><button className="button" onClick={() => { setConflict(null); navigate(0); }}>重新加载</button></div>}
      {degradation && <div className="chat-banner warn"><AlertTriangle size={15} /><div><strong>分析链路降级</strong><p>{String(degradation.reason ?? "模型链路降级，审核时请谨慎确认。")}</p></div></div>}

      <div className="review-grid">
        <article className="panel"><h2>原始材料</h2><p className="pre-wrap">{text(row.original_text, "没有原始正文。")}</p>
          {typeof extracted.requirement_title === "string" && <><h3>结构化抽取</h3><dl><dt>标题</dt><dd>{extracted.requirement_title}</dd><dt>摘要</dt><dd>{text(extracted.summary, "未提供")}</dd></dl></>}
        </article>

        <article className="panel"><h2>拟变更</h2>
          {!targetKey && <p className="hint">未选择目标需求 —— 批准后将新建一条独立需求。选择一个目标需求，可先看合并预演的增删改。</p>}
          {targetKey && preview.isLoading && <div className="state">正在预演…</div>}
          {previewFailed && <div className="state error">预演失败，无法看到真实变更。{(preview.error as ApiError)?.detail ?? ""} —— 预演不成功时不能据此批准。</div>}
          {targetKey && preview.isSuccess && (
            <div className="diff">
              <p>目标 {preview.data.target.requirement_key} · v{preview.data.target.current_version} → v{preview.data.next_version}；未变更 {preview.data.summary.keep} 项</p>
              {preview.data.warnings.map((w) => <p className="hint" key={w}>{w}</p>)}
              {preview.data.groups.filter((group) => group.added.length || group.modified.length || group.deleted.length).map((group) => (
                <div key={`${group.module_key}-${group.module_name}`} className="diff-module">
                  {group.module_name && <h3 className="diff-module-name">{group.module_name}</h3>}
                  {group.added.map((x, i) => <p className="diff-line added" key={`a${i}`}><b>新增</b> {x.content}</p>)}
                  {group.modified.map((x, i) => <p className="diff-line modified" key={`m${i}`}><b>修改</b> {x.feature_key}：{x.before} → {x.after}</p>)}
                  {group.deleted.map((x, i) => <p className="diff-line removed" key={`d${i}`}><b>删除</b> {x.feature_key}：{x.content}</p>)}
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="panel"><h2>来源与风险</h2>
          <dl>
            <dt>建议动作</dt><dd>{suggestionOf(row)}</dd>
            <dt>建议目标</dt><dd>{typeof (analysis.suggestion as Record<string, unknown>)?.target_requirement_key === "string" ? String((analysis.suggestion as Record<string, unknown>).target_requirement_key) : "未提供"}</dd>
            <dt>质量风险</dt><dd>{text(risk.quality_risk, "未提供")}</dd>
            <dt>变更风险</dt><dd>{text(risk.change_risk, "未提供")}</dd>
            <dt>技术风险</dt><dd>{text(risk.technical_impact_risk, "未提供")}</dd>
          </dl>
          {typeof analysis.reasoning === "string" && <p>{analysis.reasoning}</p>}
          <p className="hint">AI 建议与人工裁决分别标记：以上是模型产出，不会自动写入正式需求。</p>
        </article>
      </div>

      <section className="review-decide">
        <h2>人工裁决</h2>
        <div className="review-fields">
          <label>合并进目标需求
            <select value={targetKey} onChange={(e) => setTargetKey(e.target.value)}>
              <option value="">（新建独立需求）</option>
              {(requirements.data?.items ?? []).map((r) => <option key={r.requirement_key} value={r.requirement_key}>{r.requirement_key} · {r.requirement_name}</option>)}
            </select>
          </label>
          <label>合并模式
            <select value={mergeMode} onChange={(e) => setMergeMode(e.target.value as "union" | "replace")} disabled={!targetKey}>
              <option value="union">并集：保留目标既有功能</option>
              <option value="replace">替换：可能删除目标既有功能</option>
            </select>
          </label>
          <label>审核人<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="可选" /></label>
          <label className="wide">审核意见<textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="可选" /></label>
        </div>
        {submit.isError && !conflict && <p className="hint bad">提交失败。{(submit.error as ApiError)?.detail ?? ""}</p>}
        <div className="review-actions">
          <button className="button" disabled={submit.isPending} onClick={() => submit.mutate("returned")}><RotateCcw size={14} />退回修改</button>
          <button className="button" disabled={submit.isPending} onClick={() => submit.mutate("rejected")}><XCircle size={14} />拒绝</button>
          <button className="button primary" disabled={submit.isPending || !canApprove} title={!canApprove ? "预演未成功，不能据此批准" : undefined} onClick={() => submit.mutate("approved")}><CheckCircle2 size={14} />批准</button>
        </div>
      </section>
    </section>
  );
}
