import { useState } from "react";
import {
  ArrowUpRight,
  Radar,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import { useMarket } from "../market/MarketContext";
import { OpenClawActivity } from "../chat/ActivityFeed";
import { CandidateDetail, useScanner } from "./Scanner";
import { assetLabel, timeframeLabels } from "../ui/workflowLabels";
import type { ScannerCandidate } from "../api/client";
import "./trader.css";

const labels: Record<string, string> = {
  pattern: "Pattern distance / ATR",
  trend: "Higher trends",
  activity: "Activity",
  squeeze: "Squeeze",
  geometry: "Structural R:R",
  macro: "Calendar",
};

function Opportunity({
  candidate: c,
  open,
  chart,
  timezone,
}: {
  candidate: ScannerCandidate;
  open: (kind: string, id: string) => void;
  chart: () => void;
  timezone: string;
}) {
  const market = useMarket();
  const spec = market.instruments.find((i) => i.id === c.instrument_id);
  const a = c.assessment;
  const score = Number(a?.score ?? c.setup_score);
  const band = a?.rating.toLowerCase() ?? "legacy";
  return (
    <article className={`opportunity-card band-${band}`}>
      <header className="opportunity-heading">
        <div>
          <span className="eyebrow">
            {spec?.classification_source === "unclassified"
              ? "US listing"
              : assetLabel(c.asset_class)}{" "}
            · {timeframeLabels[c.interval]} · {c.direction.toUpperCase()}
          </span>
          <h2>{spec?.symbol ?? c.instrument_id.split(":").at(-1)}</h2>
          <p>{spec?.name ?? c.instrument_id}</p>
        </div>
        <div
          className="setup-score"
          aria-label={`Setup score ${Math.floor(score * 100)} out of 100`}
        >
          <span className="eyebrow">SETUP SCORE</span>
          <strong>
            {Math.floor(score * 100)}
            <small>/100</small>
          </strong>
          <span>
            {a
              ? `${a.rating === "HIGH" ? "High" : a.rating === "MODERATE" ? "Moderate" : "Low"} score`
              : "Legacy rules"}
          </span>
        </div>
      </header>
      <div
        className="score-track"
        role="meter"
        aria-label="Setup rule score"
        aria-valuenow={Math.floor(score * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span style={{ width: `${score * 100}%` }} />
      </div>
      {a ? (
        <>
          <div className="factor-grid">
            {Object.entries(labels).map(([key, label]) => (
              <div
                className={a.factors[key] === "unknown" ? "factor-unknown" : ""}
                key={key}
              >
                <span>{label}</span>
                <strong>{a.factors[key] ?? "Unknown"}</strong>
                <small>
                  {key === "macro"
                    ? `−${Number(a.macro_penalty) * 100} points`
                    : `${(Number(a.contributions[key] ?? 0) * 100).toFixed(1)} points`}
                </small>
              </div>
            ))}
          </div>
          {a.missing_evidence.length > 0 && (
            <p className="opportunity-missing">
              Incomplete evidence:{" "}
              {a.missing_evidence.map((v) => v.replaceAll("_", " ")).join(", ")}
              . High score is unavailable.
            </p>
          )}
          <dl className="opportunity-geometry">
            {[
              ["Entry reference", "suggested_entry"],
              ["Invalidation", "suggested_stop"],
              ["Illustrative 2R", "target_2r"],
              ["Illustrative 3R", "target_3r"],
            ].map(([label, key]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{a.geometry[key] ?? "Unavailable"}</dd>
              </div>
            ))}
          </dl>
          <p className="opportunity-provenance">
            Closed bar{" "}
            {new Date(a.bar_close_time).toLocaleString("en-GB", {
              timeZone: timezone,
            })}{" "}
            · {a.version} · geometry for research
          </p>
        </>
      ) : (
        <p className="opportunity-provenance">
          Saved legacy evidence · multifactor assessment was not recorded
        </p>
      )}
      <div className="opportunity-actions">
        <button
          className="secondary small"
          onClick={() => {
            market.setInstrumentId(c.instrument_id);
            market.setIntervalValue(c.interval);
            chart();
          }}
        >
          View chart
          <ArrowUpRight size={13} />
        </button>
        <span className="badge">{c.status}</span>
      </div>
      <CandidateDetail c={c} open={open} chart={chart} />
    </article>
  );
}

export function TraderDashboard({
  open,
  chart,
  scanners,
  timezone,
}: {
  open: (kind: string, id: string) => void;
  chart: () => void;
  scanners: () => void;
  timezone: string;
}) {
  const s = useScanner();
  const [asset, setAsset] = useState("all"),
    [interval, setInterval] = useState("all"),
    [band, setBand] = useState("all");
  const [query, setQuery] = useState("");
  const live = (s.state?.candidates ?? []).filter((c) =>
    ["candidate", "watch"].includes(c.status),
  );
  const candidates = live
    .filter(
      (c) =>
        (asset === "all" || c.asset_class === asset) &&
        (interval === "all" || c.interval === interval) &&
        (band === "all" || c.assessment?.rating === band) &&
        c.instrument_id.toLowerCase().includes(query.toLowerCase()),
    )
    .sort(
      (a, b) =>
        Number(b.assessment?.score ?? b.setup_score) -
          Number(a.assessment?.score ?? a.setup_score) ||
        b.detected_at.localeCompare(a.detected_at),
    );
  const high = live.filter((c) => c.assessment?.rating === "HIGH").length;
  const latest = s.state?.runs[0];
  const catalogRuns = (s.state?.runs ?? [])
    .filter((r) => r.metrics?.catalog_provider)
    .filter(
      (r, i, all) =>
        all.findIndex((v) => v.profile_version_id === r.profile_version_id) ===
        i,
    );
  return (
    <div className="page-scroll trader-dashboard">
      <header className="trader-heading">
        <div>
          <span className="eyebrow">MARKET SETUPS / PROJECT ACTIVITY</span>
          <h1>Trader dashboard</h1>
          <p>Review observable setups and follow the work behind them.</p>
        </div>
        <div className="paper-actions">
          <button
            className="secondary small"
            disabled={s.pending}
            onClick={() => void s.reload()}
          >
            <RefreshCw size={14} />
            Refresh
          </button>
          <button className="secondary small" onClick={scanners}>
            <SlidersHorizontal size={14} />
            Configure scans
          </button>
        </div>
      </header>
      <div className="trader-summary">
        <div>
          <span>Active setups</span>
          <strong>{live.length}</strong>
        </div>
        <div>
          <span>High setup score</span>
          <strong>{high}</strong>
        </div>
        <div>
          <span>Saved scanner profiles</span>
          <strong>{s.state?.profiles.length ?? "—"}</strong>
        </div>
        <div>
          <span>Latest completed scan</span>
          <strong className="summary-time">
            {latest
              ? new Date(latest.created_at).toLocaleTimeString("en-GB", {
                  timeZone: timezone,
                })
              : "Awaiting a scan"}
          </strong>
          <small>Deterministic detection · zero model calls</small>
        </div>
      </div>
      {(s.error || s.loadError) && (
        <p className="form-error" role="alert">
          {s.error || s.loadError}
        </p>
      )}
      {s.notice && (
        <p className="form-success" role="status">
          {s.notice}
        </p>
      )}
      {catalogRuns.length > 0 && (
        <div className="catalog-coverage">
          {catalogRuns.map((r) => (
            <p key={r.id}>
              <strong>
                {String(r.metrics?.catalog_provider).toUpperCase()} catalog
              </strong>{" "}
              · {String(r.metrics?.processed_universe_count)} checked this cycle
              · {String(r.metrics?.catalog_covered_count ?? 0)} verified in this
              pass · {String(r.metrics?.catalog_failed_count ?? 0)} unavailable
              · {String(r.metrics?.deferred_count)} deferred ·{" "}
              {String(r.metrics?.request_budget_remaining ?? "unknown")} shared
              request tokens at scan close
              {r.metrics?.catalog_pass_complete
                ? " · catalog pass reached its end"
                : " · coverage in progress"}
            </p>
          ))}
        </div>
      )}
      <div className="trader-columns">
        <section className="opportunity-radar" aria-label="Opportunity radar">
          <div className="radar-heading">
            <div>
              <span className="eyebrow">OPPORTUNITY RADAR</span>
              <h2>
                <Radar size={18} />
                Setups to investigate
              </h2>
            </div>
            <span className="badge">{candidates.length} visible</span>
          </div>
          <p className="radar-explanation">
            The score measures setup rules. It does not estimate win probability
            or grant trading permission.
          </p>
          <div className="radar-filters">
            <label>
              Score
              <select
                aria-label="Setup score filter"
                value={band}
                onChange={(e) => setBand(e.target.value)}
              >
                <option value="all">All scores</option>
                <option value="HIGH">High · at least 82</option>
                <option value="MODERATE">Moderate · 65 to 81</option>
                <option value="LOW">Low · below 65</option>
              </select>
            </label>
            <label>
              Market
              <select
                aria-label="Setup market filter"
                value={asset}
                onChange={(e) => setAsset(e.target.value)}
              >
                <option value="all">All markets</option>
                {["fx", "equity", "etf", "crypto_spot"].map((v) => (
                  <option key={v} value={v}>
                    {assetLabel(v)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Timeframe
              <select
                aria-label="Setup timeframe filter"
                value={interval}
                onChange={(e) => setInterval(e.target.value)}
              >
                <option value="all">All timeframes</option>
                {Object.entries(timeframeLabels).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Symbol
              <input
                aria-label="Find setup symbol"
                placeholder="EUR, SPY, BTC…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          </div>
          <div className="opportunity-list">
            {candidates.map((c) => (
              <Opportunity
                key={c.id}
                candidate={c}
                open={open}
                chart={chart}
                timezone={timezone}
              />
            ))}
            {!candidates.length && (
              <div className="radar-empty">
                <Radar size={30} />
                <h3>
                  {live.length
                    ? "No setups match these filters"
                    : "Waiting for observable setups"}
                </h3>
                <p>
                  {live.length
                    ? "Change the market, timeframe or score filter."
                    : "Configure a scanner, then run it or enable its schedule. Scores appear only when the required source evidence is recorded."}
                </p>
                <button className="secondary small" onClick={scanners}>
                  Open scanner settings
                </button>
              </div>
            )}
          </div>
        </section>
        <OpenClawActivity timezone={timezone} open={open} />
      </div>
    </div>
  );
}
