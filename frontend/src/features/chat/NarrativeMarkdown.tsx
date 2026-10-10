/** 正文 Markdown 渲染（懒加载，避免撑大首屏 chunk）。
 *
 * 安全约束（方案 §7）：「Markdown 默认禁止原始 HTML，外链安全，不能使用不受控的
 * dangerouslySetInnerHTML」。
 *  - `react-markdown` 输出的是 **React 元素**，全程没有 innerHTML，从根上没有注入面。
 *  - **不挂 `rehype-raw`**，所以原文里的 HTML 会被当作纯文本转义显示，而不是执行。
 *  - 链接走白名单 `urlTransform`，并且覆写 `a` 加 `target="_blank"` + `rel="noopener
 *    noreferrer nofollow"`。
 */
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SAFE_PROTOCOL = /^(https?:|mailto:)/i;

export default function NarrativeMarkdown({ text }: { text: string }) {
  return (
    <div className="md">
      <Markdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (SAFE_PROTOCOL.test(url) || url.startsWith("/") || url.startsWith("#") ? url : "")}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>
          ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
