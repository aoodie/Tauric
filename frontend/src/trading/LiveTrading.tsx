import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type TradingReadiness,
  type StrategyVersion,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import { TradingOperations } from "./TradingOperations";

type Provider = "oanda" | "alpaca";
type AccountMark = {
  key: string;
  name?: string;
  mode: string;
  currency?: string;
  complete: boolean;
  equity: string | null;
  cash: string | null;
  gross_exposure: string | null;
  realized_pnl?: string | null;
  unrealized_pnl?: string | null;
  fees?: string | null;
  settled_financing?: string | null;
  accrued_financing?: string | null;
  marked_max_drawdown_percent?: string;
  observed_at?: string;
  errors: { detail: string }[];
  currency_legs: Record<string, string>;
  strategy_exposure?: { key: string; gross_exposure: string | null }[];
  positions: {
    symbol?: string;
    instrument_id?: string;
    quantity: string;
    attributed_quantity?: string;
    external_quantity?: string;
    mark?: string | null;
    mark_status: string;
    mark_time?: string | null;
  }[];
};
type ProtocolPayload = {
  request: {
    provider: Provider;
    strategy_version_id: string;
    instrument_id: string;
    minimum_days: number;
    minimum_closed_trades: number;
    risk_percent: string;
  };
  research_report_ids: string[];
};
type ReportPayload = {
  provider: Provider;
  status: string;
  maximum_risk_percent: string;
  criteria: { name: string; status: string; detail: string }[];
  result: {
    closed_trades: number;
    net_pnl_home?: string;
    maximum_marked_drawdown_percent?: string;
  };
};
const explain = (e: unknown) =>
  e instanceof Error ? e.message : "The trading check could not finish.";
const display = (value: string | null | undefined) => value ?? "Unavailable";

export function BrokerModeBadge() {
  const [state, setState] = useState<TradingReadiness | null>(null);
  useEffect(() => {
    let active = true;
    const read = () => {
      if (document.hidden) return;
      void api
        .tradingReadiness()
        .then((v) => active && setState(v))
        .catch(() => active && setState(null));
    };
    read();
    const timer = window.setInterval(read, 30000);
    document.addEventListener("visibilitychange", read);
    window.addEventListener("tauric-trading-changed", read);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", read);
      window.removeEventListener("tauric-trading-changed", read);
    };
  }, []);
  const live =
    state?.brokers
      .filter((b) => b.live_enabled)
      .map((b) => String(b.provider).toUpperCase()) ?? [];
  return (
    <span title="Every live entry requires native Tauric approval and current protection checks.">
      {!state
        ? "Trading status unavailable"
        : live.length
          ? `${live.join(" + ")} LIVE`
          : "Live trading inactive"}
    </span>
  );
}

