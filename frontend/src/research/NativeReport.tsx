import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Download } from "lucide-react";
import type { NativeReport } from "../api/client";
import { ForexPolicyContext } from "../market/ForexPolicyContext";
import { ForexSupplementalContext } from "../market/ForexSupplementalContext";
import "./native-report.css";

export const REPORT_NAMES: Record<string, string> = {
  final_trade_decision: "Final decision",
  market_report: "Market analysis",
  sentiment_report: "Sentiment",
  news_report: "News",
  fundamentals_report: "Fundamentals",
  investment_plan: "Research plan",
  trader_investment_plan: "Trader proposal",
  research_debate: "Research debate",
  risk_debate: "Risk debate",
  macro_report: "Policy news & events",
  risk_report: "FX risk review",
};

export const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const text = (value: unknown) => (typeof value === "string" ? value : "");
const label = (key: string) => REPORT_NAMES[key] ?? key.replaceAll("_", " ");

export function ReportText({ children }: { children: string }) {
  return (
    <div className="report-prose">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          table: ({ children }) => (
            <div
              className="report-table-scroll"
              tabIndex={0}
              aria-label="Report table"
            >
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: ({ alt }) => <span>{alt}</span>,
          h1: ({ children }) => <h3>{children}</h3>,
          h2: ({ children }) => <h3>{children}</h3>,
          h3: ({ children }) => <h4>{children}</h4>,
        }}
      >
        {children}
      </Markdown>
    </div>
  );
}

