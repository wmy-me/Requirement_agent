import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, ExternalLink, FileText, GitCompareArrows, Loader2, Search, TableProperties, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { DocumentAsset, DocumentChunkPage, DocumentIndexStatus, ListResponse, Requirement, RequirementDiff, RequirementFeature, RequirementTrace, TraceSource, TraceVersion, UploadResult, Version } from "../../contracts/api";
import { api, listItems, NotFoundError, uploadDocumentWithProgress } from "../../lib/http";
import { date, excerpt, joined, text } from "../../lib/format";

const status = (value: string | null) => <span className={`status ${(value ?? "").includes("fail") ? "bad" : (value ?? "").includes("pending") || value === "queued" ? "warn" : ""}`}>{text(value, "未知")}</span>;
function ErrorView({ error }: { error: unknown }) { const apiError = error as { detail?: string; status?: number; requestId?: string }; return <div className="state error">无法加载数据。{apiError?.detail ?? (error instanceof Error ? error.message : "")}{apiError?.requestId ? `（request_id ${apiError.requestId}）` : ""}</div>; }
function Loading() { return <div className="state">正在加载…</div>; }

export function LibraryPage() {
  const [params, setParams] = useSearchParams(); const tab = params.get("tab") === "requirements" ? "requirements" : "documents"; const query = params.get("q") ?? "";
  const endpoint = tab === "documents" ? "/api/v1/documents?limit=50" : "/api/v1/requirements?limit=100";
  const result = useQuery({ queryKey: ["library", tab], queryFn: () => api<ListResponse<DocumentAsset> | ListResponse<Requirement>>(endpoint) });
  const source = listItems(result.data as ListResponse<DocumentAsset | Requirement> | undefined); const shown = source.filter((item) => !query || JSON.stringify(item).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const setTab = (next: "documents" | "requirements") => setParams((p) => { p.set("tab", next); p.delete("q"); return p; });
  return <section className="page"><header className="page-heading"><div><span className="eyebrow">共享资料库</span><h1>资料库</h1><p>文档与正式需求，统一查询</p></div></header><div className="tab-bar" role="tablist"><div className="tabs"><button className={tab === "documents" ? "selected" : ""} onClick={() => setTab("documents")}>文档</button><button className={tab === "requirements" ? "selected" : ""} onClick={() => setTab("requirements")}>正式需求</button></div>{tab === "documents" && <UploadButton />}</div><label className="search"><Search size={17} /><input value={query} onChange={(event) => setParams((p) => { event.target.value ? p.set("q", event.target.value) : p.delete("q"); return p; })} placeholder={tab === "documents" ? "搜索文件名、内容或关键字…" : "筛选已加载的 100 条正式需求…"} /></label><p className="window-note">搜索仅筛选当前已加载窗口（{source.length} 条），不是全库检索。</p>{result.isLoading ? <Loading /> : result.isError ? <ErrorView error={result.error} /> : tab === "documents" ? <Documents list={shown as DocumentAsset[]} /> : <Requirements list={shown as Requirement[]} />}</section>;
}

/** Upload = POST /api/v1/documents/upload (202). The response `status` is
 * "queued" or "ready" — success means the file was accepted. A "queued"
 * upload is explicitly reported as NOT yet indexed. Re-submission is blocked
 * while one upload is in flight. */
function UploadButton() {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<UploadResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ completed: number; total: number; resumed: boolean } | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => uploadDocumentWithProgress(file, (completed, total, resumed) => setProgress({ completed, total, resumed })),
    onMutate: () => { setResult(null); setFailure(null); setProgress(null); },
    onSuccess: (data) => { setResult(data); queryClient.invalidateQueries({ queryKey: ["library", "documents"] }); queryClient.invalidateQueries({ queryKey: ["recent-documents"] }); },
    onError: (error) => setFailure((error as { detail?: string }).detail ?? (error instanceof Error ? error.message : "上传失败")),
  });
  const busy = upload.isPending;
  return <div className="upload-block"><label className={`button primary upload ${busy ? "busy" : ""}`} aria-busy={busy}>{busy ? <Loader2 className="spin" size={16} /> : <Upload size={16} />}{busy ? "上传中…" : "上传资料"}<input type="file" accept=".md,.txt,.pdf,.doc,.docx" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file && !busy) upload.mutate(file); event.currentTarget.value = ""; }} /></label>{progress && <p className="upload-note warn"><Loader2 className="spin" size={14} />{progress.resumed ? "已续传" : "分段上传"}：{progress.completed}/{progress.total}</p>}{result && <p className={`upload-note ${result.status === "ready" ? "ok" : "warn"}`}>{result.status === "ready" ? <Check size={14} /> : <Loader2 className="spin" size={14} />}已上传「{result.document.file_name}」：{result.status === "ready" ? "已入库，索引就绪。" : "已接收排队（queued），索引尚未就绪，稍后在文档详情查看真实索引状态。"}</p>}{failure && <p className="upload-note bad"><AlertCircle size={14} />上传失败：{failure}</p>}</div>;
}

