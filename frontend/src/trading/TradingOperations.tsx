import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";

type OperatingState = components["schemas"]["OperatingStateView"];
const names: Record<string, string> = {
  positions: "Position protection and exits",
  orders: "Order reconciliation",
  discovery: "Signal discovery",
  advancement: "Assessment and review",
  delivery: "Approved order delivery",
  observation: "Portfolio values",
  evaluation: "Prospective evidence",
};

export function TradingOperations() {
  const [state, setState] = useState<OperatingState | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    const read = () => {
      if (document.hidden) return;
      void api
        .tradingOperations()
        .then((result) => {
          if (active) {
            setState(result);
            setError(false);
          }
        })
        .catch(() => active && setError(true));
    };
    read();
    const timer = window.setInterval(read, 30000);
    document.addEventListener("visibilitychange", read);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", read);
    };
  }, []);
  const age = (value: number | null) =>
    value === null ? "—" : `${Math.ceil(value)}s`;
  return (
    <details className="research-context">
      <summary>Trading service progress</summary>
      {error && <p role="alert">Current service progress is unavailable.</p>}
      {state && !error && (
        <>
          <p className="muted">
            Successful checks can be idle. Completed work and failures are
            counted separately.
          </p>
          <ul>
            {state.lanes.map((lane) => (
              <li key={`${lane.worker}:${lane.lane}`}>
                <strong>{names[lane.lane] ?? lane.lane}</strong>: {lane.status}{" "}
                · {lane.work_completed} completed · {lane.failures} failures
                {lane.error_category && ` · ${lane.error_category}`}
              </li>
            ))}
          </ul>
          <p>
            Native queue: {state.native_queue.count} · oldest{" "}
            {age(state.native_queue.oldest_age_seconds)}.<br />
            Review queue: {state.review_queue.count} · oldest{" "}
            {age(state.review_queue.oldest_age_seconds)}.<br />
            Unresolved entries: {state.unresolved_entries.count} · closes:{" "}
            {state.unresolved_closes.count}.
          </p>
          <p className="muted">
            Observed {new Date(state.generated_at).toLocaleString()}.
          </p>
        </>
      )}
    </details>
  );
}
