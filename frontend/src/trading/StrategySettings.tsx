import { DeploymentCosts, stockCostValues } from "./DeploymentCosts";
import { useState, type FormEvent } from "react";
import {
  api,
  type StrategyVersion,
  type StrategyParameters,
  type PaperState,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import { usePaper } from "./PaperTrading";
import { allocationPath } from "./CapitalSettings";

function parameters(f: FormData): StrategyParameters {
  return {
    label: String(f.get("label")),
    description: String(f.get("description")),
    quantity_step: String(f.get("quantity_step")),
    capital_limit: String(f.get("capital_limit")),
    risk_percent: String(f.get("risk_percent")),
    rules:
      f.get("rule_engine") === "closed_bar_breakout_v1"
        ? {
            engine: "closed_bar_breakout_v1",
            interval: String(f.get("rule_interval")) as NonNullable<
              StrategyParameters["rules"]
            >["interval"],
            entry_lookback: Number(f.get("entry_lookback")),
            minimum_volume_ratio: String(f.get("minimum_volume_ratio")),
            exit_lookback: Number(f.get("exit_lookback")),
            take_profit_r: String(f.get("take_profit_r")),
            signal_ttl_seconds: Number(f.get("signal_ttl_seconds")),
          }
        : null,
  };
}
function Fields({ value }: { value?: StrategyParameters }) {
  const [engine, setEngine] = useState<string>(
    value?.rules?.engine ?? "manual",
  );
  return (
    <>
      <div className="paper-form-grid">
        {[
          ["label", "Version label"],
          ["quantity_step", "Version quantity step"],
          ["capital_limit", "Version capital limit (account currency)"],
          ["risk_percent", "Version risk per trade (%)"],
        ].map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              name={key}
              required
              maxLength={80}
              defaultValue={String(
                value?.[key as keyof StrategyParameters] ?? "",
              )}
            />
          </label>
        ))}
      </div>
      <label>
        Entry and exit rules
        <select
          name="rule_engine"
          aria-label="Entry and exit rules"
          value={engine}
          onChange={(e) => setEngine(e.target.value)}
        >
          <option value="manual">Manual proposals</option>
          <option value="closed_bar_breakout_v1">
            Closed-candle breakout · long spot
          </option>
        </select>
      </label>
      {engine === "closed_bar_breakout_v1" && (
        <>
          <div className="paper-form-grid">
            <label>
              Rule candle interval
              <select
                name="rule_interval"
                aria-label="Rule candle interval"
                defaultValue={value?.rules?.interval ?? "15m"}
              >
                {["1m", "5m", "15m", "1h", "4h", "1d"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            {[
              [
                "entry_lookback",
                "Entry lookback (closed candles)",
                20,
                2,
                100,
                1,
              ],
              [
                "minimum_volume_ratio",
                "Minimum volume ratio",
                "1.5",
                0.01,
                100,
                "any",
              ],
              [
                "exit_lookback",
                "Exit lookback (closed candles)",
                10,
                2,
                100,
                1,
              ],
              ["take_profit_r", "Profit target (R)", "2", 1, 100, "any"],
              [
                "signal_ttl_seconds",
                "Signal valid for (seconds)",
                300,
                60,
                3600,
                1,
              ],
            ].map(([key, label, fallback, min, max, step]) => (
              <label key={key}>
                {label}
                <input
                  name={String(key)}
                  type="number"
                  required
                  min={min}
                  max={max}
                  step={step}
                  defaultValue={
                    value?.rules?.[
                      key as keyof NonNullable<StrategyParameters["rules"]>
                    ] ?? fallback
                  }
                />
              </label>
            ))}
          </div>
          <p className="muted">
            Enter when a closed candle exceeds prior highs and its volume meets
            the ratio. The lowest prior low sets the stop reference. Exit on a
            stop touch, profit target touch, or close below prior lows. R is the
            entry-to-stop distance. One open position per route; evaluate and
            apply actions in Paper trading.
          </p>
        </>
      )}
      <label>
        Strategy notes
        <textarea
          aria-label="Strategy notes"
          name="description"
          required
          minLength={3}
          maxLength={8000}
          rows={4}
          defaultValue={value?.description ?? ""}
        />
      </label>
      <label>
        Version reason
        <input name="reason" required minLength={3} maxLength={500} />
      </label>
    </>
  );
}
function VersionCard({ versions }: { versions: StrategyVersion[] }) {
  const { act, pending, state } = usePaper();
  const market = useMarket();
  const [editing, setEditing] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const ordered = [...versions].sort((a, b) => b.revision - a.revision);
  const head = ordered[0];
  async function revise(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const values = parameters(f);
    if (
      await act(
        `version-${head.id}-${JSON.stringify(values)}-${f.get("reason")}`,
        (id) =>
          api.reviseStrategy(head.strategy_id, {
            request_id: id,
            expected_revision: head.revision,
            parameters: values,
            reason: String(f.get("reason")),
          }),
        "New strategy version saved. Existing deployments keep their earlier version.",
      )
    )
      setEditing(false);
  }
  const [deploymentInstrumentId, setDeploymentInstrumentId] = useState("");
  const deploymentInstrument =
    market.instruments.find((i) => i.id === deploymentInstrumentId) ??
    market.instruments[0];
  async function deploy(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const version = ordered.find((v) => v.id === f.get("version_id"));
    const scope = state?.capital_scopes.find((s) => s.id === f.get("scope_id"));
    if (!version || !scope || !state) return;
    const root = allocationPath(scope, state.capital_scopes)[0];
    const request = {
      account_id: scope.account_id,
      instrument_id: String(f.get("instrument_id")),
      portfolio: root.name,
      capital_pot: scope.name,
      capital_scope_id: scope.id,
      strategy: version.name,
      strategy_version: version.parameters.label,
      strategy_revision_id: version.id,
      quantity_step: version.parameters.quantity_step,
      capital_limit: version.parameters.capital_limit,
      risk_percent: version.parameters.risk_percent,
      stock_costs: stockCostValues(f, deploymentInstrument?.asset_type ?? ""),
      fx_financing_daily_bps:
        deploymentInstrument?.asset_type === "fx"
          ? String(f.get("fx_financing_daily_bps"))
          : null,
      fx_triple_rollover_weekday: 2,
      reason: String(f.get("reason")),
    };
    if (
      await act(
        `route-${JSON.stringify(request)}`,
        (id) => api.createDeployment({ request_id: id, ...request }),
        "Version deployed to the selected paper account and allocation.",
      )
    )
      setDeploying(false);
  }
  return (
    <article className="paper-card" aria-label={`${head.name} versions`}>
      <div className="paper-card-head">
        <div>
          <span className="eyebrow">SAVED STRATEGY · PAPER</span>
          <h2>{head.name}</h2>
        </div>
        <span className="badge cyan">Revision {head.revision}</span>
      </div>
      <p>{head.parameters.description}</p>
      <p className="muted">
        {head.parameters.label} · {head.parameters.risk_percent}% trade risk ·{" "}
        {head.parameters.capital_limit} · account currency capital limit · step{" "}
        {head.parameters.quantity_step}
      </p>
      {head.parameters.rules && (
        <p className="muted">
          Closed-candle breakout · {head.parameters.rules.interval} · entry{" "}
          {head.parameters.rules.entry_lookback} candles · volume ≥{" "}
          {head.parameters.rules.minimum_volume_ratio}× · exit{" "}
          {head.parameters.rules.exit_lookback} candles · target{" "}
          {head.parameters.rules.take_profit_r} R
        </p>
      )}
      <div className="paper-actions">
        <button
          className="secondary small"
          disabled={pending}
          onClick={() => setEditing(!editing)}
        >
          {editing ? "Close version form" : "Create new version"}
        </button>
        <button
          className="primary small"
          disabled={pending || !state?.capital_scopes.length}
          onClick={() => setDeploying(!deploying)}
        >
          {deploying ? "Close deployment form" : "Deploy saved version"}
        </button>
      </div>
      {editing && (
        <form
          aria-label={`${head.name} new version`}
          key={head.id}
          onSubmit={revise}
        >
          <fieldset disabled={pending}>
            <Fields value={head.parameters} />
            <button className="primary small" type="submit">
              Save new version
            </button>
          </fieldset>
        </form>
      )}
      {deploying && (
        <form aria-label={`${head.name} version deployment`} onSubmit={deploy}>
          <fieldset disabled={pending}>
            <div className="paper-form-grid">
              <label>
                Saved version
                <select name="version_id" aria-label="Saved version" required>
                  {ordered.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.parameters.label} · revision {v.revision}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Account and allocation
                <select
                  name="scope_id"
                  aria-label="Account and allocation"
                  required
                >
                  <option value="">Choose account / allocation</option>
                  {state?.capital_scopes.map((v) => (
                    <option key={v.id} value={v.id}>
                      {state.accounts.find((a) => a.id === v.account_id)?.name}{" "}
                      /{" "}
                      {allocationPath(v, state.capital_scopes)
                        .map((a) => a.name)
                        .join(" / ")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Deployment instrument
                <select
                  name="instrument_id"
                  aria-label="Deployment instrument"
                  value={deploymentInstrument?.id ?? ""}
                  onChange={(e) => setDeploymentInstrumentId(e.target.value)}
                  required
                >
                  {market.instruments.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.symbol}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <DeploymentCosts asset={deploymentInstrument?.asset_type ?? ""} />
            <label>
              Deployment reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <p className="muted">
              This adds a paper route. Pause an earlier route separately when
              replacing it. Existing positions keep their opening version and
              account.
            </p>
            <button className="primary small" type="submit">
              Deploy version
            </button>
          </fieldset>
        </form>
      )}
      <details className="capital-history">
        <summary>Version history</summary>
        {ordered.map((v) => (
          <div key={v.id}>
            <strong>
              {v.parameters.label} · revision {v.revision}
            </strong>
            <span>
              {v.parameters.capital_limit} · account currency ·{" "}
              {v.parameters.risk_percent}% risk · {v.reason}
            </span>
            <p>{v.parameters.description}</p>
            <span>
              {v.parameters.rules
                ? `Closed-candle breakout · ${v.parameters.rules.interval} · ${v.parameters.rules.take_profit_r} R`
                : "Manual proposals"}
            </span>
            <time>{new Date(v.created_at).toLocaleString()}</time>
          </div>
        ))}
      </details>
    </article>
  );
}
export function StrategySettings() {
  const { state, act, pending } = usePaper();
  const [adding, setAdding] = useState(false);
  const grouped = new Map<string, StrategyVersion[]>();
  for (const v of state?.strategy_versions ?? [])
    grouped.set(v.strategy_id, [...(grouped.get(v.strategy_id) ?? []), v]);
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const values = {
      name: String(f.get("name")),
      parameters: parameters(f),
      reason: String(f.get("reason")),
    };
    if (
      await act(
        `strategy-${JSON.stringify(values)}`,
        (id) => api.createStrategy({ request_id: id, ...values }),
        "Strategy version saved. Deploy it when you choose an account and allocation.",
      )
    )
      setAdding(false);
  }
  return (
    <section
      className="paper-account-list"
      aria-label="Strategy versions and routes"
    >
      <div className="paper-intro">
        <div>
          <h2>Strategy versions &amp; routing</h2>
          <p>
            Save configuration before deploying it. New versions leave existing
            routes and positions pinned to their earlier version. Choose manual
            proposals or executable candle rules, then evaluate deployed rules
            in Paper trading.
          </p>
        </div>
      </div>
      <div>
        <button
          className="secondary small"
          disabled={!state || pending}
          onClick={() => setAdding(!adding)}
        >
          {adding ? "Close strategy version form" : "Add strategy version"}
        </button>
      </div>
      {adding && (
        <form
          className="paper-card"
          aria-label="New strategy version"
          onSubmit={create}
        >
          <h2>New strategy version</h2>
          <fieldset disabled={pending}>
            <label>
              Registered strategy name
              <input name="name" required minLength={3} maxLength={80} />
            </label>
            <Fields />
            <button className="primary small" type="submit">
              Save strategy version
            </button>
          </fieldset>
        </form>
      )}
      {[...grouped.entries()].map(([id, versions]) => (
        <VersionCard key={id} versions={versions} />
      ))}
      {state && !grouped.size && (
        <p className="muted">No saved strategy versions yet.</p>
      )}
    </section>
  );
}
export function DeploymentControls({
  deployment,
}: {
  deployment: PaperState["deployments"][number];
}) {
  const { act, pending } = usePaper();
  const [editing, setEditing] = useState(false);
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (
      await act(
        `control-${deployment.id}-${deployment.control_revision}-${f.get("reason")}`,
        () =>
          api.deploymentControl(deployment.id, {
            expected_revision: deployment.control_revision,
            enabled: !deployment.enabled,
            reason: String(f.get("reason")),
          }),
        deployment.enabled
          ? "Strategy route paused. Existing positions remain closable."
          : "Strategy route resumed. Start a new proposal.",
      )
    )
      setEditing(false);
  }
  return (
    <div className="deployment-controls">
      <span className={`badge ${deployment.enabled ? "cyan" : "neutral"}`}>
        {deployment.enabled ? "ENABLED" : "PAUSED"} ·{" "}
        {deployment.strategy_revision_id
          ? "PINNED VERSION"
          : "LABELLED DEPLOYMENT"}
      </span>
      <button
        className="secondary small"
        disabled={pending}
        onClick={() => setEditing(!editing)}
      >
        {editing
          ? "Close route control"
          : deployment.enabled
            ? "Pause strategy route"
            : "Resume strategy route"}
      </button>
      {editing && (
        <form
          aria-label={`${deployment.strategy} route control`}
          onSubmit={save}
        >
          <fieldset disabled={pending}>
            <label>
              Route change reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <button className="primary small" type="submit">
              {deployment.enabled ? "Confirm pause" : "Confirm resume"}
            </button>
          </fieldset>
        </form>
      )}
    </div>
  );
}
