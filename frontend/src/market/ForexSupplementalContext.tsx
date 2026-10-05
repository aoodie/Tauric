type Source = {
  currency: string;
  kind: string;
  provider: string;
  status: string;
  warning?: string;
  url: string;
  items: Record<string, unknown>[];
};
export function ForexSupplementalContext({ evidence }: { evidence: unknown }) {
  const data = (
    evidence as
      | {
          supplemental_context?: {
            fetched_at: string;
            coverage: string;
            sources: Source[];
          };
        }
      | undefined
  )?.supplemental_context;
  if (!data || !Array.isArray(data.sources)) return null;
  const label: Record<string, string> = {
    economic_calendar: "Economic releases",
    policy_rate: "Policy benchmark",
    broker_financing: "Broker financing",
  };
  return (
    <details className="fx-supplement" open>
      <summary>
        Economic calendar, rates & financing ·{" "}
        {data.sources.filter((s) => s.status === "available").length}/
        {data.sources.length} sources available
      </summary>
      <p>{data.coverage}</p>
      <p>Checked {new Date(data.fetched_at).toLocaleString()}</p>
      {data.sources.map((s) => (
        <div className="fx-policy-source" key={s.currency + s.kind}>
          <strong>
            {s.currency} · {label[s.kind]} · {s.status}
          </strong>
          <p>
            {s.provider}
            {s.warning ? ` · ${s.warning}` : ""}
          </p>
          <ul>
            {s.items.slice(0, 30).map((item, i) => (
              <li key={String(item.id ?? i)}>
                {s.kind === "economic_calendar" ? (
                  <>
                    <span>
                      {String(item.event)} · {String(item.time ?? item.date)}
                      {item.time ? " UTC" : " · date only"}
                    </span>
                    <small>
                      Actual {String(item.actual ?? "unavailable")} · Consensus{" "}
                      {String(item.forecast ?? "unavailable")} · Previous{" "}
                      {String(item.previous ?? "unavailable")}
                    </small>
                    <small>
                      Provider forecast{" "}
                      {String(item.provider_forecast ?? "unavailable")} ·
                      Reference {String(item.reference ?? "unavailable")}
                      {item.surprise
                        ? ` · actual minus consensus ${String((item.surprise as { value: string }).value)} ${String((item.surprise as { unit: string }).unit)}`
                        : ""}
                    </small>
                  </>
                ) : s.kind === "policy_rate" ? (
                  <>
                    <span>
                      {String(item.name)} · {String(item.value)}{" "}
                      {String(item.unit)} · {String(item.effective_date)}
                    </span>
                    <small>{String(item.scope)}</small>
                  </>
                ) : (
                  <>
                    <span>
                      Long {String(item.long_rate_decimal)} · Short{" "}
                      {String(item.short_rate_decimal)} · decimal rates
                    </span>
                    <small>{String(item.scope)}</small>
                    <small>
                      {(
                        item.schedule as { day: string; days_charged: number }[]
                      )
                        .map((d) => `${d.day}: ${d.days_charged} days`)
                        .join(" · ")}
                    </small>
                  </>
                )}
                {typeof item.url === "string" && (
                  <a href={item.url} target="_blank" rel="noopener noreferrer">
                    Original source
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}
