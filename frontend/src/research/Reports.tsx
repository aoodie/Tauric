import { connectionLabel } from "../settings/aiLabels";
import { useEffect, useState } from "react";
import { BookOpen, RefreshCw } from "lucide-react";
import {
  api,
  type Analysis,
  type NativeReport,
  type Resource,
} from "../api/client";
import { useMarket } from "../market/MarketContext";
import { NativeReportReader, record } from "./NativeReport";

export function SavedReports({
  open,
  refresh,
}: {
  open: (resource: Resource["resource"]) => void;
  refresh: number;
}) {
  const market = useMarket();
  const [jobs, setJobs] = useState<Analysis[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [report, setReport] = useState<NativeReport | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [reportError, setReportError] = useState("");
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [reload, setReload] = useState(0);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .analyses(0, 30)
      .then((next) => {
        if (!active) return;
        setJobs(next);
        setMore(next.length === 30);
        setError("");
        setSelectedId((id) =>
          next.some((job) => job.id === id)
            ? id
            : (next.find((job) => job.artifact_id)?.id ?? next[0]?.id ?? ""),
        );
      })
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refresh, reload]);
  const visible = jobs.filter((job) => !filter || job.instrument_id === filter);
  const selected = visible.find((job) => job.id === selectedId) ?? visible[0];
  useEffect(() => {
    let active = true;
    setReport(null);
    setReportError("");
    if (selected?.artifact_id) {
      api
        .nativeReport(selected.artifact_id)
        .then((next) => {
          if (active) setReport(next);
        })
        .catch((err) => {
          if (active) setReportError(err.message);
        });
    }
    return () => {
      active = false;
    };
  }, [selected?.artifact_id, retry]);
  // Read status only while a displayed job is awaiting its saved report.
  useEffect(() => {
    if (!selected || !["queued", "running"].includes(selected.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      api
        .analysis(selected.id)
        .then((next) => {
          if (active)
            setJobs((old) =>
              old.map((job) => (job.id === next.id ? next : job)),
            );
        })
        .catch((err) => {
          if (active) setReportError(err.message);
        });
    }, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selected?.id, selected?.status]);
  const symbol = (job: Analysis) =>
    market.instruments.find((i) => i.id === job.instrument_id)?.symbol ??
    String(record(job.request.instrument).symbol ?? job.instrument_id);
  const status = {
    succeeded: "Report ready",
    queued: "Waiting to start",
    running: "Research in progress",
    failed: "Research failed",
    interrupted: "Research interrupted",
  };
  async function older() {
    setLoading(true);
    try {
      const next = await api.analyses(jobs.length, 30);
      setJobs((old) => [...old, ...next]);
      setMore(next.length === 30);
      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to load older reports.",
      );
    } finally {
      setLoading(false);
    }
  }
  return (
    <section aria-label="Tauric report library">
      <p className="research-context">
        Saved Tauric research from chat, daily research and strategy
        assessments. Choose a run to read its original analysis.
      </p>
      <div className="report-history-tools">
        <label>
          Report instrument
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All instruments</option>
            {[...new Set(jobs.map((job) => job.instrument_id))].map((id) => (
              <option key={id} value={id}>
                {symbol(jobs.find((job) => job.instrument_id === id)!)}
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondary"
          disabled={loading}
          onClick={() => setReload((v) => v + 1)}
        >
          <RefreshCw size={14} /> Refresh reports
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && !jobs.length && <p role="status">Loading saved research…</p>}
      {!loading && !error && !visible.length && (
        <div className="research-block">
          <h2>
            {jobs.length
              ? "No matching reports"
              : "Your research reports will appear here"}
          </h2>
          <p>
            Ask OpenClaw to run research, or use Daily research in the
            workstation. Completed reports are saved here automatically.
          </p>
        </div>
      )}
      {!!visible.length && (
        <div className="report-history">
          <div className="report-history-list" aria-label="Saved research runs">
            <h2>Research history</h2>
            {visible.map((job) => (
              <button
                key={job.id}
                aria-pressed={selected?.id === job.id}
                onClick={() => setSelectedId(job.id)}
              >
                <strong>{symbol(job)}</strong>
                <small>
                  {job.request.mode === "strategy_qualification"
                    ? "Strategy assessment"
                    : "Daily research"}{" "}
                  ·{" "}
                  {String(
                    job.request.trade_date ?? job.created_at.slice(0, 10),
                  )}
                </small>
                <span>{status[job.status]}</span>
                <small>
                  Saved{" "}
                  {new Date(job.created_at).toLocaleString("en-GB", {
                    timeZone: "Europe/London",
                    dateStyle: "short",
                    timeStyle: "short",
                  })}{" "}
                  · {connectionLabel(job.profile)}
                </small>
              </button>
            ))}
          </div>
          <div className="report-history-view" aria-live="polite">
            {reportError && (
              <p className="error" role="alert">
                {reportError}{" "}
                <button
                  className="text-button"
                  onClick={() => setRetry((v) => v + 1)}
                >
                  Try again
                </button>
              </p>
            )}
            {report ? (
              <>
                <NativeReportReader key={report.id} report={report} />
                <button
                  className="text-button"
                  onClick={() => open({ type: "report", id: report.id })}
                >
                  <BookOpen size={14} /> Open report details
                </button>
              </>
            ) : (
              selected &&
              !reportError && (
                <>
                  <h2>
                    {symbol(selected)} · {status[selected.status]}
                  </h2>
                  <p>
                    {selected.artifact_id
                      ? "Loading report…"
                      : (selected.error ??
                        (selected.status === "succeeded"
                          ? "No saved report is available for this run."
                          : ["failed", "interrupted"].includes(selected.status)
                            ? "This run ended without a saved report."
                            : "The report will appear here when research completes."))}
                  </p>
                  {!!selected.progress.length && (
                    <details className="report-details">
                      <summary>Research progress</summary>
                      <ul>
                        {selected.progress.map((item, i) => (
                          <li key={i}>{item}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              )
            )}
          </div>
        </div>
      )}
      {more && (
        <button
          className="secondary"
          disabled={loading}
          onClick={() => void older()}
        >
          Load older research
        </button>
      )}
    </section>
  );
}
