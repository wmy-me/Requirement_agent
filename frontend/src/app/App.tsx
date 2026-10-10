import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { LibraryPage, DocumentDetail, RequirementDetail } from "../features/library/LibraryPages";
import { ChatPage } from "../features/chat/ChatPage";
import { ReviewsListPage, ReviewDetailPage } from "../features/reviews/ReviewPages";
import { Placeholder } from "../components/Placeholder";

export function App() {
  return <Routes><Route element={<AppShell />}>
    <Route index element={<ChatPage />} />
    <Route path="chat/:sessionId" element={<ChatPage />} />
    <Route path="library" element={<LibraryPage />} />
    <Route path="library/documents/:documentId" element={<DocumentDetail />} />
    <Route path="library/requirements/:requirementKey" element={<RequirementDetail />} />
    <Route path="reviews" element={<ReviewsListPage />} />
    <Route path="reviews/:sourceId" element={<ReviewDetailPage />} />
    <Route path="runs/*" element={<Placeholder title="任务记录" description="任务恢复与实时进度仍在后端演进，暂保留旧入口。" action="打开旧版任务记录" href="/app/ops.html" />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes>;
}