function Documents({ list }: { list: DocumentAsset[] }) { return <div className="data-table"><div className="table-row header"><span>名称</span><span>类型 / 来源</span><span>状态</span><span>更新时间</span></div>{list.map((doc) => <Link className="table-row" key={doc.id} to={`/library/documents/${encodeURIComponent(doc.id)}`}><span className="name"><FileText size={17} />{doc.file_name}</span><span>{doc.content_type} / {doc.source_type}</span><span>{status(doc.status)}</span><time>{date(doc.created_at)}</time></Link>)}{!list.length && <div className="state">没有匹配的文档。</div>}</div>; }

/** 所属模块 comes from the real `module_names` (active feature module_name).
 * An empty list is shown as an explicit 未提供 — business_domain is a
 * different field and is never substituted here. */
function Requirements({ list }: { list: Requirement[] }) { return <div className="data-table"><div className="table-row header requirements"><span>编号</span><span>标题</span><span>所属模块</span><span>当前版本</span><span>更新时间</span></div>{list.map((req) => <Link className="table-row requirements" key={req.requirement_key} to={`/library/requirements/${encodeURIComponent(req.requirement_key)}`}><span className="mono">{req.requirement_key}</span><span className="name">{req.requirement_name}</span><span className={req.module_names?.length ? "" : "muted-cell"}>{joined(req.module_names)}</span><span>v{req.current_version}</span><time>{date(req.latest_source_submitted_at)}</time></Link>)}{!list.length && <div className="state">没有匹配的正式需求。</div>}</div>; }

export function DocumentDetail() {
  const { documentId = "" } = useParams();
  const chunkLimit = 10;
  const [chunkOffset, setChunkOffset] = useState(0);
  const doc = useQuery({ queryKey: ["document", documentId], queryFn: () => api<DocumentAsset>(`/api/v1/documents/${encodeURIComponent(documentId)}`) });
  const chunks = useQuery({ queryKey: ["document-chunks", documentId, chunkOffset], queryFn: () => api<DocumentChunkPage>(`/api/v1/documents/${encodeURIComponent(documentId)}/chunks?limit=${chunkLimit}&offset=${chunkOffset}`) });
  const indexStatus = useQuery({ queryKey: ["document-index-status", documentId], queryFn: () => api<DocumentIndexStatus>(`/api/v1/documents/${encodeURIComponent(documentId)}/index-status`) });
  // The version stream is optional: seeded documents have none and the endpoint
  // answers 404. That is a valid state, not an error to crash on.
  const versions = useQuery({ queryKey: ["document-versions", documentId], queryFn: () => api<ListResponse<Version>>(`/api/v1/documents/${encodeURIComponent(documentId)}/versions`), retry: false });
  useEffect(() => { setChunkOffset(0); }, [documentId]);
  if (doc.isLoading) return <Loading />;
  if (doc.isError || !doc.data) return <ErrorView error={doc.error} />;
  const data = doc.data;
  const noVersionStream = versions.isError && versions.error instanceof NotFoundError;
  const page = chunks.data;
  const chunkRows = page?.items ?? [];
  return <section className="page detail"><Link className="back" to="/library?tab=documents">← 返回资料库</Link><header className="detail-heading"><FileText size={28} /><div><h1>{data.file_name}</h1><p>{data.content_type} · {status(data.status)}</p></div></header><div className="detail-grid"><article className="panel"><h2>文档信息</h2><dl><dt>来源</dt><dd>{data.source_type}</dd><dt>版本</dt><dd>{data.version_no == null ? "未提供" : `v${data.version_no}`}</dd><dt>更新时间</dt><dd>{date(data.created_at)}</dd><dt>大小</dt><dd>{data.size_bytes} B</dd></dl><DocumentIndexPanel query={indexStatus} /><a className="button primary" href={`/app/?document_id=${encodeURIComponent(documentId)}`}>基于此资料提问（旧版）</a><p className="hint">当前后端未提供资料范围检索契约，不能保证只查询本资料。</p></article><article className="panel content"><h2>内容预览</h2><p>{data.extracted_text ?? data.original_text ?? "该文档未提供可预览正文。"}</p><h3>数据库原始解析片段{page ? `（${page.total}）` : ""}</h3><p className="hint">以下正文逐字来自 document_chunk.chunk_text；未经过 AI 编辑、摘要或 Markdown 重排。</p>{chunks.isLoading ? <Loading /> : chunks.isError ? <ErrorView error={chunks.error} /> : <><ol className="chunks">{chunkRows.map((chunk) => <RawChunk key={chunk.id} chunk={chunk} />)}{!chunkRows.length && <li>该文档尚无已持久化分片。</li>}</ol>{page && page.total > chunkLimit && <div className="chunk-pagination"><button className="button" disabled={chunkOffset === 0} onClick={() => setChunkOffset((current) => Math.max(0, current - chunkLimit))}>上一页</button><span>{chunkOffset + 1}–{Math.min(chunkOffset + chunkRows.length, page.total)} / {page.total}</span><button className="button" disabled={chunkOffset + chunkLimit >= page.total} onClick={() => setChunkOffset((current) => current + chunkLimit)}>下一页</button></div>}</>}<h3>版本流</h3>{versions.isSuccess ? (listItems(versions.data).length ? <ol className="versions">{listItems(versions.data).map((v) => <li key={v.id}><strong>v{v.version_no}</strong><span>{date(v.created_at)}</span></li>)}</ol> : <p className="hint">该文档没有版本记录。</p>) : noVersionStream ? <p className="hint">该文档没有版本流（接口返回 404），无法展示历史版本。</p> : versions.isError ? <ErrorView error={versions.error} /> : <Loading />}</article></div></section>;
}

