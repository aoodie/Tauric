import { connectionLabel } from "../settings/aiLabels";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  client,
  unwrap,
  type Resource,
  type StrategyVersion,
} from "../api/client";
import type { components } from "../api/schema";
import { researchApi, type Dataset } from "./api";

type Protocol = components["schemas"]["QualificationView"];
type Portfolio = components["schemas"]["PortfolioView"];
type Job = components["schemas"]["AnalysisView"];
type Freeze = components["schemas"]["QualificationFreeze"];
const record = (value: unknown) => (value ?? {}) as Record<string, unknown>;
const date = (value: unknown) =>
  new Date(String(value)).toLocaleString("en-GB");
const message = (err: unknown) =>
  err instanceof Error ? err.message : "Qualification request failed.";
const fields = [
  ["training", "Training bars", "1"],
  ["embargo", "Embargo bars", "1"],
  ["holdout", "Minimum holdout bars", "1"],
  ["trades", "Minimum closed trades", "1"],
  ["return", "Minimum net return (%)", "any"],
  ["drawdown", "Maximum drawdown (%)", "any"],
  ["balance", "Experiment starting balance (dataset quote currency)", "any"],
  ["allocation", "Experiment allocation (%)", "any"],
  ["risk", "Experiment risk per trade (%)", "any"],
  ["fee", "Baseline fee (bps)", "any"],
  ["slip", "Baseline slippage (bps)", "any"],
  ["stressFee", "Stress fee (bps)", "any"],
  ["stressSlip", "Stress slippage (bps)", "any"],
  ["validity", "Assessment validity (hours)", "1"],
] as const;

