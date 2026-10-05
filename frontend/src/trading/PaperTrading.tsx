import { DeploymentCosts, stockCostValues } from "./DeploymentCosts";
import { connectionLabel } from "../settings/aiLabels";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  api,
  ApiError,
  type PaperState,
  type PaperPolicy,
  type PaperAccount,
  type TradingRecord,
  type GovernanceStatus,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import { ArrowRight, RefreshCw, ShieldCheck } from "lucide-react";
import { StrategySettings, DeploymentControls } from "./StrategySettings";
import { CapitalSettings, allocationPath } from "./CapitalSettings";
import { StrategyRunner } from "./StrategyRunner";
import { BrokerExecution } from "./BrokerExecution";
import { AutonomousPaper } from "./AutonomousPaper";
import { StrategySchedule } from "./StrategySchedule";
import "./paper.css";

const explain = (err: unknown) =>
  err instanceof Error ? err.message : "Could not complete the paper action.";
function usePaperState() {
  const [state, setState] = useState<PaperState | null>(null);
  const [governance, setGovernance] = useState<GovernanceStatus | null>(null);
  const [governanceError, setGovernanceError] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const active = useRef(true);
  const reading = useRef(false);
  const acting = useRef(false);
  const ids = useRef(new Map<string, string>());
  async function reload() {
    if (reading.current) return;
    reading.current = true;
    try {
      const governanceRead = api
        .governance()
        .then((value) => {
          if (active.current) {
            setGovernance(value);
            setGovernanceError("");
          }
        })
        .catch((err) => {
          if (active.current) {
            setGovernance(null);
            setGovernanceError(explain(err));
          }
        });
      const value = await api.paper();
      if (active.current) {
        setState(value);
        setLoadError("");
      }
      await governanceRead;
    } catch (err) {
      if (active.current) {
        setState(null);
        setLoadError(explain(err));
      }
    } finally {
      reading.current = false;
    }
  }
  useEffect(() => {
    active.current = true;
    void reload();
    const timer = window.setInterval(reload, 5000);
    return () => {
      active.current = false;
      window.clearInterval(timer);
    };
  }, []);
  async function act(
    key: string,
    perform: (id: string) => Promise<unknown>,
    success: string,
  ) {
    if (acting.current) return false;
    acting.current = true;
    setPending(true);
    setError("");
    setNotice("");
    const id = ids.current.get(key) ?? crypto.randomUUID();
    ids.current.set(key, id);
    try {
      await perform(id);
      ids.current.delete(key);
      await reload();
      setNotice(success);
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500)
        ids.current.delete(key);
      setError(explain(err));
      return false;
    } finally {
      acting.current = false;
      setPending(false);
    }
  }
  return {
    state,
    governance,
    governanceError,
    loadError,
    error: error || loadError,
    pending,
    notice,
    reload,
    act,
  };
}
const Context = createContext<ReturnType<typeof usePaperState> | null>(null);
export function PaperProvider({ children }: { children: ReactNode }) {
  const value = usePaperState();
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function usePaper() {
  const value = useContext(Context);
  if (!value) throw new Error("Paper context missing");
  return value;
}

const policyFields: [keyof PaperPolicy, string, boolean][] = [
  ["risk_percent", "Risk per trade (%)", false],
  ["max_total_risk_percent", "Total open risk (%)", false],
  ["max_exposure_percent", "Maximum exposure (%)", false],
  ["max_positions", "Maximum open positions", true],
  ["fee_bps", "Fee (basis points)", false],
  ["slippage_bps", "Slippage (basis points)", false],
  ["quote_max_age_seconds", "Maximum price age (seconds, 1–60)", true],
  ["decision_ttl_seconds", "Decision valid for (seconds, 1–3600)", true],
];
function policyFrom(form: FormData): PaperPolicy {
  return Object.fromEntries(
    policyFields.map(([key, , integer]) => [
      key,
      integer ? Number(form.get(key)) : String(form.get(key)),
    ]),
  ) as PaperPolicy;
}
function PolicyFields({ value }: { value?: PaperPolicy }) {
  return (
    <div className="paper-form-grid">
      {policyFields.map(([key, label, integer]) => (
        <label key={key}>
          {label}
          <input
            name={key}
            required
            inputMode={integer ? "numeric" : "decimal"}
            defaultValue={value?.[key] ?? ""}
            maxLength={40}
          />
        </label>
      ))}
    </div>
  );
}
function Status() {
  const { error, notice } = usePaper();
  return (
    <>
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
    </>
  );
}
function AccountPolicy({ account }: { account: PaperAccount }) {
  const { act, pending } = usePaper();
  const [edit, setEdit] = useState(false);
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (
      await act(
        `policy-${account.id}-${account.revision}`,
        () =>
          api.savePaperPolicy(account.id, {
            expected_revision: account.revision,
            policy: policyFrom(f),
            reason: String(f.get("reason")),
          }),
        "Paper policy saved. Earlier versions remain in history.",
      )
    )
      setEdit(false);
  }
  return (
    <article className="paper-card">
      <div className="paper-card-head">
        <div>
          <span className="eyebrow">PAPER ACCOUNT · {account.currency}</span>
          <h2>{account.name}</h2>
        </div>
        <span className={`badge ${account.halted ? "neutral" : "cyan"}`}>
          {account.halted ? "HALTED" : "PAPER"}
        </span>
      </div>
      <div className="paper-balances">
        <div>
          <span>Cash</span>
          <strong>{account.cash}</strong>
        </div>
        <div>
          <span>Realized P&amp;L</span>
          <strong>{account.realized_pnl}</strong>
        </div>
        <div>
          <span>Starting balance</span>
          <strong>{account.starting_balance}</strong>
        </div>
      </div>
      <p className="muted">
        Policy v{account.policy_revision} · {account.policy.risk_percent}% trade
        risk · {account.policy.max_total_risk_percent}% total risk ·{" "}
        {account.policy.max_exposure_percent}% exposure ·{" "}
        {account.policy.max_positions} positions · Ledger reconciled
      </p>
      <div className="paper-actions">
        <button
          className="secondary small"
          disabled={pending}
          onClick={() => setEdit(!edit)}
        >
          {edit ? "Close policy editor" : "Edit risk policy"}
        </button>
        <button
          className="secondary small"
          disabled={pending}
          onClick={() =>
            void act(
              `halt-${account.id}-${account.revision}`,
              () =>
                api.haltPaper(account.id, {
                  expected_revision: account.revision,
                  halted: !account.halted,
                  reason: account.halted
                    ? "Owner resumed paper entries"
                    : "Owner halted paper entries",
                }),
              account.halted
                ? "Paper entries resumed."
                : "New paper entries halted. Existing positions can still be closed.",
            )
          }
        >
          {account.halted ? "Resume paper entries" : "Halt new entries"}
        </button>
      </div>
      {edit && (
        <form
          aria-label={`${account.name} risk policy`}
          onSubmit={save}
          key={account.revision}
        >
          <fieldset disabled={pending}>
            <PolicyFields value={account.policy} />
            <label>
              Change reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <button className="primary small" type="submit">
              Save risk policy
            </button>
          </fieldset>
        </form>
      )}
    </article>
  );
}
export function TradingSettings() {
  const { state, pending, act } = usePaper();
  const market = useMarket();
  const [creating, setCreating] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [strategyAccount, setStrategyAccount] = useState("");
  const [instrumentId, setInstrumentId] = useState("");
  const accountId = strategyAccount || state?.accounts[0]?.id || "";
  const currency =
    state?.accounts.find((a) => a.id === accountId)?.currency ?? "USDT";
  const scopes =
    state?.capital_scopes.filter((v) => v.account_id === accountId) ?? [];
  const compatibleInstruments = market.instruments.filter(
    (i) =>
      ["crypto_spot", "fx", "equity", "etf"].includes(i.asset_type) &&
      i.quote_currency === currency,
  );
  const deploymentInstrument =
    compatibleInstruments.find((i) => i.id === instrumentId) ??
    compatibleInstruments[0];
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (
      await act(
        `account-${String(f.get("name"))}-${String(f.get("starting_balance"))}-${JSON.stringify(policyFrom(f))}`,
        (id) =>
          api.createPaperAccount({
            request_id: id,
            name: String(f.get("name")),
            currency: String(f.get("currency")) as "USDT" | "USD" | "JPY",
            starting_balance: String(f.get("starting_balance")),
            policy: policyFrom(f),
            reason: String(f.get("reason")),
          }),
        "Paper account created with your starting balance and policy.",
      )
    )
      setCreating(false);
  }
  async function deploy(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const data = Object.fromEntries(f) as Record<string, string>;
    const scope = scopes.find((v) => v.id === data.capital_scope_id);
    if (!scope || !state) return;
    const portfolio = allocationPath(scope, state.capital_scopes)[0];
    if (
      await act(
        `deploy-${JSON.stringify(data)}`,
        (id) =>
          api.createDeployment({
            request_id: id,
            account_id: data.account_id,
            instrument_id: data.instrument_id,
            portfolio: portfolio.name,
            capital_pot: scope.name,
            capital_scope_id: scope.id,
            strategy: data.strategy,
            strategy_version: data.strategy_version,
            quantity_step: data.quantity_step,
            capital_limit: data.capital_limit,
            risk_percent: data.risk_percent,
            stock_costs: stockCostValues(
              f,
              deploymentInstrument?.asset_type ?? "",
            ),
            fx_triple_rollover_weekday: 2,
            fx_financing_daily_bps:
              market.instruments.find((i) => i.id === data.instrument_id)
                ?.asset_type === "fx"
                ? data.fx_financing_daily_bps
                : undefined,
            reason: data.reason,
          }),
        "Paper strategy deployment created.",
      )
    )
      setDeploying(false);
  }
  return (
    <div className="paper-settings">
      <Status />
      <div className="paper-intro">
        <ShieldCheck size={22} />
        <div>
          <h2>Paper trading setup</h2>
          <p>
            Choose a simulated starting balance, costs and risk limits. All
            trades stay in Tauric’s paper ledger. Choose the account currency to
            match the instrument’s quote currency. Forex simulation is long-only
            and unlevered.
          </p>
        </div>
      </div>
      <div className="paper-actions">
        <button
          className="primary small"
          disabled={pending || !state}
          onClick={() => setCreating(!creating)}
        >
          {creating ? "Close account form" : "Add paper account"}
        </button>
        <button
          className="secondary small"
          disabled={pending || !state?.capital_scopes.length}
          onClick={() => setDeploying(!deploying)}
        >
          {deploying ? "Close strategy form" : "Add paper strategy"}
        </button>
      </div>
      {creating && (
        <form
          className="paper-card"
          aria-label="New paper account"
          onSubmit={create}
        >
          <h2>New paper account</h2>
          <label>
            Account currency
            <select name="currency" defaultValue="USDT">
              <option value="USDT">USDT · crypto spot</option>
              <option value="USD">USD · Forex, stocks and ETFs</option>
              <option value="JPY">JPY · USD/JPY</option>
            </select>
          </label>
          <fieldset disabled={pending}>
            <div className="paper-form-grid">
              <label>
                Account name
                <input name="name" required minLength={3} maxLength={80} />
              </label>
              <label>
                Starting balance (account currency)
                <input
                  name="starting_balance"
                  required
                  inputMode="decimal"
                  maxLength={40}
                />
              </label>
            </div>
            <PolicyFields />
            <DeploymentCosts asset={deploymentInstrument?.asset_type ?? ""} />
            <label>
              Setup reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <p className="muted">
              One basis point is 0.01%. Enter zero costs only when you
              explicitly want a zero-cost simulation.
            </p>
            <button className="primary small" type="submit">
              Create paper account
            </button>
          </fieldset>
        </form>
      )}
      <CapitalSettings />
      <StrategySettings />
      {deploying && (
        <form
          className="paper-card"
          aria-label="New paper strategy"
          onSubmit={deploy}
        >
          <h2>New paper strategy</h2>
          <fieldset disabled={pending}>
            <div className="paper-form-grid">
              <label>
                Paper account
                <select
                  name="account_id"
                  aria-label="Paper account"
                  required
                  value={accountId}
                  onChange={(e) => setStrategyAccount(e.target.value)}
                >
                  {state?.accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Instrument
                <select
                  name="instrument_id"
                  aria-label="Instrument"
                  required
                  value={deploymentInstrument?.id ?? ""}
                  onChange={(e) => setInstrumentId(e.target.value)}
                >
                  {market.instruments
                    .filter(
                      (i) =>
                        ["crypto_spot", "fx", "equity", "etf"].includes(
                          i.asset_type,
                        ) && i.quote_currency === currency,
                    )
                    .map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.symbol}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Shared capital allocation
                <select
                  name="capital_scope_id"
                  required
                  key={accountId}
                  aria-label="Shared capital allocation"
                >
                  <option value="">Choose a portfolio or pot</option>
                  {scopes.map((v) => (
                    <option key={v.id} value={v.id}>
                      {allocationPath(v, state?.capital_scopes ?? [])
                        .map((a) => a.name)
                        .join(" / ")}
                    </option>
                  ))}
                </select>
              </label>
              {[
                ["strategy", "Strategy name"],
                ["strategy_version", "Strategy version"],
                ["capital_limit", `Strategy capital limit (${currency})`],
                ["risk_percent", "Strategy risk per trade (%)"],
                ["quantity_step", "Paper quantity step"],
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input name={key} required maxLength={80} />
                </label>
              ))}
            </div>
            <label>
              Setup reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <p className="muted">
              The quantity step is your paper convention. It is not a verified
              broker contract. Strategies share the selected allocation’s
              budget. Every parent also enforces its capital, risk and position
              limits.
            </p>
            <button className="primary small" type="submit">
              Create paper strategy
            </button>
          </fieldset>
        </form>
      )}
      <div className="paper-account-list">
        {state?.accounts.map((a) => (
          <AccountPolicy key={a.id} account={a} />
        ))}
      </div>
      {state && !state.accounts.length && (
        <p className="muted">
          No paper accounts yet. Add your own balance and limits to start.
        </p>
      )}
      {!!state?.deployments.length && (
        <article className="paper-card">
          <h2>Paper strategies</h2>
          {state.deployments.map((d) => (
            <div className="paper-deployment" key={d.id}>
              <strong>
                {d.strategy} · {d.strategy_version}
              </strong>
              <span>
                {state.accounts.find((a) => a.id === d.account_id)?.name} ·{" "}
                {d.portfolio} / {d.capital_pot} ·{" "}
                {d.instrument_id.split(":").at(-1)} · {d.capital_limit}{" "}
                {state?.accounts.find((a) => a.id === d.account_id)?.currency}
                capital limit ·{" "}
                {d.capital_scope_id
                  ? "Shared allocation"
                  : "Legacy labels — assign a new deployment to use shared budgets"}
              </span>
              <DeploymentControls deployment={d} />
            </div>
          ))}
        </article>
      )}
    </div>
  );
}

