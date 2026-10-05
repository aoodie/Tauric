import { useEffect, useState } from "react";
import { client, unwrap } from "../api/client";
import type { components } from "../api/schema";

type Connection = components["schemas"]["TradingViewConnection"];

export function TradingViewConnection() {
  const [saved, setSaved] = useState<Connection | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function reload() {
    setSaved(unwrap(await client.GET("/api/v1/tradingview/connection")));
  }
  useEffect(() => {
    const status = new URLSearchParams(window.location.search).get(
      "tradingview",
    );
    if (status === "connected")
      setNotice("TradingView sign-in complete. Test economic data next.");
    if (status === "cancelled")
      setNotice(
        "TradingView sign-in was cancelled. You can reconnect when ready.",
      );
    if (status) {
      const url = new URL(window.location.href);
      url.searchParams.delete("tradingview");
      window.history.replaceState(
        window.history.state,
        "",
        url.pathname + url.search + url.hash,
      );
    }
    void reload().catch((e) => setError(e.message));
  }, []);
  async function connect() {
    if (!saved || pending) return;
    setPending(true);
    setError("");
    setNotice("");
    try {
      const value = unwrap(
        await client.POST("/api/v1/tradingview/connect", {
          body: { expected_revision: saved.revision },
        }),
      );
      // OAuth sign-in stays on TradingView. No password or token enters this form.
      const url = new URL(value.authorization_url);
      if (
        url.origin !== "https://www.tradingview.com" ||
        url.pathname !== "/mcp/oauth/authorize"
      )
        throw new Error("Unexpected sign-in address.");
      window.location.assign(url.toString());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in could not start.");
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
        await client.POST("/api/v1/tradingview/test", {
          body: { expected_revision: saved.revision },
        }),
      ) as { ready: boolean };
      await reload();
      setNotice(
        value.ready
          ? "Economic calendar and rate-series access checked for EUR/USD."
          : "Some TradingView sources were unavailable or need verification. Reconnect or retry; reports will show coverage gaps.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection check failed.");
    } finally {
      setPending(false);
    }
  }
  async function disconnect() {
    if (!saved || pending) return;
    setPending(true);
    setError("");
    setNotice("");
    try {
      setSaved(
        unwrap(
          await client.POST("/api/v1/tradingview/disconnect", {
            body: { expected_revision: saved.revision },
          }),
        ),
      );
      setNotice("TradingView disconnected from this app.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Disconnect failed.");
    } finally {
      setPending(false);
    }
  }
  return (
    <section
      className="connection-form"
      aria-label="TradingView research connection"
    >
      <h2>TradingView research</h2>
      <p>
        Use your existing Essential-or-higher subscription for the economic
        calendar and interest-rate series. Sign in on TradingView; no API key is
        needed.
      </p>
      <p>
        This app requests read-only access. OANDA continues to supply Forex
        prices and account financing. TradingView beta market data may be
        delayed.
      </p>
      <div className="connection-actions">
        <button
          className="primary small"
          type="button"
          disabled={!saved || pending}
          onClick={() => void connect()}
        >
          {saved?.configured ? "Reconnect TradingView" : "Connect TradingView"}
        </button>
        <button
          className="secondary small"
          type="button"
          disabled={!saved?.configured || pending}
          onClick={() => void test()}
        >
          Test economic data
        </button>
        {saved?.configured && (
          <button
            className="text-button"
            type="button"
            disabled={pending}
            onClick={() => void disconnect()}
          >
            Disconnect
          </button>
        )}
        <button
          className="text-button"
          type="button"
          disabled={pending}
          onClick={() => void reload().catch((e) => setError(e.message))}
        >
          Reload connection
        </button>
      </div>
      {saved && (
        <p className="credential-note">
          {saved.configured ? "Connected" : "Not connected"} · revision{" "}
          {saved.revision}
          {saved.checked_at
            ? ` · checked ${new Date(saved.checked_at).toLocaleString()}`
            : " · economic data not checked"}
          . Authorization is encrypted and never returned to the browser.
        </p>
      )}
      {pending && (
        <p role="status">
          {saved?.configured
            ? "Checking connection…"
            : "Preparing TradingView sign-in…"}
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
    </section>
  );
}
