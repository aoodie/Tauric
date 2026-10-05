import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { PortfolioResearch } from "./PortfolioResearch";

type Portfolio = components["schemas"]["PortfolioView"];
type Context = components["schemas"]["PortfolioContext"];
type Save = components["schemas"]["PortfolioSave"];
const errorText = (e: unknown) =>
  e instanceof Error ? e.message : "Portfolio unavailable.";

export function PortfolioSelector({
  value,
  onChange,
  portfolios,
}: {
  value: string;
  onChange: (id: string) => void;
  portfolios: Portfolio[];
}) {
  return (
    <label className="portfolio-picker">
      Conversation context
      <select
        aria-label="Conversation context"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">General Tauric chat</option>
        {portfolios.map((p) => (
          <option value={p.id} key={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function PortfolioPanel({
  id,
  portfolios,
  changed,
  open,
}: {
  id: string;
  portfolios: Portfolio[];
  changed: () => void;
  open: (type: string, id: string) => void;
}) {
  const [context, setContext] = useState<Context>();
  const [draft, setDraft] = useState<Save>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Record<string, unknown>[]>();
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    void load();
    return () => {
      active.current = false;
    };
  }, [id]);
  async function load() {
    try {
      const c = await api.portfolio(id);
      if (!active.current) return;
      setContext(c);
      setDraft({
        request_id: crypto.randomUUID(),
        expected_revision: c.portfolio.revision,
        preferences: c.portfolio.preferences ?? {
          mandate: "",
          research_objective: "net_return",
          capital_mode: "fixed",
          chat_edit_preferences: false,
          chat_control_deployments: false,
        },
        allocation_ids: c.portfolio.allocation_ids,
        reason: "Updated portfolio settings",
      });
      setError("");
    } catch (e) {
      if (active.current) setError(errorText(e));
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.savePortfolio(id, draft);
      if (!active.current) return;
      setNotice(
        "Portfolio settings saved. Research and orders were not started.",
      );
      changed();
      await load();
      setHistory(undefined);
    } catch (e) {
      if (active.current) setError(errorText(e));
    } finally {
      if (active.current) setBusy(false);
    }
  }
  const preferences = draft?.preferences;
  function edit(values: Partial<NonNullable<typeof preferences>>) {
    if (draft)
      setDraft({
        ...draft,
        request_id: crypto.randomUUID(),
        preferences: { ...draft.preferences, ...values },
      });
  }
  const choices = [
    ...portfolios.map((p) => ({ id: p.id, name: p.name })),
    ...(context?.portfolio.accounts ?? []).map((a) => ({
      id: String(a.allocation_id),
      name: `${a.name} allocation`,
    })),
  ].filter((p, i, all) => all.findIndex((v) => v.id === p.id) === i);
  return (
    <section className="portfolio-panel" aria-label="Portfolio workspace">
      {error && (
        <p role="alert" className="error-banner">
          {error}{" "}
          <button className="secondary" onClick={() => void load()}>
            Reload portfolio
          </button>
        </p>
      )}
      {context && (
        <>
          <div className="portfolio-summary">
            <strong>{context.portfolio.name}</strong>
            <span className="badge cyan">
              Paper · settings revision {context.portfolio.revision}
            </span>
            <span>Native Tauric evidence required</span>
          </div>
          <p className="muted">
            {context.deployments.length} attributed deployments ·{" "}
            {context.positions.length} open positions ·{" "}
            {context.portfolio.accounts.length} account allocations. Each
            account keeps its own capital limits.
          </p>
          <div className="portfolio-allocations">
            {context.allocations.map((a) => (
              <div key={String(a.id)}>
                <strong>{String(a.name)}</strong>
                <span>
                  Budget{" "}
                  {String(
                    a.limits && (a.limits as Record<string, unknown>).budget,
                  )}{" "}
                  USDT · open cost {String(a.open_cost)}
                </span>
              </div>
            ))}
          </div>
          {context.deployments.length > 0 && (
            <details>
              <summary>Attributed deployments</summary>
              {context.deployments.map((d) => (
                <p key={String(d.id)}>
                  {String(d.strategy)} ·{" "}
                  {String(d.instrument_id).split(":").at(-1)} ·{" "}
                  {d.enabled ? "Enabled" : "Paused"}
                </p>
              ))}
            </details>
          )}
          <p className="muted">
            Daily research is configurable below. Strategy qualification,
            promotion and managed capital transfers remain pending. Live
            execution is disabled.
          </p>
          <details className="portfolio-settings">
            <summary>Portfolio settings and chat permissions</summary>
            {draft && preferences && (
              <form onSubmit={save}>
                <label>
                  Mandate
                  <textarea
                    aria-label="Portfolio mandate"
                    minLength={3}
                    maxLength={2000}
                    required
                    value={preferences.mandate}
                    onChange={(e) => edit({ mandate: e.target.value })}
                  />
                </label>
                <label>
                  Research objective
                  <select
                    aria-label="Research objective"
                    value={preferences.research_objective}
                    onChange={(e) =>
                      edit({
                        research_objective: e.target
                          .value as typeof preferences.research_objective,
                      })
                    }
                  >
                    <option value="net_return">
                      Higher net returns within risk limits
                    </option>
                    <option value="balanced">
                      Balance returns and stability
                    </option>
                    <option value="consistency">
                      Prioritise consistent results
                    </option>
                  </select>
                </label>
                <label>
                  Capital preference
                  <select
                    aria-label="Capital preference"
                    value={preferences.capital_mode}
                    onChange={(e) =>
                      edit({
                        capital_mode: e.target.value as "fixed" | "managed",
                      })
                    }
                  >
                    <option value="fixed">Fixed allocations</option>
                    <option value="managed">
                      Managed capital — preference only
                    </option>
                  </select>
                </label>
                <p className="muted">
                  Choosing managed records your preference; it does not move
                  capital.
                </p>
                <fieldset>
                  <legend>Linked account allocations</legend>
                  {choices.map((p) => (
                    <label className="check-row" key={p.id}>
                      <input
                        type="checkbox"
                        checked={draft.allocation_ids.includes(p.id)}
                        disabled={p.id === id || busy}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            request_id: crypto.randomUUID(),
                            allocation_ids: e.target.checked
                              ? [...draft.allocation_ids, p.id]
                              : draft.allocation_ids.filter((v) => v !== p.id),
                          })
                        }
                      />
                      {p.name}
                    </label>
                  ))}
                  <p className="muted">
                    An allocation with its own saved mandate or conversation
                    cannot be linked here.
                  </p>
                </fieldset>
                <label className="check-row">
                  <input
                    type="checkbox"
                    aria-label="Allow chat preference changes"
                    checked={preferences.chat_edit_preferences}
                    onChange={(e) =>
                      edit({ chat_edit_preferences: e.target.checked })
                    }
                  />
                  Allow chat to change mandate and research objective
                </label>
                <label className="check-row">
                  <input
                    type="checkbox"
                    aria-label="Allow chat deployment controls"
                    checked={preferences.chat_control_deployments}
                    onChange={(e) =>
                      edit({ chat_control_deployments: e.target.checked })
                    }
                  />
                  Allow chat to pause or resume existing paper deployments
                </label>
                <label>
                  Reason
                  <input
                    aria-label="Portfolio change reason"
                    required
                    minLength={3}
                    maxLength={500}
                    value={draft.reason}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        request_id: crypto.randomUUID(),
                        reason: e.target.value,
                      })
                    }
                  />
                </label>
                <div className="portfolio-form-actions">
                  <button type="submit" disabled={busy}>
                    {busy ? "Saving…" : "Save portfolio settings"}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void load()}
                  >
                    Reload saved settings
                  </button>
                </div>
              </form>
            )}
          </details>
          <button
            className="secondary"
            onClick={() => {
              void api
                .portfolioHistory(id)
                .then((v) => {
                  if (active.current) setHistory(v);
                })
                .catch((e) => {
                  if (active.current) setError(errorText(e));
                });
            }}
          >
            View portfolio settings history
          </button>
          {history && (
            <div className="portfolio-history">
              {history.length ? (
                history.map((v) => (
                  <details key={String(v.id)}>
                    <summary>
                      Revision {String(v.revision)} · {String(v.created_at)}
                    </summary>
                    <pre>{JSON.stringify(v.payload, null, 2)}</pre>
                  </details>
                ))
              ) : (
                <p>No saved mandate yet.</p>
              )}
            </div>
          )}
          {notice && <p role="status">{notice}</p>}
          <PortfolioResearch
            key={context.portfolio.revision}
            id={id}
            open={open}
          />
        </>
      )}
    </section>
  );
}
