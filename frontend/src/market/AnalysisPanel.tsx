import { connectionLabel } from "../settings/aiLabels";
import { useEffect, useState } from "react";
import { ArrowRight, BookOpen, RefreshCw, Sparkles } from "lucide-react";
import { api, type NativeReport } from "../api/client";
import { useMarket } from "./MarketContext";
import { ForexPolicyContext } from "./ForexPolicyContext";
import { ForexSupplementalContext } from "./ForexSupplementalContext";
import { ReportText } from "../research/NativeReport";
import { OpenResearch } from "./OpenResearch";

const REPORT_NAMES: Record<string, string> = {
  market_report: "Market",
  sentiment_report: "Sentiment",
  news_report: "News",
  fundamentals_report: "Fundamentals",
  investment_plan: "Research plan",
  trader_investment_plan: "Trader proposal",
  final_trade_decision: "Portfolio decision",
  research_debate: "Research debate",
  risk_debate: "Risk debate",
  risk_report: "FX risk review",
  macro_report: "Policy news & events",
};

export function AnalysisPanel({
  open,
  setup,
  ask,
}: {
  open: (type: string, id: string) => void;
  setup: () => void;
  ask: (draft: string) => void;
}) {
  const market = useMarket();
  const [profile, setProfile] = useState<"cloud" | "local">("cloud");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [report, setReport] = useState<NativeReport | null>(null);
  const [section, setSection] = useState("final_trade_decision");
  const runs = market.jobs.filter(
    (job) => job.instrument_id === market.instrumentId,
  );
  const job = runs.find((run) => run.id === jobId) ?? runs[0];
  const connection = market.status?.profiles.find(
    (item) => item.id === profile,
  );
  useEffect(() => {
    setJobId(null);
    setReport(null);
    setError("");
  }, [market.instrumentId]);
  useEffect(() => {
    let active = true;
    setReport(null);
    if (job?.artifact_id)
      api
        .nativeReport(job.artifact_id)
        .then((result) => {
          if (active) setReport(result);
        })
        .catch((err) => {
          if (active) setError(err.message);
        });
    return () => {
      active = false;
    };
  }, [job?.artifact_id]);
  async function run() {
    setError("");
    setPending(true);
    try {
      const next = await api.requestAnalysis(
        market.instrumentId,
        profile,
        market.feed?.snapshot_id ?? undefined,
      );
      setJobId(next.id);
      await market.reloadJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis request failed.");
    } finally {
      setPending(false);
    }
  }
  const reports = (report?.payload.reports ?? {}) as Record<string, string>;
  const instrument = market.instruments.find(
    (i) => i.id === market.instrumentId,
  );
  const forex = instrument?.asset_type === "fx";
  const dataReady =
    !forex ||
    market.providers.some((p) => p.id === instrument?.provider && p.configured);
  return (
    <section className="native-panel">
      <div className="panel-kicker">MARKET RESEARCH</div>
      <OpenResearch
        symbol={instrument?.symbol ?? ""}
        interval={market.interval}
        ask={ask}
      />
      {(instrument?.native_research_ready || runs.length > 0) && (
        <details
          className="daily-research"
          open={instrument?.native_research_ready}
        >
          <summary>Built-in daily report</summary>
          <div className="panel-kicker">
            {forex ? "FOREX DAILY RESEARCH" : "NATIVE TAURIC GRAPH"}
            <span
              className={`badge ${market.status?.worker_ready ? "cyan" : "neutral"}`}
            >
              {market.status?.worker_ready ? "WORKER READY" : "WORKER OFFLINE"}
            </span>
          </div>
          <div className="native-heading">
            <Sparkles size={22} />
            <div>
              <h3>Daily research</h3>
              <p>
                {forex
                  ? "OANDA daily evidence, policy context and FX risk review."
                  : "Original analyst reports and portfolio decision."}
              </p>
            </div>
          </div>
          <div className="native-scope">
            <strong>
              {market.instruments.find((i) => i.id === market.instrumentId)
                ?.symbol ?? "Choose an instrument"}
            </strong>
            <p>{instrument?.analysis_scope ?? "Choose an instrument."}</p>
            <p>
              For other timeframes and markets, use OpenClaw research above.
            </p>
          </div>
          <label>
            AI connection
            <select
              aria-label="Analysis AI connection"
              value={profile}
              onChange={(e) => setProfile(e.target.value as "cloud" | "local")}
            >
              <option value="cloud">{connectionLabel("cloud")}</option>
              <option value="local">{connectionLabel("local")}</option>
            </select>
          </label>
          <p className="native-connection">
            {connection?.detail ?? "Checking model connections…"}
          </p>
          <button
            className="primary"
            disabled={
              pending ||
              !connection?.configured ||
              !market.status?.worker_ready ||
              !instrument?.native_research_ready ||
              !dataReady ||
              !market.instrumentId
            }
            onClick={() => void run()}
          >
            <Sparkles size={14} />
            {pending ? "Requesting…" : "Run daily analysis"}
          </button>
          {!dataReady && (
            <p className="native-connection">
              Save your OANDA connection in Settings → Market data before
              running Forex research.
            </p>
          )}
          {!connection?.configured && (
            <button className="text-button" onClick={setup}>
              View model setup
              <ArrowRight size={13} />
            </button>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="native-runs-head">
            <span className="eyebrow">SAVED RUNS</span>
            <button
              className="icon-button"
              aria-label="Refresh analysis runs"
              onClick={() =>
                void market.reloadJobs().catch((err) => setError(err.message))
              }
            >
              <RefreshCw size={13} />
            </button>
          </div>
          {!runs.length && (
            <p className="native-empty">
              No analysis has run for this instrument.
            </p>
          )}
          <div className="native-run-list">
            {runs.map((run) => (
              <button
                className={job?.id === run.id ? "selected" : ""}
                key={run.id}
                onClick={() => setJobId(run.id)}
              >
                <span>{new Date(run.created_at).toLocaleString("en-GB")}</span>
                <span className="badge neutral">{run.status}</span>
              </button>
            ))}
          </div>
          {job && (
            <div className="native-job">
              <button
                className="text-button"
                onClick={() => open("analysis", job.id)}
              >
                Run details
                <ArrowRight size={12} />
              </button>
              {job.error && (
                <p className="error" role="alert">
                  {job.error}
                </p>
              )}
              {job.status === "running" && (
                <p>
                  {job.progress.length
                    ? `Completed: ${job.progress.map((name) => REPORT_NAMES[name] ?? name).join(", ")}`
                    : forex
                      ? "Forex research is working…"
                      : "Native graph is working…"}
                </p>
              )}
              {job.status === "queued" && (
                <p>
                  Waiting for the native worker. One run is processed at a time.
                </p>
              )}
            </div>
          )}
          {report && (
            <div className="native-report">
              <div className="native-rating">
                <span>{forex ? "Research status" : "Native rating"}</span>
                <strong>{String(report.payload.native_rating)}</strong>
              </div>
              {forex && (
                <>
                  <ForexPolicyContext
                    value={
                      (
                        report.payload.forex_evidence as
                          { macro_context?: unknown } | undefined
                      )?.macro_context
                    }
                  />
                  <ForexSupplementalContext
                    evidence={report.payload.forex_evidence}
                  />
                </>
              )}
              <label>
                Report
                <select
                  aria-label="Native report section"
                  value={section}
                  onChange={(e) => setSection(e.target.value)}
                >
                  {Object.keys(reports).map((key) => (
                    <option key={key} value={key}>
                      {forex && key === "final_trade_decision"
                        ? "Daily research review"
                        : (REPORT_NAMES[key] ?? key)}
                    </option>
                  ))}
                </select>
              </label>
              <ReportText>
                {reports[section] ??
                  "This section was not supplied by the native graph."}
              </ReportText>
              <button
                className="text-button"
                onClick={() => open("report", report.id)}
              >
                <BookOpen size={13} />
                {forex
                  ? "Saved report and provenance"
                  : "Original artifact and provenance"}
              </button>
            </div>
          )}
          <div className="native-limit">
            Research only. No governance permit, sizing or order submission is
            created.
          </div>
        </details>
      )}
    </section>
  );
}
