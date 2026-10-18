import { useEffect, useRef, useState } from "react";
import { Search, Plus, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useMarket } from "./MarketContext";
import "./discovery.css";

type Discovery = components["schemas"]["DiscoveryView"];
type Asset = "fx" | "equity" | "etf" | "commodity" | "cfd";

export function InstrumentDiscovery({
  onAdded,
  selectOnAdd = false,
}: {
  onAdded?: () => Promise<void>;
  selectOnAdd?: boolean;
}) {
  const market = useMarket();
  const [provider, setProvider] = useState<"oanda" | "alpaca">("oanda");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Discovery>();
  const [classes, setClasses] = useState<Record<string, Asset>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pending = useRef(false);
  const sequence = useRef(0);
  useEffect(
    () => () => {
      sequence.current++;
    },
    [],
  );

  function changeSource(value: "oanda" | "alpaca") {
    sequence.current++;
    setProvider(value);
    setQuery("");
    setResult(undefined);
    setClasses({});
    setError("");
    setNotice("");
  }
  async function search(offset = 0) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const seq = ++sequence.current;
    try {
      const next = await api.discoverInstruments(provider, query, offset);
      if (sequence.current === seq) setResult(next);
    } catch (e) {
      if (sequence.current === seq) {
        setResult(undefined);
        setError(
          e instanceof Error ? e.message : "Symbol search failed. Try again.",
        );
      }
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function add(symbol: string, asset: Asset) {
    if (!result || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const seq = ++sequence.current;
    try {
      const spec = await api.addInstrument({
        provider,
        symbol,
        asset_type: asset,
        expected_connection_revision: result.connection_revision,
      });
      await market.reloadInstruments();
      if (sequence.current !== seq) return;
      if (selectOnAdd) market.setInstrumentId(spec.id);
      setResult(
        (current) =>
          current && {
            ...current,
            items: current.items.map((item) =>
              item.symbol === symbol
                ? {
                    ...item,
                    registered_id: spec.id,
                    registered_asset_type: asset,
                  }
                : item,
            ),
          },
      );
      setNotice(
        `${spec.symbol} added. Select it in your scanner or research universe and save those settings.`,
      );
      if (onAdded) await onAdded();
    } catch (e) {
      if (sequence.current === seq)
        setError(
          e instanceof Error
            ? e.message
            : "The symbol could not be added. Search again to check availability.",
        );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <details className="instrument-discovery">
      <summary>
        <Search size={14} /> Find more symbols
      </summary>
      <div
        className="discovery-search"
        role="search"
        aria-label="Provider symbol search"
      >
        <label>
          Market
          <select
            aria-label="Discovery market"
            value={provider}
            disabled={busy}
            onChange={(e) => changeSource(e.target.value as "oanda" | "alpaca")}
          >
            <option value="oanda">Forex · OANDA</option>
            <option value="alpaca">Stocks & ETFs · Alpaca</option>
          </select>
        </label>
        <label>
          Symbol or name
          <input
            aria-label="Find provider symbol"
            value={query}
            maxLength={100}
            disabled={busy}
            placeholder={
              provider === "oanda" ? "EUR/GBP or Euro" : "NVDA or company name"
            }
            onChange={(e) => {
              setQuery(e.target.value);
              setResult(undefined);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void search();
              }
            }}
          />
        </label>
        <button
          type="button"
          className="secondary small"
          disabled={busy}
          onClick={() => void search()}
        >
          <Search size={14} /> {busy ? "Checking…" : "Search symbols"}
        </button>
      </div>
      {error && (
        <p role="alert" className="discovery-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="discovery-notice">
          {notice}
        </p>
      )}
      {!result && !error && (
        <p className="discovery-help">
          Search your connected provider, then add symbols to your available
          research instruments.
        </p>
      )}
      {result && (
        <>
          <div className="discovery-count">
            <strong>{result.total.toLocaleString()} matches</strong>
            <span>
              {result.source} · checked{" "}
              {new Date(result.checked_at).toLocaleTimeString("en-GB")}
            </span>
          </div>
          <div className="discovery-results" aria-label="Discovered symbols">
            {result.items.map((item) => {
              const asset =
                item.registered_asset_type ??
                (provider === "oanda"
                  ? (item.category as Asset)
                  : classes[item.symbol]);
              return (
                <article
                  className="discovery-row"
                  key={item.symbol}
                  aria-label={`Discovered ${item.symbol}`}
                >
                  <div className="discovery-identity">
                    <strong>{item.symbol.replace("_", "/")}</strong>
                    <span>{item.name}</span>
                    <small>
                      {item.exchange}
                      {provider === "oanda"
                        ? " · Daily research; economic coverage varies by currency"
                        : ""}
                    </small>
                    {!item.available && (
                      <small className="discovery-warning">
                        {item.unavailable_reason}
                      </small>
                    )}
                  </div>
                  {provider === "alpaca" && !item.registered_id && (
                    <select
                      aria-label={`Asset type for ${item.symbol}`}
                      value={classes[item.symbol] ?? ""}
                      disabled={busy || !item.available}
                      onChange={(e) =>
                        setClasses({
                          ...classes,
                          [item.symbol]: e.target.value as Asset,
                        })
                      }
                    >
                      <option value="">Choose type</option>
                      <option value="equity">Stock</option>
                      <option value="etf">ETF</option>
                    </select>
                  )}
                  {item.registered_id ? (
                    <span className="discovery-saved">
                      Saved
                      {asset === "etf"
                        ? " · ETF"
                        : asset === "equity"
                          ? " · Stock"
                          : ""}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="secondary small"
                      disabled={busy || !item.available || !asset}
                      onClick={() => asset && void add(item.symbol, asset)}
                    >
                      <Plus size={13} /> Add symbol
                    </button>
                  )}
                </article>
              );
            })}
            {!result.items.length && (
              <p className="discovery-help">
                No symbols matched. Try a different symbol or name.
              </p>
            )}
          </div>
          <div className="discovery-pages">
            <button
              type="button"
              className="text-button"
              disabled={busy || result.offset === 0}
              onClick={() => void search(Math.max(0, result.offset - 50))}
            >
              <ChevronLeft size={14} /> Previous
            </button>
            <span>
              {result.items.length ? result.offset + 1 : 0}–
              {result.offset + result.items.length} of {result.total}
            </span>
            <button
              type="button"
              className="text-button"
              disabled={busy || !result.has_more}
              onClick={() => void search(result.offset + 50)}
            >
              Next <ChevronRight size={14} />
            </button>
          </div>
          <details className="discovery-coverage">
            <summary>Availability and research coverage</summary>
            {result.limitations.map((text) => (
              <p key={text}>{text}</p>
            ))}
          </details>
        </>
      )}
    </details>
  );
}
