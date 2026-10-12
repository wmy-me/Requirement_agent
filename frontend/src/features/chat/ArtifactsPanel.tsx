/** 结构化结果卡片。
 *
 * 为什么**按存在的键**判别而不是按声明类型：后端的 `artifacts` 是一个自由 dict，
 * 形状随路径变（助手路径 / 分析管线 / 闲聊 / 被拦截）。旧版 `static/app/js/pages/
 * chat.js` 的 `renderArtifacts` 用 `Array.isArray(artifacts)` 判断，而后端发的是
 * **对象** —— 那个判断永远为假，所以结构化结果**从来没有渲染过**。这里按真实形状来。
 * 真实字段：evidence = {evidence_id, source_type, source_ref, title, excerpt, content_hash}。
 */
import { AlertCircle, AlertTriangle, ExternalLink, FileText, Link2 } from "lucide-react";
import type { ChatArtifacts } from "../../contracts/api";

const asArray = (value: unknown): Array<Record<string, unknown>> =>
  Array.isArray(value) ? value.filter((x): x is Record<string, unknown> => !!x && typeof x === "object") : [];

const RISK_KEYS: Array<[string, string]> = [["quality_risk", "质量风险"], ["change_risk", "变更风险"], ["technical_impact_risk", "技术风险"]];
const LEVEL_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低" };

function RiskBadges({ risk }: { risk: Record<string, unknown> }) {
  return (
    <div className="risk-row">
      {RISK_KEYS.map(([key, label]) => {
        const level = risk[key];
        if (typeof level !== "string" || !level) return null;
        const tone = level === "high" ? "bad" : level === "medium" ? "warn" : "ok";
        return <span key={key} className={`status ${tone === "ok" ? "" : tone}`}>{label} {LEVEL_LABEL[level] ?? level}</span>;
      })}
      {typeof risk.confidence === "number" && <span className="risk-confidence">置信度 {risk.confidence.toFixed(2)}</span>}
    </div>
  );
}

function EvidenceCards({ items }: { items: Array<Record<string, unknown>> }) {
  if (!items.length) return null;
  return (
    <details className="evidence">
      <summary><span>相关资料来源（{items.length}）</span><span className="evidence-disclosure">展开来源</span></summary>
      <ul className="source-list">
        {items.map((item, index) => {
          const ref = typeof item.source_ref === "string" ? item.source_ref : "";
          const external = /^https?:/i.test(ref);
          const title = typeof item.title === "string" && item.title ? item.title : external ? sourceHost(ref) : "未命名来源";
          return (
            <li key={String(item.evidence_id ?? index)}>
              <details className="source-row">
                <summary><span className="source-type">{String(item.source_type ?? "来源")}</span><span className="evidence-source-title">{title}</span>{external && <span className="evidence-host">{sourceHost(ref)}</span>}</summary>
                {typeof item.excerpt === "string" && item.excerpt && <p className="source-excerpt">{item.excerpt}</p>}
                {ref && (external
                  ? <a className="source-link" href={ref} target="_blank" rel="noopener noreferrer nofollow">打开来源<ExternalLink size={13} /></a>
                  : <span className="source-link static"><Link2 size={13} />{ref}</span>)}
              </details>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function sourceHost(ref: string): string {
  try { return new URL(ref).hostname.replace(/^www\./i, ""); } catch { return ref; }
}

export function ArtifactsPanel({ artifacts }: { artifacts: ChatArtifacts | null }) {
  if (!artifacts || !Object.keys(artifacts).length) return null;
  const has = (...keys: string[]) => keys.some((k) => k in artifacts);

  // 闲聊 / 被拦截：不渲染成卡片，被拦截的给一句明确说明。
  if (has("blocked")) {
    return <div className="artifact-note"><AlertCircle size={15} />正式需求的写入只能走人工审核，这里不会替你创建需求。</div>;
  }
  if (!has("extracted", "candidates", "analysis", "risk", "sources", "tool_calls", "incomplete_reason", "steps")) {
    return null;
  }

  const risk = (artifacts.risk && typeof artifacts.risk === "object" ? artifacts.risk : {}) as Record<string, unknown>;
  const analysis = (artifacts.analysis && typeof artifacts.analysis === "object" ? artifacts.analysis : {}) as Record<string, unknown>;
  const extracted = (artifacts.extracted && typeof artifacts.extracted === "object" ? artifacts.extracted : {}) as Record<string, unknown>;
  const sources = asArray(artifacts.sources);
  const candidates = asArray(artifacts.candidates);
  const toolCalls = asArray(artifacts.tool_calls);
  const incompleteReason = typeof artifacts.incomplete_reason === "string" ? artifacts.incomplete_reason : null;

  return (
    <div className="artifacts">
      {incompleteReason && <div className="artifact-note warn"><AlertTriangle size={15} />{incompleteReason}</div>}

      {Boolean(extracted.requirement_title || extracted.summary) && (
        <div className="artifact-block">
          <h4>结构化需求</h4>
          {typeof extracted.requirement_title === "string" && <p className="artifact-strong">{extracted.requirement_title}</p>}
          {typeof extracted.summary === "string" && <p>{extracted.summary}</p>}
        </div>
      )}

      {candidates.length > 0 && (
        <div className="artifact-block">
          <h4>相似与冲突候选（{candidates.length}）</h4>
          <ul className="candidate-list">
            {candidates.map((item, index) => (
              <li key={String(item.requirement_key ?? index)}>
                <FileText size={14} />
                <span className="mono">{String(item.requirement_key ?? "")}</span>
                <span>{String(item.requirement_name ?? "")}</span>
                {typeof item.level === "string" && <span className="status">{item.level === "duplicate" ? "疑似重复" : item.level === "related" ? "相关" : String(item.level)}</span>}
                {typeof item.score === "number" && <em>{item.score.toFixed(2)}</em>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {Object.keys(risk).length > 0 && <div className="artifact-block"><h4>风险评估</h4><RiskBadges risk={risk} /></div>}
      {typeof analysis.reasoning === "string" && analysis.reasoning && <div className="artifact-block"><h4>分析理由</h4><p>{analysis.reasoning}</p></div>}

      <EvidenceCards items={sources} />

      {toolCalls.length > 0 && (
        <div className="artifact-block">
          <h4>本轮实际工具调用（{toolCalls.length}）</h4>
          <ul className="tool-list">
            {toolCalls.map((call, index) => <li key={index}><span className="mono">{String(call.tool ?? call.name ?? "tool")}</span><em>{String(call.status ?? "")}</em></li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
