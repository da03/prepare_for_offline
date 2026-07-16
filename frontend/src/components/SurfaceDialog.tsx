import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { Icon } from "./Icon";

type DialogVariant = "drawer" | "sheet" | "modal";

interface SurfaceDialogProps {
  title: string;
  description?: string;
  variant: DialogVariant;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

export function SurfaceDialog({
  title,
  description,
  variant,
  onClose,
  children,
  footer,
}: SurfaceDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const panel = panelRef.current;
    const backdrop = panel?.parentElement;
    const backgroundSiblings = Array.from(
      backdrop?.parentElement?.children ?? [],
    ).filter((element): element is HTMLElement => {
      return element instanceof HTMLElement && element !== backdrop;
    });
    const siblingState = backgroundSiblings.map((element) => ({
      element,
      hadInert: element.hasAttribute("inert"),
      ariaHidden: element.getAttribute("aria-hidden"),
    }));
    for (const element of backgroundSiblings) {
      element.setAttribute("inert", "");
      element.setAttribute("aria-hidden", "true");
    }
    const initial =
      panel?.querySelector<HTMLElement>("[data-autofocus]") ??
      panel?.querySelector<HTMLElement>("button, input, select, textarea, [tabindex]");
    initial?.focus();

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = previousOverflow;
      for (const state of siblingState) {
        if (!state.hadInert) state.element.removeAttribute("inert");
        if (state.ariaHidden === null) state.element.removeAttribute("aria-hidden");
        else state.element.setAttribute("aria-hidden", state.ariaHidden);
      }
      previousFocus?.focus();
    };
  }, [onClose]);

  function keepFocusInside(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Tab") return;
    const controls = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className={`dialog-backdrop dialog-backdrop--${variant}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className={`dialog-panel dialog-panel--${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        onKeyDown={keepFocusInside}
      >
        <header className="dialog-header">
          <div className="dialog-title-group">
            <h2 id={titleId}>{title}</h2>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label={`Close ${title}`}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="dialog-content">{children}</div>
        {footer ? <footer className="dialog-footer">{footer}</footer> : null}
      </div>
    </div>
  );
}
