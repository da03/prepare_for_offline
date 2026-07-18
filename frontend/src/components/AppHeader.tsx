import { Icon } from "./Icon";

export type PrimaryPage = "ask" | "prepare";

interface AppHeaderProps {
  page: PrimaryPage;
  onPageChange: (page: PrimaryPage) => void;
  onOpenHistory: () => void;
}

export function AppHeader({
  page,
  onPageChange,
  onOpenHistory,
}: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__row">
        <div className="brand-lockup" aria-label="Prepare for Offline">
          <img src="/favicon.svg" width="30" height="30" alt="" />
          <span className="brand-name">Prepare for Offline</span>
        </div>

        <nav className="primary-nav" aria-label="Primary">
          {(["ask", "prepare"] as PrimaryPage[]).map((item) => (
            <button
              key={item}
              className={page === item ? "is-active" : ""}
              type="button"
              aria-current={page === item ? "page" : undefined}
              onClick={() => onPageChange(item)}
            >
              {item === "ask" ? "Ask" : "Prepare"}
            </button>
          ))}
        </nav>

        <button
          className="icon-button"
          type="button"
          aria-label="History"
          onClick={onOpenHistory}
        >
          <Icon name="history" size={19} />
        </button>
      </div>
    </header>
  );
}
