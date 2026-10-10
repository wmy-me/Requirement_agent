export type UnknownRecord = Record<string, unknown>;
export interface DocumentItem extends UnknownRecord { id?: string | number; document_id?: string | number; name?: string; filename?: string; title?: string; status?: string; processing_status?: string; updated_at?: string; source_type?: string; original_text?: string; extracted_text?: string; }
export interface Requirement extends UnknownRecord { requirement_key?: string; title?: string; requirement_title?: string; current_version?: number; version?: number; updated_at?: string; module?: string; modules?: string[]; }
export interface Version extends UnknownRecord { version?: number; version_no?: number; created_at?: string; summary?: string; }
export interface ApiError extends Error { status?: number; requestId?: string; }
