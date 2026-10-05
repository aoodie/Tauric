import { useState, type FormEvent } from "react";
import { api, type PaperState } from "../api/client";
import { usePaper } from "./PaperTrading";

const date = (value: string) =>
  new Date(value).toLocaleString("en-GB", { timeZone: "Europe/London" });
const statuses: Record<string, string> = {
  waiting_research: "Waiting for native research",
  waiting_review: "Waiting for OpenClaw",
  waiting_exit: "Checking attributed exit",
  opened: "Paper position open",
  closed: "Paper position closed",
  blocked: "Blocked",
  cancelled: "Cancelled",
};

export function AutonomousPaper({
  deployment,
  open,
}: {
  deployment: PaperState["deployments"][number];
  open: (type: string, id: string) => void;
}) {
  const { state, pending, act } = usePaper();
  const policy = state?.autonomy.policies.find(
    (p) => p.deployment_id === deployment.id,
  );
  const settings = policy?.settings;
  const stock = !!deployment.stock_costs;
  const forex = deployment.fx_financing_daily_bps != null;
  const workflows =
    state?.autonomy.workflows
      .filter((w) => w.deployment_id === deployment.id)
      .slice(0, 10) ?? [];
  const [history, setHistory] = useState<Awaited<
    ReturnType<typeof api.autonomyHistory>
  > | null>(null);
  const [error, setError] = useState("");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const body = {
      expected_revision: policy?.revision ?? 0,
      enabled: f.get("enabled") === "on",
      auto_entries: f.get("auto_entries") === "on",
      auto_exits: f.get("auto_exits") === "on",
      profile:
        f.get("profile") === "local" ? ("local" as const) : ("cloud" as const),
      daily_research_limit: Number(f.get("daily_research_limit")),
      daily_review_limit: Number(f.get("daily_review_limit")),
      daily_entry_limit: Number(f.get("daily_entry_limit")),
      report_max_age_seconds: Number(f.get("report_max_age_seconds")),
      workflow_timeout_seconds: Number(f.get("workflow_timeout_seconds")),
      reason: String(f.get("autonomy_reason")),
    };
    if (
      await act(
        `autonomy-${deployment.id}-${JSON.stringify(body)}`,
        (id) => api.saveAutonomy(deployment.id, { ...body, request_id: id }),
        body.enabled
          ? "Autonomous paper settings saved."
          : "Autonomous paper execution stopped.",
      )
    )
      setHistory(null);
  }
  async function readHistory() {
    setError("");
    try {
      setHistory(await api.autonomyHistory(deployment.id));
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Could not load paper execution history.",
      );
    }
  }
  return (
    <article className="paper-card" aria-label="Autonomous paper execution">
      <span className="eyebrow">OPTIONAL · SIMULATED MONEY</span>
      <h2>Autonomous paper execution</h2>
      <p className="muted">
        {forex ? (
          "Forex reports have REVIEW status; use owner-reviewed paper entries. Scheduled attributed exits remain available."
        ) : stock ? (
          "Stock automatic entries await exact-version strategy assessment. Reviewed whole-share entries and automatic attributed exits are available separately."
        ) : (
          <>
            Saved monitoring signals can request native daily research, obtain a
            bound OpenClaw decision and execute a sized paper entry. Automatic
            exits use closed candles and the current observed paper price. Your
            account risk and shared capital limits still apply.
          </>
        )}
      </p>
      <p>
        <strong>
          {settings?.enabled
            ? "Autonomous paper enabled"
            : "Autonomous paper off"}
        </strong>{" "}
        ·{" "}
        {state?.autonomy.worker_ready
          ? "Paper execution service ready"
          : "Paper execution service unavailable"}
      </p>
      <p className="muted">
        Daily limits reset at midnight UTC and include earlier settings
        versions. Research uses your selected connection; fresh matching reports
        can be reused. Expired signals, changed controls and failed or
        interrupted model work block an attempt. Disabling monitoring or
        changing these settings revokes pending entries. Account halt blocks
        entries; enabled attributed exits can still close positions.
      </p>
      {policy && (
        <p>
          Today: {policy.usage_today.research} new research runs ·{" "}
          {policy.usage_today.reviews} reviews · {policy.usage_today.entries}{" "}
          paper entries
        </p>
      )}
      <form
        key={`${deployment.id}-${policy?.revision ?? 0}`}
        aria-label="Autonomous paper settings"
        onSubmit={(event) => void save(event)}
      >
        <fieldset disabled={pending}>
          <div className="paper-form-grid">
            <label className="schedule-toggle">
              <input
                name="enabled"
                type="checkbox"
                defaultChecked={settings?.enabled ?? false}
              />{" "}
              Enable autonomous paper execution
            </label>
            <label className="schedule-toggle">
              <input
                name="auto_entries"
                type="checkbox"
                defaultChecked={
                  !stock && !forex && (settings?.auto_entries ?? true)
                }
                disabled={stock || forex}
              />{" "}
              Automatic paper entries
            </label>
            <label className="schedule-toggle">
              <input
                name="auto_exits"
                type="checkbox"
                defaultChecked={settings?.auto_exits ?? true}
              />{" "}
              Automatic attributed exits
            </label>
            <label>
              Research and review connection
              <select
                name="profile"
                defaultValue={settings?.profile ?? "cloud"}
              >
                <option value="cloud">Cloud</option>
                <option value="local">Local</option>
              </select>
            </label>
            {(
              [
                ["daily_research_limit", "New research runs per day", 0, 10, 1],
                ["daily_review_limit", "OpenClaw reviews per day", 0, 50, 3],
                ["daily_entry_limit", "Paper entries per day", 1, 20, 1],
                [
                  "report_max_age_seconds",
                  "Maximum report age (seconds)",
                  300,
                  86400,
                  3600,
                ],
                [
                  "workflow_timeout_seconds",
                  "Attempt time budget (seconds)",
                  300,
                  1800,
                  900,
                ],
              ] as const
            ).map(([name, label, min, max, fallback]) => (
              <label key={name}>
                {label}
                <input
                  name={name}
                  type="number"
                  min={min}
                  max={max}
                  step={1}
                  required
                  defaultValue={settings?.[name] ?? fallback}
                />
              </label>
            ))}
            <label>
              Execution settings reason
              <input
                name="autonomy_reason"
                minLength={3}
                maxLength={500}
                required
              />
            </label>
          </div>
          <p className="muted">
            A scheduled monitor must be enabled first. Model work must finish
            before the original signal expires, even if the attempt budget is
            longer. An exit-only policy needs no new model requests.
          </p>
          <button className="secondary small" type="submit">
            Save autonomous paper settings
          </button>
        </fieldset>
      </form>
      <div className="capital-history" aria-label="Autonomous paper attempts">
        {!workflows.length && <p>No autonomous paper attempts yet.</p>}
        {workflows.map((w) => (
          <div key={w.id}>
            <strong>
              {statuses[w.status] ?? w.status} ·{" "}
              {w.kind === "entry" ? "Entry" : "Exit"} · {date(w.created_at)}{" "}
              London
            </strong>
            <span>{w.reason}</span>
            <div className="paper-actions">
              <button
                className="secondary small"
                type="button"
                onClick={() => open("strategy_run", w.initial_signal_id)}
              >
                Inspect scheduled signal
              </button>
              {w.analysis_job_id && (
                <button
                  className="secondary small"
                  type="button"
                  onClick={() => open("analysis", w.analysis_job_id!)}
                >
                  Inspect research run
                </button>
              )}
              {w.native_artifact_id && (
                <button
                  className="secondary small"
                  type="button"
                  onClick={() => open("report", w.native_artifact_id!)}
                >
                  Inspect original research
                </button>
              )}
              {w.review_job_id && (
                <button
                  className="secondary small"
                  type="button"
                  onClick={() => open("governance_job", w.review_job_id!)}
                >
                  Inspect bound review
                </button>
              )}
              {w.entry_fill_id && (
                <button
                  className="secondary small"
                  type="button"
                  onClick={() => open("trading_record", w.entry_fill_id!)}
                >
                  Inspect entry fill
                </button>
              )}
              {w.close_fill_id && (
                <button
                  className="secondary small"
                  type="button"
                  onClick={() => open("trading_record", w.close_fill_id!)}
                >
                  Inspect close fill
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      <button
        className="secondary small"
        type="button"
        disabled={pending}
        onClick={() => void readHistory()}
      >
        Load paper execution history
      </button>
      {error && <p role="alert">{error}</p>}
      {history && (
        <div className="capital-history" aria-label="Autonomous paper history">
          {history.policies.map((p) => (
            <div key={p.id}>
              <strong>
                Settings version {p.revision} · {date(p.created_at)} London
              </strong>
              <span>
                {p.settings.reason} · {p.settings.enabled ? "enabled" : "off"}
              </span>
            </div>
          ))}
          {history.events.map((e) => (
            <div key={e.id}>
              <strong>
                {e.kind.replaceAll("_", " ")} · {date(e.created_at)} London
              </strong>
              <span>{String(e.payload.reason ?? e.payload.status)}</span>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
