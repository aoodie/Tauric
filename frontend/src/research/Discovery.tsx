import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, Download, RefreshCw } from "lucide-react";
import { client, unwrap, type Interval } from "../api/client";
import type { components } from "../api/schema";
import { useMarket } from "../market/MarketContext";
import "./discovery.css";

type Parameters = Required<components["schemas"]["DiscoveryParameters"]>;
type RecordView = {
  id: string;
  kind: string;
  parent_id: string | null;
  checksum: string;
  created_at: string;
  payload: Record<string, unknown>;
};
type Library = {
  instrument_id: string;
  interval: Interval;
  source_key: string;
  ranges: string[][];
  bars: number;
  bytes: number;
  missing_bars: number;
  last_saved_at: string;
};
type State = {
  offset: number;
  has_more: boolean;
  revision: number;
  parameters: Parameters;
  records: RecordView[];
  library: Library[];
  worker_ready: boolean;
  today: { hypotheses: number; engine_jobs: number; ai_charged_usd: string };
  papers: { title: string; url: string; use: string }[];
  engines: { id: string; ready: boolean }[];
};
const object = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
const list = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.map(object) : [];
const numeric = (v: unknown, digits = 3) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("en-GB", { maximumFractionDigits: digits });
const date = (v: unknown) =>
  new Date(String(v)).toLocaleString("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/London",
  });
const label = (v: unknown) => String(v ?? "").replaceAll("_", " ");
const terminal = new Set([
  "completed",
  "failed",
  "cancelled",
  "rejected",
  "screen_passed",
]);
const read = async (offset = 0) =>
  unwrap(
    await client.GET("/api/v1/research/discovery", {
      params: { query: { offset } },
    }),
  ) as unknown as State;

function useDiscovery() {
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const latest = useRef(0);
  async function reload() {
    const request = ++latest.current;
    try {
      const next = await read(offset);
      if (mounted.current && request === latest.current) {
        setState(next);
        setError("");
      }
    } catch (e) {
      if (mounted.current && request === latest.current)
        setError(
          e instanceof Error ? e.message : "Saved research is unavailable",
        );
    }
  }
  useEffect(() => {
    mounted.current = true;
    void reload();
    const timer = window.setInterval(() => void reload(), 10000);
    return () => {
      mounted.current = false;
      latest.current++;
      window.clearInterval(timer);
    };
  }, [offset]);
  return { state, error, reload, setOffset };
}