export function NativeReportReader({
  report,
  timezone = "Europe/London",
}: {
  report: NativeReport;
  timezone?: string;
}) {
  const payload = report.payload;
  const request = record(payload.request);
  const instrument = record(request.instrument);
  const forex = instrument.asset_type === "fx";
  const brokerAssessment = record(payload.broker_setup_assessment);
  const hasBrokerAssessment = Object.keys(brokerAssessment).length > 0;
  const assessment = hasBrokerAssessment
    ? brokerAssessment
    : record(payload.strategy_assessment);
  const hasAssessment = Object.keys(assessment).length > 0;
  const reports = Object.entries(record(payload.reports))
    .filter(
      (entry): entry is [string, string] =>
        typeof entry[1] === "string" && !!entry[1].trim(),
    )
    .sort(([a], [b]) => {
      const keys = Object.keys(REPORT_NAMES);
      const rank = (key: string) =>
        keys.includes(key) ? keys.indexOf(key) : keys.length;
      return rank(a) - rank(b);
    });
  const [chosen, setChosen] = useState(
    hasAssessment ? "assessment" : "final_trade_decision",
  );
  const section =
    chosen === "assessment" && hasAssessment
      ? chosen
      : reports.some(([key]) => key === chosen)
        ? chosen
        : reports[0]?.[0];
  const symbol =
    text(instrument.symbol) ||
    text(instrument.native_symbol) ||
    text(request.instrument_id) ||
    "Tauric";
  const tradeDate = text(request.trade_date);
  const saved = new Date(report.created_at).toLocaleString("en-GB", {
    timeZone: timezone,
    dateStyle: "medium",
    timeStyle: "short",
  });
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `tauric-report-${report.id}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="report-reader" aria-label="Saved Tauric report">
      <header className="report-heading">
        <div>
          <span className="eyebrow">
            {hasBrokerAssessment
              ? "DEMO SETUP REVIEW"
              : hasAssessment
                ? "STRATEGY ASSESSMENT"
                : "DAILY RESEARCH"}
          </span>
          <h2>
            {symbol}
            {tradeDate && <span> · {tradeDate}</span>}
          </h2>
          <p>
            Saved {saved} · {timezone}
          </p>
        </div>
        <button className="secondary" onClick={download}>
          <Download size={14} /> Download original
        </button>
      </header>
      <div className="report-verdict">
        <span>
          {hasBrokerAssessment
            ? "Setup verdict"
            : hasAssessment
              ? "Strategy verdict"
              : forex
                ? "Research status"
                : "Tauric's research rating"}
        </span>
        <strong>
          {hasAssessment
            ? text(assessment.decision).replaceAll("_", " ") || "Not supplied"
            : text(payload.native_rating) || "Not supplied"}
        </strong>
        {hasAssessment && (
          <small>
            Daily background rating:{" "}
            {text(payload.native_rating) || "Not supplied"}
          </small>
        )}
        <small>Research evidence · no trading permission</small>
      </div>
      {forex && (
        <>
          <ForexPolicyContext
            value={record(payload.forex_evidence).macro_context}
          />
          <ForexSupplementalContext evidence={payload.forex_evidence} />
        </>
      )}
      <div className="report-layout">
        <nav className="report-sections" aria-label="Report sections">
          {hasAssessment && (
            <button
              aria-current={section === "assessment" ? "page" : undefined}
              onClick={() => setChosen("assessment")}
            >
              {hasBrokerAssessment
                ? "Demo setup review"
                : "Strategy assessment"}
            </button>
          )}
          {reports.map(([key]) => (
            <button
              key={key}
              aria-current={section === key ? "page" : undefined}
              onClick={() => setChosen(key)}
            >
              {label(key)}
            </button>
          ))}
        </nav>
        <article className="report-content" key={section}>
          <h2>
            {section === "assessment"
              ? hasBrokerAssessment
                ? "Demo setup review"
                : "Strategy assessment"
              : section
                ? label(section)
                : "Report unavailable"}
          </h2>
          {section === "assessment" ? (
            <>
              <ReportText>
                {text(assessment.rationale) || "No rationale was supplied."}
              </ReportText>
              {Array.isArray(assessment.limitations) &&
                assessment.limitations.length > 0 && (
                  <>
                    <h3>Limitations</h3>
                    <ul>
                      {assessment.limitations
                        .filter((v): v is string => typeof v === "string")
                        .map((v, i) => (
                          <li key={i}>{v}</li>
                        ))}
                    </ul>
                  </>
                )}
              <dl className="report-facts">
                <div>
                  <dt>Strategy version</dt>
                  <dd>
                    {text(assessment.strategy_version_id) || "Not supplied"}
                  </dd>
                </div>
                <div>
                  <dt>Intended use</dt>
                  <dd>
                    {hasBrokerAssessment
                      ? "Exact demo entry review"
                      : text(assessment.intended_mode).replaceAll("_", " ") ||
                        "Not supplied"}
                  </dd>
                </div>
                {hasBrokerAssessment && (
                  <div>
                    <dt>Requested entry</dt>
                    <dd>
                      {text(assessment.provider)} · {text(assessment.side)}{" "}
                      {text(assessment.quantity)} ·{" "}
                      {text(assessment.instrument_id)}
                    </dd>
                  </div>
                )}
              </dl>
            </>
          ) : (
            <ReportText>
              {reports.find(([key]) => key === section)?.[1] ??
                "No report sections were supplied for this run."}
            </ReportText>
          )}
        </article>
      </div>
      {text(payload.data_context) && (
        <details className="report-details">
          <summary>Data scope and limitations</summary>
          <p>{text(payload.data_context)}</p>
        </details>
      )}
      <details className="report-details">
        <summary>Technical details & original data</summary>
        <dl className="report-facts">
          <div>
            <dt>Engine</dt>
            <dd>
              {text(payload.runtime)} {text(payload.version)}
            </dd>
          </div>
          <div>
            <dt>Report ID</dt>
            <dd>{report.id}</dd>
          </div>
          <div>
            <dt>Integrity checksum</dt>
            <dd>{report.checksum}</dd>
          </div>
        </dl>
        <pre>{JSON.stringify(report, null, 2)}</pre>
      </details>
    </section>
  );
}
