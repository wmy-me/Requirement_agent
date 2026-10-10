import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { LibraryPage, DocumentDetail, RequirementDetail } from "../features/library/LibraryPages";
import { ChatPage } from "../features/chat/ChatPage";
import { Placeholder } from "../components/Placeholder";

export function App() {
  return <Routes><Route element={<AppShell />}>
    <Route index element={<ChatPage />} />
    <Route path="chat/:sessionId" element={<ChatPage />} />
    <Route path="library" element={<LibraryPage />} />
    <Route path="library/documents/:documentId" element={<DocumentDetail />} />
    <Route path="library/requirements/:requirementKey" element={<RequirementDetail />} />
    <Route path="reviews/*" element={<Placeholder title="待审核" description="审核仍使用已验证的旧页面，避免改变裁决流程。" action="打开旧版审核" href="/app/reviews.html" />} />
    <Route path="runs/*" element={<Placeholder title="任务记录" description="任务恢复与实时进度仍在后端演进，暂保留旧入口。" action="打开旧版任务记录" href="/app/ops.html" />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes>;
}
