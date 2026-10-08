type Item = {
  title?: string;
  published_at?: string;
  date?: string;
  timezone?: string;
  label?: string;
  url: string;
};
type Source = {
  currency: string;
  kind: "news" | "calendar";
  label: string;
  status: "available" | "stale" | "unavailable";
  url: string | null;
  warning?: string;
  items: Item[];
};
type Context = { fetched_at: string; currencies: string[]; sources: Source[] };

export function ForexPolicyContext({ value }: { value: unknown }) {
  if (!value || typeof value !== "object") return null;
  const context = value as Context;
  if (
    !Array.isArray(context.sources) ||
    !Array.isArray(context.currencies) ||
    typeof context.fetched_at !== "string"
  )
    return null;
  const available = context.sources.filter(
    (s) => s.status === "available",
  ).length;
  return (
    <details className="fx-policy" open>
      <summary>
        Policy news &amp; events{" "}
        <span className="badge neutral">
          {available}/{context.sources.length} sources available
        </span>
      </summary>
      <p>
        Checked {new Date(context.fetched_at).toLocaleString("en-GB")}.
        Central-bank headlines and policy dates; broader economic releases are
        not covered.
      </p>
      {context.currencies.map((currency) => (
        <div className="fx-policy-currency" key={currency}>
          <strong>{currency}</strong>
          {context.sources
            .filter((s) => s.currency === currency)
            .map((source) => (
              <div className="fx-policy-source" key={source.kind}>
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {source.label} ·{" "}
                    {source.kind === "news" ? "Headlines" : "Policy calendar"}
                  </a>
                ) : (
                  <span>
                    {source.label} ·{" "}
                    {source.kind === "news" ? "Headlines" : "Policy calendar"}
                  </span>
                )}
                {source.status !== "available" && (
                  <p className="fx-policy-warning">
                    {source.status === "stale"
                      ? "Stale headlines — check publication dates."
                      : (source.warning ??
                        "Source unavailable — coverage is incomplete.")}
                  </p>
                )}
                <ul>
                  {source.items.slice(0, 2).map((item) => (
                    <li key={item.url + (item.date ?? item.published_at)}>
                      {source.kind === "news" ? (
                        <>
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {item.title}
                          </a>
                          <small>
                            Published{" "}
                            {item.published_at
                              ? new Date(item.published_at).toLocaleDateString(
                                  "en-GB",
                                )
                              : "date unavailable"}
                          </small>
                        </>
                      ) : (
                        <>
                          <span>
                            {item.date} · {item.label}
                          </span>
                          <small>
                            Date only · {item.timezone}; release time not
                            supplied
                          </small>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
        </div>
      ))}
    </details>
  );
}
