import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { api } from "../api/client";
import "./heartbeat.css";

type Status = Awaited<ReturnType<typeof api.heartbeatStatus>>;
const labels: Record<Status["status"], string> = {
  healthy: "Healthy",
  running: "Checking",
  waiting: "Awaiting first check",
  failed: "Check failed",
  stale: "Overdue",
  unavailable: "Unavailable",
  unverified: "Result unverified",
  disabled: "Disabled",
  paused: "Paused",
};

export function useHeartbeatStatus() {
  const [status, setStatus] = useState<Status>();
  useEffect(() => {
    let active = true,
      busy = false;
    const read = async () => {
      if (busy) return;
      busy = true;
      try {
        const next = await api.heartbeatStatus();
        if (active) setStatus(next);
      } catch {
        if (active) setStatus(undefined);
      } finally {
        busy = false;
      }
    };
    void read();
    const timer = window.setInterval(() => {
      if (!document.hidden) void read();
    }, 15000);
    const wake = () => {
      if (!document.hidden) void read();
    };
    document.addEventListener("visibilitychange", wake);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);
  return status;
}

export function heartbeatLabel(status?: Status) {
  return status ? labels[status.status] : "Unavailable";
}

export function HeartbeatPanel({
  status,
  timezone,
}: {
  status?: Status;
  timezone: string;
}) {
  const time = (value?: string | null) =>
    value
      ? new Intl.DateTimeFormat("en-GB", {
          timeZone: timezone,
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }).format(new Date(value))
      : "—";
  return (
    <section className="heartbeat-panel" aria-label="OpenClaw heartbeat">
      <div className="heartbeat-heading">
        <h3>
          <Activity size={17} /> OpenClaw heartbeat
        </h3>
        <span
          className={`badge ${status?.status === "healthy" ? "green" : status?.status === "running" ? "gold" : "neutral"}`}
        >
          {heartbeatLabel(status)}
        </span>
      </div>
      <p>
        {status?.model ? "Local Qwen3 4B" : "Local service check"}
        {status?.interval_seconds
          ? ` · Every ${Math.round(status.interval_seconds / 60)} minutes`
          : ""}
        {status?.status === "unavailable" || !status
          ? " · Status could not be read"
          : ""}
      </p>
      <dl className="heartbeat-details">
        <div>
          <dt>Last run</dt>
          <dd>
            {time(status?.last_started_at)}
            {status?.automatic === true
              ? " · Automatic"
              : status?.automatic === false
                ? " · Manual"
                : ""}
          </dd>
        </div>
        <div>
          <dt>Last result</dt>
          <dd>
            {status?.ready === true
              ? `API and storage ready · HTTP ${status.http_status}`
              : status?.ready === false
                ? `API or storage unavailable · HTTP ${status.http_status}`
                : status?.last_run_status
                  ? `No verified readiness result · ${status.last_run_status}`
                  : "No verified readiness result"}
          </dd>
        </div>
        <div>
          <dt>Checked at</dt>
          <dd>{time(status?.last_check_at)}</dd>
        </div>
        <div>
          <dt>Next check</dt>
          <dd>
            {status?.status === "running"
              ? "After current check"
              : time(status?.next_check_at)}
          </dd>
        </div>
      </dl>
      <p className="heartbeat-scope">
        API and storage checks only · Times in {timezone} · Refreshes
        automatically
      </p>
    </section>
  );
}