export function LiveTrading() {
  const market = useMarket();
  const [state, setState] = useState<TradingReadiness | null>(null);
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [provider, setProvider] = useState<Provider>("oanda");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const ids = useRef(new Map<string, string>());
  const working = useRef(false);
  async function reload() {
    setState(await api.tradingReadiness());
  }
  useEffect(() => {
    let active = true;
    const read = () => {
      if (document.hidden) return;
      void api
        .tradingReadiness()
        .then((v) => active && setState(v))
        .catch((e) => active && setError(explain(e)));
    };
    read();
    void api
      .strategyVersions()
      .then((v) => active && setVersions(v.filter((s) => s.parameters.rules)))
      .catch((e) => active && setError(explain(e)));
    const timer = window.setInterval(read, 30000);
    document.addEventListener("visibilitychange", read);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", read);
    };
  }, []);
  async function act(
    key: string,
    operation: (id: string) => Promise<unknown>,
    success: string,
  ) {
    if (working.current) return;
    working.current = true;
    setPending(true);
    setError("");
    setNotice("");
    const id = ids.current.get(key) ?? crypto.randomUUID();
    ids.current.set(key, id);
    try {
      await operation(id);
      ids.current.delete(key);
      await reload();
      setNotice(success);
      window.dispatchEvent(new Event("tauric-trading-changed"));
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500)
        ids.current.delete(key);
      setError(explain(e));
    } finally {
      working.current = false;
      setPending(false);
    }
  }
  function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const request = {
      provider,
      strategy_version_id: String(f.get("strategy")),
      instrument_id: String(f.get("instrument")),
      minimum_days: Number(f.get("days")),
      minimum_closed_trades: Number(f.get("trades")),
      maximum_drawdown_percent: String(f.get("drawdown")),
      minimum_net_pnl_home: String(f.get("pnl")),
      maximum_signal_to_fill_seconds: Number(f.get("latency")),
      maximum_adverse_slippage_bps: String(f.get("slippage")),
      risk_percent: String(f.get("risk")),
      reason: String(f.get("reason")),
    };
    void act(
      "forward:" + JSON.stringify(request),
      (request_id) => api.registerForward({ ...request, request_id }),
      "Forward validation registered. Outcomes are collected from this point onward.",
    );
  }
  const snapshot = state?.valuations[0];
  const accounts = (snapshot?.payload.accounts ?? []) as AccountMark[];
  const instruments = market.instruments.filter((i) =>
    provider === "oanda"
      ? i.asset_type === "fx"
      : ["equity", "etf"].includes(i.asset_type),
  );
  async function enableLive(
    selectedProvider: Provider,
    reportId: string,
    requestId: string,
  ) {
    const account = await api.checkBroker(selectedProvider);
    if (account.error) throw new Error(account.error);
    if (account.environment !== "live")
      throw new Error(
        "Choose your live account in Market connections, then check it here.",
      );
    const brokers = await api.brokers();
    await api.saveBrokerPolicy(selectedProvider, {
      request_id: requestId,
      expected_revision:
        brokers.policies.find((p) => p.provider === selectedProvider)
          ?.revision ?? 0,
      enabled: true,
      no_owner_limits: true,
      live_acknowledged: true,
      forward_report_id: reportId,
      reason:
        "Owner enabled live automatic trading after passing prospective checks; Tauric is mandatory risk authority",
    });
    await api.captureValuation({
      request_id: crypto.randomUUID(),
      reason: "Observe the checked live account before automatic entries",
    });
  }
  return (
    <section
      className="paper-card trading-readiness"
      aria-label="Live trading readiness"
    >
      <div className="paper-card-head">
        <div>
          <span className="eyebrow">
            TAURIC APPROVAL → OPENCLAW REVIEW → PROTECTED EXECUTION
          </span>
          <h2>Portfolio and live trading</h2>
        </div>
        <span className="badge neutral">
          <BrokerModeBadge />
        </span>
      </div>
      <p>
        Tauric approves every entry and its maximum size. OpenClaw can reduce
        risk. Automatic routes execute only after both reviews and current
        account checks pass.
      </p>
      <p className="muted">
        Portfolio observations:{" "}
        {state?.valuation_worker_ready ? "running" : "unavailable"} ·
        prospective checks:{" "}
        {state?.forward_worker_ready ? "running" : "unavailable"}
      </p>
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
      <TradingOperations />
      <div className="paper-actions">
        <button
          disabled={pending}
          onClick={() =>
            void act(
              "valuation",
              (request_id) =>
                api.captureValuation({
                  request_id,
                  reason: "Owner refreshed marked portfolio values",
                }),
              "Portfolio values refreshed.",
            )
          }
        >
          Refresh portfolio values
        </button>
        <button
          className="secondary"
          disabled={pending}
          onClick={() =>
            void act(
              "refresh",
              async () => reload(),
              "Trading checks refreshed.",
            )
          }
        >
          Refresh trading checks
        </button>
      </div>
      {!snapshot && (
        <p>
          No portfolio observation yet. Refresh values to see current equity and
          exposure.
        </p>
      )}
      {snapshot && (
        <p className="muted">
          Observed {new Date(snapshot.created_at).toLocaleString()}. The
          background worker observes values each minute. Amounts retain their
          account currency.
        </p>
      )}
      {accounts.map((a) => (
        <article className="paper-card" key={a.key}>
          <h3>
            {a.name ?? a.key} · {a.mode} ·{" "}
            {a.currency ?? "Currency unavailable"}
          </h3>
          <p className={a.complete ? "muted" : "error"}>
            {a.complete
              ? "All positions marked"
              : "Incomplete valuation — entry checks require fresh marks"}
          </p>
          <div className="paper-balances">
            <div>
              <span>Equity</span>
              <strong>{display(a.equity)}</strong>
            </div>
            <div>
              <span>Cash</span>
              <strong>{display(a.cash)}</strong>
            </div>
            <div>
              <span>Gross exposure</span>
              <strong>{display(a.gross_exposure)}</strong>
            </div>
          </div>
          <p>
            Realized P&amp;L {display(a.realized_pnl)} · open P&amp;L{" "}
            {display(a.unrealized_pnl)} · observed maximum drawdown{" "}
            {a.marked_max_drawdown_percent ?? "Unavailable"}%
          </p>
          <p>
            Fees {display(a.fees)} · settled financing{" "}
            {display(a.settled_financing)} · accrued financing{" "}
            {display(a.accrued_financing)}
          </p>
          {a.errors.map((e, i) => (
            <p className="error" key={i}>
              {e.detail}
            </p>
          ))}
          {a.positions.map((p, i) => (
            <p key={i}>
              {p.symbol ?? p.instrument_id} · quantity {p.quantity} · mark{" "}
              {display(p.mark)} ({p.mark_status})
              {p.attributed_quantity !== undefined &&
                ` · Tauric ${p.attributed_quantity} · external ${p.external_quantity}`}
              {p.mark_time && ` · ${new Date(p.mark_time).toLocaleString()}`}
            </p>
          ))}
          {!!Object.keys(a.currency_legs).length && (
            <p>
              Currency exposure:{" "}
              {Object.entries(a.currency_legs)
                .map(([ccy, q]) => `${ccy} ${q}`)
                .join(" · ")}
            </p>
          )}
          {!!a.strategy_exposure?.length && (
            <p>
              Strategy exposure:{" "}
              {a.strategy_exposure
                .map(
                  (s) =>
                    `${versions.find((v) => v.id === s.key)?.name ?? (s.key === "external" ? "External holdings" : "Attributed strategy")} ${display(s.gross_exposure)} ${a.currency ?? ""}`,
                )
                .join(" · ")}
            </p>
          )}
          <small>
            Drawdown uses observed values. Unavailable cost breakdowns are shown
            explicitly. Simulated and broker accounts remain separate.
          </small>
        </article>
      ))}
      <details>
        <summary>Register prospective strategy validation</summary>
        <p>
          Use an empty, dedicated OANDA practice or Alpaca paper account.
          Complete research qualification first. Save the exact strategy and
          thresholds before observing outcomes; changing them starts a new run.
        </p>
        <form className="paper-form-grid" onSubmit={register}>
          <label>
            Validation broker
            <select
              aria-label="Validation broker"
              value={provider}
              onChange={(e) => setProvider(e.target.value as Provider)}
            >
              <option value="oanda">OANDA practice</option>
              <option value="alpaca">Alpaca paper</option>
            </select>
          </label>
          <label>
            Saved strategy
            <select name="strategy" required defaultValue="">
              <option value="" disabled>
                Choose a strategy version
              </option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} v{v.revision}
                </option>
              ))}
            </select>
          </label>
          <label>
            Instrument
            <select name="instrument" required defaultValue="" key={provider}>
              <option value="" disabled>
                Choose an instrument
              </option>
              {instruments.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.provider_symbol}
                </option>
              ))}
            </select>
          </label>
          <label>
            Minimum prospective days
            <input
              name="days"
              required
              type="number"
              min="1"
              max="365"
              defaultValue="14"
            />
          </label>
          <label>
            Minimum complete trades
            <input
              name="trades"
              required
              type="number"
              min="1"
              max="10000"
              defaultValue="20"
            />
          </label>
          <label>
            Maximum drawdown (%)
            <input
              name="drawdown"
              required
              inputMode="decimal"
              defaultValue="10"
            />
          </label>
          <label>
            Net profit must exceed (account currency)
            <input name="pnl" required inputMode="decimal" defaultValue="0" />
          </label>
          <label>
            Maximum signal-to-fill seconds
            <input
              name="latency"
              required
              type="number"
              min="1"
              max="86400"
              defaultValue="900"
            />
          </label>
          <label>
            Maximum adverse slippage (bps)
            <input
              name="slippage"
              required
              inputMode="decimal"
              defaultValue="25"
            />
          </label>
          <label>
            Risk ceiling per trade (%)
            <input name="risk" required inputMode="decimal" defaultValue="1" />
          </label>
          <label>
            Reason
            <input
              name="reason"
              required
              minLength={3}
              placeholder="Strategy hypothesis and risk rationale"
            />
          </label>
          <p>
            These are editable validation thresholds. Tauric may approve less
            risk; OpenClaw may reduce it further. A passed demo run does not
            guarantee future live returns.
          </p>
          <button
            disabled={pending || !versions.length || !instruments.length}
            type="submit"
          >
            Register forward validation
          </button>
        </form>
      </details>
      {state?.forward_protocols.map((protocol) => {
        const p = protocol.payload as unknown as ProtocolPayload;
        const latest = state.forward_reports.find(
          (r) => r.payload.protocol_checksum === protocol.checksum,
        );
        const report = latest?.payload as unknown as ReportPayload | undefined;
        return (
          <article className="paper-card" key={protocol.id}>
            <h3>
              {p.request.provider.toUpperCase()} · {p.request.instrument_id} ·
              forward validation
            </h3>
            <p>
              Registered {new Date(protocol.created_at).toLocaleString()} ·
              minimum {p.request.minimum_days} days /{" "}
              {p.request.minimum_closed_trades} trades · risk ceiling{" "}
              {p.request.risk_percent}%
            </p>
            {!p.research_report_ids.length && (
              <p className="error">
                No matching passed research qualification existed at
                registration. Qualify the strategy, then register a new run.
              </p>
            )}
            {report && (
              <>
                <p>
                  {report.status} · complete trades{" "}
                  {report.result.closed_trades} · net result{" "}
                  {display(report.result.net_pnl_home)} · observed drawdown{" "}
                  {display(report.result.maximum_marked_drawdown_percent)}%
                </p>
                <p>
                  Live risk ceiling from observed approvals:{" "}
                  {report.maximum_risk_percent}%
                </p>
                <ul>
                  {report.criteria.map((c) => (
                    <li key={c.name}>
                      <strong>
                        {c.status === "passed" ? "Passed" : "Pending"}
                      </strong>{" "}
                      — {c.detail}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <button
              disabled={pending}
              onClick={() =>
                void act(
                  `evaluate:${protocol.id}`,
                  (request_id) =>
                    api.evaluateForward(protocol.id, {
                      request_id,
                      reason:
                        "Owner evaluated actual prospective broker evidence",
                    }),
                  "Forward checks updated from broker evidence.",
                )
              }
            >
              Evaluate forward evidence
            </button>
            {report?.status === "passed" && latest && (
              <button
                disabled={pending}
                onClick={() =>
                  void act(
                    `enable:${latest.id}`,
                    (id) => enableLive(p.request.provider, latest.id, id),
                    "Live account enabled. Enable a matching automatic route to trade under Tauric approval.",
                  )
                }
              >
                Activate {p.request.provider.toUpperCase()} live trading
              </button>
            )}
          </article>
        );
      })}
      {state?.brokers.map((b) => (
        <p key={String(b.provider)}>
          {String(b.provider).toUpperCase()} · {String(b.environment)} ·{" "}
          {b.enabled ? "entries enabled" : "entries paused"}
          {!!b.entry_blockers?.length &&
            ` · Waiting: ${b.entry_blockers.join(" ")}`}
          {b.enabled && (
            <button
              className="secondary"
              disabled={pending}
              onClick={() =>
                void act(
                  `pause:${b.provider}`,
                  async (request_id) => {
                    const provider = b.provider as Provider;
                    const brokers = await api.brokers();
                    await api.saveBrokerPolicy(provider, {
                      request_id,
                      expected_revision:
                        brokers.policies.find((p) => p.provider === provider)
                          ?.revision ?? 0,
                      enabled: false,
                      no_owner_limits: true,
                      live_acknowledged: false,
                      reason: "Owner paused new automatic broker entries",
                    });
                  },
                  "New entries paused. Position protection and exits continue.",
                )
              }
            >
              Pause new {String(b.provider).toUpperCase()} entries
            </button>
          )}
        </p>
      ))}
    </section>
  );
}