export function Qualification({
  open,
}: {
  open: (resource: Resource["resource"]) => void;
}) {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [portfolioId, setPortfolioId] = useState("");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [protocols, setProtocols] = useState<Protocol[]>([]);
  const [selected, setSelected] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [strategyId, setStrategyId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [name, setName] = useState("Frozen breakout qualification");
  const [hypothesis, setHypothesis] = useState("");
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState<Freeze["intended_mode"]>(
    "research_comparison",
  );
  const [profile, setProfile] = useState<"cloud" | "local">("cloud");
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const epoch = useRef(0);
  const working = useRef(false);
  const ids = useRef(new Map<string, string>());
  useEffect(() => {
    let active = true;
    Promise.all([
      api.portfolios(),
      researchApi.state(),
      researchApi.strategies(),
    ])
      .then(([p, d, s]) => {
        if (!active) return;
        setPortfolios(p.filter((v) => v.preferences));
        setDatasets(
          d.datasets.filter((v) =>
            ["crypto_spot", "fx", "equity", "etf"].includes(v.asset_type),
          ),
        );
        setVersions(s.filter((v) => v.parameters.rules));
      })
      .catch((err) => active && setError(message(err)));
    return () => {
      active = false;
      epoch.current++;
    };
  }, []);
  async function reload(scope = portfolioId, currentEpoch = epoch.current) {
    if (!scope) return;
    try {
      const next = unwrap(
        await client.GET("/api/v1/qualification", {
          params: { query: { portfolio_id: scope } },
        }),
      );
      if (epoch.current !== currentEpoch) return;
      setProtocols(next.protocols);
      setReady(next.native_assessment_ready);
      setSelected((old) =>
        next.protocols.some((p) => p.id === old)
          ? old
          : (next.protocols[0]?.id ?? ""),
      );
    } catch (err) {
      if (epoch.current === currentEpoch) setError(message(err));
    }
  }
  useEffect(() => {
    epoch.current++;
    setProtocols([]);
    setSelected("");
    setError("");
    setNotice("");
    void reload(portfolioId, epoch.current);
  }, [portfolioId]);
  const protocol = protocols.find((p) => p.id === selected);
  const jobs = (protocol?.assessment_jobs ?? []).map(
    (a) => record(a.job) as unknown as Job,
  );
  const waiting = jobs.some(
    (j) => j.status === "queued" || j.status === "running",
  );
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => void reload(), 2500);
    return () => clearInterval(timer);
  }, [waiting, portfolioId]);
  async function perform(
    key: string,
    action: (requestId: string) => Promise<Protocol>,
    success: string,
  ) {
    if (working.current) return;
    working.current = true;
    setPending(true);
    setError("");
    setNotice("");
    const requestId = ids.current.get(key) ?? crypto.randomUUID();
    ids.current.set(key, requestId);
    try {
      const next = await action(requestId);
      ids.current.delete(key);
      setSelected(next.id);
      setProtocols((old) => [next, ...old.filter((p) => p.id !== next.id)]);
      setNotice(success);
    } catch (err) {
      setError(message(err));
    } finally {
      working.current = false;
      setPending(false);
    }
  }
  function freeze(event: FormEvent) {
    event.preventDefault();
    const version = versions.find((v) => v.id === strategyId);
    if (!version) return;
    const body: Omit<Freeze, "request_id"> = {
      portfolio_id: portfolioId,
      strategy_version_id: strategyId,
      dataset_id: datasetId,
      name,
      hypothesis,
      training_bars: Number(values.training),
      embargo_bars: Number(values.embargo),
      parameters: {
        fast_window: 5,
        slow_window: 20,
        stop_percent: "5",
        engine: "closed_bar_breakout_v1",
        strategy_version_id: strategyId,
        starting_balance: values.balance,
        allocation_percent: values.allocation,
        risk_percent: values.risk,
        quantity_step: version.parameters.quantity_step,
        fee_bps: values.fee,
        slippage_bps: values.slip,
        asset_costs:
          datasets.find((d) => d.id === datasetId)?.asset_type === "fx"
            ? {
                asset_type: "fx",
                commission_per_unit: "0",
                minimum_commission: "0",
                sell_tax_bps: "0",
                triple_rollover_weekday: 2,
                spread_bps: values.fxSpread ?? "0",
                financing_daily_bps: values.fxFinancing ?? "0",
              }
            : ["equity", "etf"].includes(
                  datasets.find((d) => d.id === datasetId)?.asset_type ?? "",
                )
              ? {
                  asset_type: datasets.find((d) => d.id === datasetId)!
                    .asset_type as "equity" | "etf",
                  spread_bps: values.stockSpread ?? "0",
                  commission_per_unit: values.stockCommission ?? "0",
                  minimum_commission: values.stockMinimum ?? "0",
                  sell_tax_bps: values.stockTax ?? "0",
                  financing_daily_bps: "0",
                  triple_rollover_weekday: 2,
                }
              : undefined,
      },
      thresholds: {
        minimum_holdout_bars: Number(values.holdout),
        minimum_closed_trades: Number(values.trades),
        minimum_net_return_percent: values.return,
        maximum_drawdown_percent: values.drawdown,
      },
      stresses: [
        {
          name: "Higher execution costs",
          fee_bps: values.stressFee,
          slippage_bps: values.stressSlip,
        },
      ],
      intended_mode: mode,
      assessment_validity_hours: Number(values.validity),
      reason,
    };
    void perform(
      "freeze:" + JSON.stringify(body),
      async (request_id) =>
        unwrap(
          await client.POST("/api/v1/qualification/protocols", {
            body: { ...body, request_id },
          }),
        ),
      "Protocol frozen. Evaluation has not run yet.",
    );
  }
  function act(action: "evaluate" | "assessment" | "reports") {
    if (!protocol) return;
    const path = { protocol_id: protocol.id };
    void perform(
      `${protocol.id}:${action}:${profile}`,
      async (request_id) => {
        const body = {
          request_id,
          reason:
            "Owner requested " + action + " for the frozen strategy protocol",
        };
        if (action === "evaluate")
          return unwrap(
            await client.POST(
              "/api/v1/qualification/protocols/{protocol_id}/evaluate",
              { params: { path }, body },
            ),
          );
        if (action === "assessment")
          return unwrap(
            await client.POST(
              "/api/v1/qualification/protocols/{protocol_id}/assessment",
              { params: { path }, body: { ...body, profile } },
            ),
          );
        return unwrap(
          await client.POST(
            "/api/v1/qualification/protocols/{protocol_id}/reports",
            { params: { path }, body },
          ),
        );
      },
      action === "assessment"
        ? "Assessment requested. Matching jobs are reused, including failures; no automatic paid retry."
        : action === "evaluate"
          ? "Separate training, holdout and cost-stress results recorded."
          : "A new report snapshot was recorded. Earlier reports remain available.",
    );
  }
  return (
    <div>
      <p className="research-context">
        Freeze an exact executable strategy and testing rules before evaluating
        held-out data. Qualification supports crypto, Forex, stock and ETF
        breakout research comparison, with explicit execution and financing
        assumptions. Paper promotion and live scalping need further execution
        evidence. A passing report grants no trading authority.
      </p>
      <section className="research-block">
        <h2>Strategy qualification</h2>
        <label>
          Qualification portfolio
          <select
            aria-label="Qualification portfolio"
            value={portfolioId}
            disabled={pending}
            onChange={(e) => setPortfolioId(e.target.value)}
          >
            <option value="">Choose a portfolio</option>
            {portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {!portfolios.length && (
          <p>
            Save a portfolio mandate before registering a qualification
            protocol.
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
      </section>
      {portfolioId && (
        <>
          <details className="research-block" open={!protocols.length}>
            <summary>Freeze a new protocol</summary>
            <form onSubmit={freeze}>
              <fieldset disabled={pending}>
                <label>
                  Protocol name
                  <input
                    required
                    minLength={3}
                    maxLength={120}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  Saved executable version
                  <select
                    aria-label="Saved executable version"
                    required
                    value={strategyId}
                    onChange={(e) => setStrategyId(e.target.value)}
                  >
                    <option value="">Choose exact version</option>
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name} · v{v.revision} ·{" "}
                        {v.parameters.rules?.interval}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Qualification dataset
                  <select
                    aria-label="Qualification dataset"
                    required
                    value={datasetId}
                    onChange={(e) => setDatasetId(e.target.value)}
                  >
                    <option value="">Choose saved dataset</option>
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} · {d.interval} · {d.bar_count} bars
                      </option>
                    ))}
                  </select>
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
                <div className="qualification-fields">
                  {fields.map(([key, label, step]) => (
                    <label key={key}>
                      {label}
                      <input
                        required
                        type="number"
                        step={step}
                        min={
                          key === "embargo" ||
                          [
                            "fee",
                            "slip",
                            "stressFee",
                            "stressSlip",
                            "return",
                          ].includes(key)
                            ? 0
                            : step === "1"
                              ? 1
                              : 0.000000000001
                        }
                        value={values[key] ?? ""}
                        onChange={(e) =>
                          setValues((old) => ({
                            ...old,
                            [key]: e.target.value,
                          }))
                        }
                      />
                    </label>
                  ))}
                </div>
                {datasets.find((d) => d.id === datasetId)?.asset_type ===
                  "fx" && (
                  <div className="paper-form-grid">
                    <label>
                      Synthetic spread (bps; zero for bid/ask data)
                      <input
                        required
                        value={values.fxSpread ?? "0"}
                        onChange={(e) =>
                          setValues((v) => ({ ...v, fxSpread: e.target.value }))
                        }
                      />
                    </label>
                    <label>
                      Daily financing assumption (bps)
                      <input
                        required
                        value={values.fxFinancing ?? "0"}
                        onChange={(e) =>
                          setValues((v) => ({
                            ...v,
                            fxFinancing: e.target.value,
                          }))
                        }
                      />
                    </label>
                    <p>
                      Forex qualification uses provider trading sessions and
                      tick activity. Costs are fixed assumptions, not historical
                      broker financing. Results grant no entry approval.
                    </p>
                  </div>
                )}
                {["equity", "etf"].includes(
                  datasets.find((d) => d.id === datasetId)?.asset_type ?? "",
                ) && (
                  <div className="paper-form-grid">
                    {[
                      ["stockSpread", "Synthetic spread (bps)"],
                      ["stockCommission", "Commission per share"],
                      ["stockMinimum", "Minimum commission"],
                      ["stockTax", "Sell-side fees / tax (bps)"],
                    ].map(([key, label]) => (
                      <label key={key}>
                        {label}
                        <input
                          required
                          inputMode="decimal"
                          value={values[key] ?? "0"}
                          onChange={(e) =>
                            setValues((v) => ({ ...v, [key]: e.target.value }))
                          }
                        />
                      </label>
                    ))}
                  </div>
                )}
                <label>
                  Intended mode
                  <select
                    aria-label="Intended mode"
                    value={mode}
                    onChange={(e) =>
                      setMode(e.target.value as Freeze["intended_mode"])
                    }
                  >
                    <option value="research_comparison">
                      Research comparison
                    </option>
                    <option value="paper_closed_bar">
                      Paper closed-bar · execution evidence pending
                    </option>
                    <option value="live_scalping">
                      Live scalping · execution evidence pending
                    </option>
                  </select>
                </label>
                <p>
                  Windows start flat and warm up independently. Stress costs
                  must increase at least one baseline cost and cannot reduce
                  either. Registered replays and earlier holdouts are checked
                  for reuse, including re-imported data. Prior knowledge outside
                  these records cannot be verified. No parameters are selected
                  or optimised on the holdout.
                </p>
                <label>
                  Registration reason
                  <input
                    required
                    minLength={3}
                    maxLength={500}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </label>
                <button className="primary" type="submit">
                  Freeze protocol
                </button>
              </fieldset>
            </form>
          </details>
          <section className="research-block">
            <h2>Recorded evidence</h2>
            <button
              className="secondary"
              disabled={pending}
              onClick={() => void reload()}
            >
              Refresh qualification
            </button>
            <label>
              Recorded protocol
              <select
                aria-label="Recorded protocol"
                value={selected}
                disabled={pending}
                onChange={(e) => {
                  setSelected(e.target.value);
                  setNotice("");
                }}
              >
                <option value="">Choose protocol</option>
                {protocols.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} · {date(p.created_at)}
                  </option>
                ))}
              </select>
            </label>
            {protocol && (
              <>
                <p>
                  Exact strategy {protocol.strategy_version_id} · frozen{" "}
                  {date(protocol.created_at)}. Report results are snapshots of
                  the stated evidence and assessment at their recorded time.
                </p>
                <p>
                  Frozen mandate:{" "}
                  {String(
                    record(protocol.frozen.portfolio_preferences).mandate,
                  )}
                </p>
                <button
                  className="secondary"
                  disabled={pending || !!protocol.evaluation}
                  onClick={() => act("evaluate")}
                >
                  Evaluate frozen protocol
                </button>
                {protocol.evaluation && (
                  <>
                    <div className="research-table-scroll">
                      <table className="research-table">
                        <caption>
                          Separate test windows and execution costs
                        </caption>
                        <thead>
                          <tr>
                            <th>Window</th>
                            <th>Fee / slippage · bps</th>
                            <th>Bars</th>
                            <th>Closed trades</th>
                            <th>Net return</th>
                            <th>Max drawdown</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[
                            [
                              "Training",
                              record(protocol.evaluation.training),
                              record(record(protocol.frozen.request).parameters)
                                .fee_bps,
                              record(record(protocol.frozen.request).parameters)
                                .slippage_bps,
                            ],
                            [
                              "Holdout",
                              record(protocol.evaluation.holdout),
                              record(record(protocol.frozen.request).parameters)
                                .fee_bps,
                              record(record(protocol.frozen.request).parameters)
                                .slippage_bps,
                            ],
                            ...(
                              (protocol.evaluation.stresses ?? []) as Record<
                                string,
                                unknown
                              >[]
                            ).map((s) => [
                              String(record(s.scenario).name),
                              record(s.result),
                              record(s.scenario).fee_bps,
                              record(s.scenario).slippage_bps,
                            ]),
                          ].map(([label, value, fee, slip]) => {
                            const r = record(value);
                            return (
                              <tr key={String(label)}>
                                <td>{String(label)}</td>
                                <td>
                                  {String(fee)} / {String(slip)}
                                </td>
                                <td>{String(r.bars)}</td>
                                <td>
                                  {String(record(r.metrics).closed_trades)}
                                </td>
                                <td>{String(r.return_percent)}%</td>
                                <td>{String(r.max_drawdown_percent)}%</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <p>
                      {protocol.evaluation.registered_holdout_unseen
                        ? "No prior registered replay or holdout overlap found."
                        : "Holdout reuse detected. This fails the registered unseen-data criterion."}{" "}
                      Prior owner knowledge remains unverifiable.
                    </p>
                    <label>
                      Assessment connection
                      <select
                        aria-label="Assessment connection"
                        value={profile}
                        disabled={pending || waiting}
                        onChange={(e) =>
                          setProfile(e.target.value as "cloud" | "local")
                        }
                      >
                        <option value="cloud">
                          {connectionLabel("cloud")}
                        </option>
                        <option value="local">
                          {connectionLabel("local")}
                        </option>
                      </select>
                    </label>
                    <p>
                      The pinned Tauric graph produces its original daily
                      reports, then its deep model separately reviews this
                      frozen strategy and evidence. This uses model calls and
                      the shared admission budget. A daily BUY cannot satisfy
                      strategy qualification.
                    </p>
                    <button
                      className="primary"
                      disabled={pending || waiting || !ready}
                      onClick={() => act("assessment")}
                    >
                      Request exact-version Tauric assessment
                    </button>
                    {!ready && (
                      <p>
                        Native worker is unavailable. Start it before requesting
                        an assessment.
                      </p>
                    )}
                    <button
                      className="secondary"
                      disabled={pending}
                      onClick={() => act("reports")}
                    >
                      Record qualification report
                    </button>
                  </>
                )}
                {jobs.map((j) => (
                  <p key={j.id}>
                    Tauric assessment: <strong>{j.status}</strong>
                    {j.error && ` · ${j.error}`}{" "}
                    {j.artifact_id && (
                      <button
                        className="text-button"
                        onClick={() =>
                          open({
                            type: "report",
                            id: j.artifact_id!,
                          })
                        }
                      >
                        Inspect original Tauric evidence
                      </button>
                    )}
                  </p>
                ))}
                {protocol.reports.map((r) => (
                  <section key={String(r.id)} className="qualification-report">
                    <h3>
                      Report {String(r.revision)} · {String(r.status)} ·
                      research scope
                    </h3>
                    <p>
                      Recorded {date(r.as_of)}. Trading and automatic promotion
                      authority: off.
                    </p>
                    <div className="research-table-scroll">
                      <table className="research-table">
                        <thead>
                          <tr>
                            <th>Criterion</th>
                            <th>Result</th>
                            <th>Evidence / requirement</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(r.criteria as Record<string, unknown>[]).map(
                            (c) => (
                              <tr key={String(c.key)}>
                                <td>{String(c.key).replaceAll("_", " ")}</td>
                                <td>{String(c.status)}</td>
                                <td>
                                  {String(c.detail)}
                                  {c.observed != null && (
                                    <>
                                      <br />
                                      Observed:{" "}
                                      {typeof c.observed === "object"
                                        ? JSON.stringify(c.observed)
                                        : String(c.observed)}
                                    </>
                                  )}
                                  {c.required != null && (
                                    <> · Required: {String(c.required)}</>
                                  )}
                                </td>
                              </tr>
                            ),
                          )}
                        </tbody>
                      </table>
                    </div>
                  </section>
                ))}
                <details>
                  <summary>
                    Frozen protocol, assumptions and full recorded results
                  </summary>
                  <pre className="qualification-json">
                    {JSON.stringify(protocol, null, 2)}
                  </pre>
                </details>
              </>
            )}
            {!protocols.length && (
              <p>No protocols registered for this portfolio.</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
