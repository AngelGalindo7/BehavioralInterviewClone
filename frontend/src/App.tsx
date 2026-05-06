import AdminPage from "./components/AdminPage";
import InterviewPage from "./components/InterviewPage";
import LoginGate from "./components/LoginGate";

export default function App() {
  return (
    <LoginGate>
      <Router />
    </LoginGate>
  );
}

function Router() {
  const path = typeof window !== "undefined" ? window.location.pathname : "/";
  if (path.startsWith("/admin")) return <AdminPage />;
  return <InterviewPage />;
}
