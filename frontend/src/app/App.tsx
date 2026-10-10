import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { LibraryPage, DocumentDetail, RequirementDetail } from "../features/library/LibraryPages";
import { ChatPage } from "../features/chat/ChatPage";
import { ReviewsListPage, ReviewDetailPage } from "../features/reviews/ReviewPages";
import { RunsListPage, RunDetailPage } from "../features/runs/RunPages";

export function App() {
  return <Routes><Route element={<AppShell />}>
    <Route index element={<ChatPage />} />
    <Route path="chat/:sessionId" element={<ChatPage />} />
    <Route path="library" element={<LibraryPage />} />
    <Route path="library/documents/:documentId" element={<DocumentDetail />} />
    <Route path="library/requirements/:requirementKey" element={<RequirementDetail />} />
    <Route path="reviews" element={<ReviewsListPage />} />
    <Route path="reviews/:sourceId" element={<ReviewDetailPage />} />
    <Route path="runs" element={<RunsListPage />} />
    <Route path="runs/:runId" element={<RunDetailPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes>;
}
