/** 对话页（方案 §4.1）。
 *
 * 阅读区居中、约 800px；用户轮是右侧浅灰块，助手轮是**排版正文**（不做气泡），
 * 证据与执行摘要挂在答案下方。与参考图的对话屏一致。
 */
import { AlertTriangle, Ban, CircleSlash, PauseCircle, RotateCcw, Sparkles } from "lucide-react";
import { Suspense, lazy, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { StepTimeline } from "./StepTimeline";
import { ArtifactsPanel } from "./ArtifactsPanel";
import { Composer, type ComposerValue } from "./Composer";
import { useChatStream } from "./useChatStream";
import type { ChatMessage } from "./reducer";

const NarrativeMarkdown = lazy(() => import("./NarrativeMarkdown"));

const EXAMPLES = ["订单模块有哪些需求？", "登录功能最近有哪些变化？", "哪些需求涉及导出 Excel？"];

/** 只标这一轮的状态。原因由下方的横幅统一说明 —— 两处都写整句会读成重复。 */
function SeverityNotice({ message }: { message: ChatMessage }) {
  const status = message.doneStatus;
  if (status === "incomplete") return <p className="turn-notice warn"><AlertTriangle size={14} />未完成（预算或超时耗尽）</p>;
  if (status === "failed") return <p className="turn-notice bad"><Ban size={14} />执行失败</p>;
  if (status === "cancelled") return <p className="turn-notice"><CircleSlash size={14} />已停止</p>;
  if (status === "paused") return <p className="turn-notice"><PauseCircle size={14} />已暂停</p>;
  if (message.severity === "bad") return <p className="turn-notice bad"><Ban size={14} />未正常结束</p>;
  return null;
}

export function ChatPage() {
  const { sessionId: routeSession = "" } = useParams();
  const navigate = useNavigate();
  const chat = useChatStream();
  const { state } = chat;
  const [options, setOptions] = useState<ComposerValue>({ mode: "auto", webPolicy: "off", analysisMode: "strict", sourceType: "web" });

  // 深链接 /chat/:sessionId 加载历史；失败就留在空态并说明，不假装有内容。
  useEffect(() => {
    if (!routeSession) return;
    let alive = true;
    chat.loadHistory(routeSession).then(() => { if (alive) chat.loadResumable(routeSession); }).catch(() => { if (alive) chat.dispatch({ type: "TRANSPORT_FAILED", message: "无法读取该会话的历史。" }); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSession]);

  const submit = (text: string, files: File[]) => {
    chat.submit(text, files, options);
    // 新会话拿到 session_id 后把 URL 补上，刷新/分享才有意义。
    if (!state.sessionId && !routeSession) navigate(".", { replace: false });
  };

  const messages = state.messages;
  const live = state.phase === "sending" || state.phase === "streaming";

  return (
    <section className="chat-page">
      <div className="chat-scroll">
        <div className="chat-column">
          {messages.length === 0 && state.phase !== "busy" && (
            <div className="chat-welcome">
              <h1>你想了解哪些需求？</h1>
              <p>基于资料库里的文档与已整理的需求来回答，不会替你创建正式需求。</p>
              <div className="chat-examples">
                {EXAMPLES.map((example) => (
                  <button key={example} onClick={() => sendExample(example)}>{example}</button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) => (
            message.role === "user"
              ? (
                <div className="turn user" key={message.localId}>
                  <div className="turn-user-block">
                    {message.content}
                    {!!message.attachments?.length && <span className="turn-attachments">{message.attachments.map((a) => a.name).join("、")}</span>}
                  </div>
                </div>
              )
              : (
                <div className="turn assistant" key={message.localId}>
                  <div className="turn-avatar"><Sparkles size={15} /></div>
                  <div className="turn-body">
                    {message.content
                      ? <Suspense fallback={<p className="md-fallback">{message.content}</p>}><NarrativeMarkdown text={message.content} /></Suspense>
                      : live && <p className="turn-thinking">正在处理…</p>}
                    <StepTimeline steps={message.steps} live={live && message === messages[messages.length - 1]} />
                    <ArtifactsPanel artifacts={message.artifacts} />
                    {!live && <SeverityNotice message={message} />}
                  </div>
                </div>
              )
          ))}

          {state.phase === "busy" && (
            <div className="chat-banner bad">
              <AlertTriangle size={15} />
              <div>
                <strong>这个对话还有一个在跑的任务</strong>
                <p>{state.busy?.message ?? ""}{state.busy?.activeRun ? `（${state.busy.activeRun.status}）` : ""}先等它结束，或到任务记录里停掉它。</p>
              </div>
              <Link className="button" to="/runs">去任务记录</Link>
            </div>
          )}
          {state.phase === "error" && (
            <div className="chat-banner bad">
              <AlertTriangle size={15} />
              <div><strong>本轮没有完成</strong><p>{state.errorMessage ?? "未知原因。"}</p></div>
              <button className="button" onClick={() => chat.dispatch({ type: "CLEAR_NOTICE" })}>知道了</button>
            </div>
          )}
          {state.phase === "clarifying" && (
            <div className="chat-banner warn">
              <AlertTriangle size={15} />
              <div><strong>需要你补充信息</strong><p>直接在下一条消息里回答即可 —— 后端会自动把补充并进这一轮。</p></div>
            </div>
          )}
          {state.phase === "paused" && state.runId && (
            <div className="chat-banner">
              <PauseCircle size={15} />
              <div><strong>已暂停</strong><p>可以从断点继续，继续会沿用原来的时间预算。</p></div>
              <button className="button primary" onClick={() => chat.resume(state.runId as string)}><RotateCcw size={14} />继续</button>
            </div>
          )}
        </div>
      </div>

      <div className="chat-footer">
        <div className="chat-column">
          {state.sessionId && <p className="chat-session">会话 {state.sessionId}</p>}
          <Composer value={options} onChange={setOptions} onSubmit={submit} onStop={chat.stop} busy={state.phase === "sending"} streaming={live || state.phase === "pausing"} />
        </div>
      </div>
    </section>
  );

  function sendExample(text: string) {
    chat.submit(text, [], options);
  }
}
