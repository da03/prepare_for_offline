import { useEffect, useState, type FormEvent } from "react";
import type {
  AppSettings,
  SearchProviderStatus,
  SettingsUpdate,
  Theme,
} from "../lib/api";
import { SurfaceDialog } from "./SurfaceDialog";

interface PreferencesSheetProps {
  settings: AppSettings;
  onClose: () => void;
  onSave: (update: SettingsUpdate) => Promise<void>;
  searchStatus: SearchProviderStatus;
  onSaveSearchKey: (key: string) => Promise<void>;
  onDeleteSearchKey: () => Promise<void>;
}

export function PreferencesSheet({
  settings,
  searchStatus,
  onClose,
  onSave,
  onSaveSearchKey,
  onDeleteSearchKey,
}: PreferencesSheetProps) {
  const [theme, setTheme] = useState<Theme>(settings.theme);
  const [searchMode, setSearchMode] = useState(
    settings.search_mode ?? "automatic",
  );
  const [optimize, setOptimize] = useState(
    settings.optimize_in_background ?? true,
  );
  const [saving, setSaving] = useState(false);
  const [searchKey, setSearchKey] = useState("");
  const [searchBusy, setSearchBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTheme(settings.theme);
    setSearchMode(settings.search_mode ?? "automatic");
    setOptimize(settings.optimize_in_background ?? true);
  }, [settings]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave({
        theme,
        search_mode: searchMode,
        optimize_in_background: optimize,
      });
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save settings.");
    } finally {
      setSaving(false);
    }
  }

  async function connectSearch() {
    if (searchKey.trim().length < 10) {
      setError("Enter a valid Brave Search API key.");
      return;
    }
    setSearchBusy(true);
    setError(null);
    try {
      await onSaveSearchKey(searchKey.trim());
      setSearchKey("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save search key.");
    } finally {
      setSearchBusy(false);
    }
  }

  return (
    <SurfaceDialog
      title="Settings"
      description="A few preferences for your travel assistant."
      variant="sheet"
      onClose={onClose}
      footer={
        <button
          className="button button--primary button--full"
          type="submit"
          form="settings-form"
          disabled={saving}
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
      }
    >
      <form id="settings-form" className="settings-form" onSubmit={save}>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <section className="settings-section">
          <span className="eyebrow">Appearance</span>
          <h3>Theme</h3>
          <fieldset className="segmented-control">
            <legend className="sr-only">Theme</legend>
            {(["system", "light", "dark"] as Theme[]).map((option) => (
              <label key={option}>
                <input
                  type="radio"
                  name="theme"
                  checked={theme === option}
                  onChange={() => setTheme(option)}
                />
                <span>{option[0].toUpperCase() + option.slice(1)}</span>
              </label>
            ))}
          </fieldset>
        </section>
        <section className="settings-section">
          <span className="eyebrow">Preparation</span>
          <h3>Trip discovery</h3>
          <label className="field">
            <span>Find current public trip information</span>
            <select
              value={searchMode}
              onChange={(event) =>
                setSearchMode(
                  event.target.value as
                    | "automatic"
                    | "official_only"
                    | "off",
                )
              }
            >
              <option value="automatic">Automatic (official first)</option>
              <option value="official_only">Official sources only</option>
              <option value="off">Off</option>
            </select>
            <small>
              Personal attachments never become search queries. Prepared pages
              are saved locally with freshness dates.
            </small>
          </label>
          <details className="advanced-settings">
            <summary>Advanced</summary>
            <label className="toggle-row">
              <span>
                <strong>Improve accuracy in the background</strong>
                <small>
                  Become ready with the fast program, then promote a tested
                  finetuned version.
                </small>
              </span>
              <input
                type="checkbox"
                role="switch"
                checked={optimize}
                onChange={(event) => setOptimize(event.target.checked)}
              />
            </label>
            <div className="field">
              <span>Brave Search API</span>
              <small>
                {searchStatus.configured
                  ? searchStatus.managed_by_environment
                    ? "Connected by the app environment"
                    : "Connected on this Mac"
                  : "Not connected; Prepare will use attachments and saved sources"}
              </small>
              {!searchStatus.managed_by_environment ? (
                <div className="inline-settings-action">
                  <input
                    type="password"
                    value={searchKey}
                    autoComplete="off"
                    placeholder="Search API key"
                    onChange={(event) => setSearchKey(event.target.value)}
                  />
                  <button
                    className="button button--secondary button--compact"
                    type="button"
                    disabled={searchBusy || (!searchKey.trim() && !searchStatus.configured)}
                    onClick={() => {
                      if (searchStatus.configured && !searchKey.trim()) {
                        void onDeleteSearchKey();
                      } else {
                        void connectSearch();
                      }
                    }}
                  >
                    {searchBusy
                      ? "Saving…"
                      : searchStatus.configured && !searchKey.trim()
                        ? "Disconnect"
                        : "Connect"}
                  </button>
                </div>
              ) : null}
            </div>
          </details>
        </section>
      </form>
    </SurfaceDialog>
  );
}