function RawChunk({ chunk }: { chunk: DocumentChunkPage["items"][number] }) {
  return <li className="raw-chunk"><div className="raw-chunk-head"><strong>Chunk {chunk.chunk_index}</strong><span>{chunk.char_count} 字符</span><span>{chunk.embedding_model ?? "embedding 未标记"}{chunk.embedding_dimension ? ` · ${chunk.embedding_dimension} 维` : ""}</span></div><pre>{chunk.chunk_text}</pre><div className="raw-chunk-foot"><span className="mono">{chunk.content_hash ? `sha256 ${chunk.content_hash.slice(0, 12)}…` : "hash 未提供"}</span><time>{date(chunk.created_at)}</time></div></li>;
}

function DocumentIndexPanel({ query }: { query: ReturnType<typeof useQuery<DocumentIndexStatus>> }) {
  if (query.isLoading) return <p className="hint">正在读取实际索引状态…</p>;
  if (query.isError || !query.data) return <p className="hint bad">索引状态暂时不可读取。</p>;
  const value = query.data;
  return <section className="document-index"><h3>索引状态</h3><p><span className={`status ${value.index_status === "failed" ? "bad" : value.index_status === "pending" || value.index_status === "processing" || value.index_status === "partial" ? "warn" : ""}`}>{value.index_status}</span></p><dl><dt>已持久化分片</dt><dd>{value.total_chunks}</dd><dt>已有向量</dt><dd>{value.embedded_chunks}</dd><dt>Embedding</dt><dd>{value.embedding_models.join("、") || "未标记"}{value.embedding_dimensions.length ? ` · ${value.embedding_dimensions.join("/")} 维` : ""}</dd><dt>切分参数</dt><dd>{value.chunking.chunk_size ? `${value.chunking.source} · ${value.chunking.chunk_size} / 重叠 ${value.chunking.overlap ?? "未提供"}` : "历史事件未记录"}</dd></dl>{value.outbox?.last_error && <p className="hint bad">最近索引错误：{value.outbox.last_error}</p>}<button className="button index-refresh" onClick={() => { void query.refetch(); }}>刷新索引状态</button></section>;
}

