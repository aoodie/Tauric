import { useEffect, useRef, useState } from "react";
import { Activity, RefreshCw, ArrowUpRight } from "lucide-react";
import { api } from "../api/client";
import type { components } from "../api/schema";
import "./activity.css";

type Feed = components["schemas"]["AssistantActivityFeed"];
type Item = components["schemas"]["AssistantActivityItem"];
const actors = {
  openclaw: "OpenClaw",
  platform_worker: "Background worker",
  owner: "You",
  unknown: "Actor unrecorded",
};
const states: Record<Item["status"], string> = {
  queued: "Queued",
  running: "Running",
  waiting: "Waiting",
  blocked: "Blocked",
  retry_scheduled: "Retry scheduled",
  reconciling: "Reconciling",
  review_required: "Review required",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};
const resources = new Set([
  "analysis",
  "scanner_candidate",
  "scanner_run",
  "governance_job",
  "trading_record",
  "assistant_receipt",
  "audit",
]);
function time(value: string, timezone: string) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function ActivityRow({
  item,
  timezone,
  open,
}: {
  item: Item;
  timezone: string;
  open: (kind: string, id: string) => void;
}) {
  const links = [
    { resource_type: item.resource_type, resource_id: item.resource_id },
    ...(item.links ?? []),
  ].filter((l) => resources.has(l.resource_type));
  return (
    <article
      className={`activity-row state-${item.status}`}
      aria-label={`${item.title} · ${states[item.status]}`}
    >
      <div className="activity-rail">
        <span />
        <time dateTime={item.timestamp}>{time(item.timestamp, timezone)}</time>
      </div>
      <div className="activity-content">
        <div className="activity-row-heading">
          <strong>{item.title}</strong>
          <span className={`activity-status state-${item.status}`}>
            {states[item.status]}
          </span>
        </div>
        <div className="activity-meta">
          <span>{actors[item.actor]}</span>
          <span>{item.category.replaceAll("_", " ")}</span>
          {(item.symbols ?? []).map((s) => (
            <span className="mono" key={s}>
              {s}
            </span>
          ))}
        </div>
        <p>{item.summary}</p>
        <details>
          <summary>Evidence and outcome</summary>
          <dl className="activity-evidence">
            <div>
              <dt>Record</dt>
              <dd className="mono">{item.resource_id}</dd>
            </div>
            <div>
              <dt>Evidence</dt>
              <dd>
                {item.verification === "checksum_verified"
                  ? "Source checksum verified"
                  : item.verification === "append_only"
                    ? "Append-only event"
                    : "Current service state"}
              </dd>
            </div>
            {item.updated_at && (
              <div>
                <dt>Last updated</dt>
                <dd>{time(item.updated_at, timezone)}</dd>
              </div>
            )}
            {item.checksum && (
              <div>
                <dt>Checksum</dt>
                <dd className="mono">{item.checksum}</dd>
              </div>
            )}
          </dl>
          {Object.keys(item.details ?? {}).length > 0 && (
            <pre>{JSON.stringify(item.details, null, 2)}</pre>
          )}
          <div className="activity-links">
            {links.map((link, index) => (
              <button
                className="text-button"
                key={`${link.resource_type}-${link.resource_id}-${index}`}
                onClick={() => open(link.resource_type, link.resource_id)}
              >
                Open {link.resource_type.replaceAll("_", " ")}
                <ArrowUpRight size={12} />
              </button>
            ))}
          </div>
        </details>
      </div>
    </article>
  );
}

