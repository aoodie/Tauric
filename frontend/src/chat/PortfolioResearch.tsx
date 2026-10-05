import { useEffect, useState, useRef, type FormEvent } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { researchApi, type Dataset, type Parameters } from "../research/api";
import { InstrumentDiscovery } from "../market/InstrumentDiscovery";

type Config = components["schemas"]["PortfolioResearchParameters"];
type State = components["schemas"]["PortfolioResearchState"];
type Scanner = components["schemas"]["ScannerProfileView"];
type Budget = components["schemas"]["SharedAdmissionBudgetView"];
const initial: Config = {
  enabled: false,
  daily_time: "08:00",
  timezone: "Europe/London",
  additional_cadence_hours: null,
  event_keys: [],
  asset_classes: ["crypto_spot"],
  universe: [],
  opportunities: true,
  strategies: true,
  scanner_profile_ids: [],
  experiments: [],
  max_experiments_per_run: 3,
  max_native_jobs_per_day: 0,
  shortlist_size: 3,
  native_wait_minutes: 90,
  model_profile: "local",
};
const replay: Parameters = {
  engine: "sma_cross_spot_v1",
  strategy_version_id: null,
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
const message = (e: unknown) =>
  e instanceof Error ? e.message : "Research unavailable.";
const stamp = (v: string | null) =>
  v
    ? new Date(v).toLocaleString("en-GB", { timeZone: "Europe/London" })
    : "Not scheduled";

export function PortfolioResearch({
  id,
  open,
}: {
  id: string;
  open: (type: string, id: string) => void;
}) {
  const [state, setState] = useState<State>();
  const [draft, setDraft] = useState<Config>();
  const [revision, setRevision] = useState(0);
  const [profiles, setProfiles] = useState<Scanner[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [versions, setVersions] = useState<
    components["schemas"]["StrategyVersionView"][]
  >([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Record<string, unknown>[]>();
  const live = useRef(true);
  async function load(reset = false) {
    try {
      const current = await api.portfolioResearch(id);
      if (!live.current) return;
      setState(current);
      if (reset) {
        setDraft(current.parameters ?? initial);
        setRevision(current.revision);
        setError("");
      }
    } catch (e) {
      if (live.current) setError(message(e));
    }
  }
  useEffect(() => {
    live.current = true;
    void load(true);
    void Promise.allSettled([
      api.scanners(),
      researchApi.state(),
      researchApi.strategies(),
    ]).then(([scan, data, strategy]) => {
      if (!live.current) return;
      if (scan.status === "fulfilled") setProfiles(scan.value.profiles);
      if (data.status === "fulfilled") setDatasets(data.value.datasets);
      if (strategy.status === "fulfilled") setVersions(strategy.value);
      if ([scan, data, strategy].some((v) => v.status === "rejected"))
        setError(
          "Some research choices could not be loaded. Reload before saving.",
        );
    });
    const timer = setInterval(() => void load(), 5000);
    return () => {
      live.current = false;
      clearInterval(timer);
    };
  }, [id]);
  function edit(p: Partial<Config>) {
    if (draft) setDraft({ ...draft, ...p });
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api.savePortfolioResearch(id, {
        request_id: crypto.randomUUID(),
        expected_revision: revision,
        parameters: draft,
        reason: "Owner updated portfolio research schedule",
      });
      if (live.current) {
        setState(saved);
        setDraft(saved.parameters ?? initial);
        setRevision(saved.revision);
        setHistory(undefined);
        setNotice(
          "Research settings saved. Scheduled runs follow this policy.",
        );
      }
    } catch (e) {
      if (live.current) setError(message(e));
    } finally {
      if (live.current) setBusy(false);
    }
  }
  async function request() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const run = await api.runPortfolioResearch(id);
      if (live.current) {
        setNotice(`Research queued · ${run.id}. Progress appears below.`);
        await load();
      }
    } catch (e) {
      if (live.current) setError(message(e));
    } finally {
      if (live.current) setBusy(false);
    }
  }
  function toggle<T>(list: T[], value: T) {
    return list.includes(value)
      ? list.filter((v) => v !== value)
      : [...list, value];
  }
  function experiment(
    index: number,
    p: Partial<NonNullable<Config["experiments"]>[number]>,
  ) {
    edit({
      experiments: (draft?.experiments ?? []).map((v, i) =>
        i === index ? { ...v, ...p } : v,
      ),
    });
  }
  function parameter(
    index: number,
    key: keyof Parameters,
    value: string | number,
  ) {
    const item = draft?.experiments?.[index];
    if (item)
      experiment(index, { parameters: { ...item.parameters, [key]: value } });
  }
  return (
    <details className="portfolio-settings portfolio-research">
      <summary>Daily opportunity and strategy research</summary>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {state && (
        <>
          <p className="muted">
            Coordinator {state.worker_ready ? "ready" : "unavailable"} ·
            revision {state.revision}. Next daily run:{" "}
            {stamp(state.next_daily_due)} (London display time).{" "}
            {state.next_cadence_due &&
              `Additional run: ${stamp(state.next_cadence_due)}.`}
          </p>
          <p>
            Research produces findings and original evidence. Qualification and
            required native Tauric checks remain mandatory before automated
            deployment.
          </p>
          <details>
            <summary>Asset readiness · FX and stocks next together</summary>
            <div className="research-capabilities">
              {state.asset_capabilities.map((c) => (
                <p key={String(c.asset_class)}>
                  <strong>{String(c.label)}</strong> · data{" "}
                  {c.data_ready ? "ready" : "pending"}, screening{" "}
                  {c.screening_ready ? "ready" : "pending"}, replay{" "}
                  {c.replay_ready ? "ready" : "pending"}, native research{" "}
                  {c.native_research_ready ? "daily context" : "pending"}, live
                  execution disabled. {String(c.detail)}
                </p>
              ))}
            </div>
          </details>
          <SharedBudget />
        </>
      )}
      {draft && (
        <form onSubmit={save}>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => edit({ enabled: e.target.checked })}
            />
            Automatic daily research
          </label>
          <div className="portfolio-research-grid">
            <label>
              Daily time
              <input
                type="time"
                required
                aria-label="Research daily time"
                value={draft.daily_time}
                onChange={(e) => edit({ daily_time: e.target.value })}
              />
            </label>
            <label>
              Timezone
              <input
                required
                aria-label="Research timezone"
                value={draft.timezone}
                onChange={(e) => edit({ timezone: e.target.value })}
              />
            </label>
            <label>
              Additional cadence · hours (optional)
              <input
                type="number"
                min={1}
                max={168}
                aria-label="Additional research cadence"
                value={draft.additional_cadence_hours ?? ""}
                onChange={(e) =>
                  edit({
                    additional_cadence_hours: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
              />
            </label>
          </div>
          <label>
            Allowed event names · comma separated
            <input
              aria-label="Research events"
              value={draft.event_keys?.join(", ") ?? ""}
              onChange={(e) =>
                edit({
                  event_keys: e.target.value
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
          <fieldset>
            <legend>Research tracks</legend>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.opportunities}
                onChange={(e) => edit({ opportunities: e.target.checked })}
              />
              Potential opportunities
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.strategies}
                onChange={(e) => edit({ strategies: e.target.checked })}
              />
              Strategy experiments
            </label>
          </fieldset>
          <fieldset>
            <legend>
              Asset families · unavailable research routes produce deferred
              findings
            </legend>
            {state?.asset_capabilities.map((c) => {
              const key = c.asset_class as NonNullable<
                Config["asset_classes"]
              >[number];
              return (
                <label className="checkbox" key={key}>
                  <input
                    type="checkbox"
                    checked={draft.asset_classes?.includes(key)}
                    onChange={() =>
                      edit({
                        asset_classes: toggle(draft.asset_classes ?? [], key),
                      })
                    }
                  />
                  {String(c.label)}
                  {!c.data_ready &&
                    (c.data_implemented
                      ? " · data connection needed"
                      : " · adapter pending")}
                </label>
              );
            })}
          </fieldset>
          <fieldset>
            <legend>Available research instruments</legend>
            <InstrumentDiscovery onAdded={() => load()} />
            {state?.asset_capabilities
              .filter((c) => c.data_ready)
              .flatMap((c) => c.instruments as string[])
              .map((key) => (
                <label className="checkbox" key={key}>
                  <input
                    type="checkbox"
                    checked={draft.universe?.includes(key)}
                    onChange={() =>
                      edit({ universe: toggle(draft.universe ?? [], key) })
                    }
                  />
                  {key.split(":").at(-1)}
                </label>
              ))}
          </fieldset>
          <fieldset>
            <legend>Opportunity scanner profiles</legend>
            {profiles.length ? (
              profiles.map((p) => (
                <label className="checkbox" key={p.id}>
                  <input
                    type="checkbox"
                    checked={draft.scanner_profile_ids?.includes(p.id)}
                    onChange={() =>
                      edit({
                        scanner_profile_ids: toggle(
                          draft.scanner_profile_ids ?? [],
                          p.id,
                        ),
                      })
                    }
                  />
                  {p.name} · revision {p.revision} ·{" "}
                  {p.parameters.universe.join(", ")}
                  {!p.parameters.enabled && " · paused"}
                </label>
              ))
            ) : (
              <p className="muted">
                Create a scanner profile in the Scanner workspace first. Its
                instruments must fit this universe.
              </p>
            )}
          </fieldset>
          <fieldset>
            <legend>Recorded strategy experiments</legend>
            <p className="muted">
              Use saved historical datasets and explicit cost and risk
              assumptions. Results remain unqualified research.
            </p>
            {(draft.experiments ?? []).map((item, index) => (
              <div className="portfolio-experiment" key={index}>
                <label>
                  Dataset
                  <select
                    aria-label={`Experiment ${index + 1} dataset`}
                    value={item.dataset_id}
                    onChange={(e) =>
                      experiment(index, { dataset_id: e.target.value })
                    }
                  >
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} · {d.instrument_id.split(":").at(-1)} ·{" "}
                        {d.interval}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Hypothesis
                  <textarea
                    minLength={3}
                    maxLength={2000}
                    required
                    aria-label={`Experiment ${index + 1} hypothesis`}
                    value={item.hypothesis}
                    onChange={(e) =>
                      experiment(index, { hypothesis: e.target.value })
                    }
                  />
                </label>
                <label>
                  Replay rules
                  <select
                    value={item.parameters.engine}
                    onChange={(e) =>
                      experiment(index, {
                        parameters: {
                          ...item.parameters,
                          engine: e.target.value as Parameters["engine"],
                          strategy_version_id:
                            e.target.value === "closed_bar_breakout_v1"
                              ? (versions.find((v) => v.parameters.rules)?.id ??
                                null)
                              : null,
                        },
                      })
                    }
                  >
                    <option value="sma_cross_spot_v1">
                      Moving average crossover
                    </option>
                    <option value="closed_bar_breakout_v1">
                      Saved breakout strategy
                    </option>
                  </select>
                </label>
                {item.parameters.engine === "closed_bar_breakout_v1" ? (
                  <label>
                    Exact saved strategy version
                    <select
                      required
                      value={item.parameters.strategy_version_id ?? ""}
                      onChange={(e) =>
                        parameter(index, "strategy_version_id", e.target.value)
                      }
                    >
                      <option value="">Choose strategy</option>
                      {versions
                        .filter((v) => v.parameters.rules)
                        .map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.name} · {v.revision}
                          </option>
                        ))}
                    </select>
                  </label>
                ) : (
                  <>
                    <div className="portfolio-research-grid">
                      {(["fast_window", "slow_window"] as const).map((key) => (
                        <label key={key}>
                          {key === "fast_window"
                            ? "Fast average bars"
                            : "Slow average bars"}
                          <input
                            required
                            type="number"
                            min={key === "fast_window" ? 1 : 2}
                            max={key === "fast_window" ? 200 : 500}
                            value={item.parameters[key]}
                            onChange={(e) =>
                              parameter(index, key, Number(e.target.value))
                            }
                          />
                        </label>
                      ))}
                    </div>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={item.explore_sma_neighbours}
                        onChange={(e) =>
                          experiment(index, {
                            explore_sma_neighbours: e.target.checked,
                          })
                        }
                      />
                      Compare neighbouring fast average windows
                    </label>
                  </>
                )}
                <div className="portfolio-research-grid">
                  {(
                    [
                      ["starting_balance", "Starting balance"],
                      ["allocation_percent", "Allocation · %"],
                      ["risk_percent", "Risk per trade · %"],
                      ["stop_percent", "Stop distance · %"],
                      ["quantity_step", "Quantity step"],
                      ["fee_bps", "Fee · basis points"],
                      ["slippage_bps", "Slippage · basis points"],
                    ] as const
                  ).map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <input
                        required
                        inputMode="decimal"
                        value={item.parameters[key]}
                        onChange={(e) => parameter(index, key, e.target.value)}
                      />
                    </label>
                  ))}
                </div>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    edit({
                      experiments: draft.experiments?.filter(
                        (_, i) => i !== index,
                      ),
                    })
                  }
                >
                  Remove experiment {index + 1}
                </button>
              </div>
            ))}
            <button
              type="button"
              className="secondary"
              disabled={
                !datasets.length || (draft.experiments?.length ?? 0) >= 5
              }
              onClick={() =>
                edit({
                  experiments: [
                    ...(draft.experiments ?? []),
                    {
                      dataset_id: datasets[0].id,
                      hypothesis: "",
                      parameters: { ...replay },
                      explore_sma_neighbours: false,
                    },
                  ],
                })
              }
            >
              Add experiment
            </button>
            {!datasets.length && (
              <p className="muted">
                Save a dataset in the Research workspace to add an experiment.
              </p>
            )}
          </fieldset>
          <div className="portfolio-research-grid">
            {(
              [
                ["max_experiments_per_run", "Experiment limit per run", 0, 15],
                ["shortlist_size", "Opportunity shortlist size", 0, 10],
                [
                  "max_native_jobs_per_day",
                  "New native research jobs per UTC day",
                  0,
                  20,
                ],
                ["native_wait_minutes", "Native result wait · minutes", 5, 360],
              ] as const
            ).map(([key, label, min, max]) => (
              <label key={key}>
                {label}
                <input
                  required
                  type="number"
                  min={min}
                  max={max}
                  value={draft[key]}
                  onChange={(e) => edit({ [key]: Number(e.target.value) })}
                />
              </label>
            ))}
          </div>
          <label>
            Native research AI connection
            <select
              value={draft.model_profile}
              onChange={(e) =>
                edit({
                  model_profile: e.target.value as Config["model_profile"],
                })
              }
            >
              <option value="local">Local</option>
              <option value="cloud">Cloud</option>
            </select>
          </label>
          <p className="muted">
            A zero native allowance permits reuse of compatible research and
            blocks new jobs. Configure the shared allowance before admitting new
            automatic AI work. Daily native context does not validate an
            intraday scalp setup.
          </p>
          <div className="portfolio-form-actions">
            <button disabled={busy} type="submit">
              Save research settings
            </button>
            <button
              disabled={busy}
              type="button"
              className="secondary"
              onClick={() => void load(true)}
            >
              Reload research settings
            </button>
            <button
              disabled={busy || !state?.version_id || !state.worker_ready}
              type="button"
              className="secondary"
              onClick={() => void request()}
            >
              Run portfolio research now
            </button>
          </div>
        </form>
      )}
      <button
        className="secondary"
        onClick={() =>
          void api
            .portfolioResearchHistory(id)
            .then(setHistory)
            .catch((e) => setError(message(e)))
        }
      >
        View research settings history
      </button>
      {history?.map((v) => (
        <details key={String(v.id)}>
          <summary>
            Research revision {String(v.revision)} ·{" "}
            {stamp(String(v.created_at))}
          </summary>
          <pre>{JSON.stringify(v.payload, null, 2)}</pre>
        </details>
      ))}
      <div className="portfolio-research-runs">
        {state?.runs.map((r) => (
          <details key={r.id}>
            <summary>
              Research {r.status.replaceAll("_", " ")} · {r.trigger} ·{" "}
              {stamp(r.created_at)}
            </summary>
            <p>
              Run {r.id} · {r.steps.length} recorded steps. No execution
              authority.
            </p>
            {r.steps.map((step, index) => (
              <div key={index}>
                <strong>
                  {String(step.track)} · {String(step.status)}
                </strong>
                {!!step.reason && <p>{String(step.reason)}</p>}
                {!!step.replay_id && (
                  <p>
                    Replay {String(step.replay_id)} ·{" "}
                    {step.reused ? "reused" : "new"} · net P&amp;L{" "}
                    {String((step.metrics as Record<string, unknown>)?.net_pnl)}{" "}
                    · unqualified{" "}
                    <button
                      type="button"
                      className="secondary"
                      onClick={() =>
                        open("research_run", String(step.replay_id))
                      }
                    >
                      Open replay evidence
                    </button>
                  </p>
                )}
                {!!step.shortlist && (
                  <p>
                    {(step.shortlist as Record<string, unknown>[]).length}{" "}
                    shortlisted opportunities; ordered by observed volume, not
                    predicted return.
                  </p>
                )}
              </div>
            ))}
            {r.native_jobs.map((j) => {
              const job = j.job as Record<string, unknown>;
              return (
                <p key={String(job.id)}>
                  Original native job {String(job.id)} · {String(job.status)} ·{" "}
                  {j.charged ? "new admission" : "reused"}{" "}
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => open("analysis", String(job.id))}
                  >
                    Open native run
                  </button>
                  {!!job.artifact_id && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => open("report", String(job.artifact_id))}
                    >
                      Open original report
                    </button>
                  )}
                </p>
              );
            })}
            {r.digest && (
              <p>
                Digest saved · qualification incomplete. Required exact version
                Tauric evidence remains pending.
              </p>
            )}
            <details>
              <summary>Saved research evidence</summary>
              <pre>{JSON.stringify(r.digest ?? r.steps, null, 2)}</pre>
            </details>
          </details>
        ))}
      </div>
    </details>
  );
}

