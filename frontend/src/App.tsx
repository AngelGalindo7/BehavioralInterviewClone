import InterviewPage from "./components/InterviewPage";
import LoginGate from "./components/LoginGate";

// AdminPage and /admin route removed — stories are managed via data/stories.md.
// AdminPage.tsx + adminApi.ts retained on disk for RAG re-adoption reference.
// See: docs/DECISION_LOG.md — 05/05/2026

export default function App() {
  return (
    <LoginGate>
      <InterviewPage />
    </LoginGate>
  );
}
