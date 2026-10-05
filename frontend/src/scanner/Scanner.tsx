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
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const detectors = f
      .getAll("detector_ids")
      .map(String) as ScannerParameters["detector_id"][];
    if (!detectors.length) {
      setSetupError("Choose at least one setup family.");
      return;
    }
    setSetupError("");
    const parameters: ScannerParameters = {
      ...p,
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
        <h2>{profile ? "Edit scanner profile" : "Add scanner profile"}</h2>
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
              placeholder="e.g. Crypto breakout watch"
            />
          </label>
          <label>
            Strategy link (optional)
            <select
              name="strategy_version_id"
              aria-label="Scanner strategy version"
              defaultValue={profile?.strategy_version_id ?? ""}
            >
              <option value="">
                No strategy — screening and research only
              </option>
              {paper?.strategy_versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {label(v)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Candle interval
            <select
              name="interval"
              aria-label="Candle interval"
              required
              defaultValue={p?.interval ?? ""}
            >
              <option value="" disabled>
                Choose an interval
              </option>
              {["1m", "5m", "15m", "1h", "4h", "1d"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <label>
            Prior closed candles
            <input
              name="lookback"
              type="number"
              required
              min={2}
              max={100}
              defaultValue={p?.lookback}
            />
          </label>
          <label>
            Minimum activity ratio
            <input
              name="minimum_volume_ratio"
              required
              inputMode="decimal"
              defaultValue={p?.minimum_volume_ratio}
            />
          </label>
          <label>
            Candidate lifetime (seconds)
            <input
              name="candidate_ttl_seconds"
              required
              type="number"
              min={60}
              max={86400}
              defaultValue={p?.candidate_ttl_seconds}
            />
          </label>
          <label>
            Minimum setup score
            <input
              name="minimum_setup_score"
              type="number"
              min={0}
              max={1}
              step={0.01}
              required
              defaultValue={p?.minimum_setup_score ?? 0.5}
            />
          </label>
          <label>
            Minimum research score
            <input
              name="ranking_minimum_score"
              type="number"
              min={0}
              max={1}
              step={0.01}
              required
              defaultValue={p?.ranking?.minimum_score ?? 0.65}
            />
          </label>
          <label>
            Top candidates per cycle
            <input
              name="max_candidates_per_cycle"
              type="number"
              min={1}
              max={100}
              required
              defaultValue={p?.ranking?.max_candidates_per_cycle ?? 2}
            />
          </label>
          <label>
            Normal scan interval (seconds)
            <input
              name="scan_seconds"
              type="number"
              min={5}
              max={3600}
              required
              defaultValue={p?.scan_seconds ?? 30}
            />
          </label>
          <label>
            Candidate scan interval (seconds)
            <input
              name="watch_scan_seconds"
              type="number"
              min={5}
              max={3600}
              required
              defaultValue={p?.watch_scan_seconds ?? 10}
            />
          </label>
          <label>
            Setup cooldown (seconds)
            <input
              name="cooldown_seconds"
              type="number"
              min={0}
              max={86400}
              required
              defaultValue={p?.cooldown_seconds ?? 0}
            />
          </label>
          <label>
            New research runs per UTC day
            <input
              name="max_analyses_per_day"
              required
              type="number"
              min={0}
              max={100}
              defaultValue={p?.max_analyses_per_day}
            />
          </label>
          <label>
            Research AI connection
            <select
              name="model_profile"
              aria-label="Research AI connection"
              required
              defaultValue={p?.model_profile ?? ""}
            >
              <option value="" disabled>
                Choose a connection
              </option>
              <option value="local">{connectionLabel("local")}</option>
              <option value="cloud">{connectionLabel("cloud")}</option>
            </select>
          </label>
        </div>
        <fieldset className="scanner-universe">
          <legend>Crypto, Forex, stock & ETF universe</legend>
          <InstrumentDiscovery />
          {market.instruments
            .filter((v) =>
              ["crypto_spot", "fx", "equity", "etf"].includes(v.asset_type),
            )
            .map((v) => (
              <label key={v.id} className="checkbox">
                <input
                  name="universe"
                  type="checkbox"
                  value={v.id}
                  defaultChecked={p?.universe.includes(v.id) ?? false}
                />
                {v.symbol}
              </label>
            ))}
        </fieldset>
        <fieldset className="scanner-universe">
          <legend>Setup families</legend>
          {setupError && (
            <p className="form-error" role="alert">
              {setupError}
            </p>
          )}
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
        <label className="checkbox">
          <input
            name="enabled"
            type="checkbox"
            defaultChecked={p?.enabled ?? false}
          />
          Enable screening
        </label>
        <label className="checkbox">
          <input
            name="automatic_enabled"
            type="checkbox"
            defaultChecked={p?.automatic_enabled ?? false}
          />
          Run scheduled screening (requires scanner worker)
        </label>
        <label className="checkbox">
          <input
            name="automatic_research_enabled"
            type="checkbox"
            defaultChecked={p?.automatic_research_enabled ?? false}
          />
          Automatically research qualified candidates
        </label>
        <p className="muted">
          Breakouts cross prior highs; breakdowns cross prior lows. Trend
          pullbacks recover through the prior average in the observed trend
          direction. All setups require closed candles and your activity ratio.
          Forex activity is provider tick count. Missing activity or candle gaps
          block detection. Forming candles are excluded.
        </p>
        <p className="muted">
          Research remains the full daily Tauric graph. Screening never calls
          AI. When automatic research is enabled, a separate coordinator admits
          published candidates after checking the gate, ranking and allowance.
          The allowance counts newly admitted research jobs, including failed or
          interrupted jobs; it is not a monetary spending limit.
        </p>
        <label>
          Scanner change reason
          <input name="reason" required minLength={3} maxLength={500} />
        </label>
        <div className="paper-actions">
          <button className="primary small" type="submit">
            Save scanner profile
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
      <p>
        {p.parameters.interval} · {p.parameters.lookback} prior candles ·
        activity ≥ {p.parameters.minimum_volume_ratio}× · expires after{" "}
        {p.parameters.candidate_ttl_seconds}s
      </p>
      <p className="muted">
        Research allowance: {p.charged_analyses_today}/
        {p.parameters.max_analyses_per_day} new runs today ·{" "}
        {connectionLabel(p.parameters.model_profile)} · profile revision{" "}
        {p.revision}
      </p>
      <p className="mono scanner-symbols">
        {p.parameters.universe.map((v) => v.split(":").at(-1)).join(" / ")}
      </p>
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
          Edit scanner profile
        </button>
        <button className="text-button" onClick={() => void showHistory()}>
          Profile history
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
          <h2>Scanners</h2>
          <p>
            Find opportunities before creating a strategy. Give each scanner a
            name and choose its markets and setup families. Enable scheduled
            screening to keep scanning with the browser closed. Enable automatic
            research to send qualified candidates to Tauric within your
            allowance. Strategy links are optional.
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
        {adding ? "Close scanner form" : "Add scanner profile"}
      </button>
      {adding && <ProfileForm close={() => setAdding(false)} />}
      {s.state?.profiles.map((p) => (
        <ProfileCard key={p.profile_id} p={p} />
      ))}
      <p className="muted">
        Crypto, Forex and registered stocks/ETFs support closed-bar screening.
        Stock volume describes the selected feed. Small-cap universes, event
        detectors and crypto futures remain separate capabilities.
      </p>
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
      <summary>Setup evidence and actions</summary>
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
        Scanner profile for research
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
        Candidate action reason
        <input
          aria-label="Candidate action reason"
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
          Request daily research
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
              "Candidate added to watch.",
            )
          }
        >
          Watch
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
              "Candidate ignored. Queued research cannot start from it.",
            )
          }
        >
          Ignore
        </button>
      </div>
      <p className="muted">
        The native graph currently researches daily context. Scanner features
        are retained as evidence and are not injected into that graph. Intraday
        screening does not establish intraday trading permission.
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
  const families: Record<string, string[]> = {
    Crypto: ["crypto_spot"],
    Forex: ["fx"],
    Stocks: ["equity", "etf"],
  };
  const candidates =
    s.state?.candidates.filter(
      (c) => family === "All" || families[family]?.includes(c.asset_class),
    ) ?? [];
  return (
    <div className="scanner-panel">
      <div className="scanner-panel-head">
        <div>
          <span className="eyebrow">DETERMINISTIC SCREENING</span>
          <h3>Setup candidates</h3>
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
        role="tablist"
        aria-label="Scanner families"
      >
        {["All", "Crypto", "Forex", "Stocks", "Small Caps", "Events"].map(
          (v) => (
            <button
              key={v}
              role="tab"
              aria-selected={v === family}
              disabled={v === "Small Caps" || v === "Events"}
              onClick={() => setFamily(v)}
              title={
                v === "Small Caps" || v === "Events"
                  ? "Universe and detector pending"
                  : undefined
              }
            >
              {v}
            </button>
          ),
        )}
      </div>
      <Notices />
      <div className="scanner-panel-summary">
        <span>
          {s.state?.candidates.filter(
            (c) => c.status === "candidate" || c.status === "watch",
          ).length ?? 0}{" "}
          eligible contexts · no AI during screening
        </span>
        <button className="text-button" onClick={setup}>
          Scanner settings <ArrowRight size={13} />
        </button>
      </div>
      {!s.loadError && !s.state?.profiles.length && (
        <p className="empty-copy">
          Add a scanner profile in Settings, then run screening with your chosen
          instruments and thresholds.
        </p>
      )}
      {!!s.state?.profiles.length && !candidates.length && (
        <p className="empty-copy">
          No candidates yet. Run a configured scanner; its results show which
          instruments passed, were filtered, or lacked evidence.
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
              {c.interval} · score {c.setup_score?.toFixed(2)}
            </span>
            <span className={`badge ${c.status === "candidate" ? "cyan" : ""}`}>
              {c.status.toUpperCase()}
            </span>
          </div>
          <p className="muted">
            {detectorLabels[c.detector_id as keyof typeof detectorLabels] ??
              c.detector_id}
            {" · "}
            {c.direction?.toUpperCase() ?? "LONG"}
          </p>
          <p className="scanner-match-names">
            {c.matches.map((m) => String(m.profile_name)).join(" / ")}
          </p>
          <CandidateDetail c={c} open={open} chart={chart} />
        </article>
      ))}
      {!!s.state?.runs.length && (
        <details className="scanner-runs">
          <summary>Recent screening results</summary>
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
                  {String(v.status)}
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