export function RequirementDetail() {
  const { requirementKey = "" } = useParams(); const [params, setParams] = useSearchParams(); const encoded = encodeURIComponent(requirementKey);
  const versions = useQuery({ queryKey: ["versions", requirementKey], queryFn: () => api<ListResponse<Version>>(`/api/v1/requirements/${encoded}/versions`) });
  const selected = Number(params.get("version")) || undefined;
  const features = useQuery({ queryKey: ["features", requirementKey, selected], queryFn: () => api<ListResponse<RequirementFeature>>(`/api/v1/requirements/${encoded}/features${selected ? `?at_version=${selected}` : ""}`) });
  const trace = useQuery({ queryKey: ["trace", requirementKey], queryFn: () => api<RequirementTrace>(`/api/v1/requirements/${encoded}/trace`) });
  const diff = useQuery({ queryKey: ["diff", requirementKey, selected], queryFn: () => api<RequirementDiff>(`/api/v1/requirements/${encoded}/diff${selected ? `?to_version=${selected}` : ""}`) });
  const head = trace.data?.requirement;
  return <section className="page detail"><Link className="back" to="/library?tab=requirements">← 返回资料库</Link><header className="detail-heading"><TableProperties size={28} /><div><span className="eyebrow">正式需求</span><h1>{head?.requirement_name ?? requirementKey}</h1><p><span className="mono">{requirementKey}</span> · 当前版本 v{head?.current_version ?? "未提供"} · 更新于 {date(head?.updated_at)}</p></div><a className="button primary" href={`/app/?requirement_key=${encoded}`}>基于此需求提问（旧版）</a></header><label className="version-picker">查看版本 <select value={selected ?? ""} onChange={(e) => setParams((p) => { e.target.value ? p.set("version", e.target.value) : p.delete("version"); return p; })}><option value="">当前版本</option>{listItems(versions.data).map((v) => <option key={v.id} value={v.version_no}>v{v.version_no} · {date(v.created_at)}</option>)}</select></label><div className="detail-grid requirement-detail"><article className="panel"><h2>功能与模块</h2>{features.isLoading ? <Loading /> : features.isError ? <ErrorView error={features.error} /> : <FeatureList rows={listItems(features.data)} />}</article><article className="panel"><h2><GitCompareArrows size={18} />版本与 Diff</h2><Diff data={diff.data} loading={diff.isLoading} error={diff.error} /></article><article className="panel"><h2><ExternalLink size={18} />来源证据</h2>{trace.isLoading ? <Loading /> : trace.isError ? <ErrorView error={trace.error} /> : <Trace trace={trace.data} />}</article></div></section>;
}

function FeatureList({ rows }: { rows: RequirementFeature[] }) { return rows.length ? <ul className="feature-list">{rows.map((row) => <li key={row.id}><div className="feature-title"><strong>{row.feature_key}</strong>{row.module_name && <span className="module-tag">{row.module_name}</span>}</div><p>{row.content}</p></li>)}</ul> : <div className="state">没有可用功能明细。</div>; }

/** Per version, list its real evidence sources: source type, submitter and a
 * truncated excerpt of the original text. Each links to the existing legacy
 * review surface with the real source_id, which opens the review drawer. */
function Trace({ trace }: { trace: RequirementTrace | undefined }) {
  if (!trace?.versions.length) return <div className="state">没有可公开的来源版本记录。</div>;
  return <ol className="versions trace-list">{trace.versions.map((version) => <TraceGroup key={version.version_id} version={version} />)}{" "}</ol>;
}
function TraceGroup({ version }: { version: TraceVersion }) {
  return <li><div className="trace-head"><strong>v{version.version_no}</strong><span>{text(version.change_type, "变更类型未提供")} · {date(version.created_at)}</span></div>{version.change_summary && <p className="trace-summary">{version.change_summary}</p>}{version.sources?.length ? <ul className="source-list">{version.sources.map((source) => <SourceRow key={source.source_id} source={source} />)}</ul> : <p className="hint">该版本没有关联来源。</p>}</li>;
}
function SourceRow({ source }: { source: TraceSource }) {
  const body = excerpt(source.original_text) || excerpt(source.extracted_text) || "来源未提供正文。";
  return <li className="source-row"><div className="source-meta"><span className="source-type">{text(source.source_type, "来源类型未提供")}</span><span>{text(source.requester_name, "提交人未提供")}</span><time>{date(source.submitted_at)}</time></div><p className="source-excerpt">{body}</p><a className="source-link" href={`/app/reviews?source_id=${encodeURIComponent(source.source_id)}`} target="_blank" rel="noreferrer">在旧版审核中查看来源 {source.source_id}<ExternalLink size={13} /></a></li>;
}

function Diff({ data, loading, error }: { data: RequirementDiff | undefined; loading: boolean; error: unknown }) { if (loading) return <Loading />; if (error) return <ErrorView error={error} />; if (!data) return <div className="state">没有版本差异。</div>; return <section className="diff"><p>v{data.from_version} → v{data.to_version}；未变更 {data.unchanged} 项</p><DiffGroup title="新增" tone="added" rows={data.added.map((x) => ({ key: x.feature_key, text: x.content }))} /><DiffGroup title="删除" tone="removed" rows={data.removed.map((x) => ({ key: x.feature_key, text: x.content }))} /><DiffGroup title="修改" tone="modified" rows={data.modified.map((x) => ({ key: x.feature_key, text: `${x.before} → ${x.after}` }))} />{!data.added.length && !data.removed.length && !data.modified.length && <p className="hint">两个版本之间没有功能变更。</p>}</section>; }
function DiffGroup({ title, tone, rows }: { title: string; tone: string; rows: Array<{ key: string; text: string }> }) { return rows.length ? <div className={`diff-group ${tone}`}><h3>{title}</h3>{rows.map((row) => <p key={row.key}><strong>{row.key}</strong> {row.text}</p>)}</div> : null; }
