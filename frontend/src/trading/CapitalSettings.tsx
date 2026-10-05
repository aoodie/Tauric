import { useState, type FormEvent } from "react";
import {
  api,
  type CapitalScope,
  type CapitalLimits,
  type CapitalVersion,
} from "../api/client";
import { usePaper } from "./PaperTrading";

export function allocationPath(
  scope: CapitalScope,
  scopes: CapitalScope[],
): CapitalScope[] {
  const result = [scope];
  const seen = new Set([scope.id]);
  let parent = scope.parent_id;
  while (parent && !seen.has(parent)) {
    seen.add(parent);
    const row = scopes.find((s) => s.id === parent);
    if (!row) break;
    result.unshift(row);
    parent = row.parent_id;
  }
  return result;
}
function limitsFrom(f: FormData): CapitalLimits {
  return {
    budget: String(f.get("budget")),
    max_total_risk_percent: String(f.get("max_total_risk_percent")),
    max_positions: Number(f.get("max_positions")),
  };
}
function LimitFields({ value }: { value?: CapitalLimits }) {
  return (
    <div className="paper-form-grid">
      <label>
        Allocation budget (account currency)
        <input
          name="budget"
          required
          inputMode="decimal"
          maxLength={40}
          defaultValue={value?.budget ?? ""}
        />
      </label>
      <label>
        Allocation total risk (%)
        <input
          name="max_total_risk_percent"
          required
          inputMode="decimal"
          maxLength={40}
          defaultValue={value?.max_total_risk_percent ?? ""}
        />
      </label>
      <label>
        Allocation maximum positions
        <input
          name="max_positions"
          required
          inputMode="numeric"
          maxLength={4}
          defaultValue={value?.max_positions ?? ""}
        />
      </label>
    </div>
  );
}
function AllocationCard({ scope }: { scope: CapitalScope }) {
  const { state, act, pending } = usePaper();
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState<CapitalVersion[] | null>(null);
  const [historyError, setHistoryError] = useState("");
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (
      await act(
        `capital-${scope.id}-${scope.revision}-${JSON.stringify(limitsFrom(f))}`,
        () =>
          api.saveCapital(scope.id, {
            expected_revision: scope.revision,
            limits: limitsFrom(f),
            reason: String(f.get("reason")),
          }),
        "Allocation saved. Earlier plans need a new proposal.",
      )
    ) {
      setEditing(false);
      setHistory(null);
    }
  }
  async function loadHistory() {
    setHistory(null);
    setHistoryError("");
    try {
      setHistory(await api.capitalHistory(scope.id));
    } catch (err) {
      setHistoryError(
        err instanceof Error
          ? err.message
          : "Could not read allocation history.",
      );
    }
  }
  return (
    <article className="paper-card" aria-label={`${scope.name} allocation`}>
      <div className="paper-card-head">
        <div>
          <span className="eyebrow">
            {scope.kind.toUpperCase()} · PAPER · · account currency ·{" "}
            {state?.accounts.find((a) => a.id === scope.account_id)?.name}
          </span>
          <h2>
            {allocationPath(scope, state?.capital_scopes ?? [])
              .map((v) => v.name)
              .join(" / ")}
          </h2>
        </div>
        <span className="badge cyan">v{scope.revision}</span>
      </div>
      <div className="paper-balances">
        <div>
          <span>Shared budget</span>
          <strong>{scope.limits.budget}</strong>
        </div>
        <div>
          <span>Reserved for children</span>
          <strong>{scope.allocated_to_children}</strong>
        </div>
        <div>
          <span>Open acquisition cost</span>
          <strong>{scope.open_cost}</strong>
        </div>
      </div>
      <p className="muted">
        {scope.open_positions} / {scope.limits.max_positions} open positions ·{" "}
        {scope.limits.max_total_risk_percent}% total risk. Parent limits include
        every descendant. New entries reserve the greater of acquisition cost
        and current marked value, plus entry fees.
      </p>
      <div className="paper-actions">
        <button
          className="secondary small"
          disabled={pending}
          onClick={() => setEditing(!editing)}
        >
          {editing ? "Close allocation editor" : "Edit allocation"}
        </button>
        <button className="secondary small" onClick={() => void loadHistory()}>
          Allocation history
        </button>
      </div>
      {editing && (
        <form
          aria-label={`${scope.name} allocation limits`}
          key={scope.revision}
          onSubmit={save}
        >
          <fieldset disabled={pending}>
            <LimitFields value={scope.limits} />
            <label>
              Allocation change reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <button className="primary small" type="submit">
              Save allocation
            </button>
          </fieldset>
        </form>
      )}
      {historyError && (
        <p className="error" role="alert">
          {historyError}
        </p>
      )}
      {history && (
        <div
          className="capital-history"
          aria-label={`${scope.name} allocation history`}
        >
          <button className="text-button" onClick={() => setHistory(null)}>
            Close history
          </button>
          {history.map((v) => (
            <div key={v.id}>
              <strong>Version {v.revision}</strong>
              <span>
                {String((v.payload.limits as CapitalLimits).budget)} · account
                currency · {String(v.payload.reason)}
              </span>
              <time>{new Date(v.created_at).toLocaleString()}</time>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}
export function CapitalSettings() {
  const { state, pending, act } = usePaper();
  const [adding, setAdding] = useState<"portfolio" | "pot" | null>(null);
  const [selectedAccount, setSelectedAccount] = useState("");
  const accountId = selectedAccount || state?.accounts[0]?.id || "";
  const scopes =
    state?.capital_scopes.filter((v) => v.account_id === accountId) ?? [];
  const ordered = [...(state?.capital_scopes ?? [])].sort(
    (a, b) =>
      a.account_id.localeCompare(b.account_id) ||
      allocationPath(a, state?.capital_scopes ?? [])
        .map((v) => v.name)
        .join(" / ")
        .localeCompare(
          allocationPath(b, state?.capital_scopes ?? [])
            .map((v) => v.name)
            .join(" / "),
        ),
  );
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!adding) return;
    const f = new FormData(e.currentTarget);
    const data = {
      account_id: accountId,
      kind: adding,
      parent_id: adding === "pot" ? String(f.get("parent_id")) : null,
      name: String(f.get("name")),
      limits: limitsFrom(f),
      reason: String(f.get("reason")),
    };
    if (
      await act(
        `capital-create-${JSON.stringify(data)}`,
        (id) => api.createCapital({ request_id: id, ...data }),
        "Shared allocation created.",
      )
    )
      setAdding(null);
  }
  return (
    <section
      className="paper-account-list"
      aria-label="Portfolios and capital pots"
    >
      <div className="paper-intro">
        <div>
          <h2>Portfolios &amp; capital pots</h2>
          <p>
            Give each portfolio a fixed share of the account’s starting capital,
            then divide it into nested pots. Child budgets reserve capital;
            direct strategies use the parent’s remaining allocation. Close any
            unallocated legacy positions before adding the first portfolio.
          </p>
        </div>
      </div>
      <div className="paper-actions">
        <button
          className="secondary small"
          disabled={pending || !state?.accounts.length}
          onClick={() => setAdding(adding === "portfolio" ? null : "portfolio")}
        >
          Add portfolio
        </button>
        <button
          className="secondary small"
          disabled={pending || !state?.capital_scopes.length}
          onClick={() => setAdding(adding === "pot" ? null : "pot")}
        >
          Add capital pot
        </button>
      </div>
      {adding && (
        <form
          className="paper-card"
          aria-label="New capital allocation"
          key={adding}
          onSubmit={create}
        >
          <h2>
            {adding === "portfolio" ? "New portfolio" : "New capital pot"}
          </h2>
          <fieldset disabled={pending}>
            <div className="paper-form-grid">
              <label>
                Allocation account
                <select
                  aria-label="Allocation account"
                  required
                  value={accountId}
                  onChange={(e) => setSelectedAccount(e.target.value)}
                >
                  {state?.accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Allocation name
                <input name="name" required minLength={3} maxLength={80} />
              </label>
              {adding === "pot" && (
                <label>
                  Parent allocation
                  <select
                    name="parent_id"
                    aria-label="Parent allocation"
                    key={accountId}
                    required
                  >
                    <option value="">Choose a parent</option>
                    {scopes.map((v) => (
                      <option key={v.id} value={v.id}>
                        {allocationPath(v, state?.capital_scopes ?? [])
                          .map((a) => a.name)
                          .join(" / ")}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <LimitFields />
            <label>
              Allocation setup reason
              <input name="reason" required minLength={3} maxLength={500} />
            </label>
            <button className="primary small" type="submit">
              Create allocation
            </button>
            <button
              className="text-button"
              type="button"
              onClick={() => setAdding(null)}
            >
              Cancel
            </button>
          </fieldset>
        </form>
      )}
      {ordered.map((scope) => (
        <AllocationCard key={scope.id} scope={scope} />
      ))}
      {state && !state.capital_scopes.length && (
        <p className="muted">
          No shared allocations yet. Create a portfolio before adding a paper
          strategy.
        </p>
      )}
    </section>
  );
}
