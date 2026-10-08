import { useEffect, useState, type FormEvent } from "react";
import { Download, RefreshCw } from "lucide-react";
import { client, unwrap, type Interval } from "../api/client";
import type { components } from "../api/schema";
import { useMarket } from "../market/MarketContext";
import { researchApi, type Dataset } from "./api";
import "./lab.css";

type LabState = components["schemas"]["LabState"];
type LabRecord = components["schemas"]["LabRecordView"];
type Session = components["schemas"]["SessionWindow"];
const utcInput = (value: string) => new Date(value).toISOString().slice(0, 16);
const instant = (value: string) => new Date(value + "Z").toISOString();
const message = (error: unknown) =>
  error instanceof Error ? error.message : "The request failed.";
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const format = (value: unknown) => {
  const numeric =
    typeof value === "number"
      ? value
      : typeof value === "string" &&
          /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)
        ? Number(value)
        : null;
  if (numeric === null) return String(value ?? "—");
  if (!Number.isFinite(numeric)) return "—";
  return numeric !== 0 && Math.abs(numeric) < 0.0001
    ? numeric.toExponential(3)
    : numeric.toLocaleString("en-GB", { maximumFractionDigits: 6 });
};

function Export({ record }: { record: LabRecord }) {
  const [error, setError] = useState("");
  return (
    <>
      <button
        className="text-button"
        onClick={async () => {
          setError("");
          try {
            const full = unwrap(
              await client.GET("/api/v1/research/lab/records/{record_id}", {
                params: { path: { record_id: record.id } },
              }),
            );
            const link = document.createElement("a");
            const url = URL.createObjectURL(
              new Blob([JSON.stringify(full, null, 2)], {
                type: "application/json",
              }),
            );
            link.href = url;
            link.download = `tauric-lab-${record.id}.json`;
            link.click();
            URL.revokeObjectURL(url);
          } catch (err) {
            setError(message(err));
          }
        }}
      >
        <Download size={14} /> Export record
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

function Results({ record }: { record: LabRecord }) {
  const payload = record.payload as Record<string, unknown>;
  const result = object(payload.result);
  const statistics =
    record.kind === "session_report" ? object(payload.statistics) : result;
  const keys =
    record.kind === "session_report"
      ? ["bars", "return_percent", "high", "low", "mean_spread"]
      : payload.engine === "lean"
        ? [
            "net_pnl",
            "return_percent",
            "max_drawdown_percent",
            "fees",
            "financing",
          ]
        : ["training_samples", "best_iteration"];
  const warnings = (payload.warnings ?? result.warnings ?? []) as string[];
  const metrics = object(result.metrics);
  const curve = Array.isArray(result.equity_curve)
    ? result.equity_curve.map(object)
    : [];
  const values = curve.map((p) => Number(p.equity));
  const min = Math.min(...values),
    span = Math.max(...values) - min || 1;
  return (
    <div className="lab-result">
      {payload.as_of != null && (
        <p>
          Requested cutoff: {String(payload.as_of)} · Latest included bar:{" "}
          {String(payload.evidence_end)} · {String(payload.interval)} bars
        </p>
      )}
      <dl className="research-metrics">
        {keys.map((key) => (
          <div key={key}>
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>{format(statistics[key] ?? payload[key])}</dd>
          </div>
        ))}
      </dl>
      {result.forecast != null && (
        <p>
          Last closed-bar forecast as of {String(object(result.forecast).as_of)}
          : estimated return{" "}
          {format(Number(object(result.forecast).estimated_return) * 100)}% over{" "}
          {String(object(result.forecast).horizon_bars)} subsequent bars. This
          is a model estimate; no probability is assigned.
        </p>
      )}
      {curve.length > 1 && (
        <figure className="lab-curve">
          <figcaption>
            Unseen test equity · {String(result.currency)}
          </figcaption>
          <svg
            viewBox="0 0 800 160"
            role="img"
            aria-label="Unseen test equity curve"
          >
            <polyline
              fill="none"
              stroke="var(--cyan)"
              strokeWidth="2"
              points={values
                .map(
                  (v, i) =>
                    `${10 + (i / (values.length - 1)) * 780},${150 - ((v - min) / span) * 140}`,
                )
                .join(" ")}
            />
          </svg>
          <span>
            {String(curve[0].time)} → {String(curve.at(-1)?.time)}
          </span>
        </figure>
      )}
      {Object.keys(metrics).length > 0 && (
        <div className="research-table-scroll">
          <table className="research-table">
            <caption>
              Forecast accuracy · lower MSE is better; compare with the
              training-mean baseline
            </caption>
            <thead>
              <tr>
                <th>Period</th>
                <th>Samples</th>
                <th>Model MSE</th>
                <th>Baseline MSE</th>
                <th>Direction accuracy</th>
                <th>Return correlation</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(metrics).map(([name, raw]) => {
                const m = object(raw);
                return (
                  <tr key={name}>
                    <td>{name}</td>
                    {[
                      "samples",
                      "mse",
                      "baseline_mse",
                      "direction_accuracy",
                      "return_correlation",
                    ].map((key) => (
                      <td key={key}>{format(m[key])}</td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {Array.isArray(payload.scenarios) && (
        <div className="lab-scenarios">
          {payload.scenarios.map((raw, i) => {
            const s = object(raw);
            return (
              <div key={i}>
                <strong>{String(s.name)}</strong>
                <p>{String(s.condition)}</p>
                <small>Invalidation: {String(s.invalidation)}</small>
              </div>
            );
          })}
        </div>
      )}
      {Array.isArray(payload.evidence) && payload.evidence.length > 0 && (
        <details>
          <summary>Timestamped evidence</summary>
          {payload.evidence.map((raw, i) => {
            const e = object(raw);
            return (
              <p key={i}>
                <strong>{String(e.title)}</strong>
                <br />
                {String(e.content)}
                <br />
                <small>
                  {String(e.source)} · available {String(e.available_at)}
                </small>
              </p>
            );
          })}
        </details>
      )}
      {warnings.length > 0 && (
        <details>
          <summary>Assumptions and limits</summary>
          <ul>
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </details>
      )}
      {Boolean(payload.error) && (
        <p className="error" role="alert">
          {String(payload.error)}
        </p>
      )}
      <Export record={record} />
      <small className="lab-checksum">
        Record checksum · {record.checksum}
      </small>
    </div>
  );
}

export function ResearchLab() {
  const market = useMarket();
  const [state, setState] = useState<LabState | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [mode, setMode] = useState<
    "historical" | "current" | "forecast" | "lean" | "qlib"
  >("historical");
  const [session, setSession] = useState<Session>({
    name: "All trading hours",
    timezone: "UTC",
    opening: "00:00",
    closing: "00:00",
    weekdays: [0, 1, 2, 3, 4, 5, 6],
  });
  const [name, setName] = useState("Session research");
  const [hypothesis, setHypothesis] = useState(
    "Compare session behaviour and test whether past features explain subsequent returns.",
  );
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [trainEnd, setTrainEnd] = useState("");
  const [validEnd, setValidEnd] = useState("");
  const [evidence, setEvidence] = useState("");
  const [modelId, setModelId] = useState("");
  const [horizon, setHorizon] = useState(4);
  const [lookback, setLookback] = useState(12);
  const [fast, setFast] = useState(5);
  const [slow, setSlow] = useState(20);
  const [capital, setCapital] = useState("10000");
  const [allocation, setAllocation] = useState("25");
  const [fee, setFee] = useState("0");
  const [slip, setSlip] = useState("5");
  const [commission, setCommission] = useState("0");
  const [minimumFee, setMinimumFee] = useState("0");
  const [sellTax, setSellTax] = useState("0");
  const [financing, setFinancing] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [historyInstrument, setHistoryInstrument] = useState("");
  const [historyInterval, setHistoryInterval] = useState<Interval>("1h");
  const [historyStart, setHistoryStart] = useState("");
  const [historyEnd, setHistoryEnd] = useState("");
  const dataset = datasets.find((d) => d.id === datasetId);
  const experiment = mode === "lean" || mode === "qlib";
  const fx = dataset?.asset_type === "fx";
  async function reload() {
    const [lab, research] = await Promise.all([
      client.GET("/api/v1/research/lab").then(unwrap),
      researchApi.state(),
    ]);
    setState(lab);
    setDatasets(research.datasets);
  }
  useEffect(() => {
    let active = true;
    const read = async () => {
      try {
        if (active) await reload();
      } catch (err) {
        if (active) setError(message(err));
      }
    };
    void read();
    const timer = window.setInterval(read, 10000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    if (!dataset) return;
    const a = Date.parse(dataset.start_time),
      b = Date.parse(dataset.end_time);
    setStart(utcInput(dataset.start_time));
    setEnd(utcInput(dataset.end_time));
    setTrainEnd(utcInput(new Date(a + (b - a) * 0.55).toISOString()));
    setValidEnd(utcInput(new Date(a + (b - a) * 0.8).toISOString()));
    setModelId("");
  }, [datasetId]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const common = {
        request_id: crypto.randomUUID(),
        dataset_id: datasetId,
        name,
        hypothesis,
        reason: "Owner submitted a research-only lab study",
      };
      if (experiment) {
        if (!dataset || !["fx", "equity", "etf"].includes(dataset.asset_type))
          throw new Error(
            "Choose a Forex, stock or ETF dataset for this engine.",
          );
        unwrap(
          await client.POST("/api/v1/research/lab/experiments", {
            body: {
              ...common,
              engine: mode,
              session,
              train_end: instant(trainEnd),
              validation_end: instant(validEnd),
              test_end: instant(end),
              horizon_bars: horizon,
              lookback_bars: lookback,
              fast_window: fast,
              slow_window: slow,
              starting_balance: capital,
              allocation_percent: allocation,
              fee_bps: fee,
              slippage_bps: slip,
              asset_costs: {
                asset_type: dataset.asset_type as "fx" | "equity" | "etf",
                spread_bps: "0",
                commission_per_unit: commission,
                minimum_commission: minimumFee,
                sell_tax_bps: sellTax,
                ...(fx ? { financing_daily_bps: financing } : {}),
                triple_rollover_weekday: 2,
              },
              ...(modelId ? { model_run_id: modelId } : {}),
            },
          }),
        );
        setNotice(
          "Experiment queued. The separate research worker will process it; results appear below.",
        );
      } else {
        const notes: components["schemas"]["EvidenceNote"][] = evidence.trim()
          ? JSON.parse(evidence)
          : [];
        if (!Array.isArray(notes))
          throw new Error("Evidence must be a JSON list of timestamped notes.");
        unwrap(
          await client.POST("/api/v1/research/lab/reports", {
            body: {
              ...common,
              mode,
              start: instant(start),
              as_of: instant(end),
              session,
              forecast_bars: horizon,
              evidence: notes,
            },
          }),
        );
        setNotice(
          "Session report saved with its evidence cutoff and dataset checksum.",
        );
      }
      await reload();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  async function history(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = unwrap(
        await client.POST("/api/v1/research/lab/datasets/history", {
          body: {
            request_id: crypto.randomUUID(),
            name: "Historical research dataset",
            instrument_id: historyInstrument,
            interval: historyInterval,
            start: instant(historyStart),
            end: instant(historyEnd),
            reason: "Owner requested a bounded historical dataset",
          },
        }),
      );
      await reload();
      setDatasetId(saved.id);
      setNotice(
        `Saved ${saved.bar_count} closed bars. Historical provider revisions remain retrospective.`,
      );
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="research-lab" aria-label="Research Lab">
      <div className="lab-intro">
        <div>
          <span className="eyebrow">SESSION → MODEL → EXECUTION</span>
          <h2>Research Lab</h2>
          <p>
            Study saved prices by session. Train on the past. Measure execution
            on an unseen period.
          </p>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void reload().catch((e) => setError(message(e)))}
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      <div className="lab-engines">
        {state?.engines.map((raw) => {
          const e = object(raw);
          return (
            <div key={String(e.id)}>
              <span className={e.ready ? "lab-ready" : "lab-offline"} />
              <strong>{String(e.label)}</strong>
              <small>{e.ready ? "Ready" : "Worker offline"}</small>
            </div>
          );
        })}
      </div>
      <p className="research-context">
        Research only. Session reports describe observed prices and supplied
        evidence; forward reports contain conditional scenarios. These
        experiments do not authorize trading. FX uses historical bid/ask quotes
        and explicit financing; stocks use their saved adjustment and calendar.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="research-notice" role="status">
          {notice}
        </p>
      )}
      <details className="research-block">
        <summary>Retrieve a historical dataset</summary>
        <form onSubmit={history}>
          <div className="qualification-fields">
            <label>
              Connected instrument
              <select
                required
                value={historyInstrument}
                onChange={(e) => setHistoryInstrument(e.target.value)}
              >
                <option value="">Select Forex, stock or ETF</option>
                {market.instruments
                  .filter((i) =>
                    ["oanda_v20", "alpaca_stocks"].includes(i.provider),
                  )
                  .map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.symbol}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Bar interval
              <select
                value={historyInterval}
                onChange={(e) => setHistoryInterval(e.target.value as Interval)}
              >
                {["1m", "5m", "15m", "1h", "4h", "1d"].map((i) => (
                  <option key={i}>{i}</option>
                ))}
              </select>
            </label>
            <label>
              Start · UTC
              <input
                type="datetime-local"
                required
                value={historyStart}
                onChange={(e) => setHistoryStart(e.target.value)}
              />
            </label>
            <label>
              End · UTC
              <input
                type="datetime-local"
                required
                value={historyEnd}
                onChange={(e) => setHistoryEnd(e.target.value)}
              />
            </label>
          </div>
          <p>
            Requires a configured data connection. Save up to 5,000 closed bars.
            A long intraday range may exceed provider limits.
          </p>
          <button className="secondary" disabled={busy}>
            Retrieve and save
          </button>
        </form>
      </details>
      <form className="research-block" onSubmit={submit}>
        <fieldset disabled={busy}>
          <div className="qualification-fields">
            <label>
              Study
              <select
                aria-label="Study"
                value={mode}
                onChange={(e) => setMode(e.target.value as typeof mode)}
              >
                <option value="historical">Historical session report</option>
                <option value="current">
                  Current / intraday session report
                </option>
                <option value="forecast">Forward scenarios</option>
                <option value="lean">LEAN execution backtest</option>
                <option value="qlib">Qlib machine learning</option>
              </select>
            </label>
            <label>
              Saved dataset
              <select
                aria-label="Saved dataset"
                required
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
              >
                <option value="">Select a dataset</option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} · {d.asset_type} · {d.interval} · {d.bar_count}{" "}
                    bars
                  </option>
                ))}
              </select>
            </label>
            <label>
              Study name
              <input
                required
                minLength={3}
                maxLength={120}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          </div>
          <label>
            Hypothesis
            <textarea
              required
              minLength={3}
              maxLength={2000}
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
            />
          </label>
          <div className="qualification-fields">
            <label>
              Session preset
              <select
                aria-label="Session preset"
                value={session.name}
                onChange={(e) => {
                  const next = state?.sessions.find(
                    (s) => s.name === e.target.value,
                  );
                  if (next) setSession(next);
                }}
              >
                <option value="Custom">Custom</option>
                {state?.sessions.map((s) => (
                  <option key={s.name}>{s.name}</option>
                ))}
              </select>
            </label>
            <label>
              Session time zone
              <input
                required
                value={session.timezone}
                onChange={(e) =>
                  setSession({
                    ...session,
                    name: "Custom",
                    timezone: e.target.value,
                  })
                }
              />
            </label>
            <label>
              Opening
              <input
                type="time"
                required
                value={session.opening}
                onChange={(e) =>
                  setSession({
                    ...session,
                    name: "Custom",
                    opening: e.target.value,
                  })
                }
              />
            </label>
            <label>
              Closing
              <input
                type="time"
                required
                value={session.closing}
                onChange={(e) =>
                  setSession({
                    ...session,
                    name: "Custom",
                    closing: e.target.value,
                  })
                }
              />
            </label>
          </div>
          <div
            className="lab-weekdays"
            role="group"
            aria-label="Session opening weekdays"
          >
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, i) => (
              <label key={day}>
                <input
                  type="checkbox"
                  checked={session.weekdays?.includes(i) ?? false}
                  onChange={(e) =>
                    setSession({
                      ...session,
                      name: "Custom",
                      weekdays: e.target.checked
                        ? [...(session.weekdays ?? []), i].sort()
                        : session.weekdays?.filter((d) => d !== i),
                    })
                  }
                />
                {day}
              </label>
            ))}
          </div>
          <p className="research-context">
            Equal opening and closing times mean a full day. Overnight sessions
            belong to their opening weekday. A bar must fit wholly inside the
            session; prediction targets may span subsequent sessions.
          </p>
          <div className="qualification-fields">
            {!experiment && (
              <label>
                Study start · UTC
                <input
                  required
                  type="datetime-local"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
            )}
            {experiment && (
              <>
                <label>
                  Training ends · UTC
                  <input
                    required
                    type="datetime-local"
                    value={trainEnd}
                    onChange={(e) => setTrainEnd(e.target.value)}
                  />
                </label>
                <label>
                  Validation ends · UTC
                  <input
                    required
                    type="datetime-local"
                    value={validEnd}
                    onChange={(e) => setValidEnd(e.target.value)}
                  />
                </label>
              </>
            )}
            <label>
              {experiment ? "Unseen test ends · UTC" : "Evidence cutoff · UTC"}
              <input
                required
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </label>
            <label>
              Prediction horizon · bars
              <input
                type="number"
                min={1}
                max={100}
                required
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
              />
            </label>
            {experiment && (
              <label>
                Feature lookback · bars
                <input
                  type="number"
                  min={3}
                  max={200}
                  required
                  value={lookback}
                  onChange={(e) => setLookback(Number(e.target.value))}
                />
              </label>
            )}
          </div>
          {!experiment && (
            <details>
              <summary>Add timestamped news or macro evidence</summary>
              <p>
                Paste a JSON list with title, source, content, published_at and
                available_at (ISO timestamps). Notes available after the cutoff
                are rejected. Availability is declared by you.
              </p>
              <textarea
                aria-label="Timestamped evidence JSON"
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
                placeholder='[{"title":"Policy decision","source":"Central bank release URL","content":"Your evidence summary","published_at":"2025-01-01T12:00:00Z","available_at":"2025-01-01T12:00:00Z"}]'
              />
            </details>
          )}
          {experiment && (
            <>
              <p>
                Each chronological partition needs 20 samples after targets
                crossing its cutoff are removed. Features use only closed bars.
                Validation selects the model iteration; test labels remain
                unseen during fitting.
              </p>
              <div className="qualification-fields">
                {mode === "lean" && (
                  <>
                    <label>
                      Signal model
                      <select
                        value={modelId}
                        onChange={(e) => setModelId(e.target.value)}
                      >
                        <option value="">Moving-average baseline</option>
                        {state?.records
                          .filter(
                            (r) =>
                              r.payload.engine === "qlib" &&
                              r.payload.status === "completed",
                          )
                          .map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.name}
                            </option>
                          ))}
                      </select>
                    </label>
                    <label>
                      Fast average
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={fast}
                        onChange={(e) => setFast(Number(e.target.value))}
                      />
                    </label>
                    <label>
                      Slow average
                      <input
                        type="number"
                        min={2}
                        max={300}
                        value={slow}
                        onChange={(e) => setSlow(Number(e.target.value))}
                      />
                    </label>
                  </>
                )}
                <label>
                  Starting capital ·{" "}
                  {dataset?.quote_currency ?? "quote currency"}
                  <input
                    required
                    value={capital}
                    onChange={(e) => setCapital(e.target.value)}
                  />
                </label>
                <label>
                  Allocation · %
                  <input
                    required
                    value={allocation}
                    onChange={(e) => setAllocation(e.target.value)}
                  />
                </label>
                <label>
                  Commission · bps
                  <input
                    required
                    value={fee}
                    onChange={(e) => setFee(e.target.value)}
                  />
                </label>
                <label>
                  Slippage · bps
                  <input
                    required
                    value={slip}
                    onChange={(e) => setSlip(e.target.value)}
                  />
                </label>
                <label>
                  Commission per unit
                  <input
                    required
                    value={commission}
                    onChange={(e) => setCommission(e.target.value)}
                  />
                </label>
                <label>
                  Minimum commission
                  <input
                    required
                    value={minimumFee}
                    onChange={(e) => setMinimumFee(e.target.value)}
                  />
                </label>
                <label>
                  Sell tax · bps
                  <input
                    required
                    value={sellTax}
                    onChange={(e) => setSellTax(e.target.value)}
                  />
                </label>
                {fx && (
                  <label>
                    FX daily financing debit · bps (negative = credit)
                    <input
                      required
                      value={financing}
                      onChange={(e) => setFinancing(e.target.value)}
                    />
                  </label>
                )}
              </div>
              <p>
                FX spread comes from the saved bid/ask data. Financing applies
                at 17:00 New York with Wednesday triple rollover. A model
                handoff requires the same dataset, session, split dates, horizon
                and lookback. Qlib accuracy excludes these execution costs; LEAN
                applies them.
              </p>
            </>
          )}
          <button className="primary" disabled={busy || !datasetId}>
            {busy
              ? "Saving…"
              : experiment
                ? "Queue experiment"
                : "Save session report"}
          </button>
        </fieldset>
      </form>
      <section className="lab-history" aria-label="Saved studies">
        <h2>Saved studies</h2>
        {!state?.records.length && (
          <p className="research-empty">
            Your session reports and experiments will appear here.
          </p>
        )}
        {state?.records.map((r) => (
          <details className="research-block" key={r.id}>
            <summary>
              <span>{r.name}</span>
              <small>
                {r.kind === "session_report"
                  ? String(r.payload.mode)
                  : `${r.payload.engine} · ${r.payload.status}`}
              </small>
            </summary>
            <p>
              {String(object(r.payload.request).hypothesis ?? "")} ·{" "}
              {new Date(r.created_at).toLocaleString("en-GB")}
            </p>
            <Results record={r} />
            {["queued", "running"].includes(String(r.payload.status)) && (
              <button
                className="secondary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    unwrap(
                      await client.POST(
                        "/api/v1/research/lab/experiments/{record_id}/cancel",
                        { params: { path: { record_id: r.id } } },
                      ),
                    );
                    await reload();
                  } catch (err) {
                    setError(message(err));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Cancel experiment
              </button>
            )}
          </details>
        ))}
      </section>
    </section>
  );
}