function RecordButton({
  record,
  open,
}: {
  record: TradingRecord;
  open: (type: string, id: string) => void;
}) {
  return (
    <button
      className="text-button"
      onClick={() => open("trading_record", record.id)}
    >
      {record.kind}
      <ArrowRight size={12} />
    </button>
  );
}
export function PaperJournal({
  open,
  fillsOnly = false,
}: {
  open: (type: string, id: string) => void;
  fillsOnly?: boolean;
}) {
  const { state, loadError } = usePaper();
  const records =
    state?.records.filter((r) => !fillsOnly || r.kind === "fill") ?? [];
  return (
    <div className="paper-journal">
      <div className="panel-kicker">
        PAPER {fillsOnly ? "FILLS" : "DECISIONS & TRADES"}
        <span className="badge cyan">SIMULATED</span>
      </div>
      {!state && (
        <p className="error">{loadError || "Loading paper ledger…"}</p>
      )}
      {state && !records.length && (
        <p className="muted">No paper trades recorded yet.</p>
      )}
      {records.slice(0, 20).map((r) => (
        <div className="paper-journal-row" key={r.id}>
          <RecordButton record={r} open={open} />
          <div>
            <strong>{String(r.payload.side ?? r.payload.outcome ?? "")}</strong>
            <span>{String(r.payload.reason ?? "")}</span>
          </div>
          <time>{new Date(r.created_at).toLocaleTimeString("en-GB")}</time>
        </div>
      ))}
    </div>
  );
}
export function PaperPositions({
  open,
  compact = false,
}: {
  open: (type: string, id: string) => void;
  compact?: boolean;
}) {
  const { state, loadError, pending, act } = usePaper();
  return (
    <div className={`paper-positions ${compact ? "compact" : ""}`}>
      <div className="panel-kicker">
        OPEN PAPER POSITIONS{" "}
        <span className="badge cyan">{state?.positions.length ?? 0}</span>
      </div>
      {!state && (
        <p className="error">{loadError || "Loading paper ledger…"}</p>
      )}
      {state && !state.positions.length && (
        <p className="muted">No open paper positions.</p>
      )}
      {state?.positions.map((pos) => (
        <article key={pos.id} className="paper-position">
          <div>
            <strong>{pos.instrument_id.split(":").at(-1)}</strong>
            <span>
              {pos.strategy} · {pos.strategy_version}
            </span>
            <span>
              {state.accounts.find((a) => a.id === pos.account_id)?.name} ·{" "}
              {pos.portfolio} / {pos.capital_pot}
            </span>
          </div>
          <div>
            <strong>{pos.quantity}</strong>
            <span>
              Entry {pos.entry_price}{" "}
              {state?.accounts.find((a) => a.id === pos.account_id)?.currency}
            </span>
            <span>Stop reference {pos.stop_reference}</span>
          </div>
          <div className="paper-actions">
            <button
              className="text-button"
              onClick={() => open("trading_record", pos.id)}
            >
              Details
            </button>
            <button
              className="secondary small"
              disabled={pending}
              onClick={() =>
                void act(
                  `close-${pos.id}`,
                  async (id) => {
                    await api.refreshMarket(pos.instrument_id, "1m");
                    return api.closePaper(pos.id, {
                      request_id: id,
                      reason: "Owner closed attributed paper position",
                    });
                  },
                  "Paper position closed. The journal includes costs and realized P&L.",
                )
              }
            >
              Close paper position
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}
export function PaperWorkbench({
  setup,
  open,
}: {
  setup: () => void;
  open: (type: string, id: string) => void;
}) {
  const { state, pending, act, reload, governance, governanceError } =
    usePaper();
  const market = useMarket();
  const [deploymentId, setDeploymentId] = useState("");
  const [proposalId, setProposalId] = useState("");
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<"allow" | "reject" | "defer">("defer");
  const [reviewer, setReviewer] = useState<"owner" | "cloud" | "local">(
    "owner",
  );
  const dep = state?.deployments.find((d) => d.id === deploymentId);
  const stock = !!dep?.stock_costs;
  const currency =
    state?.accounts.find((a) => a.id === dep?.account_id)?.currency ??
    "account currency";
  const rules = state?.strategy_versions.find(
    (v) => v.id === dep?.strategy_revision_id,
  )?.parameters.rules;
  const ruleRun = state?.strategy_runs.find((r) => r.deployment_id === dep?.id);
  const signalProposed = state?.records.some(
    (r) =>
      r.kind === "proposal" &&
      r.deployment_id === dep?.id &&
      r.payload.strategy_signal_key === ruleRun?.payload.signal_key,
  );
  const entrySignal =
    rules &&
    ruleRun?.action === "enter" &&
    new Date(String(ruleRun.payload.expires_at)).getTime() > Date.now()
      ? ruleRun
      : undefined;
  const proposal = state?.records.find(
    (r) => r.id === proposalId && r.kind === "proposal",
  );
  const decision = state?.records.find(
    (r) => r.kind === "governance" && r.payload.proposal_id === proposalId,
  );
  const reviewJob = governance?.jobs.find((j) => j.proposal_id === proposalId);
  const reviewing =
    reviewJob?.status === "queued" || reviewJob?.status === "running";
  const route = governance?.routes.find((r) => r.id === reviewer);
  const plan = state?.records.find(
    (r) => r.kind === "plan" && r.payload.decision_id === decision?.id,
  );
  const fill = state?.records.find(
    (r) => r.kind === "fill" && r.payload.plan_id === plan?.id,
  );
  const reports = market.jobs.filter(
    (j) =>
      j.status === "succeeded" &&
      j.artifact_id &&
      j.instrument_id === dep?.instrument_id,
  );
  async function propose(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!dep) return;
    const f = new FormData(e.currentTarget);
    const stop = String(f.get("stop_price")),
      why = String(f.get("reason")),
      report = String(f.get("native_artifact_id") ?? "");
    await act(
      `proposal-${dep.id}-${entrySignal?.id ?? "manual"}-${stop}-${why}-${report}`,
      async (id) => {
        await api.refreshMarket(dep.instrument_id, "1m");
        const r = await api.proposePaper({
          request_id: id,
          deployment_id: dep.id,
          stop_price: stop,
          reason: why,
          native_artifact_id: report || null,
          strategy_run_id: rules ? entrySignal?.id : null,
        });
        setProposalId(r.id);
        setReason("");
        return r;
      },
      "Paper proposal recorded. Review it before planning.",
    );
  }
  return (
    <div className="page-scroll paper-workbench">
      <div className="page-heading">
        <div>
          <span className="eyebrow">GOVERNANCE → PLAN → LEDGER</span>
          <h1>Paper trading</h1>
          <p>Test the full decision and sizing path with simulated money.</p>
        </div>
        <div className="paper-actions">
          <button className="secondary small" onClick={setup}>
            Trading settings
          </button>
          <button
            className="icon-button"
            aria-label="Reload paper records"
            onClick={() => void reload()}
          >
            <RefreshCw size={17} />
          </button>
          <span className="badge cyan">PAPER ONLY</span>
        </div>
      </div>
      <Status />
      <BrokerExecution />
      <div className="paper-warning">
        Stops are sizing references. Executable strategies can monitor closed
        candles when you enable a schedule. Enable autonomous paper execution
        for reviewed entries and attributed rule exits. Prices use observed
        candle closes with your configured costs.
      </div>
      {rules && dep && (
        <>
          <StrategySchedule key={dep.id} deployment={dep} rules={rules} />
          <AutonomousPaper
            key={`autonomy-${dep.id}`}
            deployment={dep}
            open={open}
          />
        </>
      )}
      {!state?.deployments.length ? (
        <article className="paper-card">
          <h2>Set up your paper account</h2>
          <p>
            Add a starting balance, risk policy and strategy in Settings to
            begin.
          </p>
          <button className="primary small" onClick={setup}>
            Open trading settings
          </button>
        </article>
      ) : (
        <div className="paper-flow-grid">
          <form
            className="paper-card"
            aria-label="Paper proposal"
            onSubmit={propose}
          >
            <span className="eyebrow">01 · PROPOSAL</span>
            <h2>Define a paper trade</h2>
            <fieldset disabled={pending}>
              <label>
                Paper strategy
                <select
                  required
                  aria-label="Paper strategy"
                  value={deploymentId}
                  onChange={(e) => {
                    setDeploymentId(e.target.value);
                    if (
                      state.deployments.find((d) => d.id === e.target.value)
                        ?.stock_costs
                    )
                      setReviewer("cloud");
                  }}
                >
                  <option value="">Choose a strategy</option>
                  {state.deployments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.strategy} · {d.strategy_version} ·{" "}
                      {d.instrument_id.split(":").at(-1)}
                    </option>
                  ))}
                </select>
              </label>
              {rules && dep && (
                <StrategyRunner deployment={dep} run={ruleRun} open={open} />
              )}
              <label>
                Stop reference ({currency})
                <input
                  key={`${deploymentId}-${entrySignal?.id ?? "manual"}`}
                  name="stop_price"
                  required
                  inputMode="decimal"
                  maxLength={40}
                  readOnly={!!rules}
                  defaultValue={
                    rules ? String(entrySignal?.payload.stop_price ?? "") : ""
                  }
                />
              </label>
              <label>
                Original native report
                <select
                  name="native_artifact_id"
                  aria-label="Original native report"
                  required={stock}
                >
                  <option value="">
                    {stock
                      ? "Choose current daily stock research"
                      : "Owner test proposal — no AI report"}
                  </option>
                  {reports.map((j) => (
                    <option key={j.id} value={j.artifact_id!}>
                      {connectionLabel(j.profile)} ·{" "}
                      {new Date(j.created_at).toLocaleString("en-GB")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Proposal reason
                <input name="reason" required minLength={3} maxLength={500} />
              </label>
              <p className="muted">
                Long spot entries only. Tauric refreshes 1-minute observations,
                then sizes from your stop and policy. Native daily USD reports
                are background evidence; Forex reports do not approve a paper
                entry.
              </p>
              <button
                className="primary small"
                type="submit"
                disabled={
                  !dep ||
                  !dep.enabled ||
                  (!!rules && (!entrySignal || signalProposed))
                }
              >
                Record paper proposal
              </button>
            </fieldset>
          </form>
          <article className="paper-card">
            <span className="eyebrow">02 · GOVERNANCE</span>
            <h2>Review the proposal</h2>
            <label>
              Recorded proposal
              <select
                aria-label="Recorded proposal"
                value={proposalId}
                onChange={(e) => {
                  setProposalId(e.target.value);
                  setReason("");
                }}
              >
                <option value="">Choose a proposal</option>
                {state.records
                  .filter((r) => r.kind === "proposal")
                  .map((r) => (
                    <option key={r.id} value={r.id}>
                      {String(r.payload.instrument_id).split(":").at(-1)} ·{" "}
                      {String(r.payload.reason)}
                    </option>
                  ))}
              </select>
            </label>
            {proposal && (
              <>
                <RecordButton record={proposal} open={open} />
                <p className="muted">
                  Observed {String(proposal.payload.observed_price)} {currency}{" "}
                  · Stop {String(proposal.payload.stop_price)}
                </p>
              </>
            )}
            {!decision ? (
              <>
                <label>
                  Reviewer
                  <select
                    aria-label="Reviewer"
                    value={reviewer}
                    disabled={pending || reviewing}
                    onChange={(e) =>
                      setReviewer(e.target.value as typeof reviewer)
                    }
                  >
                    <option value="owner" disabled={stock}>
                      Owner test review
                    </option>
                    <option value="cloud">
                      OpenClaw · {connectionLabel("cloud")}
                    </option>
                    <option value="local">
                      OpenClaw · {connectionLabel("local")}
                    </option>
                  </select>
                </label>
                {reviewJob && (
                  <div className="paper-decision">
                    <span className="badge cyan">
                      {reviewJob.status.toUpperCase()} ·{" "}
                      {connectionLabel(reviewJob.profile)}
                    </span>
                    <p className="muted">{reviewJob.model}</p>
                    {reviewJob.error && (
                      <p role="alert" className="error">
                        {reviewJob.error}
                      </p>
                    )}
                    <button
                      className="text-button"
                      onClick={() => open("governance_job", reviewJob.id)}
                    >
                      Inspect AI review
                    </button>
                  </div>
                )}
                {reviewer !== "owner" ? (
                  <>
                    <p className="muted">
                      {route?.detail ||
                        governanceError ||
                        "Loading review connection…"}
                    </p>
                    {!proposal?.payload.native_artifact_id && (
                      <p className="muted">
                        Link a successful original native report when creating
                        the proposal to request an AI review.
                      </p>
                    )}
                    <button
                      className="primary small"
                      disabled={
                        pending ||
                        reviewing ||
                        !proposal?.payload.native_artifact_id ||
                        !route?.ready
                      }
                      onClick={() =>
                        void act(
                          `ai-review-${proposalId}-${reviewer}`,
                          (id) =>
                            api.requestGovernance({
                              request_id: id,
                              proposal_id: proposalId,
                              profile: reviewer,
                              reason:
                                "Owner requested OpenClaw paper governance",
                            }),
                          "OpenClaw review queued. Its result will be validated before paper planning.",
                        )
                      }
                    >
                      {reviewing
                        ? "Review in progress…"
                        : "Request OpenClaw review"}
                    </button>
                  </>
                ) : (
                  <>
                    <label>
                      Review outcome
                      <select
                        aria-label="Review outcome"
                        value={outcome}
                        disabled={pending}
                        onChange={(e) =>
                          setOutcome(e.target.value as typeof outcome)
                        }
                      >
                        <option value="defer">Defer</option>
                        <option value="reject">Reject</option>
                        <option value="allow">Allow paper planning</option>
                      </select>
                    </label>
                    <label>
                      Review reason
                      <input
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        minLength={3}
                        maxLength={500}
                      />
                    </label>
                    <button
                      className="primary small"
                      disabled={
                        pending ||
                        reviewing ||
                        !proposal ||
                        reason.trim().length < 3
                      }
                      onClick={() =>
                        void act(
                          `review-${proposalId}-${outcome}-${reason}`,
                          (id) =>
                            api.reviewPaper(proposalId, {
                              request_id: id,
                              outcome,
                              reason,
                            }),
                          "Owner paper review recorded separately from native research.",
                        )
                      }
                    >
                      Save owner review
                    </button>
                  </>
                )}
              </>
            ) : (
              <div className="paper-decision">
                <span className="badge cyan">
                  {String(decision.payload.outcome).toUpperCase()} ·{" "}
                  {decision.payload.source === "openclaw"
                    ? "OPENCLAW REVIEW"
                    : "OWNER REVIEW"}
                </span>
                <p>{String(decision.payload.reason)}</p>
                <RecordButton record={decision} open={open} />
              </div>
            )}
            <p className="muted">
              Reviews are stored separately from the original research. Both
              reviewers authorize paper planning only.
            </p>
          </article>
          <article className="paper-card">
            <span className="eyebrow">03 · PLAN & SIMULATE</span>
            <h2>Inspect the sized order</h2>
            {!plan ? (
              <>
                <p className="muted">
                  An allowed decision lets Tauric calculate size from cash,
                  exposure, aggregate risk, strategy capital and fees.
                </p>
                <button
                  className="primary small"
                  disabled={pending || decision?.payload.outcome !== "allow"}
                  onClick={() =>
                    decision &&
                    void act(
                      `plan-${decision.id}`,
                      async (id) => {
                        const instruments = new Set([
                          String(proposal?.payload.instrument_id),
                          ...(state?.positions.map((p) => p.instrument_id) ??
                            []),
                        ]);
                        await Promise.all(
                          [...instruments].map((instrument) =>
                            api.refreshMarket(instrument, "1m"),
                          ),
                        );
                        return api.planPaper(decision.id, {
                          request_id: id,
                          reason: "Owner requested deterministic paper plan",
                        });
                      },
                      "Paper order plan ready. Inspect the size and costs before simulation.",
                    )
                  }
                >
                  Build paper plan
                </button>
              </>
            ) : (
              <>
                <dl className="paper-plan">
                  {[
                    ["Quantity", plan.payload.quantity],
                    ["Simulated entry", plan.payload.price],
                    ["Entry fee", plan.payload.fee],
                    ["Estimated stop risk", plan.payload.estimated_stop_risk],
                    [
                      "Price expires",
                      new Date(
                        String(plan.payload.expires_at),
                      ).toLocaleTimeString("en-GB"),
                    ],
                  ].map(([label, value]) => (
                    <div key={String(label)}>
                      <dt>{String(label)}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ))}
                </dl>
                <RecordButton record={plan} open={open} />
                {fill ? (
                  <p className="settings-success">
                    Simulated fill recorded in the paper ledger.
                  </p>
                ) : (
                  <button
                    className="primary small"
                    disabled={pending}
                    onClick={() =>
                      void act(
                        `execute-${plan.id}`,
                        (id) =>
                          api.executePaper(plan.id, {
                            request_id: id,
                            reason: "Owner executed reviewed paper plan",
                          }),
                        "Paper fill recorded. Balances and positions reconcile.",
                      )
                    }
                  >
                    Execute paper plan
                  </button>
                )}
                <p className="muted">
                  If policy, balance or price changes, start a new proposal.
                  Every plan can fill once.
                </p>
              </>
            )}
          </article>
        </div>
      )}
      <div className="paper-flow-grid paper-results">
        <article className="paper-card">
          <PaperPositions open={open} />
        </article>
        <article className="paper-card">
          <PaperJournal open={open} />
        </article>
      </div>
    </div>
  );
}
export function PaperGovernance({ show }: { show: () => void }) {
  const { state, governance, governanceError } = usePaper();
  const decisions = state?.records.filter((r) => r.kind === "governance") ?? [];
  return (
    <div className="paper-governance">
      <span className="eyebrow">SEPARATE GOVERNANCE RECORDS</span>
      <h2>Paper decision review</h2>
      <p>
        Review a proposal with its original native research and your account
        policy. Sizing stays in the paper planner.
      </p>
      <span className="badge neutral">
        {governance?.routes.some((r) => r.ready)
          ? "AI REVIEW READY"
          : "AI SETUP NEEDED"}
      </span>
      {governanceError && (
        <p role="alert" className="error">
          {governanceError}
        </p>
      )}
      {governance?.jobs.slice(0, 3).map((job) => (
        <p key={job.id}>
          {connectionLabel(job.profile)} · {job.status} · {job.model}
        </p>
      ))}
      <p>
        {decisions.length
          ? `${decisions.length} recorded paper decisions`
          : "No paper decisions yet."}
      </p>
      <button className="text-button" onClick={show}>
        Open paper workflow
        <ArrowRight size={14} />
      </button>
    </div>
  );
}
