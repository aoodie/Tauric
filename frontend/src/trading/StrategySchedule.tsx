import { useState, type FormEvent } from "react";
import { api, type PaperState, type StrategyParameters } from "../api/client";
import { usePaper } from "./PaperTrading";

const london = (value: string) =>
  new Date(value).toLocaleString("en-GB", { timeZone: "Europe/London" });

export function StrategySchedule({
  deployment,
  rules,
}: {
  deployment: PaperState["deployments"][number];
  rules: NonNullable<StrategyParameters["rules"]>;
}) {
  const { state, pending, act } = usePaper();
  const schedule = state?.strategy_schedules.find(
    (s) => s.deployment_id === deployment.id,
  );
  const [history, setHistory] = useState<Awaited<
    ReturnType<typeof api.strategyScheduleHistory>
  > | null>(null);
  const [historyError, setHistoryError] = useState("");
  const intervalSeconds = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
  }[rules.interval];
  const maximum = Math.min(3600, intervalSeconds, rules.signal_ttl_seconds);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const f = new FormData(event.currentTarget);
    const enabled = f.get("enabled") === "on";
    const cadence = Number(f.get("cadence"));
    const reason = String(f.get("schedule_reason"));
    if (
      await act(
        `schedule-${deployment.id}-${schedule?.revision ?? 0}-${enabled}-${cadence}-${reason}`,
        (id) =>
          api.saveStrategySchedule(deployment.id, {
            request_id: id,
            expected_revision: schedule?.revision ?? 0,
            enabled,
            cadence_seconds: cadence,
            reason,
          }),
        enabled
          ? "Paper monitoring schedule saved."
          : "Paper monitoring stopped.",
      )
    ) {
      setHistory(null);
    }
  }
  async function readHistory() {
    setHistoryError("");
    try {
      setHistory(await api.strategyScheduleHistory(deployment.id));
    } catch (error) {
      setHistoryError(
        error instanceof Error
          ? error.message
          : "Could not load monitoring history.",
      );
    }
  }
  return (
    <article
      className="paper-card strategy-schedule"
      aria-label="Paper strategy monitoring"
    >
      <span className="eyebrow">BACKGROUND CHECKS</span>
      <h2>
        Monitor {deployment.strategy} · {deployment.strategy_version}
      </h2>
      <p className="muted">
        Check closed candles for this route while the browser is closed. Entry
        and exit signals appear in the rule results. Enable autonomous paper
        execution below to act on them in the background. Checks after downtime
        use current evidence.
      </p>
      <p>
        <strong>
          {schedule?.enabled ? "Monitoring enabled" : "Monitoring off"}
        </strong>{" "}
        ·{" "}
        {state?.strategy_scheduler.ready
          ? "Monitor service ready"
          : "Monitor service unavailable — saved checks will wait"}
      </p>
      {schedule?.next_due_at && (
        <p className="muted">
          Next check {london(schedule.next_due_at)} London
        </p>
      )}
      {schedule?.last_checked_at && (
        <p>
          Last check {london(schedule.last_checked_at)} London ·{" "}
          {schedule.last_action?.toUpperCase()}
        </p>
      )}
      {schedule?.last_error && <p role="alert">{schedule.last_error}</p>}
      <form
        key={`${deployment.id}-${schedule?.revision ?? 0}`}
        aria-label="Paper monitoring schedule"
        onSubmit={(event) => void save(event)}
      >
        <fieldset disabled={pending}>
          <div className="paper-form-grid">
            <label className="schedule-toggle">
              <input
                name="enabled"
                type="checkbox"
                defaultChecked={schedule?.enabled ?? false}
              />{" "}
              Enable scheduled monitoring
            </label>
            <label>
              Check every (seconds)
              <input
                name="cadence"
                type="number"
                min={30}
                max={maximum}
                required
                defaultValue={schedule?.cadence_seconds ?? 60}
              />
            </label>
            <label>
              Monitoring change reason
              <input
                name="schedule_reason"
                minLength={3}
                maxLength={500}
                required
              />
            </label>
          </div>
          <button className="secondary small" type="submit">
            Save monitoring schedule
          </button>
        </fieldset>
      </form>
      <button
        className="secondary small"
        type="button"
        disabled={pending}
        onClick={() => void readHistory()}
      >
        Load monitoring history
      </button>
      {historyError && <p role="alert">{historyError}</p>}
      {history && (
        <div className="capital-history" aria-label="Monitoring history">
          {!history.versions.length && <p>No schedule has been saved.</p>}
          {history.versions.map((v) => (
            <div key={String(v.id)}>
              <strong>Schedule change · {london(String(v.created_at))}</strong>
              <span>
                {String(v.payload.reason)} ·{" "}
                {v.payload.enabled ? "enabled" : "off"} · every{" "}
                {String(v.payload.cadence_seconds)} seconds
              </span>
            </div>
          ))}
          {history.ticks.map((t) => (
            <div key={String(t.id)}>
              <strong>
                {String(t.payload.action).toUpperCase()} ·{" "}
                {london(String(t.created_at))}
              </strong>
              <span>
                Due {london(String(t.payload.due_at))} London
                {t.payload.error ? ` · ${String(t.payload.error)}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
