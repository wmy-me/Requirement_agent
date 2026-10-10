/** The backend serialises UUID and snowflake IDs as strings. Never coerce one
 * to a JavaScript number, which loses snowflake precision. */
export type Id = string;
export interface ListResponse<T> { items: T[]; }
export interface DocumentAsset { id: Id; file_name: string; content_type: string; storage_uri: string; checksum: string; size_bytes: number; source_type: string; source_id: Id | null; original_text: string | null; extracted_text: string | null; metadata: Record<string, string | number | boolean | null>; stream_id: Id | null; version_no: number | null; status: string | null; superseded_by_version_no: number | null; created_at: string; }
export interface DocumentChunk { id: Id; document_id: Id; chunk_index: number; chunk_text: string; created_at: string; }

/** GET /api/v1/requirements items.
 * `module_names` is the real 所属模块 field: distinct active
 * requirement_feature.module_name (migration 014). It is NOT the business
 * domain — `business_domain(s)` must never be relabelled as the module. */
export interface Requirement { requirement_key: string; requirement_name: string; final_requirement: string; status: string; business_domain: string; business_domains: string[]; module_names: string[]; current_version: number; feature_count: number; requester_names: string[]; source_types: string[]; departments: string[]; sensitivity_levels: string[]; first_source_submitted_at: string | null; latest_source_submitted_at: string | null; }

/** GET /api/v1/requirements/{key}/versions item. */
export interface Version { id: Id; requirement_id: Id; version_no: number; parent_version_no: number | null; status: string; version_title: string | null; change_type: string | null; requirement_snapshot: string | null; change_summary: string | null; created_by: string | null; reviewed_by: string | null; created_at: string; }

/** GET /api/v1/requirements/{key}/features item — carries module_key/module_name. */
export interface RequirementFeature { id: Id; requirement_id?: Id; feature_key: string; content: string; status?: string | null; module_key?: string | null; module_name?: string | null; ordinal?: number | null; origin_version_no?: number | null; removed_version_no?: number | null; }

/** One evidence source attached to a version, as returned by /trace. */
export interface TraceSource { source_id: Id; source_type: string; source_event_id: Id | null; requester_id: Id | null; requester_name: string | null; original_text: string | null; extracted_text: string | null; original_payload?: Record<string, unknown>; structured_requirement?: Record<string, unknown>; relation_type: string | null; processing_status: string | null; submitted_at: string | null; }
export interface TraceVersion { version_id: Id; version_no: number; parent_version_no: number | null; status: string; version_title: string | null; change_type: string | null; requirement_snapshot: string | null; change_summary: string | null; diff_payload?: Record<string, unknown>; feature_changes?: unknown[]; created_by: string | null; reviewed_by: string | null; created_at: string; sources: TraceSource[]; }
export interface TraceRequirement { id: Id; requirement_key: string; requirement_name: string; final_requirement: string; current_version: number; status: string; lock_version?: number; created_at: string; updated_at: string; }
/** GET /api/v1/requirements/{key}/trace — the real server shape. */
export interface RequirementTrace { requirement: TraceRequirement; versions: TraceVersion[]; }

/** GET /api/v1/requirements/{key}/diff — added/removed are full feature rows. */
export interface RequirementDiff { requirement_key: string; from_version: number; to_version: number; added: RequirementFeature[]; removed: RequirementFeature[]; modified: Array<{ feature_key: string; before: string; after: string }>; unchanged: number; }

export interface Conversation { id: Id; title: string; summary: string | null; status: string; updated_at: string; message_count: number; }
/** POST /api/v1/documents/upload -> 202. `status` is "queued" or "ready":
 * success means the file was accepted, NOT that the index is ready. */
export interface UploadResult { document: DocumentAsset; status: "queued" | "ready"; result: { id?: string } | null; }
export interface ApiError extends Error { status?: number; requestId?: string; detail?: string; }

/* ── 对话（F3）─────────────────────────────────────────────────────────
   `artifacts` 的形状**不固定**，取决于走哪条路径（见 api/routes/agent_chat.py）：
     - 助手路径：{intent, status, sources, tool_calls, incomplete_reason}
     - 分析管线：{status, steps, source_type, extracted, candidates, analysis, risk, next_action, ...}
     - 闲聊：{intent, answer_mode}；被拦截的操作请求：{intent, blocked}
   因此**按存在的键判别**，不要按声明的类型判别。 */
