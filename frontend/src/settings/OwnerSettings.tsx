import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, LockKeyhole, RefreshCw, Save } from "lucide-react";
import {
  api,
  type ModelConnection,
  type ModelConnectionChange,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import "./settings.css";
import { connectionLabel } from "./aiLabels";

const errorText = (err: unknown) =>
  err instanceof Error ? err.message : "Could not save. Try again.";

function ConnectionForm({ initial }: { initial: ModelConnection }) {
  const market = useMarket();
  const [saved, setSaved] = useState(initial);
  const [provider, setProvider] = useState(initial.provider);
  const [address, setAddress] = useState(initial.base_url);
  const [quick, setQuick] = useState(initial.quick_model);
  const [deep, setDeep] = useState(initial.deep_model);
  const [key, setKey] = useState("");
  const [keyAction, setKeyAction] = useState<"keep" | "replace" | "clear">(
    "keep",
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const label = connectionLabel(initial.id);
  function apply(value: ModelConnection) {
    setSaved(value);
    setProvider(value.provider);
    setAddress(value.base_url);
    setQuick(value.quick_model);
    setDeep(value.deep_model);
    setKey("");
    setKeyAction("keep");
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError("");
    setNotice("");
    try {
      const value = await api.saveModelConnection(initial.id, {
        provider: provider as ModelConnectionChange["provider"],
        base_url: address,
        quick_model: quick,
        deep_model: deep,
        api_key: keyAction === "replace" ? key : "",
        key_action: keyAction,
        expected_revision: saved.revision,
      });
      apply(value);
      setNotice("Connection saved. Applies to the next analysis or review.");
      await market.reloadJobs();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  }
  return (
    <form
      className="connection-form"
      aria-label={`${label} settings`}
      onSubmit={save}
    >
      <div className="owner-settings-heading">
        <div>
          <span className="eyebrow">
            {initial.id === "cloud" ? "DIRECT API" : "ROUTER API"}
          </span>
          <h2>{label}</h2>
        </div>
        <span className={`badge ${saved.configured ? "cyan" : "neutral"}`}>
          {saved.configured ? "CONFIGURED" : "SETUP NEEDED"}
        </span>
      </div>
      <p>
        {initial.id === "cloud"
          ? "Connect directly to OpenAI, Anthropic, Google or another provider using its API key and model names."
          : "Connect through 9router or another OpenAI-compatible API. Paste its API address and the model names from its dashboard. Use an address reachable from Tauric."}
      </p>
      <fieldset disabled={pending}>
        <label>
          Provider
          <select
            aria-label="Provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="google">Google</option>
            <option value="openrouter">OpenRouter</option>
            <option value="openai_compatible">
              9router / OpenAI-compatible API
            </option>
            <option value="ollama">Ollama</option>
          </select>
        </label>
        <label>
          API address
          <input
            type="url"
            value={address}
            maxLength={2048}
            placeholder={
              provider === "openai_compatible"
                ? "Your router’s API address ending in /v1"
                : "Use provider default (optional)"
            }
            onChange={(e) => setAddress(e.target.value)}
          />
        </label>
        <div className="connection-models">
          <label>
            Quick model
            <input
              value={quick}
              maxLength={120}
              autoComplete="off"
              onChange={(e) => setQuick(e.target.value)}
              placeholder="Exact model name"
            />
          </label>
          <label>
            Deep model
            <input
              value={deep}
              maxLength={120}
              autoComplete="off"
              onChange={(e) => setDeep(e.target.value)}
              placeholder="Exact model name"
            />
          </label>
        </div>
        <label>
          API key action
          <select
            aria-label="API key action"
            value={keyAction}
            onChange={(e) => {
              setKeyAction(e.target.value as typeof keyAction);
              setKey("");
            }}
          >
            <option value="keep">
              {saved.has_key ? "Keep saved key" : "No key supplied"}
            </option>
            <option value="replace">Replace key</option>
            <option value="clear">Clear key</option>
          </select>
        </label>
        {keyAction === "replace" && (
          <label>
            New API key
            <input
              type="password"
              value={key}
              maxLength={4096}
              autoComplete="new-password"
              required
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
        )}
        {keyAction === "clear" && (
          <p className="credential-note">
            The saved key will be removed from this connection. APIs that
            require a key will need a replacement before they can be used.
          </p>
        )}
        <div className="connection-actions">
          <button
            className="primary small"
            type="submit"
            disabled={!saved.storage_ready}
          >
            <Save size={14} />
            {pending ? "Saving…" : "Save connection"}
          </button>
          <span>Saved revision {saved.revision}</span>
        </div>
      </fieldset>
      {!saved.storage_ready && (
        <p className="error" role="alert">
          Private storage is unavailable. The host needs its encryption key
          before connections can be saved.
        </p>
      )}
      {error && (
        <div className="connection-error">
          <p className="error" role="alert">
            {error}
          </p>
          <button
            type="button"
            className="text-button"
            disabled={pending}
            onClick={async () => {
              setError("");
              try {
                const values = await api.modelConnections();
                apply(values.find((value) => value.id === initial.id)!);
                setNotice("Current settings reloaded.");
              } catch (err) {
                setError(errorText(err));
              }
            }}
          >
            <RefreshCw size={13} />
            Reload connection
          </button>
        </div>
      )}
      {notice && (
        <p className="settings-success" role="status">
          {notice}
        </p>
      )}
    </form>
  );
}

export function ModelConnections() {
  const [values, setValues] = useState<ModelConnection[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .modelConnections()
      .then((next) => {
        if (active) setValues(next);
      })
      .catch((err) => {
        if (active) setError(errorText(err));
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <div className="owner-settings-section">
      <div className="settings-intro">
        <KeyRound size={20} />
        <p>
          Keys are encrypted on the server and are never sent back to your
          browser. Saving a connection does not start an analysis or verify
          model access.
        </p>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="connection-grid">
        {values.map((value) => (
          <ConnectionForm key={value.id} initial={value} />
        ))}
      </div>
      {!values.length && !error && <p>Loading connections…</p>}
    </div>
  );
}

export function SecuritySettings({ done }: { done: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function change(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setPending(true);
    try {
      await api.changePassword(current, next, confirm);
      setCurrent("");
      setNext("");
      setConfirm("");
      done();
    } catch (err) {
      setError(errorText(err));
      setCurrent("");
    } finally {
      setPending(false);
    }
  }
  return (
    <form
      className="password-form connection-form"
      onSubmit={change}
      aria-label="Change password"
    >
      <div className="owner-settings-heading">
        <div>
          <span className="eyebrow">OWNER ACCOUNT</span>
          <h2>Change password</h2>
        </div>
        <LockKeyhole size={22} />
      </div>
      <p>
        Choose at least 12 characters. Changing your password signs out every
        session, including this one.
      </p>
      <fieldset disabled={pending}>
        <label>
          Current password
          <input
            type="password"
            value={current}
            autoComplete="current-password"
            required
            maxLength={1024}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          New password
          <input
            type="password"
            value={next}
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={1024}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <label>
          Confirm new password
          <input
            type="password"
            value={confirm}
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={1024}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <button className="primary" type="submit">
          <LockKeyhole size={14} />
          {pending ? "Changing…" : "Change password"}
        </button>
      </fieldset>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
