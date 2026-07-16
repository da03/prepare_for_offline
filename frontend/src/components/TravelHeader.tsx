import { useRef } from "react";
import { Icon } from "./Icon";

export type PrimaryPage = "ask" | "prepare";

interface TravelHeaderProps {
  page: PrimaryPage;
  onPageChange: (page: PrimaryPage) => void;
  onOpenHistory: () => void;
  onOpenSettings: () => void;
}

export function TravelHeader({
  page,
  onPageChange,
  onOpenHistory,
  onOpenSettings,
}: TravelHeaderProps) {
  const menuRef = useRef<HTMLDetailsElement>(null);

  function choose(action: () => void) {
    menuRef.current?.removeAttribute("open");
    action();
  }

  return (
    <header className="app-header">
      <div className="brand-lockup" aria-label="PAW">
        <img
          src="/favicon.svg"
          width="30"
          height="30"
          alt=""
          onError={(event) => {
            event.currentTarget.hidden = true;
          }}
        />
        <span className="brand-name">PAW</span>
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
      <details className="overflow-menu" ref={menuRef}>
        <summary aria-label="Open app menu">
          <Icon name="more" />
        </summary>
        <div className="overflow-popover" role="menu">
          <button type="button" role="menuitem" onClick={() => choose(onOpenHistory)}>
            <Icon name="history" size={18} />
            History
          </button>
          <button type="button" role="menuitem" onClick={() => choose(onOpenSettings)}>
            <Icon name="settings" size={18} />
            Settings
          </button>
        </div>
      </details>
    </header>
  );
}