function SharedBudget() {
  const [budget, setBudget] = useState<Budget>();
  const [limit, setLimit] = useState("");
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  async function load() {
    try {
      const v = await api.admissionBudget();
      if (active.current) {
        setBudget(v);
        setRevision(v.revision);
        setLimit(v.daily_job_limit == null ? "" : String(v.daily_job_limit));
        setError("");
      }
    } catch (e) {
      if (active.current) setError(message(e));
    }
  }
  useEffect(() => {
    active.current = true;
    void load();
    return () => {
      active.current = false;
    };
  }, []);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const v = await api.saveAdmissionBudget({
        request_id: crypto.randomUUID(),
        expected_revision: revision,
        daily_job_limit: Number(limit),
        reason: "Owner set shared daily AI job allowance",
      });
      if (active.current) {
        setBudget(v);
        setRevision(v.revision);
        setNotice("Shared allowance saved.");
      }
    } catch (e) {
      if (active.current) setError(message(e));
    } finally {
      if (active.current) setBusy(false);
    }
  }
  return (
    <details>
      <summary>Shared daily AI job allowance · all portfolios</summary>
      <p className="muted">
        Covers new native research and governance jobs across chat, scanners,
        portfolios and other routes. Each admission counts, including failures.
        Measured by UTC day; this is a job limit, not a currency or token cap.
        OpenClaw chat replies have their own settings.
      </p>
      <p>
        Saved allowance: {budget?.daily_job_limit ?? "Not configured"} · used
        today: {budget?.used_today ?? "—"}. Zero blocks all new covered jobs.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form onSubmit={save}>
        <label>
          Shared jobs per UTC day
          <input
            type="number"
            min={0}
            max={200}
            required
            aria-label="Shared jobs per UTC day"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
          />
        </label>
        <div className="portfolio-form-actions">
          <button type="submit" disabled={busy}>
            Save shared allowance
          </button>
          <button
            className="secondary"
            type="button"
            onClick={() => void load()}
            disabled={busy}
          >
            Reload shared allowance
          </button>
        </div>
      </form>
    </details>
  );
}
