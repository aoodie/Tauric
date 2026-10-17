import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, Download, FlaskConical, RefreshCw } from "lucide-react";
import { api, type Resource, type StrategyVersion } from "../api/client";
import { useMarket } from "../market/MarketContext";
import {
  researchApi,
  type Analytics,
  type Metrics,
  type Parameters,
  type Point,
  type Replay,
  type Research,
} from "./api";
import "./research.css";
import { Qualification } from "./Qualification";
import { SavedReports } from "./Reports";
import { ResearchLab } from "./Lab";

const initial: Parameters = {
  engine: "sma_cross_spot_v1",
  fast_window: 5,
  slow_window: 20,
  starting_balance: "10000",
  allocation_percent: "50",
  risk_percent: "1",
  stop_percent: "5",
  quantity_step: "0.00001",
  fee_bps: "10",
  slippage_bps: "5",
};
const amount = (value: string | number | null | undefined, digits = 2) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("en-GB", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      });
const date = (value: string) =>
  new Date(value).toLocaleString("en-GB", {
    timeZone: "Europe/London",
    dateStyle: "short",
    timeStyle: "short",
  });
const errorText = (err: unknown) =>
  err instanceof Error ? err.message : "The request failed. Try again.";
type Open = (resource: Resource["resource"]) => void;

function download(value: unknown, name: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Outcome({
  value,
  returns,
  drawdown,
  currency = "USDT",
}: {
  value: Metrics;
  returns?: string;
  drawdown?: string;
  currency?: string;
}) {
  const values = [
    [`Net P/L · ${currency}`, amount(value.net_pnl)],
    ["Closed trades", value.closed_trades],
    [
      "Win rate",
      value.win_rate_percent == null
        ? "—"
        : amount(value.win_rate_percent) + "%",
    ],
    ["Profit factor", amount(value.profit_factor)],
    [`Fees · ${currency}`, amount(value.total_fees)],
    [`Average trade · ${currency}`, amount(value.average_trade_pnl)],
    ...(returns != null ? [["Return", amount(returns) + "%"]] : []),
    ...(drawdown != null ? [["Max drawdown", amount(drawdown) + "%"]] : []),
  ];
  return (
    <dl className="research-metrics">
      {values.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Curve({
  points,
  label,
  currency = "USDT",
}: {
  points: Point[];
  label: string;
  currency?: string;
}) {
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(Math.max(0, points.length - 1)), [points]);
  if (!points.length) return null;
  const values = points.map((p) => Number(p.equity));
  const low = Math.min(...values),
    high = Math.max(...values),
    span = high - low || Math.max(high * 0.01, 1);
  const x = (i: number) => 18 + (i / Math.max(1, points.length - 1)) * 844;
  const y = (v: number) => 164 - ((v - low) / span) * 132;
  const selected = points[Math.min(index, points.length - 1)];
  return (
    <figure className="research-curve">
      <figcaption>
        <strong>{label}</strong>
        <span>
          {date(selected.time)} · {amount(selected.equity)} {currency} ·
          drawdown {amount(selected.drawdown_percent)}%
        </span>
      </figcaption>
      <svg
        viewBox="0 0 880 190"
        role="img"
        aria-label={label}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setIndex(
            Math.round(
              Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) *
                (points.length - 1),
            ),
          );
        }}
      >
        {[32, 98, 164].map((line) => (
          <line
            key={line}
            x1="18"
            y1={line}
            x2="862"
            y2={line}
            className="curve-grid"
          />
        ))}
        <polyline
          points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
          fill="none"
          className="curve-line"
        />
        <line
          x1={x(index)}
          y1="24"
          x2={x(index)}
          y2="172"
          className="curve-cursor"
        />
        <circle
          cx={x(index)}
          cy={y(Number(selected.equity))}
          r="4"
          className="curve-dot"
        />
        <text x="18" y="187">
          {date(points[0].time)}
        </text>
        <text x="862" y="187" textAnchor="end">
          {date(points[points.length - 1].time)}
        </text>
      </svg>
      <label className="curve-slider">
        Inspect equity point
        <input
          aria-label="Inspect equity point"
          type="range"
          min="0"
          max={points.length - 1}
          value={index}
          onChange={(e) => setIndex(Number(e.target.value))}
        />
      </label>
    </figure>
  );
}

