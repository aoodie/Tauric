import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { ArrowRight, Database, RefreshCw, Sparkles, Waves } from "lucide-react";
import { type Candle, type Interval } from "../api/client";
import { useMarket } from "./MarketContext";
import { InstrumentDiscovery } from "./InstrumentDiscovery";

export function MarketChart({
  open,
  analyze,
}: {
  open: (type: string, id: string) => void;
  analyze: () => void;
}) {
  const market = useMarket();
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const sma = useRef<ISeriesApi<"Line"> | null>(null);
  const candles = useRef<Candle[]>([]);
  const [inspected, setInspected] = useState<Candle | null>(null);
  const [period, setPeriod] = useState(20);
  const [overlay, setOverlay] = useState(false);
  const [selectedKey, setSelectedKey] = useState("");
  useEffect(() => {
    if (!host.current) return;
    const instance = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1a2a" },
        textColor: "#91a7bb",
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: "#183247" },
        horzLines: { color: "#183247" },
      },
      rightPriceScale: { borderColor: "#223d54" },
      timeScale: {
        borderColor: "#223d54",
        timeVisible: true,
        secondsVisible: false,
      },
      localization: { locale: "en-GB" },
    });
    series.current = instance.addSeries(CandlestickSeries, {
      upColor: "#4bd3a0",
      downColor: "#ff9298",
      borderVisible: false,
      wickUpColor: "#4bd3a0",
      wickDownColor: "#ff9298",
    });
    sma.current = instance.addSeries(LineSeries, {
      color: "#f4b84a",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    instance.subscribeCrosshairMove((event) => {
      const timestamp = typeof event.time === "number" ? event.time : null;
      setInspected(
        candles.current.find(
          (c) => new Date(c.open_time).getTime() / 1000 === timestamp,
        ) ?? null,
      );
    });
    chart.current = instance;
    return () => {
      instance.remove();
      chart.current = null;
      series.current = null;
      sma.current = null;
    };
  }, []);
  useEffect(() => {
    const rows = market.feed?.candles ?? [];
    candles.current = rows;
    setInspected(null);
    series.current?.setData(
      rows.map((c) => ({
        time: (Date.parse(c.open_time) / 1000) as UTCTimestamp,
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      })),
    );
    const key = `${market.instrumentId}/${market.interval}`;
    if (selectedKey !== key && rows.length) {
      chart.current?.timeScale().fitContent();
      setSelectedKey(key);
    }
    const points = [];
    let sum = 0;
    for (let i = 0; i < rows.length; i++) {
      sum += Number(rows[i].close);
      if (i >= period) sum -= Number(rows[i - period].close);
      if (i >= period - 1 && rows[i].closed)
        points.push({
          time: (Date.parse(rows[i].open_time) / 1000) as UTCTimestamp,
          value: sum / period,
        });
    }
    sma.current?.setData(overlay ? points : []);
  }, [market.feed, market.instrumentId, market.interval, period, overlay]);
  const spec = market.instruments.find((i) => i.id === market.instrumentId);
  const active = inspected ?? market.feed?.candles.at(-1);
  const hasData = !!market.feed?.candles.length;
  return (
    <section className="market-chart-panel">
      <div className="market-controls">
        <select
          aria-label="Chart instrument"
          value={market.instrumentId}
          onChange={(e) => market.setInstrumentId(e.target.value)}
        >
          {market.instruments.map((i) => (
            <option key={i.id} value={i.id}>
              {i.symbol}
            </option>
          ))}
        </select>
        <select
          aria-label="Chart timeframe"
          value={market.interval}
          onChange={(e) => market.setIntervalValue(e.target.value as Interval)}
        >
          {["1m", "5m", "15m", "1h", "4h", "1d"].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <button
          className="secondary small"
          disabled={market.loading || !spec}
          onClick={() => void market.refresh()}
        >
          <RefreshCw size={14} />
          {market.loading ? "Refreshing…" : "Refresh market"}
        </button>
        <label className="market-check">
          <input
            type="checkbox"
            checked={market.auto}
            onChange={(e) => market.setAuto(e.target.checked)}
          />
          Every 30s
        </label>
        <button
          className="text-button market-analyze"
          onClick={analyze}
          disabled={!spec?.native_research_ready}
          title={
            !spec?.native_research_ready
              ? "Native research for this asset is pending"
              : undefined
          }
        >
          <Sparkles size={14} />
          Daily research
        </button>
      </div>
      <InstrumentDiscovery selectOnAdd />
      <div className="market-meta">
        <button
          className="text-button"
          disabled={!spec}
          onClick={() => spec && open("instrument", spec.id)}
        >
          {spec?.symbol ?? "Loading instruments"}
          <ArrowRight size={12} />
        </button>
        <span>
          {market.providers.find((p) => p.id === spec?.provider)?.label ??
            spec?.provider}
          {market.feed?.data_context?.feed
            ? ` · ${market.feed.data_context.feed.toUpperCase()}`
            : ""}
        </span>
        {spec?.asset_type !== "crypto_spot" && (
          <span>
            {market.providers.find((p) => p.id === spec?.provider)?.configured
              ? "Data credentials configured"
              : "Data credentials needed"}
          </span>
        )}
        {market.feed?.session_state !== "unknown" && (
          <span>Session {market.feed?.session_state}</span>
        )}
        <span
          className={`badge ${market.feed?.state === "fresh" ? "green" : "neutral"}`}
        >
          {market.feed?.state === "fresh"
            ? "RECEIVED"
            : market.feed?.state === "stale"
              ? "STALE"
              : "NO DATA"}
        </span>
      </div>
      {market.error && (
        <p className="market-error" role="alert">
          {market.error}
        </p>
      )}
      {!!market.feed?.quality?.warnings?.length && (
        <details className="market-meta">
          <summary>
            Data quality · {market.feed.quality.status} ·{" "}
            {spec?.volume_type.replaceAll("_", " ")}
          </summary>
          {market.feed.quality.warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </details>
      )}
      <div className="market-readout" aria-live="polite">
        {active ? (
          <>
            <time>
              {new Date(active.open_time).toLocaleString("en-GB", {
                timeZone: "UTC",
              })}{" "}
              UTC
            </time>
            {["open", "high", "low", "close", "volume"].map((key) => (
              <span key={key}>
                {key === "volume"
                  ? spec?.volume_type === "provider_ticks"
                    ? "Ticks"
                    : "Vol"
                  : key[0].toUpperCase()}{" "}
                <strong>{active[key as "open"]}</strong>
              </span>
            ))}
            <span>{active.closed ? "Closed candle" : "Forming candle"}</span>
          </>
        ) : (
          <span>No candle selected</span>
        )}
      </div>
      <div className="market-canvas-wrap">
        <div className="market-canvas" ref={host} />
        {!hasData && (
          <div className="market-await">
            <Waves size={25} />
            <h3>Your market view starts here</h3>
            <p>
              Refresh to load candles from the selected data provider. Saved
              snapshots remain inspectable.
            </p>
          </div>
        )}
      </div>
      <div className="market-overlay-controls">
        <label className="market-check">
          <input
            type="checkbox"
            checked={overlay}
            onChange={(e) => setOverlay(e.target.checked)}
          />
          SMA
        </label>
        <input
          aria-label="SMA period"
          type="number"
          min={2}
          max={200}
          value={period}
          onChange={(e) =>
            setPeriod(Math.max(2, Math.min(200, Number(e.target.value) || 2)))
          }
        />
        <span>Closed candles · SMA v1 · visual reference</span>
        <button
          className="text-button"
          disabled={!market.feed?.snapshot_id}
          onClick={() =>
            market.feed?.snapshot_id &&
            open("market_snapshot", market.feed.snapshot_id)
          }
        >
          <Database size={12} />
          Data details
        </button>
      </div>
      <div className="market-foot">
        <span>
          {market.feed?.received_at
            ? `Received ${new Date(market.feed.received_at).toLocaleTimeString("en-GB")} · ${market.feed.candles.length} candles · ${market.feed.gaps} gaps`
            : "No saved market snapshot"}
        </span>
        <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">
          Charts by TradingView
        </a>
      </div>
    </section>
  );
}