export function ResearchSettings() {
  const { state, error, reload } = useDiscovery();
  const market = useMarket();
  const [form, setForm] = useState<Parameters | null>(null);
  const [message, setMessage] = useState("");
  const [formRevision, setFormRevision] = useState<number | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const ids = useRef(new Map<string, string>());
  useEffect(() => {
    if (state && !form) {
      setForm(state.parameters);
      setFormRevision(state.revision);
    }
  }, [state, form]);
  function field<K extends keyof Parameters>(key: K, value: Parameters[K]) {
    setForm((p) => (p ? { ...p, [key]: value } : p));
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form || !state) return;
    setBusy(true);
    setFailure("");
    setMessage("");
    try {
      const key = JSON.stringify([formRevision, form]);
      if (!ids.current.has(key)) ids.current.set(key, crypto.randomUUID());
      const saved = unwrap(
        await client.PUT("/api/v1/research/discovery/settings", {
          body: {
            request_id: ids.current.get(key)!,
            expected_revision: formRevision ?? state.revision,
            parameters: form,
            reason: "Owner saved discovery settings",
          },
        }),
      );
      setFormRevision(Number(saved.revision));
      await reload();
      setMessage("Research settings saved. New cycles use this revision.");
    } catch (e) {
      setFailure(
        e instanceof Error ? e.message : "Research settings could not be saved",
      );
    } finally {
      setBusy(false);
    }
  }
  if (!form || !state)
    return (
      <p role={error ? "alert" : "status"}>
        {error || "Loading research settings…"}
      </p>
    );
  return (
    <section className="discovery-settings" aria-label="Research settings">
      <h2>Strategy research</h2>
      <p>
        Choose what automatic research can explore and how much it can use.
        These settings do not enable broker trading.
      </p>
      <form onSubmit={(e) => void save(e)}>
        <fieldset>
          <legend>Schedule and scope</legend>
          <label className="discovery-check">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => field("enabled", e.target.checked)}
            />
            Automatic strategy exploration
          </label>
          <label>
            Research instruments
            <select
              multiple
              value={form.instrument_ids}
              onChange={(e) =>
                field(
                  "instrument_ids",
                  Array.from(e.target.selectedOptions, (o) => o.value),
                )
              }
            >
              {market.instruments
                .filter(
                  (i) => i.provider === "oanda_v20" && i.asset_type === "fx",
                )
                .map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.symbol}
                  </option>
                ))}
            </select>
          </label>
          <div className="discovery-options">
            {(["1m", "5m", "15m", "1h", "4h", "1d"] as Interval[]).map((v) => (
              <label className="discovery-check" key={v}>
                <input
                  type="checkbox"
                  checked={form.intervals.includes(v)}
                  onChange={(e) =>
                    field(
                      "intervals",
                      e.target.checked
                        ? [...form.intervals, v]
                        : form.intervals.filter((x) => x !== v),
                    )
                  }
                />
                {v}
              </label>
            ))}
          </div>
          <div className="discovery-options">
            {(["trend", "breakout", "mean_reversion"] as const).map((v) => (
              <label className="discovery-check" key={v}>
                <input
                  type="checkbox"
                  checked={form.families.includes(v)}
                  onChange={(e) =>
                    field(
                      "families",
                      e.target.checked
                        ? [...form.families, v]
                        : form.families.filter((x) => x !== v),
                    )
                  }
                />
                {label(v)}
              </label>
            ))}
          </div>
          {(
            [
              ["multi_timeframe", "Higher-timeframe context"],
              ["mixtures", "Combined strategies"],
              ["qlib", "Qlib model experiments"],
              ["walk_forward", "Earlier walk-forward comparison"],
            ] as const
          ).map(([key, text]) => (
            <label className="discovery-check" key={key}>
              <input
                type="checkbox"
                checked={form[key]}
                onChange={(e) => field(key, e.target.checked)}
              />
              {text}
            </label>
          ))}
          <div className="discovery-fields">
            {(
              [
                ["cadence_hours", "Hours between cycles"],
                ["daily_hypothesis_limit", "New hypotheses per UTC day"],
                ["daily_experiment_limit", "Engine jobs per UTC day"],
                ["lookback_days", "History window in days"],
              ] as const
            ).map(([key, text]) => (
              <label key={key}>
                {text}
                <input
                  type="number"
                  min="1"
                  required
                  value={form[key]}
                  onChange={(e) => field(key, Number(e.target.value))}
                />
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Research costs and screening</legend>
          <div className="discovery-fields">
            {(
              [
                ["slippage_bps", "Baseline slippage · bps"],
                ["stressed_slippage_bps", "Stressed slippage · bps"],
                ["fee_bps", "Notional fee · bps"],
                ["financing_daily_bps", "Daily FX financing · bps"],
                ["minimum_return_percent", "Minimum net return · %"],
                ["maximum_drawdown_percent", "Maximum sampled drawdown · %"],
              ] as const
            ).map(([key, text]) => (
              <label key={key}>
                {text}
                <input
                  inputMode="decimal"
                  required
                  value={form[key]}
                  onChange={(e) => field(key, e.target.value)}
                />
              </label>
            ))}
            <label>
              Minimum closed simulated trades
              <input
                type="number"
                min="1"
                required
                value={form.minimum_closed_trades}
                onChange={(e) =>
                  field("minimum_closed_trades", Number(e.target.value))
                }
              />
            </label>
          </div>
          <p>
            Historical bid/ask already includes spread. Slippage and financing
            remain declared assumptions.
          </p>
        </fieldset>
        <fieldset>
          <legend>AI ideas and spending</legend>
          <label className="discovery-check">
            <input
              type="checkbox"
              checked={form.ai_enabled}
              onChange={(e) => field("ai_enabled", e.target.checked)}
            />
            AI-generated hypotheses
          </label>
          <div className="discovery-fields">
            <label>
              AI connection
              <select
                value={form.ai_profile}
                onChange={(e) =>
                  field("ai_profile", e.target.value as "cloud" | "local")
                }
              >
                <option value="local">Local / compatible</option>
                <option value="cloud">Cloud</option>
              </select>
            </label>
            {(
              [
                ["daily_ai_budget_usd", "Daily AI ceiling · USD"],
                [
                  "ai_input_usd_per_million",
                  "Input price · USD per million tokens",
                ],
                [
                  "ai_output_usd_per_million",
                  "Output price · USD per million tokens",
                ],
              ] as const
            ).map(([key, text]) => (
              <label key={key}>
                {text}
                <input
                  inputMode="decimal"
                  required
                  value={form[key]}
                  onChange={(e) => field(key, e.target.value)}
                />
              </label>
            ))}
            <label>
              Maximum output tokens per request
              <input
                type="number"
                min="500"
                max="4000"
                required
                value={form.ai_max_output_tokens}
                onChange={(e) =>
                  field("ai_max_output_tokens", Number(e.target.value))
                }
              />
            </label>
          </div>
          <p>
            The ceiling uses your saved token prices. Each request reserves its
            maximum before calling the model. Unknown usage retains the
            reservation. Charged or reserved today: $
            {numeric(state.today.ai_charged_usd)}.
          </p>
        </fieldset>
        {(error || failure) && (
          <p className="error" role="alert">
            {failure || error}
          </p>
        )}
        {message && <p role="status">{message}</p>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save research settings"}
        </button>
      </form>
    </section>
  );
}

type Series = { name: string; values: { time: string; value: number }[] };
function Plot({
  title,
  series,
  unit,
  markers = [],
}: {
  title: string;
  series: Series[];
  unit: string;
  markers?: {
    time: string;
    value: number;
    buy: boolean;
    explanation: string;
  }[];
}) {
  const [at, setAt] = useState(0);
  const [width, setWidth] = useState(720);
  const svg = useRef<SVGSVGElement>(null);
  const points = [...series.flatMap((s) => s.values), ...markers].filter((p) =>
    Number.isFinite(p.value),
  );
  const hasPoints = points.length > 0;
  useEffect(() => {
    if (!svg.current) return;
    const observer = new ResizeObserver((entries) =>
      setWidth(Math.max(280, entries[0].contentRect.width)),
    );
    observer.observe(svg.current);
    return () => observer.disconnect();
  }, [hasPoints]);
  if (!points.length)
    return <p>No observations are available for this chart.</p>;
  const lo = Math.min(...points.map((p) => p.value)),
    hi = Math.max(...points.map((p) => p.value));
  const left = Math.min(...points.map((p) => Date.parse(p.time))),
    right = Math.max(...points.map((p) => Date.parse(p.time)));
  const y = (v: number) => 245 - ((v - lo) / (hi - lo || 1)) * 215;
  const x = (t: string) =>
    76 + ((Date.parse(t) - left) / (right - left || 1)) * (width - 100);
  const primary = series[0]?.values ?? [],
    inspected = primary[Math.min(at, primary.length - 1)];
  return (
    <figure className="discovery-plot">
      <figcaption>
        {title} <span>{unit}</span>
      </figcaption>
      <svg ref={svg} viewBox={`0 0 ${width} 290`} role="img" aria-label={title}>
        {[0, 1, 2, 3, 4].map((i) => {
          const v = lo + ((hi - lo) * i) / 4;
          return (
            <g key={i}>
              <line
                x1="76"
                x2={width - 24}
                y1={y(v)}
                y2={y(v)}
                className="discovery-grid"
              />
              <text x="68" y={y(v) + 4} textAnchor="end">
                {numeric(v, unit === "quote currency" ? 5 : 3)}
              </text>
            </g>
          );
        })}
        {series.map((s, i) => (
          <polyline
            key={s.name}
            className={`discovery-line line-${i % 3}`}
            points={s.values.map((p) => `${x(p.time)},${y(p.value)}`).join(" ")}
          />
        ))}
        {inspected && (
          <circle
            cx={x(inspected.time)}
            cy={y(inspected.value)}
            r="4"
            className="discovery-point"
          />
        )}
        <text x="76" y="278">
          {new Date(left).toLocaleDateString("en-GB")}
        </text>
        {markers
          .filter(
            (m) =>
              Number.isFinite(m.value) &&
              Date.parse(m.time) >= left &&
              Date.parse(m.time) <= right,
          )
          .map((m, i) => (
            <g key={i}>
              <path
                className={m.buy ? "discovery-buy" : "discovery-sell"}
                d={
                  m.buy
                    ? `M ${x(m.time)} ${y(m.value) - 6} l -5 10 h 10 z`
                    : `M ${x(m.time)} ${y(m.value) + 6} l -5 -10 h 10 z`
                }
              >
                <title>{`${m.buy ? "Buy" : "Sell"} ${numeric(m.value)} · ${m.explanation}`}</title>
              </path>
            </g>
          ))}
        <text x={width - 24} y="278" textAnchor="end">
          {new Date(right).toLocaleDateString("en-GB")}
        </text>
      </svg>
      <div className="discovery-legend">
        {series.map((s, i) => (
          <span key={s.name}>
            <i className={`legend-${i % 3}`} />
            {s.name}
          </span>
        ))}
      </div>
      {primary.length > 1 && (
        <label className="discovery-inspect">
          Inspect observation
          <input
            aria-label={`Inspect ${title}`}
            type="range"
            min="0"
            max={primary.length - 1}
            value={Math.min(at, primary.length - 1)}
            onChange={(e) => setAt(Number(e.target.value))}
          />
          <span>
            {inspected
              ? `${date(inspected.time)} · ${numeric(inspected.value, unit === "quote currency" ? 5 : 3)} ${unit}`
              : ""}
          </span>
        </label>
      )}
    </figure>
  );
}

function History({
  state,
  reload,
}: {
  state: State;
  reload: () => Promise<void>;
}) {
  const market = useMarket();
  const [instrument, setInstrument] = useState("oanda:fx:EUR_USD");
  const [interval, setInterval] = useState<Interval>("1h");
  const [start, setStart] = useState(
    new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10),
  );
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [selection, setSelection] = useState<Library | null>(null);
  const [chartStart, setChartStart] = useState("");
  const [chartEnd, setChartEnd] = useState("");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const ids = useRef(new Map<string, string>());
  async function load(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const key = JSON.stringify([instrument, interval, start, end]);
      if (!ids.current.has(key)) ids.current.set(key, crypto.randomUUID());
      unwrap(
        await client.POST("/api/v1/research/discovery/history", {
          body: {
            request_id: ids.current.get(key)!,
            instrument_id: instrument,
            interval,
            start: start + "T00:00:00Z",
            end: end + "T00:00:00Z",
            reason: "Owner requested reusable OANDA history",
          },
        }),
      );
      setNotice(
        "History load queued. Saved ranges will be reused; only missing ranges are downloaded.",
      );
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "History could not be loaded");
    } finally {
      setBusy(false);
    }
  }
  async function inspect(row: Library, from?: string, to?: string) {
    setSelection(row);
    setData(null);
    setError("");
    try {
      const starts = row.ranges.map((r) => r[0]).sort(),
        ends = row.ranges.map((r) => r[1]).sort();
      const seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
      }[row.interval];
      const right = to ?? ends.at(-1)!;
      const left =
        from ??
        new Date(
          Math.max(
            Date.parse(starts[0]),
            Date.parse(right) - Math.min(1500 * 86400, 99900 * seconds) * 1000,
          ),
        ).toISOString();
      setChartStart(new Date(left).toISOString().slice(0, 16));
      setChartEnd(new Date(right).toISOString().slice(0, 16));
      setData(
        unwrap(
          await client.GET("/api/v1/research/discovery/history", {
            params: {
              query: {
                instrument_id: row.instrument_id,
                interval: row.interval,
                source_key: row.source_key,
                start: left,
                end: right,
              },
            },
          }),
        ) as Record<string, unknown>,
      );
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Saved history could not be read",
      );
    }
  }
  const bars = list(data?.candles);
  return (
    <div className="discovery-view">
      <h2>Saved OANDA history</h2>
      <p>
        Download once, reuse across strategies. Existing history stays available
        when the provider is offline.
      </p>
      <details className="discovery-loader">
        <summary>Load or extend saved history</summary>
        <form onSubmit={(e) => void load(e)} className="discovery-fields">
          <label>
            Currency pair
            <select
              value={instrument}
              onChange={(e) => setInstrument(e.target.value)}
            >
              {market.instruments
                .filter(
                  (i) => i.provider === "oanda_v20" && i.asset_type === "fx",
                )
                .map((i) => (
                  <option value={i.id} key={i.id}>
                    {i.symbol}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Bar interval
            <select
              value={interval}
              onChange={(e) => setInterval(e.target.value as Interval)}
            >
              {["1m", "5m", "15m", "1h", "4h", "1d"].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label>
            History from · UTC
            <input
              type="date"
              required
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            History to · UTC
            <input
              type="date"
              required
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button className="primary" disabled={busy}>
            Save OANDA history
          </button>
        </form>
      </details>
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="discovery-rows">
        {state.library.map((row) => (
          <button
            className="discovery-row"
            key={row.instrument_id + row.interval + row.source_key}
            onClick={() => void inspect(row)}
          >
            <strong>
              {row.instrument_id.split(":").at(-1)?.replace("_", "/")}{" "}
              <small>{row.interval}</small>
            </strong>
            <span>{row.bars.toLocaleString()} saved bars</span>
            <span>
              {numeric(row.bytes / 1024)} KB · {row.missing_bars} missing bars
            </span>
            <span>Inspect saved prices →</span>
          </button>
        ))}
      </div>
      {!state.library.length && (
        <p>No history saved yet. Load a currency pair to begin.</p>
      )}
      {selection && (
        <form
          className="discovery-fields"
          onSubmit={(e) => {
            e.preventDefault();
            void inspect(
              selection,
              new Date(chartStart + "Z").toISOString(),
              new Date(chartEnd + "Z").toISOString(),
            );
          }}
        >
          <label>
            Chart from · UTC
            <input
              type="datetime-local"
              required
              value={chartStart}
              onChange={(e) => setChartStart(e.target.value)}
            />
          </label>
          <label>
            Chart to · UTC
            <input
              type="datetime-local"
              required
              value={chartEnd}
              onChange={(e) => setChartEnd(e.target.value)}
            />
          </label>
          <button className="secondary">Inspect saved range</button>
        </form>
      )}
      {selection && data && (
        <>
          <h3>
            {selection.instrument_id.split(":").at(-1)?.replace("_", "/")} ·{" "}
            {selection.interval}
          </h3>
          <Plot
            title="Saved bid and ask closes"
            unit="quote currency"
            series={[
              {
                name: "Bid",
                values: bars.map((b) => ({
                  time: String(b.close_time),
                  value: Number(object(b.bid).close),
                })),
              },
              {
                name: "Ask",
                values: bars.map((b) => ({
                  time: String(b.close_time),
                  value: Number(object(b.ask).close),
                })),
              },
            ]}
          />
          <p>
            {String(data.total_bars)} saved bars; {bars.length} plotted
            observations. Reading this chart made no provider request.
          </p>
          {list(data.missing_ranges).length > 0 && (
            <p>
              Saved coverage has gaps. Complete the range before using it for
              research.
            </p>
          )}
          <details>
            <summary>Data provenance and limitations</summary>
            <p>
              OANDA provider tick activity; historical bid/ask retained. Saved
              downloads are retrospective receipts, not independently verified
              point-in-time archives.
            </p>
            <p>
              Last saved {date(selection.last_saved_at)}. Chunk checksums are
              included in each study.
            </p>
          </details>
        </>
      )}
    </div>
  );
}

export function StrategyDiscovery() {
  const { state, error, reload, setOffset } = useDiscovery();
  const [view, setView] = useState("strategies");
  const [selectedId, setSelectedId] = useState("");
  const [metric, setMetric] = useState("equity");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [prices, setPrices] = useState<Record<string, unknown>[]>([]);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  const trials =
    state?.records.filter(
      (r) => r.kind === "trial" || r.kind === "confirmation",
    ) ?? [];
  const selected = trials.find((t) => t.id === selectedId);
  const campaign = state?.records.find((r) => r.id === selected?.parent_id);
  const diagnostic = object(
    object(campaign?.payload.selection_diagnostics)[selectedId],
  );
  const input = object(selected?.payload.input),
    explanation = object(input.explanation),
    analysis = object(selected?.payload.analysis);
  const baseline = object(selected?.payload.jobs).baseline;
  useEffect(() => {
    let active = true;
    setPrices([]);
    if (selectedId)
      void client
        .GET("/api/v1/research/discovery/records/{record_id}/prices", {
          params: { path: { record_id: selectedId } },
        })
        .then(unwrap)
        .then((r) => {
          if (active) setPrices(list(r.candles));
        })
        .catch(() => {});
    return () => {
      active = false;
    };
  }, [selectedId, selected?.payload.dataset_id]);
  useEffect(() => {
    let active = true;
    setResult(null);
    if (baseline)
      void client
        .GET("/api/v1/research/lab/records/{record_id}", {
          params: { path: { record_id: String(baseline) } },
        })
        .then(unwrap)
        .then((r) => {
          if (active) setResult(object(object(r.payload).result));
        })
        .catch(() => {});
    return () => {
      active = false;
    };
  }, [baseline, selected?.payload.status]);
  async function start() {
    setBusy(true);
    setFailure("");
    try {
      unwrap(
        await client.POST("/api/v1/research/discovery/cycles", {
          body: {
            request_id: requestId.current,
            reason: "Owner requested strategy discovery",
          },
        }),
      );
      requestId.current = crypto.randomUUID();
      await reload();
      setView("activity");
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Research could not start");
    } finally {
      setBusy(false);
    }
  }
  async function control(
    row: RecordView,
    action: "pause" | "resume" | "cancel",
  ) {
    try {
      unwrap(
        await client.POST(
          "/api/v1/research/discovery/records/{record_id}/{action}",
          { params: { path: { record_id: row.id, action } } },
        ),
      );
      await reload();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Research control failed");
    }
  }
  function download(row: RecordView) {
    const a = document.createElement("a");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(row, null, 2)], { type: "application/json" }),
    );
    a.href = url;
    a.download = `tauric-strategy-${row.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="discovery-workspace" aria-label="Strategy discovery">
      <header className="discovery-heading">
        <div>
          <span className="eyebrow">RESEARCH</span>
          <h2>Strategy discovery</h2>
          <p>
            Follow an idea from saved prices to explained, reproducible results.
          </p>
        </div>
        <div className="discovery-actions">
          <button className="text-button" onClick={() => void reload()}>
            <RefreshCw size={14} />
            Refresh
          </button>
          <button
            className="primary"
            disabled={busy || !state?.revision}
            onClick={() => void start()}
          >
            Explore strategies
          </button>
        </div>
      </header>
      {(error || failure) && (
        <p role="alert" className="error">
          {failure || error}
        </p>
      )}
      {state && (
        <>
          <p className="discovery-status">
            Automatic exploration{" "}
            {state.parameters.enabled ? "enabled" : "paused"} ·{" "}
            {state.worker_ready
              ? "Research worker ready"
              : "Research worker offline"}{" "}
            ·{" "}
            {state.engines
              .map(
                (e) => `${e.id.toUpperCase()} ${e.ready ? "ready" : "offline"}`,
              )
              .join(" · ")}
          </p>
          <nav className="discovery-nav" aria-label="Discovery views">
            {[
              ["strategies", "Strategies"],
              ["results", "Results"],
              ["data", "Data library"],
              ["activity", "Activity"],
              ["sources", "Research papers"],
            ].map(([id, title]) => (
              <button
                key={id}
                aria-current={view === id ? "page" : undefined}
                onClick={() => setView(id)}
              >
                {title}
              </button>
            ))}
          </nav>
          {view === "data" && <History state={state} reload={reload} />}
          {view === "strategies" && (
            <div className="discovery-view">
              {!selected ? (
                <>
                  <h3>Recorded strategies</h3>
                  <p>
                    Every hypothesis and outcome stays in the research history,
                    including rejected ideas.
                  </p>
                  <div className="discovery-rows">
                    {trials.map((t) => (
                      <button
                        className="discovery-row"
                        key={t.id}
                        onClick={() => setSelectedId(t.id)}
                      >
                        <strong>
                          {t.kind === "confirmation" && "Final confirmation · "}
                          {String(
                            object(object(t.payload.input).explanation).title,
                          )}
                        </strong>
                        <span>
                          {String(object(t.payload.input).instrument_id)
                            .split(":")
                            .at(-1)
                            ?.replace("_", "/")}{" "}
                          · {String(object(t.payload.input).interval)}
                          {object(object(t.payload.input).definition)
                            .context_interval
                            ? ` + ${String(object(object(t.payload.input).definition).context_interval)} context`
                            : ""}
                        </span>
                        <span>{label(t.payload.status)}</span>
                        <span>Explain strategy →</span>
                      </button>
                    ))}
                  </div>
                  {!trials.length && (
                    <p>
                      No strategies recorded yet. Save research settings, load
                      history and start a discovery cycle.
                    </p>
                  )}
                </>
              ) : (
                <>
                  <button
                    className="text-button"
                    onClick={() => setSelectedId("")}
                  >
                    <ArrowLeft size={14} />
                    All strategies
                  </button>
                  <h3>{String(explanation.title)}</h3>
                  <p>
                    {selected.kind === "confirmation"
                      ? "Final confirmation on reserved data"
                      : "Strategy screening"}{" "}
                    · {label(selected.payload.status)}
                  </p>
                  <p>
                    {String(input.instrument_id)} · {String(input.interval)}
                    {object(input.definition).context_interval
                      ? ` entry · ${String(object(input.definition).context_interval)} context`
                      : ""}
                  </p>
                  <dl className="discovery-explanation">
                    {[
                      ["Idea", explanation.entry],
                      ["Exit", explanation.exit],
                      ["Timeframe context", explanation.context],
                      ["Combination", explanation.combination],
                      ["When it can fail", explanation.failure_conditions],
                      ["Execution status", explanation.execution_eligibility],
                    ]
                      .filter(([, v]) => v != null)
                      .map(([name, value]) => (
                        <div key={String(name)}>
                          <dt>{String(name)}</dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                  </dl>
                  <p>
                    Origin:{" "}
                    {String(input.origin).startsWith("ai:")
                      ? "AI-generated hypothesis"
                      : "Supported research template"}
                    . Recorded {date(selected.created_at)}.
                  </p>
                  <div className="discovery-actions">
                    <button
                      className="primary"
                      onClick={() => setView("results")}
                    >
                      Inspect results
                    </button>
                    <button
                      className="secondary"
                      onClick={() => download(selected)}
                    >
                      <Download size={14} />
                      Export strategy record
                    </button>
                  </div>
                  <details>
                    <summary>Exact rules and record identity</summary>
                    <pre>{JSON.stringify(input.definition, null, 2)}</pre>
                    <p className="discovery-hash">
                      Checksum {selected.checksum}
                    </p>
                  </details>
                </>
              )}
            </div>
          )}
          {view === "results" && (
            <div className="discovery-view">
              <h3>Research results</h3>
              <label>
                Recorded experiment
                <select
                  value={selectedId}
                  onChange={(e) => setSelectedId(e.target.value)}
                >
                  <option value="">Choose a strategy</option>
                  {trials.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.kind === "confirmation" && "Final confirmation · "}
                      {String(
                        object(object(t.payload.input).explanation).title,
                      )}{" "}
                      · {String(object(t.payload.input).interval)} ·{" "}
                      {label(t.payload.status)}
                    </option>
                  ))}
                </select>
              </label>
              {selected && (
                <>
                  <p>
                    {selected.kind === "confirmation"
                      ? "Final confirmation"
                      : "Screening"}{" "}
                    · {label(selected.payload.status)} ·{" "}
                    {String(explanation.title)}
                  </p>
                  {selected.payload.error != null && (
                    <p role="alert">{String(selected.payload.error)}</p>
                  )}
                  {analysis.return_percent != null && (
                    <>
                      <div className="discovery-outcomes">
                        <span>
                          Net return{" "}
                          <strong>{numeric(analysis.return_percent)}%</strong>
                        </span>
                        <span>
                          Stressed return{" "}
                          <strong>
                            {numeric(analysis.stressed_return_percent)}%
                          </strong>
                        </span>
                        <span>
                          Drawdown{" "}
                          <strong>
                            {numeric(analysis.max_drawdown_percent)}%
                          </strong>
                        </span>
                        <span>
                          Closed trades{" "}
                          <strong>{numeric(analysis.closed_trades)}</strong>
                        </span>
                      </div>
                      <div className="discovery-options">
                        {["equity", "drawdown", "price with trades"].map(
                          (v) => (
                            <button
                              key={v}
                              aria-pressed={metric === v}
                              className="secondary"
                              onClick={() => setMetric(v)}
                            >
                              {label(v)}
                            </button>
                          ),
                        )}
                      </div>
                      {metric === "price with trades" && prices.length > 0 && (
                        <Plot
                          title="Price and simulated trades"
                          unit="quote currency"
                          series={[
                            {
                              name: "Bid close",
                              values: prices.map((p) => ({
                                time: String(p.close_time),
                                value: Number(object(p.bid).close),
                              })),
                            },
                          ]}
                          markers={list(result?.fills).map((f) => ({
                            time: String(f.time),
                            value: Number(f.price),
                            buy: Number(f.quantity) > 0,
                            explanation: list(object(f.signal).components)
                              .map(
                                (c) =>
                                  `${label(c.family)}: ${String(c.reason)}`,
                              )
                              .join("; "),
                          }))}
                        />
                      )}
                      {result && metric !== "price with trades" && (
                        <Plot
                          title={
                            metric === "equity"
                              ? "Simulated equity"
                              : "Sampled drawdown"
                          }
                          unit={
                            metric === "equity" ? String(result.currency) : "%"
                          }
                          series={[
                            {
                              name: String(explanation.title),
                              values: (() => {
                                let peak = 10000;
                                return list(result.equity_curve).map((p) => {
                                  const equity = Number(p.equity);
                                  peak = Math.max(peak, equity);
                                  return {
                                    time: String(p.time),
                                    value:
                                      metric === "equity"
                                        ? equity
                                        : (1 - equity / peak) * 100,
                                  };
                                });
                              })(),
                            },
                          ]}
                        />
                      )}
                      <table className="discovery-table">
                        <thead>
                          <tr>
                            <th>Screening criterion</th>
                            <th>Observed</th>
                            <th>Required</th>
                            <th>Result</th>
                          </tr>
                        </thead>
                        <tbody>
                          {list(analysis.criteria).map((c) => (
                            <tr key={String(c.name)}>
                              <td>{String(c.name)}</td>
                              <td>{numeric(c.observed)}</td>
                              <td>{numeric(c.required)}</td>
                              <td>{c.passed ? "Passed" : "Rejected"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {Object.keys(diagnostic).length > 0 && (
                        <details>
                          <summary>Deflated Sharpe diagnostic</summary>
                          <p>
                            {diagnostic.probability == null
                              ? "Insufficient daily observations or recorded trials to calculate the diagnostic."
                              : `Estimated probability: ${numeric(Number(diagnostic.probability) * 100)}%.`}{" "}
                            {numeric(diagnostic.daily_observations)} daily
                            observations across{" "}
                            {numeric(diagnostic.recorded_trials)} recorded
                            trials.
                          </p>
                          <p>
                            Trial and return dependence, or research outside the
                            recorded history, can invalidate this estimate. This
                            diagnostic does not qualify a strategy for trading.
                          </p>
                        </details>
                      )}
                    </>
                  )}
                  <details>
                    <summary>Simulated fills and signal explanations</summary>
                    {list(result?.fills)
                      .slice(0, 100)
                      .map((f, i) => (
                        <p key={i}>
                          {date(f.time)} ·{" "}
                          {Number(f.quantity) > 0 ? "Buy" : "Sell"}{" "}
                          {numeric(Math.abs(Number(f.quantity)))} at{" "}
                          {numeric(f.price)}
                          {f.signal != null
                            ? ` · ${list(object(f.signal).components)
                                .map(
                                  (c) =>
                                    `${label(c.family)}: ${String(c.reason)}`,
                                )
                                .join("; ")}`
                            : ""}
                        </p>
                      ))}
                  </details>
                  <p>
                    Research screening and final confirmation are separate
                    records. Exact native qualification is still required before
                    broker-demo promotion.
                  </p>
                </>
              )}
              <h3>Compare recorded strategies</h3>
              <div className="discovery-table-scroll">
                <table className="discovery-table">
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Interval</th>
                      <th>Net %</th>
                      <th>Stress %</th>
                      <th>Drawdown %</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trials
                      .filter((t) => t.payload.analysis)
                      .map((t) => {
                        const a = object(t.payload.analysis);
                        return (
                          <tr key={t.id}>
                            <td>
                              <button
                                className="text-button"
                                onClick={() => setSelectedId(t.id)}
                              >
                                {t.kind === "confirmation" &&
                                  "Final confirmation · "}
                                {String(
                                  object(object(t.payload.input).explanation)
                                    .title,
                                )}
                              </button>
                            </td>
                            <td>{String(object(t.payload.input).interval)}</td>
                            <td>{numeric(a.return_percent)}</td>
                            <td>{numeric(a.stressed_return_percent)}</td>
                            <td>{numeric(a.max_drawdown_percent)}</td>
                            <td>{label(t.payload.status)}</td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {view === "activity" && (
            <div className="discovery-view">
              <h3>Research activity</h3>
              <p>
                Today: {state.today.hypotheses} hypotheses ·{" "}
                {state.today.engine_jobs} engine jobs. Daily ceilings are
                adjustable in Settings → Research.
              </p>
              {state.records
                .filter(
                  (r) => r.kind === "campaign" || r.kind === "history_sync",
                )
                .map((r) => (
                  <article className="discovery-activity" key={r.id}>
                    <div>
                      <strong>
                        {r.kind === "history_sync"
                          ? "History load"
                          : "Discovery cycle"}
                      </strong>
                      <span>
                        {date(r.created_at)} · {label(r.payload.status)}
                      </span>
                    </div>
                    {r.payload.error != null && (
                      <p>{String(r.payload.error)}</p>
                    )}
                    {object(r.payload.input).deadline_at != null && (
                      <p>
                        Cycle deadline{" "}
                        {date(object(r.payload.input).deadline_at)}.
                      </p>
                    )}
                    {r.payload.requires_owner === true && (
                      <p>
                        Research paused for review. Saved results and original
                        request identities are retained.
                      </p>
                    )}
                    {r.kind === "history_sync" && (
                      <p>
                        {String(object(r.payload.request).instrument_id)} ·{" "}
                        {String(object(r.payload.request).interval)} ·{" "}
                        {String(r.payload.pages_loaded)} new pages ·{" "}
                        {String(r.payload.cache_hits)} reused pages
                      </p>
                    )}
                    {r.payload.summary != null && (
                      <p>
                        {String(object(r.payload.summary).trials)} hypotheses ·{" "}
                        {String(object(r.payload.summary).rejected)} rejected ·{" "}
                        {String(object(r.payload.summary).screen_passed)} passed
                        numerical screening
                      </p>
                    )}
                    {!terminal.has(String(r.payload.status)) && (
                      <div className="discovery-actions">
                        <button
                          className="secondary"
                          onClick={() =>
                            void control(
                              r,
                              r.payload.status === "paused"
                                ? "resume"
                                : "pause",
                            )
                          }
                        >
                          {r.payload.status === "paused" ? "Resume" : "Pause"}
                        </button>
                        <button
                          className="text-button"
                          onClick={() => void control(r, "cancel")}
                        >
                          Cancel
                        </button>
                      </div>
                    )}
                  </article>
                ))}
              {!state.records.length && (
                <p>No research work has been requested.</p>
              )}
            </div>
          )}
          {view === "sources" && (
            <div className="discovery-view">
              <h3>Research references</h3>
              {state.papers.map((p) => (
                <article className="discovery-source" key={p.url}>
                  <a href={p.url} target="_blank" rel="noreferrer">
                    {p.title}
                  </a>
                  <p>{p.use}</p>
                </article>
              ))}
              <p>
                References explain research methods. Their reported results do
                not establish performance for Tauric's instruments or rules.
              </p>
            </div>
          )}
          {view !== "data" &&
            view !== "sources" &&
            (state.offset > 0 || state.has_more) && (
              <div
                className="discovery-actions"
                aria-label="Research history pages"
              >
                <button
                  className="secondary"
                  disabled={state.offset === 0}
                  onClick={() => {
                    setSelectedId("");
                    setOffset(Math.max(0, state.offset - 100));
                  }}
                >
                  Newer research
                </button>
                <button
                  className="secondary"
                  disabled={!state.has_more}
                  onClick={() => {
                    setSelectedId("");
                    setOffset(state.offset + 100);
                  }}
                >
                  Older research
                </button>
              </div>
            )}
        </>
      )}
    </section>
  );
}
