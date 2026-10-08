import { connectionLabel } from "../settings/aiLabels";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
import {
  api,
  ApiError,
  type ScannerState,
  type ScannerProfile,
  type ScannerParameters,
  type ScannerCandidate,
  type StrategyVersion,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import { InstrumentDiscovery } from "../market/InstrumentDiscovery";
import {
  assetLabel,
  opportunityLabels,
  timeframeLabels,
} from "../ui/workflowLabels";
import { WorkflowTabs } from "../ui/WorkflowTabs";
import { usePaper } from "../trading/PaperTrading";
import { Radar, ArrowRight, RefreshCw } from "lucide-react";
import "./scanner.css";

const detectorLabels = {
  closed_bar_breakout_v1: "Long breakout",
  closed_bar_breakdown_v1: "Short breakdown",
  trend_pullback_v1: "Trend pullback",
} as const;

function useScannerState() {
  const [state, setState] = useState<ScannerState | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const acting = useRef(false),
    reading = useRef(false),
    active = useRef(true);
  const ids = useRef(new Map<string, string>());
  async function reload() {
    if (reading.current) return;
    reading.current = true;
    try {
      const s = await api.scanners();
      if (active.current) {
        setState(s);
        setLoadError("");
      }
    } catch (e) {
      if (active.current) {
        setState(null);
        setLoadError(
          e instanceof Error ? e.message : "Scanner state is unavailable.",
        );
      }
    } finally {
      reading.current = false;
    }
  }
  useEffect(() => {
    active.current = true;
    void reload();
    const timer = window.setInterval(reload, 10000);
    return () => {
      active.current = false;
      window.clearInterval(timer);
    };
  }, []);
  async function act(
    key: string,
    call: (id: string) => Promise<unknown>,
    message: string,
  ) {
    if (acting.current) return false;
    acting.current = true;
    setPending(true);
    setError("");
    setNotice("");
    const id = ids.current.get(key) ?? crypto.randomUUID();
    ids.current.set(key, id);
    try {
      await call(id);
      ids.current.delete(key);
      setNotice(message);
      await reload();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scanner action failed.");
      if (e instanceof ApiError && e.status < 500) ids.current.delete(key);
      await reload();
      return false;
    } finally {
      acting.current = false;
      setPending(false);
    }
  }
  return { state, error, loadError, notice, pending, act, reload };
}
const Context = createContext<ReturnType<typeof useScannerState> | null>(null);
export function ScannerProvider({ children }: { children: ReactNode }) {
  const value = useScannerState();
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
function useScanner() {
  const value = useContext(Context);
  if (!value) throw new Error("Scanner context required");
  return value;
}
function Notices() {
  const s = useScanner();
  return (
    <>
      {s.error && (
        <p className="form-error" role="alert">
          {s.error}
        </p>
      )}
      {s.loadError && (
        <p className="form-error" role="alert">
          {s.loadError}{" "}
          <button className="text-button" onClick={() => void s.reload()}>
            Reload scanners
          </button>
        </p>
      )}
      {s.notice && (
        <p className="form-success" role="status">
          {s.notice}
        </p>
      )}
    </>
  );
}
function label(v: StrategyVersion) {
  return `${v.name} · ${v.parameters.label} · revision ${v.revision}`;
}
function ProfileForm({
  profile,
  close,
}: {
  profile?: ScannerProfile;
  close: () => void;
}) {
  const { state: paper } = usePaper(),
    market = useMarket(),
    s = useScanner();
  const p = profile?.parameters;
  const [setupError, setSetupError] = useState("");
  const [universe, setUniverse] = useState<string[]>(p?.universe ?? []);
  const [marketSearch, setMarketSearch] = useState("");
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const detectors = f
      .getAll("detector_ids")
      .map(String) as ScannerParameters["detector_id"][];
    if (!universe.length) {
      setSetupError("Choose at least one market to scan.");
      return;
    }
    if (!detectors.length) {
      setSetupError("Choose at least one setup family.");
      return;
    }
    setSetupError("");
    const parameters: ScannerParameters = {
      ...p,
      disabled_instruments:
        p?.disabled_instruments?.filter((id) => universe.includes(id)) ?? [],
      evaluate_on_candle_close: p?.evaluate_on_candle_close ?? true,
      max_data_age_seconds: p?.max_data_age_seconds ?? 960,
      require_active_session: p?.require_active_session ?? false,
      minimum_volatility_percent: p?.minimum_volatility_percent ?? 0,
      minimum_setup_score: Number(f.get("minimum_setup_score")),
      automatic_enabled: f.get("automatic_enabled") === "on",
      automatic_research_enabled: f.get("automatic_research_enabled") === "on",
      scan_seconds: Number(f.get("scan_seconds")),
      watch_scan_seconds: Number(f.get("watch_scan_seconds")),
      cooldown_seconds: Number(f.get("cooldown_seconds")),
      ranking: {
        ...p?.ranking,
        max_candidates_per_asset_class:
          p?.ranking?.max_candidates_per_asset_class ?? 2,
        max_candidates_per_strategy:
          p?.ranking?.max_candidates_per_strategy ?? 2,
        minimum_score: Number(f.get("ranking_minimum_score")),
        max_candidates_per_cycle: Number(f.get("max_candidates_per_cycle")),
      },
      enabled: f.get("enabled") === "on",
      universe: f.getAll("universe").map(String),
      interval: String(f.get("interval")) as ScannerParameters["interval"],
      detector_id: detectors[0],
      detector_ids: detectors,
      lookback: Number(f.get("lookback")),
      minimum_volume_ratio: String(f.get("minimum_volume_ratio")),
      candidate_ttl_seconds: Number(f.get("candidate_ttl_seconds")),
      max_analyses_per_day: Number(f.get("max_analyses_per_day")),
      model_profile: String(f.get("model_profile")) as "cloud" | "local",
    };
    const body = {
      profile_id: profile?.profile_id ?? null,
      name: String(f.get("name")).trim(),
      strategy_version_id: String(f.get("strategy_version_id") ?? "") || null,
      expected_revision: profile?.revision ?? 0,
      parameters,
      reason: String(f.get("reason")),
    };
    if (
      await s.act(
        `profile-${JSON.stringify(body)}`,
        (id) => api.saveScanner({ ...body, request_id: id }),
        "Scanner profile saved. Scheduled discovery and automatic research follow the selected settings.",
      )
    )
      close();
  }
  return (
    <form
      className="scanner-form paper-card"
      aria-label="Scanner profile form"
      onSubmit={save}
    >
      <fieldset disabled={s.pending}>
        <div className="paper-card-head">
          <h2>{profile ? "Edit scanner" : "New scanner"}</h2>
          <span className="badge">
            {profile ? `Version ${profile.revision + 1}` : "Research only"}
          </span>
        </div>
        <p className="muted">
          Choose where to look and what to look for. Scanning uses market rules;
          Tauric research is a separate step.
        </p>
        <div className="paper-form-grid">
          <label>
            Scanner name
            <input
              name="name"
              aria-label="Scanner name"
              required
              minLength={3}
              maxLength={80}
              defaultValue={profile?.name ?? ""}
              placeholder="e.g. Forex trend watch"
            />
          </label>
          <label>
            Timeframe
            <select
              name="interval"
              aria-label="Timeframe"
              defaultValue={p?.interval ?? "15m"}
              required
            >
              {Object.entries(timeframeLabels).map(([value, name]) => (
                <option key={value} value={value}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <fieldset className="scanner-market-picker">
          <legend>1. Markets to scan</legend>
          <label>
            Find a saved symbol
            <input
              type="search"
              value={marketSearch}
              onChange={(e) => setMarketSearch(e.target.value)}
              placeholder="Search Bitcoin, EUR/USD, Apple…"
            />
          </label>
          <div className="scanner-market-options">
            {market.instruments
              .filter((v) =>
                ["crypto_spot", "fx", "equity", "etf"].includes(v.asset_type),
              )
              .filter((v) =>
                `${v.symbol} ${v.name}`
                  .toLowerCase()
                  .includes(marketSearch.toLowerCase()),
              )
              .map((v) => (
                <label key={v.id} className="checkbox">
                  <input
                    type="checkbox"
                    aria-label={v.symbol}
                    checked={universe.includes(v.id)}
                    onChange={(e) =>
                      setUniverse((prev) =>
                        e.target.checked
                          ? [...prev, v.id]
                          : prev.filter((id) => id !== v.id),
                      )
                    }
                  />
                  {v.symbol}
                  <small>{assetLabel(v.asset_type)}</small>
                </label>
              ))}
          </div>
          {universe.map((id) => (
            <input key={id} type="hidden" name="universe" value={id} />
          ))}
          <p className="muted">
            {universe.length} selected
            {universe.length > 0
              ? ` · ${universe.map((id) => market.instruments.find((v) => v.id === id)?.symbol ?? id.split(":").at(-1)).join(" / ")}`
              : " · choose at least one symbol"}
          </p>
          <InstrumentDiscovery />
        </fieldset>
        <fieldset className="scanner-universe">
          <legend>2. Setups to find</legend>
          {Object.entries(detectorLabels).map(([id, name]) => (
            <label className="checkbox" key={id}>
              <input
                name="detector_ids"
                type="checkbox"
                value={id}
                defaultChecked={(p?.detector_ids?.length
                  ? p.detector_ids
                  : [p?.detector_id ?? "closed_bar_breakout_v1"]
                ).includes(id as ScannerParameters["detector_id"])}
              />
              {name}
            </label>
          ))}
        </fieldset>
        {setupError && (
          <p className="form-error" role="alert">
            {setupError}
          </p>
        )}
        <fieldset className="scanner-automation">
          <legend>3. When to run</legend>
          <label className="checkbox">
            <input
              name="enabled"
              type="checkbox"
              defaultChecked={p?.enabled ?? false}
            />
            Enable this scanner
          </label>
          <label className="checkbox">
            <input
              name="automatic_enabled"
              type="checkbox"
              defaultChecked={p?.automatic_enabled ?? false}
            />
            Scan automatically
          </label>
          <label className="checkbox">
            <input
              name="automatic_research_enabled"
              type="checkbox"
              defaultChecked={p?.automatic_research_enabled ?? false}
            />
            Ask Tauric to research matches automatically
          </label>
          <p className="muted">
            Automatic scanning needs a ready scanner service. Automatic research
            also needs a research allowance below and a ready research
            coordinator.
          </p>
        </fieldset>
        <details className="workflow-advanced">
          <summary>
            Advanced settings <span>Filters, timing and research limits</span>
          </summary>
          <div className="paper-form-grid">
            {[
              [
                "lookback",
                "Look back (closed candles)",
                p?.lookback ?? 20,
                2,
                100,
                1,
              ],
              [
                "minimum_volume_ratio",
                "Minimum activity (× average)",
                p?.minimum_volume_ratio ?? "1.5",
                0,
                undefined,
                "any",
              ],
              [
                "candidate_ttl_seconds",
                "Opportunity valid for (seconds)",
                p?.candidate_ttl_seconds ?? 3600,
                60,
                86400,
                1,
              ],
              [
                "minimum_setup_score",
                "Minimum setup score (0–1)",
                p?.minimum_setup_score ?? 0.5,
                0,
                1,
                0.01,
              ],
              [
                "ranking_minimum_score",
                "Minimum research score (0–1)",
                p?.ranking?.minimum_score ?? 0.65,
                0,
                1,
                0.01,
              ],
              [
                "max_candidates_per_cycle",
                "Research shortlist per scan",
                p?.ranking?.max_candidates_per_cycle ?? 2,
                1,
                100,
                1,
              ],
              [
                "scan_seconds",
                "Scan every (seconds)",
                p?.scan_seconds ?? 30,
                5,
                3600,
                1,
              ],
              [
                "watch_scan_seconds",
                "Recheck watching list every (seconds)",
                p?.watch_scan_seconds ?? 10,
                5,
                3600,
                1,
              ],
              [
                "cooldown_seconds",
                "Wait between repeated setups (seconds)",
                p?.cooldown_seconds ?? 0,
                0,
                86400,
                1,
              ],
              [
                "max_analyses_per_day",
                "Tauric research runs per day",
                p?.max_analyses_per_day ?? 0,
                0,
                100,
                1,
              ],
            ].map(([key, name, value, min, max, step]) => (
              <label key={String(key)}>
                {name}
                <input
                  name={String(key)}
                  type="number"
                  required
                  min={min}
                  max={max}
                  step={step}
                  defaultValue={value}
                />
              </label>
            ))}
            <label>
              Research AI connection
              <select
                name="model_profile"
                aria-label="Research AI connection"
                defaultValue={p?.model_profile ?? "local"}
                required
              >
                <option value="local">{connectionLabel("local")}</option>
                <option value="cloud">{connectionLabel("cloud")}</option>
              </select>
            </label>
            <label>
              Link a strategy (optional)
              <select
                name="strategy_version_id"
                aria-label="Scanner strategy version"
                defaultValue={profile?.strategy_version_id ?? ""}
              >
                <option value="">Screening and research only</option>
                {paper?.strategy_versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {label(v)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="muted">
            Only completed candles count. Activity means traded volume for
            crypto and stocks, and provider tick count for Forex. Missing data
            blocks a setup. Research allowance counts new jobs, including failed
            jobs; zero allows scanning without research.
          </p>
        </details>
        <label>
          Why save this change?
          <input name="reason" required minLength={3} maxLength={500} />
        </label>
        <div className="paper-actions">
          <button className="primary small" type="submit">
            Save scanner
          </button>
          <button className="secondary small" type="button" onClick={close}>
            Cancel
          </button>
        </div>
      </fieldset>
    </form>
  );
}

function ProfileCard({ p }: { p: ScannerProfile }) {
  const s = useScanner();
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState<ScannerProfile[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  async function showHistory() {
    try {
      setHistory(await api.scannerHistory(p.profile_id));
      setHistoryError("");
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "History unavailable.");
    }
  }
  return (
    <article className="paper-card" aria-label={`${p.name} scanner profile`}>
      <div className="paper-card-head">
        <div>
          <span className="eyebrow">
            CLOSED CANDLES ·{" "}
            {p.parameters.automatic_enabled
              ? "SCHEDULED SCREENING"
              : "MANUAL SCREENING"}
          </span>
          <h2>{p.name}</h2>
        </div>
        <span className={`badge ${p.parameters.enabled ? "cyan" : ""}`}>
          {p.parameters.enabled ? "ENABLED" : "DISABLED"}
        </span>
      </div>
      <p className="muted">
        {p.strategy_version_id
          ? `Linked strategy: ${p.strategy_name} · ${p.strategy_label}`
          : "Screening and research only · no strategy linked"}
      </p>
      <p className="muted">
        {(p.parameters.detector_ids?.length
          ? p.parameters.detector_ids
          : [p.parameters.detector_id]
        )
          .map((id) => detectorLabels[id])
          .join(" / ")}
        {" · "}Automatic research{" "}
        {p.parameters.automatic_research_enabled ? "on" : "off"}
      </p>
      <p className="scanner-symbols">
        {timeframeLabels[p.parameters.interval] ?? p.parameters.interval} ·{" "}
        {p.parameters.universe.map((v) => v.split(":").at(-1)).join(" / ")}
      </p>
      <details className="workflow-advanced">
        <summary>Filters &amp; research allowance</summary>
        <p>
          {p.parameters.lookback} prior candles · activity ≥{" "}
          {p.parameters.minimum_volume_ratio}× · opportunities expire after{" "}
          {p.parameters.candidate_ttl_seconds}s
        </p>
        <p className="muted">
          {p.charged_analyses_today}/{p.parameters.max_analyses_per_day} Tauric
          runs used today · {connectionLabel(p.parameters.model_profile)} ·
          version {p.revision}
        </p>
      </details>
      <div className="paper-actions">
        <button
          className="primary small"
          disabled={s.pending || !p.parameters.enabled}
          onClick={() =>
            void s.act(
              `scan-${p.id}`,
              (id) =>
                api.runScanner({
                  request_id: id,
                  profile_version_id: p.id,
                  trigger: "manual",
                }),
              "Screening completed. No AI calls were made.",
            )
          }
        >
          Scan now
        </button>
        <button
          className="secondary small"
          disabled={s.pending}
          onClick={() => setEditing(!editing)}
        >
          Edit scanner
        </button>
        <button className="text-button" onClick={() => void showHistory()}>
          Version history
        </button>
      </div>
      {editing && (
        <ProfileForm key={p.id} profile={p} close={() => setEditing(false)} />
      )}
      {historyError && (
        <p role="alert" className="form-error">
          {historyError}
        </p>
      )}
      {history && (
        <details open>
          <summary>Saved profile history</summary>
          {history.map((v) => (
            <p key={v.id}>
              Revision {v.revision} · {v.reason} ·{" "}
              {v.parameters.enabled ? "enabled" : "disabled"}
            </p>
          ))}
        </details>
      )}
    </article>
  );
}
export function ScannerSettings() {
  const s = useScanner();
  const [adding, setAdding] = useState(false);
  return (
    <div className="scanner-settings">
      <div className="paper-intro">
        <Radar size={22} />
        <div>
          <h2>My scans</h2>
          <p>
            Choose a saved scan or create one. Scans find setups; Tauric checks
            their market context when you request research.
          </p>
        </div>
      </div>
      <Notices />
      {s.state?.profiles.some(
        (p) => p.parameters.automatic_research_enabled,
      ) && (
        <p
          className={s.state.automatic_research_ready ? "muted" : "form-error"}
          role="status"
        >
          {s.state.automatic_research_ready
            ? "Automatic research coordinator is ready."
            : "Automatic research is waiting for its coordinator."}
        </p>
      )}
      <button
        className="secondary"
        disabled={s.pending}
        onClick={() => setAdding(!adding)}
      >
        {adding ? "Close scanner form" : "New scanner"}
      </button>
      {adding && <ProfileForm close={() => setAdding(false)} />}
      {s.state?.profiles.map((p) => (
        <ProfileCard key={p.profile_id} p={p} />
      ))}
      {s.state && !s.state.profiles.length && !adding && (
        <p className="empty-copy">
          Your first scan needs a name, markets and a setup. You can create it
          here or ask OpenClaw to prepare it.
        </p>
      )}
    </div>
  );
}
function CandidateDetail({
  c,
  open,
  chart,
}: {
  c: ScannerCandidate;
  open: (kind: string, id: string) => void;
  chart: () => void;
}) {
  const s = useScanner(),
    market = useMarket();
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState("");
  const matches = c.matches.filter((m) => m.eligible);
  const match =
    matches.find((m) => m.profile_version_id === selected) ?? matches[0];
  const reasonReady = reason.trim().length >= 3;
  const available = c.status === "candidate" || c.status === "watch";
  async function research() {
    if (!match) return;
    await s.act(
      `research-${c.id}-${match.profile_version_id}-${reason}`,
      (id) =>
        api.escalateCandidate(c.id, {
          request_id: id,
          profile_version_id: String(match.profile_version_id),
          reason,
        }),
      "Daily research requested or reused. This candidate has no trading permission.",
    );
    await market.reloadJobs();
  }
  return (
    <details className="scanner-detail">
      <summary>Review setup</summary>
      <details className="workflow-advanced">
        <summary>Why this setup matched</summary>
        <div className="scanner-features">
          {Object.entries(c.features).map(([k, v]) => (
            <div key={k}>
              <span>{k.replaceAll("_", " ")}</span>
              <strong className="mono">{v}</strong>
            </div>
          ))}
        </div>
        <ul>
          {c.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </details>
      <p className="muted">
        Detected {new Date(c.detected_at).toLocaleString()} · expires{" "}
        {new Date(c.expires_at).toLocaleString()} · research only
      </p>
      <div className="paper-actions">
        <button
          className="secondary small"
          onClick={() => {
            market.setInstrumentId(c.instrument_id);
            market.setIntervalValue(c.interval);
            chart();
          }}
        >
          Open chart
        </button>
        <button
          className="text-button"
          onClick={() => open("scanner_candidate", c.id)}
        >
          View original evidence
        </button>
      </div>
      <label>
        Use this scan for research
        <select
          aria-label="Candidate scanner profile"
          value={String(match?.profile_version_id ?? "")}
          onChange={(e) => setSelected(e.target.value)}
          disabled={!matches.length}
        >
          {matches.map((m) => (
            <option
              key={String(m.profile_version_id)}
              value={String(m.profile_version_id)}
            >
              {String(m.profile_name)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Why take this action?
        <input
          aria-label="Why take this action?"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
        />
      </label>
      <div className="paper-actions">
        <button
          className="primary small"
          disabled={s.pending || !available || !match || !reasonReady}
          onClick={() => void research()}
        >
          Ask Tauric for research
        </button>
        <button
          className="secondary small"
          disabled={s.pending || !reasonReady || c.status === "expired"}
          onClick={() =>
            void s.act(
              `watch-${c.id}-${reason}`,
              (id) =>
                api.candidateAction(c.id, {
                  request_id: id,
                  action: "watch",
                  reason,
                }),
              "Setup added to your watching list.",
            )
          }
        >
          Watch setup
        </button>
        <button
          className="secondary small"
          disabled={s.pending || !reasonReady}
          onClick={() =>
            void s.act(
              `ignore-${c.id}-${reason}`,
              (id) =>
                api.candidateAction(c.id, {
                  request_id: id,
                  action: "ignore",
                  reason,
                }),
              "Setup dismissed. Queued research cannot start from it.",
            )
          }
        >
          Dismiss setup
        </button>
      </div>
      <p className="muted">
        Tauric researches daily market context. This timeframe’s setup stays
        separate evidence; trading still follows the route’s required approvals.
      </p>
      {c.analysis && (
        <p>
          Daily research:{" "}
          <button
            className="text-button"
            onClick={() =>
              open(
                c.analysis?.artifact_id ? "report" : "analysis",
                c.analysis?.artifact_id ?? c.analysis!.id,
              )
            }
          >
            {c.analysis.status} <ArrowRight size={12} />
          </button>
        </p>
      )}
      <details>
        <summary>Candidate history</summary>
        {c.history.map((h) => (
          <p key={String(h.id)}>
            {String(h.action).replaceAll("_", " ")} · {String(h.reason)}
          </p>
        ))}
      </details>
    </details>
  );
}
export function ScannerPanel({
  open,
  setup,
  chart,
}: {
  open: (kind: string, id: string) => void;
  setup: () => void;
  chart: () => void;
}) {
  const s = useScanner();
  const [family, setFamily] = useState("All");
  const [status, setStatus] = useState("active");
  const [search, setSearch] = useState("");
  const families: Record<string, string[]> = {
    Crypto: ["crypto_spot"],
    Forex: ["fx"],
    Stocks: ["equity", "etf"],
  };
  const candidates =
    s.state?.candidates.filter(
      (c) =>
        (family === "All" || families[family]?.includes(c.asset_class)) &&
        (status === "all" ||
          (status === "active"
            ? ["candidate", "watch"].includes(c.status)
            : c.status === "watch")) &&
        `${c.instrument_id} ${c.matches.map((m) => m.profile_name).join(" ")}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    ) ?? [];
  return (
    <div className="scanner-panel">
      <div className="scanner-panel-head">
        <div>
          <span className="eyebrow">MARKET SETUPS</span>
          <h3>Opportunities</h3>
        </div>
        <button
          className="icon-button"
          aria-label="Reload scanner state"
          onClick={() => void s.reload()}
        >
          <RefreshCw size={15} />
        </button>
      </div>
      <div
        className="scanner-families"
        role="group"
        aria-label="Filter by market"
      >
        {["All", "Crypto", "Forex", "Stocks"].map((v) => (
          <button
            key={v}
            aria-pressed={v === family}
            onClick={() => setFamily(v)}
          >
            {v}
          </button>
        ))}
      </div>
      <div className="scanner-filters">
        <label>
          Search opportunities
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Symbol or scan name"
          />
        </label>
        <label>
          Status
          <select
            aria-label="Opportunity status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="active">Ready &amp; watching</option>
            <option value="watch">Watching only</option>
            <option value="all">All recent results</option>
          </select>
        </label>
      </div>
      <Notices />
      <div className="scanner-panel-summary">
        <span>
          {s.state?.candidates.filter(
            (c) => c.status === "candidate" || c.status === "watch",
          ).length ?? 0}{" "}
          ready or watching · scanning uses market rules
        </span>
        <button className="text-button" onClick={setup}>
          Manage scans <ArrowRight size={13} />
        </button>
      </div>
      {!s.loadError && !s.state?.profiles.length && (
        <p className="empty-copy">
          Create your first scanner in My scans, choose markets, then run it to
          find opportunities.
        </p>
      )}
      {!!s.state?.profiles.length && !candidates.length && (
        <p className="empty-copy">
          No opportunities match this view. Run a saved scan or choose All
          recent results to see earlier setups.
        </p>
      )}
      {candidates.map((c) => (
        <article
          key={c.id}
          className="scanner-candidate"
          aria-label={`${c.instrument_id} candidate`}
        >
          <div className="scanner-candidate-head">
            <strong className="mono">
              {c.instrument_id.split(":").at(-1)}
            </strong>
            <span>
              {timeframeLabels[c.interval] ?? c.interval} · setup score{" "}
              {Math.round(c.setup_score * 100)}%
            </span>
            <span className={`badge ${c.status === "candidate" ? "cyan" : ""}`}>
              {opportunityLabels[c.status] ?? c.status}
            </span>
          </div>
          <p className="muted">
            {detectorLabels[c.detector_id as keyof typeof detectorLabels] ??
              c.detector_id}
            {" · "}
            {c.direction === "short" ? "Short setup" : "Long setup"}
          </p>
          <p className="scanner-match-names">
            {c.matches.map((m) => String(m.profile_name)).join(" / ")}
          </p>
          <CandidateDetail c={c} open={open} chart={chart} />
        </article>
      ))}
      {!!s.state?.runs.length && (
        <details className="scanner-runs">
          <summary>Scan history</summary>
          {s.state.runs.map((r) => (
            <div key={r.id}>
              <button
                className="text-button"
                onClick={() => open("scanner_run", r.id)}
              >
                {new Date(r.created_at).toLocaleString()}
              </button>
              <p className="muted">
                {String(r.metrics?.universe_count ?? "—")} instruments ·{" "}
                {String(r.metrics?.published_candidate_count ?? 0)} shortlisted
                · {String(r.metrics?.scan_duration ?? "—")} seconds
              </p>
              {r.results.map((v, i) => (
                <p key={i}>
                  {String(v.instrument_id).split(":").at(-1)} ·{" "}
                  {String(v.status).replaceAll("_", " ")}
                  {v.reason ? ` · ${String(v.reason)}` : ""}
                  {v.gate_reason_code || v.ranking_reason_code || v.reason_code
                    ? ` · ${String(v.gate_reason_code || v.ranking_reason_code || v.reason_code)}`
                    : ""}
                </p>
              ))}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

export function ScannerWorkspace({
  open,
  chart,
  ask,
}: {
  open: (kind: string, id: string) => void;
  chart: () => void;
  ask: (draft: string) => void;
}) {
  const s = useScanner();
  const [tab, setTab] = useState("opportunities");
  const ready = s.state?.candidates.filter(
    (c) => c.status === "candidate" || c.status === "watch",
  ).length;
  return (
    <div className="page-scroll workflow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">DISCOVER → RESEARCH → TEST</span>
          <h1>Scanners</h1>
          <p>
            Find a setup, review its evidence, then ask Tauric for research.
          </p>
        </div>
        <button
          className="secondary"
          onClick={() =>
            ask(
              "Review my scanners. Suggest a simpler setup for my registered markets, explain the tradeoffs, and preserve my current research allowances and automation settings.",
            )
          }
        >
          Ask OpenClaw
        </button>
      </div>
      <WorkflowTabs
        label="Scanner views"
        value={tab}
        select={setTab}
        options={[
          { id: "opportunities", label: "Opportunities", count: ready },
          { id: "scans", label: "My scans", count: s.state?.profiles.length },
        ]}
      />
      <section
        role="tabpanel"
        id={`workflow-panel-${tab}`}
        aria-labelledby={`workflow-${tab}`}
      >
        {tab === "opportunities" ? (
          <ScannerPanel
            open={open}
            chart={chart}
            setup={() => setTab("scans")}
          />
        ) : (
          <ScannerSettings />
        )}
      </section>
      <p className="workflow-authority">
        Scanners find setups. Tauric supplies the required intelligence. A setup
        alone cannot authorize a trade.
      </p>
    </div>
  );
}
