import { X } from "lucide-react";
import { FormEvent, useState } from "react";
import type { Home2Config } from "../lib/home-core/config";

type Props = {
  config: Home2Config;
  open: boolean;
  onClose: () => void;
  onSave: (config: Home2Config) => void;
};

export function SettingsPanel({ config, open, onClose, onSave }: Props) {
  const [draft, setDraft] = useState(config);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSave(draft);
  };

  return (
    <section className={`settings-panel ${open ? "is-open" : ""}`} aria-label="Settings">
      <form onSubmit={submit}>
        <div className="drawer-titlebar">
          <div>
            <div className="drawer-title">settings</div>
            <div className="connection-label is-online">
              <span />
              home2.config.v1
            </div>
          </div>
          <button className="icon-button" type="button" aria-label="Close settings" title="Close settings" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="settings-grid">
          <Field label="Home Assistant" value={draft.haBaseUrl} onChange={(value) => setDraft({ ...draft, haBaseUrl: value })} />
          <Field label="HA token" value={draft.haToken} secret onChange={(value) => setDraft({ ...draft, haToken: value })} />
          <Field label="Metrics" value={draft.metricsBaseUrl} onChange={(value) => setDraft({ ...draft, metricsBaseUrl: value })} />
          <Field label="Intelligence" value={draft.intelligenceBaseUrl} onChange={(value) => setDraft({ ...draft, intelligenceBaseUrl: value })} />
          <Field label="Tracker" value={draft.trackerBaseUrl} onChange={(value) => setDraft({ ...draft, trackerBaseUrl: value })} />
          <Field label="Supervisor" value={draft.supervisorBaseUrl} onChange={(value) => setDraft({ ...draft, supervisorBaseUrl: value })} />
          <Field label="Supervisor token" value={draft.supervisorToken} secret onChange={(value) => setDraft({ ...draft, supervisorToken: value })} />
          <Field label="Video labeler" value={draft.videoLabelerBaseUrl} onChange={(value) => setDraft({ ...draft, videoLabelerBaseUrl: value })} />
          <Field label="Vision" value={draft.visionBaseUrl} onChange={(value) => setDraft({ ...draft, visionBaseUrl: value })} />
          <Field label="Apartment assets" value={draft.apartmentAssetBase} onChange={(value) => setDraft({ ...draft, apartmentAssetBase: value })} />
        </div>
        <div className="settings-actions">
          <button className="secondary-button" type="button" onClick={() => setDraft(config)}>Reset</button>
          <button className="primary-button" type="submit">Save</button>
        </div>
      </form>
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  secret = false
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  secret?: boolean;
}) {
  return (
    <label className="settings-field">
      <span>{label}</span>
      <input type={secret ? "password" : "text"} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}
