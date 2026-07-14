import { NavLink, Route, Routes } from "react-router-dom";
import ChatPage from "./pages/ChatPage";
import PreparePage from "./pages/PreparePage";
import PacksPage from "./pages/PacksPage";
import QueuePage from "./pages/QueuePage";

function Nav() {
  const base =
    "px-3 py-1.5 rounded-md text-sm font-medium transition-colors";
  const cls = ({ isActive }: { isActive: boolean }) =>
    isActive
      ? `${base} bg-ink text-white`
      : `${base} text-slate-600 hover:bg-slate-200`;
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">✈️</span>
          <span className="font-semibold">Prepare for Offline</span>
        </div>
        <nav className="flex gap-1">
          <NavLink to="/" end className={cls}>
            Ask
          </NavLink>
          <NavLink to="/prepare" className={cls}>
            Prepare
          </NavLink>
          <NavLink to="/packs" className={cls}>
            Packs
          </NavLink>
          <NavLink to="/queue" className={cls}>
            Verify
          </NavLink>
        </nav>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <div className="min-h-full">
      <Nav />
      <main className="mx-auto max-w-3xl px-4 py-6">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/prepare" element={<PreparePage />} />
          <Route path="/packs" element={<PacksPage />} />
          <Route path="/queue" element={<QueuePage />} />
        </Routes>
      </main>
    </div>
  );
}
