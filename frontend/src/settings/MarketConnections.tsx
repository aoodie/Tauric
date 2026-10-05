import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, RefreshCw, Save, PlugZap } from "lucide-react";
import {
  api,
  type MarketConnection,
  type MarketConnectionChange,
  type MarketConnectionCheck,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import { ForexDataConnection } from "./ForexDataConnection";
import { TradingViewConnection } from "./TradingViewConnection";
import "./settings.css";

const message = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Could not save the connection. Try again.";

function MarketConnectionForm({ initial }: { initial: MarketConnection }) {
  const market = useMarket();
  const [saved, setSaved] = useState(initial);
  const [environment, setEnvironment] = useState(initial.environment);
  const [accountId, setAccountId] = useState(initial.account_id ?? "");
  const [feed, setFeed] = useState<"iex" | "sip">(initial.feed ?? "iex");
  const [delay, setDelay] = useState(initial.delay_minutes ?? 15);
  const [action, setAction] = useState<
    MarketConnectionChange["credential_action"]
  >(initial.configured ? "keep" : "replace");
  const [token, setToken] = useState("");
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState<MarketConnectionCheck | null>(
    initial.last_check ?? null,
  );
  const fx = initial.id === "oanda";
  const label = fx ? "OANDA FX" : "Alpaca stocks & ETFs";
  const dirty =
    environment !== saved.environment ||
    action !== "keep" ||
    (fx
      ? accountId !== (saved.account_id ?? "")
      : feed !== saved.feed || delay !== saved.delay_minutes);
  function clearInputs() {
    setToken("");
    setKey("");
    setSecret("");
  }
  function apply(value: MarketConnection) {
    setSaved(value);
    setEnvironment(value.environment);
    setAccountId(value.account_id ?? "");
    setFeed(value.feed ?? "iex");
    setDelay(value.delay_minutes ?? 15);
    setAction(value.configured ? "keep" : "replace");
    clearInputs();
    setCheck(value.last_check ?? null);
  }
  async function testConnection() {
    setChecking(true);
    setCheck(null);
    setError("");
    setNotice("");
    try {
      setCheck(await api.testMarketConnection(initial.id, saved.revision));
    } catch (err) {
      setError(message(err));
    } finally {
      setChecking(false);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    setNotice("");
    try {
      const value = await api.saveMarketConnection(initial.id, {
        environment,
        account_id: fx ? accountId : undefined,
        feed,
        delay_minutes: delay,
        credential_action: action,
        expected_revision: saved.revision,
        token: action === "replace" && fx ? token : "",
        key: action === "replace" && !fx ? key : "",
        secret: action === "replace" && !fx ? secret : "",
      });
      apply(value);
      setNotice(
        value.configured
          ? "Connection saved. Refresh a chart to check data access."
          : "Credentials cleared. Saved chart data remains available.",
      );
      await market.reloadProviders();
    } catch (err) {
      setError(message(err));
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
            {fx ? "FOREIGN EXCHANGE" : "US STOCKS & ETFS"}
          </span>
          <h2>{label}</h2>
        </div>
        <span className={`badge ${saved.configured ? "cyan" : "neutral"}`}>
          {saved.configured ? "CREDENTIALS SAVED" : "SETUP NEEDED"}
        </span>
      </div>
      <p>
        {fx
          ? "Use an OANDA v20 token for midpoint, bid and ask candles."
          : "Use your Alpaca API key and secret for US regular-session data."}
      </p>
      <fieldset disabled={pending || checking}>
        <label>
          Environment
          <select
            aria-label={`${fx ? "OANDA" : "Alpaca"} environment`}
            value={environment}
            onChange={(event) => {
              setEnvironment(
                event.target.value as MarketConnection["environment"],
              );
              if (saved.configured) {
                setAction("replace");
                clearInputs();
              }
            }}
          >
            <option value={fx ? "practice" : "paper"}>
              {fx ? "Practice" : "Paper"}
            </option>
            <option value="live">Live</option>
          </select>
        </label>
        {fx && (
          <>
            <label>
              OANDA account ID
              <input
                value={accountId}
                maxLength={128}
                pattern="[0-9]+(-[0-9]+)*"
                autoComplete="off"
                placeholder="Your v20 account ID, including hyphens"
                onChange={(event) => setAccountId(event.target.value)}
              />
            </label>
            <p className="credential-note">
              Use the account ID for the selected environment. Optional for
              historical candles; when supplied, refresh checks which
              instruments your account can access.
            </p>
          </>
        )}
        {!fx && (
          <div className="connection-models">
            <label>
              Market data feed
              <select
                value={feed}
                onChange={(event) => setFeed(event.target.value as typeof feed)}
              >
                <option value="iex">IEX</option>
                <option value="sip">SIP</option>
              </select>
            </label>
            <label>
              Data delay · minutes
              <input
                type="number"
                min={0}
                max={15}
                step={1}
                required
                value={delay}
                onChange={(event) => setDelay(Number(event.target.value))}
              />
            </label>
          </div>
        )}
        {!fx && (
          <p className="credential-note">
            Choose a feed and delay covered by your data subscription. IEX
            covers one exchange; SIP access depends on your entitlement.
          </p>
        )}
        <label>
          Credentials
          <select
            aria-label={`${fx ? "OANDA" : "Alpaca"} credential action`}
            value={action}
            onChange={(event) => {
              setAction(event.target.value as typeof action);
              clearInputs();
            }}
          >
            <option value="keep">
              {saved.configured
                ? "Keep saved credentials"
                : "No credentials supplied"}
            </option>
            <option value="replace">
              {saved.configured ? "Replace credentials" : "Enter credentials"}
            </option>
            <option value="clear">Clear credentials</option>
          </select>
        </label>
        {action === "replace" &&
          (fx ? (
            <label>
              OANDA API token
              <input
                type="password"
                autoComplete="new-password"
                maxLength={4096}
                required
                value={token}
                onChange={(event) => setToken(event.target.value)}
              />
            </label>
          ) : (
            <>
              <label>
                Alpaca API key
                <input
                  type="password"
                  autoComplete="new-password"
                  maxLength={4096}
                  required
                  value={key}
                  onChange={(event) => setKey(event.target.value)}
                />
              </label>
              <label>
                Alpaca API secret
                <input
                  type="password"
                  autoComplete="new-password"
                  maxLength={4096}
                  required
                  value={secret}
                  onChange={(event) => setSecret(event.target.value)}
                />
              </label>
            </>
          ))}
        {action === "clear" && (
          <p className="credential-note">
            New provider refreshes will need credentials again. Existing
            snapshots and encrypted connection history are retained.
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
          <button
            type="button"
            className="secondary small"
            disabled={!saved.configured || dirty || !saved.storage_ready}
            onClick={() => void testConnection()}
          >
            <PlugZap size={14} />
            {checking ? "Checking…" : "Test connection"}
          </button>
          <span>Saved revision {saved.revision}</span>
        </div>
        <p className="credential-note">
          {saved.configured && dirty
            ? "Save your changes before testing."
            : "Test connection checks a sample of market data using your saved settings."}
        </p>
      </fieldset>
      {!saved.storage_ready && (
        <p className="error" role="alert">
          Private storage is unavailable. Restore the host encryption key before
          saving.
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
            disabled={pending || checking}
            onClick={async () => {
              setError("");
              setNotice("");
              try {
                const values = await api.marketConnections();
                apply(values.find((value) => value.id === initial.id)!);
                setNotice("Current settings reloaded.");
              } catch (err) {
                setError(message(err));
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
      {check && !dirty && (
        <div className="market-connection-check" role="status">
          <strong>
            {check.quality === "verified"
              ? "Access checked"
              : "Access checked · data warnings"}
          </strong>
          <p>
            {check.symbol} · {check.interval} · {check.feed} · {check.candles}{" "}
            candles
          </p>
          <p>
            {check.account_checked
              ? "Selected OANDA account access checked. "
              : ""}
            Checked {new Date(check.checked_at).toLocaleString()}.
          </p>
          {check.warnings.length > 0 && (
            <ul>
              {check.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          <p className="credential-note">
            This check covers this sample at the recorded time. Recheck after
            saving different settings.
          </p>
        </div>
      )}
    </form>
  );
}

export function MarketConnections() {
  const [values, setValues] = useState<MarketConnection[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .marketConnections()
      .then((value) => {
        if (active) setValues(value);
      })
      .catch((err) => {
        if (active) setError(message(err));
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
          API tokens and secrets are encrypted on the server and are never
          returned to your browser. Saving does not contact a provider. Charts
          verify access when you refresh.
        </p>
      </div>
      <p className="muted">
        Binance public spot data is available without credentials. These
        connections supply market data for FX, stocks and ETFs.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="connection-grid">
        {values.map((value) => (
          <MarketConnectionForm key={value.id} initial={value} />
        ))}
      </div>
      {!values.length && !error && <p>Loading market connections…</p>}
      <TradingViewConnection />
      <details>
        <summary>Optional paid economic calendar</summary>
        <p>Trading Economics is optional and requires its own entitlement.</p>
        <ForexDataConnection />
      </details>
    </div>
  );
}
