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
