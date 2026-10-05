import { useEffect, useState, type FormEvent } from "react";
import { client, unwrap } from "../api/client";
import type { components } from "../api/schema";

type Connection = components["schemas"]["ForexDataConnection"];
export function ForexDataConnection() {
  const [saved, setSaved] = useState<Connection | null>(null);
  const [action, setAction] = useState<"keep" | "replace" | "clear">("replace");
  const [key, setKey] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  async function reload() {
    const value = unwrap(await client.GET("/api/v1/forex-data/connection"));
    setSaved(value);
    setAction(value.configured ? "keep" : "replace");
    setKey("");
  }
  useEffect(() => {
    void reload().catch((e) => setError(e.message));
  }, []);
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!saved || pending) return;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const value = unwrap(
        await client.PUT("/api/v1/forex-data/connection", {
          body: {
            expected_revision: saved.revision,
            key_action: action,
            api_key: action === "replace" ? key : "",
          },
        }),
      );
      setSaved(value);
      setKey("");
      setAction(value.configured ? "keep" : "replace");
      setNotice("Connection saved. Test calendar and policy-rate access next.");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Connection could not be saved.",
      );
    } finally {
      setPending(false);
    }
  }
  async function test() {
    if (!saved || pending) return;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const value = unwrap(
        await client.POST("/api/v1/forex-data/test", {
          body: { expected_revision: saved.revision },
        }),
      ) as { ready: boolean };
      await reload();
      setNotice(
        value.ready
          ? "Calendar and policy-rate access checked for EUR/USD."
          : "Some sources were unavailable. Check your calendar and indicators entitlement.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection check failed.");
    } finally {
      setPending(false);
    }
  }
  return (
    <form
      className="connection-form"
      aria-label="Forex economic data settings"
      onSubmit={save}
    >
      <h2>Forex economic data</h2>
      <p>
        Trading Economics supplies scheduled releases, survey forecasts, actual
        results and policy-rate benchmarks for the currencies in your report.
        Your API plan needs calendar and indicators access.
      </p>
      <p>
        OANDA account instrument financing uses the saved OANDA token and
        account ID above.
      </p>
      <fieldset disabled={pending || !saved}>
        <label>
          API key action
          <select
            value={action}
            onChange={(e) => setAction(e.target.value as typeof action)}
          >
            <option value="keep">Keep saved key</option>
            <option value="replace">Replace key</option>
            <option value="clear">Clear key</option>
          </select>
        </label>
        {action === "replace" && (
          <label>
            Trading Economics API key
            <input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              required
              maxLength={4096}
            />
          </label>
        )}
        <div className="connection-actions">
          <button className="primary small" type="submit">
            Save connection
          </button>
          <button
            className="secondary small"
            type="button"
            disabled={!saved?.configured || action !== "keep"}
            onClick={() => void test()}
          >
            Test calendar access
          </button>
          <button
            type="button"
            className="text-button"
            onClick={() => void reload().catch((e) => setError(e.message))}
          >
            Reload connection
          </button>
        </div>
      </fieldset>
      {saved && (
        <p className="credential-note">
          {saved.configured ? "Key saved" : "No key saved"} · revision{" "}
          {saved.revision}
          {saved.checked_at
            ? ` · checked ${new Date(saved.checked_at).toLocaleString()}`
            : " · access not checked"}
          . Keys are encrypted and never returned to the browser.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="settings-success" role="status">
          {notice}
        </p>
      )}
    </form>
  );
}
