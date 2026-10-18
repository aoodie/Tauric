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
import { WorkflowTabs } from "../ui/WorkflowTabs";

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
          ["label", "Version name"],
          ["quantity_step", "Order size increment"],
          ["capital_limit", "Capital ceiling (account currency)"],
          ["risk_percent", "Risk per trade (%)"],
        ].map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              name={key}
              required
              maxLength={80}
              defaultValue={String(
                value?.[key as keyof StrategyParameters] ??
                  (key === "label" ? "Initial" : ""),
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
          <option value="manual">Review each trade manually</option>
          <option value="closed_bar_breakout_v1">
            Breakout rules · long only
          </option>
        </select>
      </label>
      {engine === "closed_bar_breakout_v1" && (
        <details className="workflow-advanced">
          <summary>
            Breakout rules and exits{" "}
            <span>Timeframe, filters and profit target</span>
          </summary>
          <div className="paper-form-grid">
            <label>
              Rule timeframe
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
              [
                "take_profit_r",
                "Profit target (times initial risk)",
                "2",
                1,
                100,
                "any",
              ],
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
        </details>
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
        Why save this version?
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
          <span className="eyebrow">
            {head.parameters.rules ? "BREAKOUT RULES" : "MANUAL TRADE REVIEW"}
          </span>
          <h2>{head.name}</h2>
        </div>
        <span className="badge cyan">Revision {head.revision}</span>
      </div>
      <p className="strategy-description">{head.parameters.description}</p>
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
      <p className="muted">
        {state?.deployments.filter((d) =>
          ordered.some((v) => v.id === d.strategy_revision_id),
        ).length ?? 0}{" "}
        paper routes use this strategy.
      </p>
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
export function StrategySettings({ embedded = false }: { embedded?: boolean }) {
  const { state, act, pending } = usePaper();
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
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
      {!embedded && (
        <div className="paper-intro">
          <div>
            <h2>Strategy library</h2>
            <p>
              Save your idea and rules, then choose a paper account when you are
              ready to test. Existing routes keep the version they started with.
            </p>
          </div>
        </div>
      )}
      <div className="workflow-library-tools">
        <label>
          Find a strategy
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name or idea"
          />
        </label>
        <button
          className="secondary small"
          disabled={!state || pending}
          onClick={() => setAdding(!adding)}
        >
          {adding ? "Close strategy form" : "New strategy"}
        </button>
      </div>
      {adding && (
        <form
          className="paper-card"
          aria-label="New strategy version"
          onSubmit={create}
        >
          <h2>New strategy</h2>
          <fieldset disabled={pending}>
            <label>
              Strategy name
              <input name="name" required minLength={3} maxLength={80} />
            </label>
            <Fields />
            <button className="primary small" type="submit">
              Save strategy
            </button>
          </fieldset>
        </form>
      )}
      {[...grouped.entries()]
        .filter(([, versions]) =>
          versions.some((v) =>
            `${v.name} ${v.parameters.description}`
              .toLowerCase()
              .includes(search.toLowerCase()),
          ),
        )
        .map(([id, versions]) => (
          <VersionCard key={id} versions={versions} />
        ))}
      {state && !grouped.size && (
        <p className="empty-copy">
          Create a strategy with your idea, trade rules and risk settings. You
          can also ask OpenClaw to develop a hypothesis and test plan.
        </p>
      )}
    </section>
  );
}

export function StrategiesPage({
  ask,
  trading,
  research,
}: {
  ask: (draft: string) => void;
  trading: () => void;
  research: () => void;
}) {
  const { state, error, notice } = usePaper();
  const market = useMarket();
  const [tab, setTab] = useState("library");
  const count = new Set(state?.strategy_versions.map((v) => v.strategy_id))
    .size;
  return (
    <div className="page-scroll workflow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">IDEA → RULES → EVIDENCE</span>
          <h1>Strategies</h1>
          <p>
            Develop an idea, save its rules, and test it before changing a
            trading route.
          </p>
        </div>
        <button
          className="secondary"
          onClick={() =>
            ask(
              "Review my strategy library like a quant researcher. Identify weak assumptions, propose one testable improvement with a simple baseline and realistic costs, and save a research note. Keep deployed versions and risk limits unchanged.",
            )
          }
        >
          Ask OpenClaw
        </button>
      </div>
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
      <WorkflowTabs
        label="Strategy views"
        value={tab}
        select={setTab}
        options={[
          { id: "library", label: "Strategy library", count },
          {
            id: "routes",
            label: "Paper routes",
            count: state?.deployments.length,
          },
        ]}
      />
      <section
        role="tabpanel"
        id={`workflow-panel-${tab}`}
        aria-labelledby={`workflow-${tab}`}
      >
        {tab === "library" ? (
          <StrategySettings embedded />
        ) : (
          <div className="paper-account-list">
            <div className="workflow-route-intro">
              <p>
                Each route ties a saved version to a market and a paper account.
              </p>
              <button className="secondary small" onClick={trading}>
                Open paper trading
              </button>
            </div>
            {state?.deployments.map((d) => (
              <article className="paper-card" key={d.id}>
                <h2>{d.strategy}</h2>
                <p>
                  {market.instruments.find((i) => i.id === d.instrument_id)
                    ?.symbol ?? d.instrument_id.split(":").at(-1)}{" "}
                  ·{" "}
                  {state.accounts.find((a) => a.id === d.account_id)?.name ??
                    "Paper account"}{" "}
                  · {d.strategy_version}
                </p>
                <DeploymentControls deployment={d} />
              </article>
            ))}
            {state && !state.deployments.length && (
              <p className="empty-copy">
                No paper routes yet. Choose a saved strategy in the library,
                then select an account and allocation.
              </p>
            )}
          </div>
        )}
      </section>
      <div className="workflow-next-step">
        <span>Have an idea ready to test?</span>
        <button className="text-button" onClick={research}>
          Open research &amp; backtests
        </button>
      </div>
      <p className="workflow-authority">
        New versions leave existing routes unchanged. Tauric evidence and
        approvals are required wherever the trading policy calls for them.
      </p>
    </div>
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