export type ChatArtifacts = Record<string, unknown>;

/** `GET /api/v1/agent/chat/{session_id}` 的历史项，也是落库的 assistant 消息形状。 */
export interface ChatHistoryItem { role: "user" | "assistant" | string; content: string; artifacts?: ChatArtifacts | null; client_message_id?: string | null; created_at?: string | null; }
export interface ChatHistory { session_id: string; history: ChatHistoryItem[]; }
/** `GET /api/v1/agent/chat/{session_id}/resumable` —— 只报 **paused 且有 checkpoint** 的 run。 */
export interface ResumableRun { run_id: string; status: string; stage: string | null; steps_done: number | null; checkpoint_keys: string[]; updated_at: string | null; }
export interface ResumableResponse { run: ResumableRun | null; }
export interface RunSummary { run_id: string; status: string; run_type?: string | null; current_node?: string | null; source_id?: Id | null; conversation_id?: string | null; created_at?: string | null; updated_at?: string | null; ended_at?: string | null; meta?: Record<string, unknown> | null; }
/** 真实可选值（后端 `mode` 参数）。 */
export type ChatMode = "auto" | "ask_library" | "draft_requirement" | "research";
export type WebPolicy = "off" | "auto" | "on";
export type AnalysisMode = "strict" | "balanced" | "broad";
/** 只存在于 `/chat/stream`（JSON），**不存在于** `/chat/stream-with-files`。 */
export type SourceType = "web" | "email" | "meeting" | "manual";

/* ── 审核（F4）─────────────────────────────────────────────────────────
   ⚠️ `source_id` 在**所有**端点都是字符串（`to_sid`）。曾经 `/reviews/pending`
   发字符串、`/merge-preview` 发 number，前端把两边拼起来就撞类型 —— 现在统一。
   提交裁决用 `POST /api/v1/reviews/submit`，任何 409 都**不可自动重试**。 */
export interface ReviewSourceRow {
  source_id: Id; source_type: string; requester_id: string | null; requester_name: string | null;
  original_text: string | null; extracted_text: string | null;
  metadata: Record<string, unknown>; processing_status: string;
  submitted_at: string | null; updated_at: string | null;
}
export interface ReviewDetail extends ReviewSourceRow { source_event_id: string | null; original_payload: Record<string, unknown>; }
/** 合并预演返回。`overrides` 就是提交时要原样回传的逐条裁决。 */
export interface MergePreview {
  source_id: Id; merge_mode: "union" | "replace";
  target: { requirement_key: string; requirement_name: string; current_version: number };
  next_version: number;
  groups: Array<{ module_key: string | null; module_name: string | null; added: Array<{ content: string }>; modified: Array<{ feature_key: string; before: string; after: string }>; deleted: Array<{ feature_key: string; content: string }>; kept: number }>;
  summary: { add: number; modify: number; delete: number; keep: number; active_after: number };
  overrides: Array<Record<string, unknown>>;
  warnings: string[];
}
export type ReviewDecision = "approved" | "rejected" | "returned";

/* ── 任务记录（F5）──────────────────────────────────────────────────────
   `meta` 经 `stringify_run_meta` 出来，可能是对象也可能是字符串 —— 按两种都处理。
   普通页面**不展示** execution_token / lease_expires_at（§4.7：普通视图不显示
   租约字段），它们只在诊断需要时才有意义。 */
export interface RunRow {
  id: Id; run_id: string; conversation_id: string | null; source_id: Id | null;
  run_type: string | null; client_message_id?: string | null; status: string; error: string | null;
  meta: Record<string, unknown> | string | null; stage: string | null;
  current_node: string | null; created_at: string | null; started_at: string | null;
  ended_at: string | null; updated_at: string | null;
}
export interface RunEvent { id: string; run_id: string; sequence: number; event_type: string; node: string | null; payload: Record<string, unknown>; created_at: string | null; }
export interface ToolInvocation { id: string; run_id: string; tool_name: string; arguments_summary: string | null; result_summary: string | null; status: string | null; elapsed_ms: number | null; error_message: string | null; created_at: string | null; }
export interface RunInvocations { run_id: string; tools: ToolInvocation[]; models: unknown[]; }
