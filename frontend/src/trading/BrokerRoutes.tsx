import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type AnalysisStatus,
  type BrokerRoute,
  type BrokerRoutes as RoutesState,
  type Instrument,
  type StrategyVersion,
} from "../api/client";

type RouteRequest = {
  id: string;
  body: Parameters<typeof api.saveBrokerRoute>[1];
};
const explain = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "The automatic route could not be updated.";

export function BrokerRoutes({
  provider,
  onEntry,
}: {
  provider: "oanda" | "alpaca";
  onEntry: () => Promise<void>;
}) {
  const [state, setState] = useState<RoutesState | null>(null);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [models, setModels] = useState<AnalysisStatus | null>(null);
  const [profile, setProfile] = useState<"cloud" | "local">("cloud");
  const [pending, setPending] = useState(false);
  const [retry, setRetry] = useState<RouteRequest | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [history, setHistory] = useState<Awaited<
    ReturnType<typeof api.brokerRouteHistory>
  > | null>(null);
  const refreshEntry = useRef(onEntry);
  refreshEntry.current = onEntry;
  const seen = useRef(new Set<string>());
  useEffect(() => {
    let mounted = true;
    void Promise.all([
      api.brokerRoutes(),
      api.instruments(),
      api.strategyVersions(),
      api.analysisStatus(),
    ])
      .then(([routes, items, saved, status]) => {
        if (!mounted) return;
        setState(routes);
        setInstruments(items);
        setVersions(saved.filter((v) => !!v.parameters.rules));
        setModels(status);
        setProfile(status.profiles.find((p) => p.configured)?.id ?? "cloud");
        routes.workflows.forEach((w) => {
          if (w.intent_id) seen.current.add(w.intent_id);
        });
      })
      .catch((err) => {
        if (mounted) setError(explain(err));
      });
    const timer = window.setInterval(() => {
      void api
        .brokerRoutes()
        .then((routes) => {
          if (!mounted) return;
          setState(routes);
          let changed = false;
          for (const flow of routes.workflows)
            if (flow.intent_id && !seen.current.has(flow.intent_id)) {
              seen.current.add(flow.intent_id);
              changed = true;
            }
          if (changed)
            void refreshEntry.current().catch((err) => {
              if (mounted) setError(explain(err));
            });
        })
        .catch((err) => {
          if (mounted) setError(explain(err));
        });
    }, 5000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    setHistory(null);
    setNotice("");
  }, [provider]);
  async function save(request: RouteRequest) {
    setPending(true);
    setRetry(request);
    setError("");
    setNotice("");
    try {
      const result = await api.saveBrokerRoute(request.id, request.body);
      setRetry(null);
      setState(await api.brokerRoutes());
      setNotice(
        result.settings.enabled
          ? "Automatic route enabled. Fresh signals can request AI reviews and place one exact approved entry."
          : "Automatic route paused. Pending entries stop; existing positions keep their protection and exit monitoring.",
      );
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500)
        setRetry(null);
      setError(explain(err));
    } finally {
      setPending(false);
    }
  }
  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void save({
      id: crypto.randomUUID(),
      body: {
        request_id: crypto.randomUUID(),
        expected_revision: 0,
        enabled: form.get("route_enabled") === "on",
        provider,
        instrument_id: String(form.get("route_instrument")),
        strategy_version_id: String(form.get("route_strategy")),
        risk_percent: String(form.get("route_risk")),
        cadence_seconds: Number(form.get("route_cadence")),
        native_profile: profile,
        review_profile: profile,
        reason: String(form.get("route_reason")),
      },
    });
  }
  function toggle(route: BrokerRoute) {
    void save({
      id: route.id,
      body: {
        ...route.settings,
        request_id: crypto.randomUUID(),
        expected_revision: route.revision,
        enabled: !route.settings.enabled,
        reason: route.settings.enabled
          ? "Owner paused the automatic strategy route"
          : "Owner resumed the saved automatic strategy route with current broker and AI settings",
      },
    });
  }
  function revise(event: FormEvent<HTMLFormElement>, route: BrokerRoute) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void save({
      id: route.id,
      body: {
        ...route.settings,
        request_id: crypto.randomUUID(),
        expected_revision: route.revision,
        risk_percent: String(form.get("saved_risk")),
        cadence_seconds: Number(form.get("saved_cadence")),
        reason: String(form.get("saved_reason")),
      },
    });
  }
  const eligible = instruments.filter((item) =>
    provider === "oanda"
      ? item.asset_type === "fx" &&
        [item.base_currency, item.quote_currency].every((c) =>
          ["USD", "EUR", "GBP", "JPY"].includes(c),
        )
      : ["equity", "etf"].includes(item.asset_type),
  );
  const routes =
    state?.routes.filter((route) => route.settings.provider === provider) ?? [];
  return (
    <section className="paper-card" aria-label="Automatic strategy routes">
      <h3>Automatic strategy routes</h3>
      <p>
        Size each order from account equity, the strategy stop distance and the
        risk ceiling below. Live routes must stay within their validated risk
        ceiling. OANDA uses the broker’s account-currency conversions; Alpaca
        uses whole shares. Broker buying power, margin and order increments
        apply.
      </p>
      <p>
        The stop-risk amount is an estimate: gaps, slippage and costs can
        increase the loss. Tauric must approve the size and portfolio exposure;
        OpenClaw may reduce that approved size. Each new completed-candle signal
        needs fresh evidence, a Tauric setup approval and a separate OpenClaw
        approval.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <p>
        Trading worker: {state?.worker_ready ? "ready" : "unavailable"}. Both AI
        reviews may incur charges through your selected connection.
      </p>
      {!versions.length && (
        <p>
          Save a strategy with executable entry and exit rules in Settings →
          Strategies first.
        </p>
      )}
      <form onSubmit={create} key={provider}>
        <fieldset className="paper-form-grid" disabled={pending || !!retry}>
          <label>
            Automatic instrument
            <select
              name="route_instrument"
              aria-label="Automatic instrument"
              required
            >
              {eligible.map((i) => (
                <option value={i.id} key={i.id}>
                  {i.symbol}
                </option>
              ))}
            </select>
          </label>
          <label>
            Automatic strategy version
            <select
              name="route_strategy"
              aria-label="Automatic strategy version"
              required
            >
              {versions.map((v) => (
                <option value={v.id} key={v.id}>
                  {v.name} · {v.parameters.label} · revision {v.revision}
                </option>
              ))}
            </select>
          </label>
          <label>
            Estimated equity risk per trade (%)
            <input
              name="route_risk"
              aria-label="Automatic risk percentage"
              inputMode="decimal"
              defaultValue="1"
              required
            />
          </label>
          <label>
            Check every (seconds)
            <input
              name="route_cadence"
              aria-label="Automatic check interval"
              type="number"
              min="30"
              max="3600"
              defaultValue="30"
              required
            />
          </label>
          <label>
            Automatic AI connection
            <select
              aria-label="Automatic AI connection"
              value={profile}
              onChange={(e) => setProfile(e.target.value as "cloud" | "local")}
            >
              <option value="cloud">
                Cloud
                {models?.profiles.find((p) => p.id === "cloud")?.configured
                  ? ""
                  : " · not configured"}
              </option>
              <option value="local">
                Local
                {models?.profiles.find((p) => p.id === "local")?.configured
                  ? ""
                  : " · not configured"}
              </option>
            </select>
          </label>
          <label>
            Automatic route reason
            <input
              name="route_reason"
              aria-label="Automatic route reason"
              minLength={3}
              required
            />
          </label>
          <label>
            <input
              name="route_enabled"
              type="checkbox"
              aria-label="Enable automatic entries"
            />{" "}
            Enable automatic entries and attributed channel exits
          </label>
          <button type="submit" disabled={!versions.length || !eligible.length}>
            Save automatic route
          </button>
        </fieldset>
      </form>
      {retry && (
        <p>
          The save response was not received.{" "}
          <button disabled={pending} onClick={() => void save(retry)}>
            Recover this same automatic route
          </button>
        </p>
      )}
      {routes.length === 0 && <p>No automatic routes saved for this broker.</p>}
      {routes.map((route) => (
        <article
          className="paper-card"
          key={route.id}
          aria-label="Saved automatic route"
        >
          <p>
            <strong>{route.settings.enabled ? "Enabled" : "Paused"}</strong> ·{" "}
            {instruments.find((i) => i.id === route.settings.instrument_id)
              ?.symbol ?? route.settings.instrument_id}{" "}
            · revision {route.revision}
          </p>
          <p>
            {versions.find((v) => v.id === route.settings.strategy_version_id)
              ?.name ?? "Saved strategy"}{" "}
            · estimated equity risk {route.settings.risk_percent}% · checks
            every {route.settings.cadence_seconds}s
          </p>
          {route.last_event && (
            <p>
              {String(route.last_event.action)} ·{" "}
              {String(route.last_event.detail)}
            </p>
          )}
          {route.last_checked_at && (
            <p>Last check {new Date(route.last_checked_at).toLocaleString()}</p>
          )}
          <form
            key={route.version_id}
            onSubmit={(event) => revise(event, route)}
          >
            <fieldset className="paper-form-grid" disabled={pending || !!retry}>
              <label>
                Equity risk for future signals (%)
                <input
                  name="saved_risk"
                  aria-label="Saved route risk percentage"
                  inputMode="decimal"
                  defaultValue={route.settings.risk_percent}
                  required
                />
              </label>
              <label>
                Check every (seconds)
                <input
                  name="saved_cadence"
                  aria-label="Saved route check interval"
                  type="number"
                  min="30"
                  max="3600"
                  defaultValue={route.settings.cadence_seconds}
                  required
                />
              </label>
              <label>
                Change reason
                <input
                  name="saved_reason"
                  aria-label="Saved route change reason"
                  minLength={3}
                  required
                />
              </label>
              <button type="submit">Update automatic route</button>
            </fieldset>
          </form>
          <button disabled={pending || !!retry} onClick={() => toggle(route)}>
            {route.settings.enabled
              ? "Pause automatic route"
              : "Resume automatic route"}
          </button>{" "}
          <button
            className="secondary"
            disabled={pending}
            onClick={() => {
              void api
                .brokerRouteHistory(route.id)
                .then(setHistory)
                .catch((err) => setError(explain(err)));
            }}
          >
            Read automatic route history
          </button>
          {state?.workflows
            .filter((flow) => flow.route_id === route.id)
            .map((flow) => (
              <div key={flow.id} aria-label="Automatic signal">
                <p>
                  {flow.status} · signal{" "}
                  {new Date(flow.signal_bar_close).toLocaleString()} ·
                  calculated quantity {String(flow.sizing.quantity)}
                </p>
                <p>
                  Estimated stop risk{" "}
                  {String(flow.sizing.estimated_stop_loss_home)}{" "}
                  {String(flow.sizing.account_currency)} against budget{" "}
                  {String(flow.sizing.risk_budget_home)}
                </p>
                {flow.intent_id && (
                  <p>
                    Broker entry {flow.intent_id}. Actual fills and closes
                    appear in the broker journal below.
                  </p>
                )}
              </div>
            ))}
        </article>
      ))}
      {history && (
        <details open>
          <summary>Automatic route history</summary>
          {history.map((event) => (
            <p key={event.id}>
              {new Date(event.created_at).toLocaleString()} ·{" "}
              {String(event.payload.action)} · {String(event.payload.detail)}
            </p>
          ))}
        </details>
      )}
      <p>
        Pausing stops pending and future entries. Existing broker positions keep
        their attached stop/target and continue monitoring the original
        strategy’s completed-candle channel exit. Missed checks are coalesced;
        expired or interrupted signals are not replayed.
      </p>
    </section>
  );
}
