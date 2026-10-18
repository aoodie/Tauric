import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type BrokerSetup,
  type BrokerSetupPreview,
  type Instrument,
  type StrategyVersion,
  type NativeReport,
  type AnalysisStatus,
} from "../api/client";
import { BrokerEntryReviews } from "./BrokerEntryReviews";
import { NativeReportReader } from "../research/NativeReport";

const explain = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "The setup review could not be completed.";

export function BrokerSetupReview({
  provider,
  onEntry,
}: {
  onEntry: () => Promise<void>;
  provider: "oanda" | "alpaca";
}) {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [versions, setVersions] = useState<StrategyVersion[]>([]);
  const [reviews, setReviews] = useState<BrokerSetup[]>([]);
  const [preview, setPreview] = useState<BrokerSetupPreview | null>(null);
  const [request, setRequest] = useState<
    Parameters<typeof api.requestBrokerSetup>[0] | null
  >(null);
  const [retry, setRetry] = useState<
    Parameters<typeof api.requestBrokerSetup>[0] | null
  >(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [original, setOriginal] = useState<NativeReport | null>(null);
  const [models, setModels] = useState<AnalysisStatus | null>(null);
  const [profile, setProfile] = useState<"cloud" | "local">("cloud");
  async function load() {
    const [saved, status] = await Promise.all([
      api.brokerSetups(),
      api.analysisStatus(),
    ]);
    setReviews(saved);
    setModels(status);
  }
  useEffect(() => {
    let mounted = true;
    void Promise.all([
      api.instruments(),
      api.strategyVersions(),
      api.brokerSetups(),
      api.analysisStatus(),
    ])
      .then(([items, saved, results, status]) => {
        if (mounted) {
          setInstruments(items);
          setVersions(saved.filter((v) => !!v.parameters.rules));
          setReviews(results);
          setModels(status);
          setProfile(status.profiles.find((p) => p.configured)?.id ?? "cloud");
        }
      })
      .catch((err) => {
        if (mounted) setError(explain(err));
      });
    return () => {
      mounted = false;
    };
  }, []);
  useEffect(() => {
    setPreview(null);
    setRequest(null);
    setError("");
    setNotice("");
  }, [provider]);
  useEffect(() => {
    if (!reviews.some((r) => ["queued", "running"].includes(r.job.status)))
      return;
    let mounted = true;
    const timer = window.setInterval(() => {
      void api
        .brokerSetups()
        .then((value) => {
          if (mounted) setReviews(value);
        })
        .catch((err) => {
          if (mounted) setError(explain(err));
        });
    }, 5000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [reviews]);
  async function act(work: () => Promise<void>) {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (err) {
      setError(explain(err));
    } finally {
      setPending(false);
    }
  }
  async function check(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const body: Parameters<typeof api.requestBrokerSetup>[0] = {
      request_id: crypto.randomUUID(),
      provider,
      strategy_version_id: String(form.get("setup_version")),
      instrument_id: String(form.get("setup_instrument")),
      quantity: String(form.get("setup_quantity")),
      profile: String(form.get("setup_profile")) as "cloud" | "local",
      reason: String(form.get("setup_reason")),
    };
    setPreview(null);
    setRequest(null);
    await act(async () => {
      const result = await api.brokerSetupPreview(body);
      setPreview(result);
      setRequest(body);
    });
  }
  async function review(body: NonNullable<typeof request>) {
    setRetry(body);
    await act(async () => {
      try {
        const job = await api.requestBrokerSetup(body);
        setRetry(null);
        setPreview(null);
        setRequest(null);
        setNotice(
          `Setup review ${job.status}. Your selected AI connection may incur charges.`,
        );
        await load();
      } catch (err) {
        // A definite refusal did not queue a job. A lost response retains its exact ID.
        if (err instanceof ApiError && err.status >= 400 && err.status < 500)
          setRetry(null);
        throw err;
      }
    });
  }
  const eligible = instruments.filter((item) =>
    provider === "oanda"
      ? ["fx", "commodity", "cfd"].includes(item.asset_type)
      : ["equity", "etf"].includes(item.asset_type),
  );
  return (
    <section className="paper-card" aria-label="Broker setup reviews">
      <h3>Review a broker trade setup</h3>
      <p>
        Check a saved strategy’s current long-entry signal and broker quote,
        then request a separate Tauric review of the exact quantity. Original
        daily research stays available. This review does not place an order; a
        separate OpenClaw approval can authorize one exact broker entry.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!versions.length && (
        <p>
          Save a strategy with entry and exit rules in Settings → Strategies
          first.
        </p>
      )}
      <form
        onSubmit={(e) => void check(e)}
        onChange={() => {
          setPreview(null);
          setRequest(null);
        }}
      >
        <fieldset className="paper-form-grid" disabled={pending || !!retry}>
          <label>
            Setup instrument
            <select
              name="setup_instrument"
              aria-label="Setup instrument"
              required
              key={provider}
            >
              {eligible.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.symbol}
                </option>
              ))}
            </select>
          </label>
          <label>
            Setup strategy version
            <select
              name="setup_version"
              aria-label="Setup strategy version"
              required
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} · {v.parameters.label} · revision {v.revision}
                </option>
              ))}
            </select>
          </label>
          <label>
            {provider === "oanda"
              ? "Setup currency units"
              : "Setup whole shares"}
            <input
              name="setup_quantity"
              aria-label="Setup quantity"
              inputMode="decimal"
              required
            />
          </label>
          <label>
            Setup AI connection
            <select
              name="setup_profile"
              aria-label="Setup AI connection"
              value={profile}
              onChange={(e) => setProfile(e.target.value as "cloud" | "local")}
            >
              <option value="cloud">
                Cloud
                {models?.profiles.find((p) => p.id === "cloud")?.configured
                  ? ""
                  : " · not configured"}
              </option>
              <option value="local">
                Local
                {models?.profiles.find((p) => p.id === "local")?.configured
                  ? ""
                  : " · not configured"}
              </option>
            </select>
          </label>
          <label>
            Setup reason
            <input
              name="setup_reason"
              aria-label="Setup reason"
              required
              minLength={3}
            />
          </label>
          <button type="submit" disabled={!versions.length || !eligible.length}>
            Check setup evidence
          </button>
        </fieldset>
      </form>
      {preview && (
        <div role="status">
          <p>
            {preview.entry_evidence_ready
              ? "Entry evidence passed the initial checks."
              : "Entry evidence is unavailable or did not pass."}
          </p>
          <p>{preview.reasons.join(" ")}</p>
          {preview.quote && (
            <p>
              Bid {preview.quote.bid} · ask {preview.quote.ask} · observed{" "}
              {new Date(preview.quote.observed_at).toLocaleString()}
            </p>
          )}
          {preview.planned_order && (
            <p>
              Buy {preview.planned_order.quantity} · stop{" "}
              {preview.planned_order.stop_loss} · target{" "}
              {preview.planned_order.take_profit}
            </p>
          )}
          <p>
            Valid until {new Date(preview.expires_at).toLocaleString()}.
            Evidence is checked again before review.
          </p>
          <button
            disabled={
              pending ||
              !!retry ||
              !request ||
              !preview.entry_evidence_ready ||
              !models?.profiles.find((p) => p.id === profile)?.configured
            }
            onClick={() => request && void review(request)}
          >
            Request Tauric setup review
          </button>
          <p>
            Uses the selected AI connection and may incur charges. Your
            no-limits choice applies; broker restrictions remain.
          </p>
          {!models?.profiles.find((p) => p.id === profile)?.configured && (
            <p>
              Configure this AI connection in Settings before requesting a
              review.
            </p>
          )}
        </div>
      )}
      {retry && (
        <p>
          A review response was not received.{" "}
          <button disabled={pending} onClick={() => void review(retry)}>
            Recover this same setup review
          </button>
        </p>
      )}
      <h4>Saved setup reviews</h4>
      <button
        className="secondary"
        disabled={pending}
        onClick={() => void act(load)}
      >
        Refresh setup reviews
      </button>
      {!reviews.length && <p>No broker setup reviews recorded.</p>}
      {reviews.map((result) => (
        <article
          className="paper-card"
          key={result.job.id}
          aria-label="Saved setup review"
        >
          <p>
            {result.job.instrument_id} · {result.job.status} ·{" "}
            {new Date(result.job.created_at).toLocaleString()}
          </p>
          {result.assessment && (
            <>
              <p>
                Setup verdict: <strong>{result.assessment.decision}</strong> ·
                buy {result.assessment.quantity}
              </p>
              <p>{result.assessment.rationale}</p>
              <ul>
                {result.assessment.limitations.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
              <p>
                {result.evidence_current
                  ? "Captured setup evidence is still current."
                  : "Captured setup evidence has expired or changed."}
              </p>
            </>
          )}
          {result.job.error && <p>{result.job.error}</p>}
          <p>{result.detail}</p>
          {result.native_artifact_id && (
            <button
              disabled={pending}
              onClick={() =>
                void act(async () => {
                  setOriginal(
                    await api.nativeReport(result.native_artifact_id!),
                  );
                })
              }
            >
              Read original Tauric report
            </button>
          )}
        </article>
      ))}
      <BrokerEntryReviews
        setups={reviews.filter((s) =>
          s.job.instrument_id.startsWith(provider + ":"),
        )}
        onEntry={onEntry}
      />
      {original && (
        <>
          <button onClick={() => setOriginal(null)}>
            Close original report
          </button>
          <NativeReportReader key={original.id} report={original} />
        </>
      )}
    </section>
  );
}