function Trades({
  trades,
  open,
  currency = "USDT",
}: {
  trades: Record<string, unknown>[];
  open?: Open;
  currency?: string;
}) {
  if (!trades.length)
    return (
      <p className="research-empty">
        No closed trades in this result. Win rate and profit factor need trade
        outcomes.
      </p>
    );
  return (
    <div className="research-table-scroll">
      <table className="research-table">
        <thead>
          <tr>
            <th>Entry → exit</th>
            <th>Quantity</th>
            <th>Entry / exit price</th>
            <th>Fees</th>
            <th>Net P/L · {currency}</th>
            <th>{open ? "Fill record" : "Exit reason"}</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={String(t.exit_fill_id ?? i)}>
              <td>
                {date(String(t.entry_time))}
                <br />
                <span>{date(String(t.exit_time))}</span>
              </td>
              <td>{amount(String(t.quantity), 8)}</td>
              <td>
                {amount(String(t.entry_price))}
                <br />
                <span>{amount(String(t.exit_price))}</span>
              </td>
              <td>{amount(String(t.fees))}</td>
              <td
                className={
                  Number(t.net_pnl) >= 0 ? "outcome-gain" : "outcome-loss"
                }
              >
                {amount(String(t.net_pnl))}
              </td>
              <td>
                {open ? (
                  <button
                    className="text-button"
                    onClick={() =>
                      open({
                        type: "trading_record",
                        id: String(t.exit_fill_id),
                      })
                    }
                  >
                    Inspect fill <ArrowRight size={12} />
                  </button>
                ) : (
                  String(t.exit_reason).replaceAll("_", " ")
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResearchPage({ open }: { open: Open }) {
  const market = useMarket();
  const [tab, setTab] = useState("research");
  const [reportRefresh, setReportRefresh] = useState(0);
  const [state, setState] = useState<Research | null>(null);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [datasetId, setDatasetId] = useState("");
  const [datasetName, setDatasetName] = useState("Spot candle experiment");
  const [runName, setRunName] = useState("SMA crossover experiment");
  const [hypothesis, setHypothesis] = useState(
    "Test whether a fast average crossing above a slow average survives fees and slippage.",
  );
  const [parameters, setParameters] = useState<Parameters>(initial);
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [versionError, setVersionError] = useState("");
  const [selectedRun, setSelectedRun] = useState<Replay | null>(null);
  const [accountId, setAccountId] = useState("");
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const working = useRef(false);
  const ids = useRef(new Map<string, string>());
  const file = useRef<HTMLInputElement>(null);
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  async function reload(pageOffset = offset) {
    if (tab === "reports") {
      setReportRefresh((value) => value + 1);
      setLoadError("");
      return;
    }
    try {
      if (tab === "analytics") {
        const next = await researchApi.analytics();
        if (active.current) {
          setAnalytics(next);
          setAccountId((old) =>
            next.accounts.some((a) => a.account_id === old)
              ? old
              : (next.accounts[0]?.account_id ?? ""),
          );
        }
      } else {
        const next = await researchApi.state(pageOffset);
        if (active.current) {
          setState(next);
          setDatasetId((old) =>
            next.datasets.some((d) => d.id === old)
              ? old
              : (next.datasets[0]?.id ?? ""),
          );
        }
        try {
          const savedVersions = await researchApi.strategies();
          if (active.current) {
            setVersions(
              savedVersions.filter((version) => version.parameters.rules),
            );
            setVersionError("");
          }
        } catch (err) {
          if (active.current) {
            setVersions([]);
            setVersionError(errorText(err));
          }
        }
      }
      if (active.current) setLoadError("");
    } catch (err) {
      if (active.current) setLoadError(errorText(err));
    }
  }
  useEffect(() => {
    void reload(offset);
  }, [tab, offset]);
  async function act<T>(
    key: string,
    task: (requestId: string) => Promise<T>,
    done: (result: T) => void,
  ) {
    if (working.current) return;
    working.current = true;
    setPending(true);
    setError("");
    setNotice("");
    if (!ids.current.has(key)) ids.current.set(key, crypto.randomUUID());
    try {
      const value = await task(ids.current.get(key)!);
      ids.current.delete(key);
      if (active.current) done(value);
    } catch (err) {
      if (active.current) setError(errorText(err));
    } finally {
      working.current = false;
      if (active.current) setPending(false);
    }
  }
  async function capture(e: FormEvent) {
    e.preventDefault();
    const snapshot = market.feed?.snapshot_id;
    if (!snapshot) return;
    await act(
      JSON.stringify(["capture", snapshot, datasetName]),
      (request_id) =>
        researchApi.capture({
          request_id,
          name: datasetName,
          snapshot_id: snapshot,
          reason: "Saved chart candles for retrospective research",
        }),
      (saved) => {
        setOffset(0);
        void researchApi
          .state(0)
          .then((next) => {
            if (active.current) {
              setState(next);
              setDatasetId(saved.id);
            }
          })
          .catch((err) => {
            if (active.current) setLoadError(errorText(err));
          });
        setNotice("Dataset saved. Closed candles are ready for replay.");
      },
    );
  }
  async function importFile(selected: File) {
    if (selected.size > 5_000_000) {
      setError("Choose a JSON dataset smaller than 5 MB.");
      return;
    }
    try {
      const parsed = JSON.parse(await selected.text());
      const data = parsed?.payload ?? parsed;
      if (
        !data ||
        !Array.isArray(data.candles) ||
        !data.instrument_id ||
        !data.interval ||
        !data.source
      )
        throw new Error(
          "The JSON needs instrument_id, interval, source and a candles array.",
        );
      const input = {
        name: datasetName,
        instrument_id: data.instrument_id,
        interval: data.interval,
        source: data.source,
        candles: data.candles,
        data_context: data.data_context,
        reason: "Imported owner-supplied retrospective candles",
      };
      await act(
        JSON.stringify(["import", input]),
        (request_id) => researchApi.import({ request_id, ...input }),
        (saved) => {
          setOffset(0);
          void researchApi
            .state(0)
            .then((next) => {
              if (active.current) {
                setState(next);
                setDatasetId(saved.id);
              }
            })
            .catch((err) => {
              if (active.current) setLoadError(errorText(err));
            });
          setNotice(
            "Dataset imported. Source availability remains unverified.",
          );
        },
      );
    } catch (err) {
      setError(errorText(err));
    }
  }
  async function replay(e: FormEvent) {
    e.preventDefault();
    const input = {
      dataset_id: datasetId,
      name: runName,
      hypothesis,
      parameters: {
        ...parameters,
        asset_costs:
          dataset?.asset_type === "crypto_spot"
            ? undefined
            : parameters.asset_costs,
      },
      reason: "Explicit owner research experiment",
    };
    await act(
      JSON.stringify(["run", input]),
      (request_id) => researchApi.run({ request_id, ...input }),
      (saved) => {
        setSelectedRun(saved);
        setOffset(0);
        void reload(0);
        setNotice(
          "Replay saved. Select the result to inspect every assumption and fill.",
        );
      },
    );
  }
  async function exportRecord(type: string, id: string) {
    await act(
      `export/${id}`,
      () => api.resource(type, id),
      (result) => download(result.data, `tauric-${type}-${id}.json`),
    );
  }
  const dataset = state?.datasets.find((d) => d.id === datasetId);
  const breakout = parameters.engine === "closed_bar_breakout_v1";
  const version = versions.find(
    (value) => value.id === parameters.strategy_version_id,
  );
  const intervalMismatch = !!(
    version &&
    dataset &&
    version.parameters.rules?.interval !== dataset.interval
  );
  const account = analytics?.accounts.find((a) => a.account_id === accountId);
  const numeric: [keyof Parameters, string][] = [
    ["fast_window", "Fast average · candles"],
    ["slow_window", "Slow average · candles"],
  ];
  const decimal: [keyof Parameters, string][] = [
    [
      "starting_balance",
      `Starting balance · ${dataset?.quote_currency ?? "USDT"}`,
    ],
    ["allocation_percent", "Capital allocation · %"],
    ["risk_percent", "Risk budget · %"],
    ["stop_percent", "Stop distance · %"],
    ["quantity_step", "Quantity step"],
    ["fee_bps", "Fee · basis points"],
    ["slippage_bps", "Slippage · basis points"],
  ];
  return (
    <div className="page-scroll research-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">EVIDENCE → EXPERIMENT → OUTCOME</span>
          <h1>Research & analytics</h1>
          <p>
            Save the data, make assumptions explicit, inspect what happened.
          </p>
        </div>
        <button
          className="secondary"
          disabled={pending}
          onClick={() => void reload()}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>
      <div
        className="research-tabs"
        role="tablist"
        aria-label="Research and analytics"
      >
        <button
          role="tab"
          aria-selected={tab === "lab"}
          onClick={() => {
            setTab("lab");
            setError("");
            setNotice("");
          }}
        >
          Research Lab
        </button>
        <button
          role="tab"
          aria-selected={tab === "reports"}
          onClick={() => {
            setTab("reports");
            setError("");
            setNotice("");
            setLoadError("");
          }}
        >
          Tauric reports
        </button>
        <button
          role="tab"
          aria-selected={tab === "research"}
          onClick={() => {
            setTab("research");
            setError("");
            setNotice("");
          }}
        >
          Research experiments
        </button>
        <button
          role="tab"
          aria-selected={tab === "analytics"}
          onClick={() => {
            setTab("analytics");
            setError("");
            setNotice("");
          }}
        >
          Paper analytics
        </button>
        <button
          role="tab"
          aria-selected={tab === "qualification"}
          onClick={() => {
            setTab("qualification");
            setError("");
            setNotice("");
          }}
        >
          Strategy qualification
        </button>
      </div>
      {loadError && (
        <p className="error" role="alert">
          {loadError}{" "}
          <button className="text-button" onClick={() => void reload()}>
            Try again
          </button>
        </p>
      )}
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
      {tab === "qualification" && <Qualification open={open} />}
      {tab === "lab" && <ResearchLab />}
      {tab === "reports" && (
        <SavedReports open={open} refresh={reportRefresh} />
      )}
      {tab === "research" && (
        <>
          <p className="research-context">
            Retrospective asset replay · closed-candle signals · next-open fills
            · no AI calls
          </p>
          <div className="research-setup">
            <section className="research-block">
              <div className="research-block-heading">
                <span className="eyebrow">DATASET</span>
                <span className="badge neutral">RETROSPECTIVE</span>
              </div>
              <h2>Freeze the evidence</h2>
              <form onSubmit={capture}>
                <fieldset disabled={pending}>
                  <label>
                    Dataset name
                    <input
                      required
                      minLength={3}
                      maxLength={120}
                      value={datasetName}
                      onChange={(e) => setDatasetName(e.target.value)}
                    />
                  </label>
                  <p>
                    {market.feed?.instrument.symbol ??
                      "Choose a chart instrument"}{" "}
                    · {market.interval} ·{" "}
                    {market.feed?.candles.filter((c) => c.closed).length ?? 0}{" "}
                    closed candles
                  </p>
                  <button
                    className="secondary"
                    type="button"
                    disabled={market.loading}
                    onClick={() => void market.refresh()}
                  >
                    <RefreshCw size={13} />
                    Refresh chart data
                  </button>
                  <button
                    className="primary"
                    disabled={!market.feed?.snapshot_id}
                    type="submit"
                  >
                    Save chart dataset
                  </button>
                </fieldset>
              </form>
              {market.error && (
                <p className="error" role="alert">
                  {market.error}
                </p>
              )}
              <div className="research-import">
                <input
                  ref={file}
                  type="file"
                  accept="application/json,.json"
                  aria-label="Import candle JSON"
                  disabled={pending}
                  onChange={(e) => {
                    const selected = e.target.files?.[0];
                    if (selected) void importFile(selected);
                    e.target.value = "";
                  }}
                />
                <button
                  className="text-button"
                  disabled={pending}
                  onClick={() => file.current?.click()}
                >
                  Import candle JSON
                </button>
                <details>
                  <summary>Dataset format</summary>
                  <p>
                    JSON: instrument_id, interval, source and candles. Each
                    candle needs timezone-aware open_time/close_time,
                    decimal-string open/high/low/close/volume and closed: true.
                    Use a registered spot instrument and 3–5,000 continuous
                    candles.
                  </p>
                </details>
              </div>
              <label>
                Saved dataset
                <select
                  aria-label="Saved dataset"
                  value={datasetId}
                  onChange={(e) => setDatasetId(e.target.value)}
                >
                  <option value="">Select saved evidence</option>
                  {state?.datasets.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} · {d.interval} · {d.bar_count} candles
                    </option>
                  ))}
                </select>
              </label>
              {dataset && (
                <div className="research-provenance">
                  <p>
                    {dataset.instrument_id}
                    <br />
                    {date(dataset.start_time)} → {date(dataset.end_time)}
                    <br />
                    {dataset.source}
                    <br />
                    {dataset.asset_type} · {dataset.quote_currency} ·{" "}
                    {dataset.data_context?.calendar_id} ·{" "}
                    {dataset.data_context?.price_side}
                  </p>
                  <code title={dataset.checksum}>
                    SHA-256 {dataset.checksum.slice(0, 16)}…
                  </code>
                  <div>
                    <button
                      className="text-button"
                      onClick={() =>
                        open({ type: "research_dataset", id: dataset.id })
                      }
                    >
                      Inspect source
                    </button>
                    <button
                      className="text-button"
                      disabled={pending}
                      onClick={() =>
                        void exportRecord("research_dataset", dataset.id)
                      }
                    >
                      <Download size={13} />
                      Export dataset
                    </button>
                  </div>
                </div>
              )}
            </section>
            <section className="research-block">
              <span className="eyebrow">EXPERIMENT</span>
              <h2>
                {breakout ? "Test saved strategy rules" : "Test a crossover"}
              </h2>
              <p>
                {breakout
                  ? "Replay the exact saved breakout and volume filters, opening stop reference and stop/target/channel exits. Every signal fills at the next open, not at its trigger price. No approvals or orders are created."
                  : "Buy after the fast SMA crosses above the slow SMA. Exit after a downward cross or a simulated stop."}
              </p>
              <form onSubmit={replay}>
                <fieldset disabled={pending}>
                  <label>
                    Replay rules
                    <select
                      aria-label="Replay rules"
                      value={parameters.engine}
                      onChange={(event) =>
                        setParameters({
                          ...parameters,
                          engine: event.target.value as Parameters["engine"],
                          strategy_version_id: undefined,
                        })
                      }
                    >
                      <option value="sma_cross_spot_v1">
                        SMA crossover experiment
                      </option>
                      <option value="closed_bar_breakout_v1">
                        Saved closed-candle breakout
                      </option>
                    </select>
                  </label>
                  {breakout && (
                    <>
                      <label>
                        Saved strategy version
                        <select
                          required
                          aria-label="Saved strategy version"
                          value={parameters.strategy_version_id ?? ""}
                          onChange={(event) =>
                            setParameters({
                              ...parameters,
                              strategy_version_id:
                                event.target.value || undefined,
                            })
                          }
                        >
                          <option value="">Select an executable version</option>
                          {versions.map((saved) => (
                            <option key={saved.id} value={saved.id}>
                              {saved.name} · {saved.parameters.label} · revision{" "}
                              {saved.revision} ·{" "}
                              {saved.parameters.rules?.interval}
                            </option>
                          ))}
                        </select>
                      </label>
                      {versionError && (
                        <p className="error" role="alert">
                          {versionError} Use Refresh to reload saved versions.
                        </p>
                      )}
                      {!versions.length && !versionError && (
                        <p>
                          Save an executable breakout version in Settings →
                          Trading first.
                        </p>
                      )}
                      {version && (
                        <p>
                          Saved capital ceiling{" "}
                          {amount(version.parameters.capital_limit)} USDT ·
                          strategy risk {version.parameters.risk_percent}% of
                          that ceiling · quantity step{" "}
                          {version.parameters.quantity_step}. Experiment caps
                          can only tighten these limits.
                        </p>
                      )}
                      {intervalMismatch && (
                        <p className="error" role="alert">
                          Choose a {version?.parameters.rules?.interval} dataset
                          to match this saved version.
                        </p>
                      )}
                    </>
                  )}
                  <label>
                    Experiment name
                    <input
                      required
                      minLength={3}
                      maxLength={120}
                      value={runName}
                      onChange={(e) => setRunName(e.target.value)}
                    />
                  </label>
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
                  <div className="research-fields">
                    {(breakout ? [] : numeric).map(([key, label]) => (
                      <label key={key}>
                        {label}
                        <input
                          required
                          type="number"
                          min={key === "fast_window" ? 1 : 2}
                          max={key === "fast_window" ? 200 : 500}
                          value={String(parameters[key])}
                          onChange={(e) =>
                            setParameters({
                              ...parameters,
                              [key]: Number(e.target.value),
                            })
                          }
                        />
                      </label>
                    ))}
                    {decimal
                      .filter(
                        ([key]) =>
                          !breakout ||
                          (key !== "stop_percent" && key !== "quantity_step"),
                      )
                      .map(([key, label]) => (
                        <label key={key}>
                          {label}
                          <input
                            required
                            inputMode="decimal"
                            value={String(parameters[key])}
                            onChange={(e) =>
                              setParameters({
                                ...parameters,
                                [key]: e.target.value,
                              })
                            }
                          />
                        </label>
                      ))}
                  </div>
                  {dataset && dataset.asset_type !== "crypto_spot" && (
                    <>
                      <p>
                        {dataset.asset_type.toUpperCase()} costs · amounts in{" "}
                        {dataset.quote_currency}.{" "}
                        {dataset.asset_type === "fx"
                          ? "Saved bid/ask data includes spread; enter zero additional spread. Financing is a daily debit; negative values model credits."
                          : "Set the spread, commission and sell-side tax for this experiment."}
                      </p>
                      <div className="research-fields">
                        {(
                          [
                            ["spread_bps", "Full spread · basis points"],
                            [
                              "commission_per_unit",
                              "Commission per unit / share",
                            ],
                            [
                              "minimum_commission",
                              "Minimum commission per fill",
                            ],
                            ["sell_tax_bps", "Sell-side tax · basis points"],
                            ...(dataset.asset_type === "fx"
                              ? [
                                  [
                                    "financing_daily_bps",
                                    "Daily FX financing · basis points",
                                  ],
                                ]
                              : []),
                          ] as [string, string][]
                        ).map(([key, label]) => (
                          <label key={key}>
                            {label}
                            <input
                              required
                              inputMode="decimal"
                              value={String(
                                parameters.asset_costs?.asset_type ===
                                  dataset.asset_type
                                  ? ((
                                      parameters.asset_costs as Record<
                                        string,
                                        unknown
                                      >
                                    )[key] ?? "")
                                  : "",
                              )}
                              onChange={(e) =>
                                setParameters({
                                  ...parameters,
                                  asset_costs: {
                                    ...(parameters.asset_costs?.asset_type ===
                                    dataset.asset_type
                                      ? parameters.asset_costs
                                      : {
                                          spread_bps: "",
                                          commission_per_unit: "0",
                                          minimum_commission: "0",
                                          sell_tax_bps: "0",
                                          triple_rollover_weekday: 2,
                                        }),
                                    asset_type: dataset.asset_type as
                                      "fx" | "equity" | "etf",
                                    [key]: e.target.value,
                                  },
                                })
                              }
                            />
                          </label>
                        ))}
                      </div>
                      {dataset.asset_type === "fx" && (
                        <label>
                          Triple rollover day
                          <select
                            value={
                              parameters.asset_costs?.triple_rollover_weekday ??
                              2
                            }
                            onChange={(e) =>
                              setParameters({
                                ...parameters,
                                asset_costs: {
                                  commission_per_unit: "0",
                                  minimum_commission: "0",
                                  sell_tax_bps: "0",
                                  ...parameters.asset_costs,
                                  asset_type: "fx",
                                  spread_bps:
                                    parameters.asset_costs?.spread_bps ?? "",
                                  triple_rollover_weekday: Number(
                                    e.target.value,
                                  ),
                                },
                              })
                            }
                          >
                            {[
                              "Monday",
                              "Tuesday",
                              "Wednesday",
                              "Thursday",
                              "Friday",
                            ].map((label, value) => (
                              <option key={label} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                    </>
                  )}
                  <button
                    className="primary"
                    type="submit"
                    disabled={
                      !datasetId || (breakout && (!version || intervalMismatch))
                    }
                  >
                    <FlaskConical size={14} />
                    {pending ? "Working…" : "Run replay"}
                  </button>
                </fieldset>
              </form>
            </section>
          </div>
          <section className="research-block research-history">
            <div className="research-block-heading">
              <h2>Saved experiments</h2>
              <div className="research-paging">
                <button
                  className="text-button"
                  disabled={!offset || pending}
                  onClick={() => setOffset(Math.max(0, offset - 25))}
                >
                  Newer
                </button>
                <span>
                  {offset + 1}–
                  {offset +
                    Math.max(
                      state?.runs.length ?? 0,
                      state?.datasets.length ?? 0,
                    )}
                </span>
                <button
                  className="text-button"
                  disabled={
                    pending ||
                    Math.max(
                      state?.runs.length ?? 0,
                      state?.datasets.length ?? 0,
                    ) < 25
                  }
                  onClick={() => setOffset(offset + 25)}
                >
                  Older
                </button>
              </div>
            </div>
            {!state ? (
              <p className="research-empty">
                {loadError
                  ? "Saved experiments are unavailable. Use Refresh to retry."
                  : "Loading saved experiments…"}
              </p>
            ) : !state.runs.length ? (
              <p className="research-empty">
                Save a dataset, then run an experiment. Each result keeps its
                hypothesis, costs, source version and trade history.
              </p>
            ) : (
              <div className="research-run-list">
                {state.runs.map((run) => (
                  <button
                    key={run.id}
                    className={selectedRun?.id === run.id ? "selected" : ""}
                    onClick={() => setSelectedRun(run)}
                  >
                    <span>
                      {run.name}
                      <small>
                        {date(run.created_at)} ·{" "}
                        {run.strategy_version
                          ? `${run.strategy_version.name} · ${run.strategy_version.parameters.label} · revision ${run.strategy_version.revision}`
                          : `SMA ${run.parameters.fast_window}/${run.parameters.slow_window}`}
                      </small>
                    </span>
                    <strong>
                      {amount(run.result.return_percent)}%
                      <small>
                        {run.result.metrics.closed_trades} closed trades
                      </small>
                    </strong>
                  </button>
                ))}
              </div>
            )}
          </section>
          {selectedRun && (
            <section className="research-block research-result">
              <div className="research-block-heading">
                <div>
                  <span className="eyebrow">SAVED OUTCOME</span>
                  <h2>{selectedRun.name}</h2>
                </div>
                <button
                  className="secondary"
                  disabled={pending}
                  onClick={() =>
                    void exportRecord("research_run", selectedRun.id)
                  }
                >
                  <Download size={14} />
                  Export result
                </button>
              </div>
              <p>{selectedRun.hypothesis}</p>
              {selectedRun.parameters.asset_costs?.asset_type === "fx" && (
                <p>
                  FX financing debit / credit:{" "}
                  {amount(selectedRun.result.financing_cost)} ·{" "}
                  {selectedRun.result.financing_events?.length ?? 0} saved
                  rollovers.
                </p>
              )}
              {selectedRun.strategy_version && (
                <p>
                  Pinned strategy:{" "}
                  <button
                    className="text-button"
                    onClick={() =>
                      open({
                        type: "strategy_version",
                        id: selectedRun.strategy_version!.id,
                      })
                    }
                  >
                    {selectedRun.strategy_version.name} ·{" "}
                    {selectedRun.strategy_version.parameters.label} · revision{" "}
                    {selectedRun.strategy_version.revision}
                  </button>
                </p>
              )}
              <Outcome
                value={selectedRun.result.metrics}
                currency={selectedRun.quote_currency}
                returns={selectedRun.result.return_percent}
                drawdown={selectedRun.result.max_drawdown_percent}
              />
              <Curve
                points={selectedRun.result.equity}
                label="Replay equity · bar-close marks"
                currency={selectedRun.quote_currency}
              />
              <Trades
                trades={selectedRun.result.trades}
                currency={selectedRun.quote_currency}
              />
              <details>
                <summary>Assumptions and provenance</summary>
                <ul>
                  {selectedRun.assumptions.map((value) => (
                    <li key={value}>{value}</li>
                  ))}
                </ul>
                <p>
                  {selectedRun.engine_version} · {selectedRun.result.bars}{" "}
                  candles · {selectedRun.result.skipped_entries} skipped entries
                  (quantity step or invalid opening stop)
                </p>
                <code>
                  Dataset {selectedRun.dataset_checksum}
                  <br />
                  Engine {selectedRun.engine_checksum}
                  <br />
                  Result {selectedRun.checksum}
                  {selectedRun.strategy_version && (
                    <>
                      <br />
                      Strategy {selectedRun.strategy_version.checksum}
                    </>
                  )}
                </code>
                <pre>{JSON.stringify(selectedRun.parameters, null, 2)}</pre>
                <button
                  className="text-button"
                  onClick={() =>
                    open({ type: "research_run", id: selectedRun.id })
                  }
                >
                  Inspect complete record <ArrowRight size={13} />
                </button>
              </details>
            </section>
          )}
        </>
      )}
      {tab === "analytics" && (
        <>
          <p className="research-context">
            {analytics?.detail ?? "Loading verified paper outcomes…"}
          </p>
          {!analytics ? (
            <section className="research-block">
              <h2>
                {loadError
                  ? "Paper outcomes unavailable"
                  : "Loading paper outcomes…"}
              </h2>
              <p>
                {loadError
                  ? "Use Refresh to read the account ledger again."
                  : "Reading recorded fills and their strategy attribution."}
              </p>
            </section>
          ) : !analytics.accounts.length ? (
            <section className="research-block">
              <h2>No paper account outcomes yet</h2>
              <p>
                Create an account in Settings → Paper trading. Closed trades
                will appear here with their strategy and fill records.
              </p>
            </section>
          ) : (
            <>
              <label className="research-account">
                Paper account
                <select
                  aria-label="Paper account"
                  value={accountId}
                  onChange={(e) => setAccountId(e.target.value)}
                >
                  {analytics.accounts.map((a) => (
                    <option key={a.account_id} value={a.account_id}>
                      {a.name} · {a.currency}
                    </option>
                  ))}
                </select>
              </label>
              {account && (
                <section className="research-block">
                  <div className="research-block-heading">
                    <h2>{account.name}</h2>
                    <button
                      className="secondary"
                      onClick={() =>
                        download(
                          account,
                          `tauric-paper-analytics-${account.account_id}.json`,
                        )
                      }
                    >
                      <Download size={14} />
                      Export analytics
                    </button>
                  </div>
                  <p>
                    Cash {amount(account.cash)} USDT · realized balance{" "}
                    {amount(account.realized_balance)} USDT ·{" "}
                    {account.open_positions} open positions
                  </p>
                  <Outcome
                    value={account.metrics}
                    returns={account.realized_return_percent}
                    drawdown={account.realized_max_drawdown_percent}
                  />
                  <Curve
                    points={account.equity}
                    label="Realized balance · closed trades only"
                  />
                  <h3>Strategy attribution</h3>
                  <div className="research-table-scroll">
                    <table className="research-table">
                      <thead>
                        <tr>
                          <th>Strategy / version</th>
                          <th>Instrument</th>
                          <th>Closed / open</th>
                          <th>Win rate</th>
                          <th>Fees · USDT</th>
                          <th>Net P/L · USDT</th>
                        </tr>
                      </thead>
                      <tbody>
                        {account.strategies.map((s) => (
                          <tr key={s.deployment_id}>
                            <td>
                              <button
                                className="text-button"
                                onClick={() =>
                                  open({
                                    type: "paper_deployment",
                                    id: s.deployment_id,
                                  })
                                }
                              >
                                {s.strategy} · {s.strategy_version}
                              </button>
                            </td>
                            <td>{s.instrument_id}</td>
                            <td>
                              {s.metrics.closed_trades} / {s.open_positions}
                            </td>
                            <td>
                              {amount(s.metrics.win_rate_percent)}
                              {s.metrics.win_rate_percent == null ? "" : "%"}
                            </td>
                            <td>{amount(s.metrics.total_fees)}</td>
                            <td>{amount(s.metrics.net_pnl)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <h3>Closed trades</h3>
                  <Trades trades={account.trades} open={open} />
                  <p className="research-digest">
                    Ledger SHA-256 {account.ledger_digest} · generated{" "}
                    {date(analytics.generated_at)}
                  </p>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
