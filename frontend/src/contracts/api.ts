/** The backend serialises UUID and snowflake IDs as strings. Never coerce one
 * to a JavaScript number, which loses snowflake precision. */
export type Id = string;
export interface ListResponse<T> { items: T[]; }
export interface DocumentAsset { id: Id; file_name: string; content_type: string; storage_uri: string; checksum: string; size_bytes: number; source_type: string; source_id: Id | null; original_text: string | null; extracted_text: string | null; metadata: Record<string, string | number | boolean | null>; stream_id: Id | null; version_no: number | null; status: string | null; superseded_by_version_no: number | null; created_at: string; }
export interface DocumentChunk { id: Id; document_id: Id; chunk_index: number; chunk_text: string; created_at: string; }
export interface Requirement { requirement_key: string; requirement_name: string; final_requirement: string; status: string; business_domain: string; business_domains: string[]; current_version: number; feature_count: number; requester_names: string[]; source_types: string[]; departments: string[]; sensitivity_levels: string[]; first_source_submitted_at: string | null; latest_source_submitted_at: string | null; }
export interface Version { id: Id; version_no: number; created_at: string; summary?: string | null; }
export interface RequirementFeature { id: Id; feature_key: string; content: string; status?: string | null; }
export interface RequirementTraceItem { version_no?: number; source_id?: Id; source_type?: string; created_at?: string; }
export interface RequirementTrace { requirement_key: string; versions: RequirementTraceItem[]; }
export interface RequirementDiff { requirement_key: string; from_version: number; to_version: number; added: RequirementFeature[]; removed: RequirementFeature[]; modified: Array<{feature_key: string; before: string; after: string}>; unchanged: number; }
export interface Conversation { id: Id; title: string; summary: string | null; status: string; updated_at: string; message_count: number; }
export interface UploadResult { document: DocumentAsset; status: "queued" | "ready"; result: { id?: string } | null; }
export interface ApiError extends Error { status?: number; requestId?: string; }
