/** 输入区。
 *
 * 两个真实约束决定了这里的交互：
 *  1. **走 multipart 端点时没有 mode / web_policy**（后端 `stream-with-files` 的参数
 *     就那六个）。所以一带附件就把这两项**禁用并置灰**，而不是静默丢弃 —— 让用户
 *     看见它不可用，比让他以为设置了更诚实。
 *  2. **没有任何文档范围参数**（两个端点都没有 document_ids/scope）。所以不做文档
 *     选择器，只给一行提示。
 */
import { CornerDownLeft, Globe, Paperclip, Send, Square, X } from "lucide-react";
import { useRef, useState } from "react";
import type { AnalysisMode, ChatMode, SourceType, WebPolicy } from "../../contracts/api";

export interface ComposerValue { mode: ChatMode; webPolicy: WebPolicy; analysisMode: AnalysisMode; sourceType: SourceType; }

const MODE_LABEL: Record<ChatMode, string> = { auto: "自动判断", ask_library: "需求库问答", draft_requirement: "整理需求", research: "联网研究" };
const WEB_LABEL: Record<WebPolicy, string> = { off: "联网关闭", auto: "联网自动", on: "联网开启" };
const ANALYSIS_LABEL: Record<AnalysisMode, string> = { strict: "严格", balanced: "均衡", broad: "宽松" };
const SOURCE_LABEL: Record<SourceType, string> = { web: "网页", email: "邮件", meeting: "会议", manual: "手工" };

export function Composer({ value, onChange, onSubmit, onStop, busy, streaming, hint }: {
  value: ComposerValue;
  onChange: (next: ComposerValue) => void;
  onSubmit: (text: string, files: File[]) => void;
  onStop: () => void;
  busy: boolean;
  streaming: boolean;
  hint?: string;
}) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const composingRef = useRef(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const hasFiles = files.length > 0;

  const send = () => {
    if (busy) return;
    if (!text.trim() && !files.length) return;
    onSubmit(text, files);
    setText("");
    setFiles([]);
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法组合期间的回车不能当发送 —— isComposing 是唯一可靠的判据。
    if (event.key !== "Enter" || event.shiftKey || composingRef.current || event.nativeEvent.isComposing) return;
    event.preventDefault();
    send();
  };

  return (
    <div className="composer">
      {hasFiles && (
        <ul className="composer-files">
          {files.map((file, index) => (
            <li key={`${file.name}-${index}`}>
              <Paperclip size={13} />
              <span>{file.name}</span>
              <button aria-label={`移除 ${file.name}`} onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}><X size={13} /></button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        value={text} rows={1} placeholder="向需求库提问，或整理新的需求…"
        onChange={(event) => setText(event.target.value)} onKeyDown={onKeyDown}
        onCompositionStart={() => { composingRef.current = true; }}
        onCompositionEnd={() => { composingRef.current = false; }}
      />
      <div className="composer-bar">
        <input ref={fileRef} type="file" hidden multiple accept=".pdf,.doc,.docx,.txt,.md" onChange={(event) => { setFiles((current) => [...current, ...Array.from(event.target.files ?? [])]); event.currentTarget.value = ""; }} />
        <button className="composer-icon" aria-label="添加附件" onClick={() => fileRef.current?.click()} disabled={busy}><Paperclip size={16} /></button>

        <label className={`composer-select ${hasFiles ? "disabled" : ""}`} title={hasFiles ? "带附件时不支持模式与联网设置（后端该端点没有这两个参数）" : undefined}>
          <select value={value.mode} disabled={hasFiles || busy} onChange={(e) => onChange({ ...value, mode: e.target.value as ChatMode })}>
            {Object.entries(MODE_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        <label className={`composer-select ${hasFiles ? "disabled" : ""}`} title={hasFiles ? "带附件时不支持联网设置" : undefined}>
          <Globe size={14} />
          <select value={value.webPolicy} disabled={hasFiles || busy} onChange={(e) => onChange({ ...value, webPolicy: e.target.value as WebPolicy })}>
            {Object.entries(WEB_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        <label className="composer-select">
          <select value={value.analysisMode} disabled={busy} onChange={(e) => onChange({ ...value, analysisMode: e.target.value as AnalysisMode })}>
            {Object.entries(ANALYSIS_LABEL).map(([k, label]) => <option key={k} value={k}>召回{label}</option>)}
          </select>
        </label>
        <label className={`composer-select ${hasFiles ? "disabled" : ""}`} title={hasFiles ? "带附件时来源固定为网页" : undefined}>
          <select value={value.sourceType} disabled={hasFiles || busy} onChange={(e) => onChange({ ...value, sourceType: e.target.value as SourceType })}>
            {Object.entries(SOURCE_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>

        <span className="composer-spacer" />
        {streaming
          ? <button className="composer-send stop" aria-label="停止" onClick={onStop}><Square size={15} /></button>
          : <button className="composer-send" aria-label="发送" onClick={send} disabled={busy || (!text.trim() && !hasFiles)}><Send size={15} /></button>}
      </div>
      <p className="composer-hint">{hint ?? "Enter 发送，Shift+Enter 换行。要限定范围，请在问题里直接写上 REQ 编号或文件名。"}</p>
    </div>
  );
}