export function OpenClawActivity({
  timezone = "Europe/London",
  open,
  compact = false,
}: {
  timezone?: string;
  open: (kind: string, id: string) => void;
  compact?: boolean;
}) {
  const [feed, setFeed] = useState<Feed>();
  const [items, setItems] = useState<Item[]>([]);
  const [cursor, setCursor] = useState<string | null>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [actor, setActor] = useState("all");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [clock, setClock] = useState(Date.now());
  const paged = useRef(false);
  const unfinished = useRef<string[]>([]);
  const observationOffset = useRef(0);
  const busy = useRef(false),
    active = useRef(true);
  async function read(older = false) {
    if (busy.current || (older && !cursor)) return;
    busy.current = true;
    setPending(true);
    try {
      const next = await api.assistantActivities(
        older ? (cursor ?? undefined) : undefined,
      );
      const observationIds = [
        ...unfinished.current.slice(observationOffset.current),
        ...unfinished.current.slice(0, observationOffset.current),
      ].slice(0, 100);
      const observations =
        !older && observationIds.length
          ? await api.observeActivities(observationIds)
          : [];
      if (!older)
        observationOffset.current =
          (observationOffset.current + 100) %
          Math.max(unfinished.current.length, 1);
      if (!active.current) return;
      setItems((previous) => {
        const merged = new Map(previous.map((item) => [item.id, item]));
        for (const item of [...next.items, ...observations])
          merged.set(item.id, item);
        unfinished.current = [...merged.values()]
          .filter(
            (item) =>
              [
                "queued",
                "running",
                "waiting",
                "blocked",
                "retry_scheduled",
                "reconciling",
              ].includes(item.status) &&
              /^(assistant|research|governance|chat|quant):/.test(item.id),
          )
          .map((item) => item.id);
        return [...merged.values()]
          .sort(
            (a, b) =>
              b.timestamp.localeCompare(a.timestamp) ||
              b.id.localeCompare(a.id),
          )
          .slice(0, 1000);
      });
      if (!older) setFeed(next);
      if (older) paged.current = true;
      setCursor((previous) =>
        older || !paged.current ? (next.next_cursor ?? null) : previous,
      );
      setClock(Date.now());
      setError("");
    } catch (e) {
      if (active.current)
        setError(
          e instanceof Error
            ? e.message
            : "Activity is unavailable. Try refreshing.",
        );
    } finally {
      busy.current = false;
      if (active.current) setPending(false);
    }
  }
  useEffect(() => {
    active.current = true;
    void read();
    const timer = window.setInterval(() => {
      setClock(Date.now());
      if (!document.hidden) void read();
    }, 10000);
    const wake = () => {
      if (!document.hidden) void read();
    };
    document.addEventListener("visibilitychange", wake);
    return () => {
      active.current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);
  const visible = items.filter(
    (item) =>
      (actor === "all" || item.actor === actor) &&
      (category === "all" || item.category === category) &&
      (status === "all" || item.status === status) &&
      `${item.title} ${item.summary} ${item.operation} ${(item.symbols ?? []).join(" ")} ${JSON.stringify(item.details)}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <section
      className={`openclaw-activity ${compact ? "compact" : ""}`}
      aria-label="OpenClaw activity feed"
    >
      <header className="activity-heading">
        <div>
          <span className="eyebrow">WORK AND OUTCOMES</span>
          <h2>
            <Activity size={19} />
            OpenClaw activity
          </h2>
          <p>
            {feed
              ? `Updated ${time(feed.generated_at, timezone)} · refreshes automatically`
              : "Reading recorded work…"}
          </p>
        </div>
        <button
          className="secondary small"
          aria-label="Refresh OpenClaw activity"
          disabled={pending}
          onClick={() => void read()}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </header>
      {error && (
        <p className="activity-error" role="alert">
          {error}
          {feed &&
            " Showing the last successful update; activity may be stale."}
        </p>
      )}
      {feed && clock - Date.parse(feed.generated_at) > 30000 && !error && (
        <p className="activity-error" role="status">
          Activity is stale. The last successful update is shown above.
        </p>
      )}
      {feed && (
        <div className="activity-now">
          <span className="eyebrow">CURRENT WORK</span>
          {feed.current_work.length ? (
            feed.current_work.map((item) => (
              <div key={item.id} className="activity-current">
                <span className={`activity-status state-${item.status}`}>
                  {states[item.status]}
                </span>
                <strong>{item.title}</strong>
                <span>{actors[item.actor]}</span>
              </div>
            ))
          ) : (
            <p>No active work is recorded. Scheduled workers may be idle.</p>
          )}
        </div>
      )}
      <div className="activity-filters">
        <label>
          Actor
          <select
            aria-label="Activity actor"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          >
            <option value="all">All actors</option>
            {Object.entries(actors).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Type
          <select
            aria-label="Activity type"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="all">All activity</option>
            {[
              "scan",
              "proposal",
              "research",
              "governance",
              "paper_trade",
              "upgrade",
              "chat",
              "operations",
            ].map((v) => (
              <option key={v} value={v}>
                {v.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            aria-label="Activity status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="all">All statuses</option>
            {Object.entries(states).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {!compact && (
          <label>
            Find work
            <input
              aria-label="Search OpenClaw activity"
              placeholder="Symbol, task or file"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        )}
      </div>
      <p className="activity-coverage">
        {visible.length} of {items.length} loaded records · filters apply to
        loaded history
        {items.length === 1000 &&
          " · recent 1,000 record display limit reached"}
      </p>
      <div className="activity-timeline">
        {visible.map((item) => (
          <ActivityRow
            key={item.id}
            item={item}
            timezone={timezone}
            open={open}
          />
        ))}
        {feed && !visible.length && (
          <p className="activity-empty">
            {items.length
              ? "No loaded records match these filters. Load older activity or change the filters."
              : "Recorded actions and their outcomes will appear here when work starts."}
          </p>
        )}
      </div>
      {cursor && items.length < 1000 && (
        <button
          className="secondary small"
          disabled={pending}
          onClick={() => void read(true)}
        >
          Load older activity
        </button>
      )}
      {feed && (
        <details className="activity-scope">
          <summary>What this feed covers</summary>
          <ul>
            {(feed.coverage ?? []).map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
