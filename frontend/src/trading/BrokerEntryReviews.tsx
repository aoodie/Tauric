import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type BrokerReview,
  type BrokerSetup,
} from "../api/client";
const explain = (e: unknown) =>
  e instanceof Error
    ? e.message
    : "The demo entry review could not be completed.";
type ReviewRequest = Parameters<typeof api.requestBrokerReview>[0];
type EntryRequest = Parameters<typeof api.submitReviewedBrokerEntry>[1];
export function BrokerEntryReviews({
  setups,
  onEntry,
}: {
  setups: BrokerSetup[];
  onEntry: () => Promise<void>;
}) {
  const [reviews, setReviews] = useState<BrokerReview[]>([]);
  const [profile, setProfile] = useState<"cloud" | "local">("cloud");
  const [routes, setRoutes] = useState<
    { id: "cloud" | "local"; ready: boolean }[]
  >([]);
  const [automatic, setAutomatic] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState<ReviewRequest | null>(null);
  const [entryRetry, setEntryRetry] = useState<{
    id: string;
    body: EntryRequest;
  } | null>(null);
  const delivered = useRef(new Set<string>());
  const entryCallback = useRef(onEntry);
  entryCallback.current = onEntry;
  async function load() {
    const [saved, status] = await Promise.all([
      api.brokerReviews(),
      api.governance(),
    ]);
    setReviews(saved);
    setRoutes(status.routes);
  }
  useEffect(() => {
    let mounted = true;
    void Promise.all([api.brokerReviews(), api.governance()])
      .then(([saved, status]) => {
        if (!mounted) return;
        setReviews(saved);
        setRoutes(status.routes);
        setProfile(status.routes.find((r) => r.ready)?.id ?? "cloud");
      })
      .catch((err) => {
        if (mounted) setError(explain(err));
      });
    return () => {
      mounted = false;
    };
  }, []);
  useEffect(() => {
    if (
      !reviews.some(
        (r) =>
          ["queued", "running"].includes(r.status) ||
          (r.submit_on_allow &&
            r.decision?.outcome === "allow" &&
            !r.intent_id),
      )
    )
      return;
    let mounted = true;
    const timer = window.setInterval(() => {
      void api
        .brokerReviews()
        .then((saved) => {
          if (mounted) setReviews(saved);
        })
        .catch((err) => {
          if (mounted) setError(explain(err));
        });
    }, 3000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [reviews]);
  useEffect(() => {
    const added = reviews.flatMap((r) =>
      r.intent_id && !delivered.current.has(r.intent_id) ? [r.intent_id] : [],
    );
    if (!added.length) return;
    added.forEach((id) => delivered.current.add(id));
    void entryCallback.current().catch((err) => setError(explain(err)));
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
  async function request(body: ReviewRequest) {
    setRetry(body);
    await act(async () => {
      try {
        const job = await api.requestBrokerReview(body);
        setRetry(null);
        setNotice(
          `OpenClaw review requested. ${job.submit_on_allow ? "One exact demo entry will be submitted if allowed and still valid." : "Review only; the entry requires a separate submit action."}`,
        );
        await load();
      } catch (err) {
        if (err instanceof ApiError && err.status >= 400 && err.status < 500)
          setRetry(null);
        throw err;
      }
    });
  }
  async function submit(id: string, body: EntryRequest) {
    setEntryRetry({ id, body });
    await act(async () => {
      try {
        const intent = await api.submitReviewedBrokerEntry(id, body);
        setEntryRetry(null);
        setNotice(`Demo entry ${intent.status}. ${intent.detail}`);
        await load();
        await onEntry();
      } catch (err) {
        if (err instanceof ApiError && err.status >= 400 && err.status < 500)
          setEntryRetry(null);
        throw err;
      }
    });
  }
  const activeSetups = setups.filter(
    (s) =>
      s.evidence_current &&
      s.assessment?.decision === "approve" &&
      !reviews.some(
        (r) =>
          r.assessment_job_id === s.job.id &&
          (["queued", "running"].includes(r.status) || !!r.decision),
      ),
  );
  const blocked = pending || !!retry || !!entryRetry;
  const related = reviews.filter((r) =>
    setups.some((s) => s.job.id === r.assessment_job_id),
  );
  return (
    <section className="paper-card" aria-label="OpenClaw demo entry reviews">
      <h4>OpenClaw demo entry approval</h4>
      <p>
        After Tauric approves the exact setup, OpenClaw can allow, reject or
        defer that entry. An allow covers one order with the reviewed quantity,
        stop and target. Your no-limits choice applies; broker restrictions and
        fresh evidence checks remain.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <label>
        OpenClaw review connection{" "}
        <select
          aria-label="OpenClaw review connection"
          value={profile}
          disabled={blocked}
          onChange={(e) => setProfile(e.target.value as "cloud" | "local")}
        >
          {(["cloud", "local"] as const).map((id) => (
            <option key={id} value={id}>
              {id === "cloud" ? "Cloud" : "Local"}
              {routes.find((r) => r.id === id)?.ready ? "" : " · unavailable"}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          aria-label="Submit demo entry automatically if allowed"
          checked={automatic}
          disabled={blocked}
          onChange={(e) => setAutomatic(e.target.checked)}
        />{" "}
        Submit this exact demo entry automatically if allowed
      </label>
      <p>
        This applies to the selected setup only. Scheduled discovery of new
        trades is still being completed. Reviewing uses your configured AI
        connection and may incur charges.
      </p>
      {!activeSetups.length && (
        <p>No current native-approved setup is awaiting an OpenClaw review.</p>
      )}
      {activeSetups.map((s) => (
        <div key={s.job.id}>
          <p>
            {s.job.instrument_id} · buy {s.assessment?.quantity}
          </p>
          <button
            disabled={blocked || !routes.find((r) => r.id === profile)?.ready}
            onClick={() =>
              void request({
                request_id: crypto.randomUUID(),
                assessment_job_id: s.job.id,
                profile,
                submit_on_allow: automatic,
                reason: automatic
                  ? "Owner requested OpenClaw review and one exact allowed demo entry"
                  : "Owner requested OpenClaw demo setup review only",
              })
            }
          >
            {automatic
              ? "Review and submit demo entry if allowed"
              : "Request OpenClaw demo review"}
          </button>
        </div>
      ))}
      {retry && (
        <p>
          A review response was not received.{" "}
          <button disabled={pending} onClick={() => void request(retry)}>
            Recover this same OpenClaw review
          </button>
        </p>
      )}
      {entryRetry && (
        <p>
          An entry response was not received.{" "}
          <button
            disabled={pending}
            onClick={() => void submit(entryRetry.id, entryRetry.body)}
          >
            Recover this same reviewed entry
          </button>
        </p>
      )}
      <button
        className="secondary"
        disabled={pending}
        onClick={() => void act(load)}
      >
        Refresh OpenClaw demo reviews
      </button>
      {related.map((r) => (
        <article
          className="paper-card"
          key={r.id}
          aria-label="Saved OpenClaw demo review"
        >
          <p>
            OpenClaw · {r.status} · {r.profile} · {r.model}
          </p>
          <p>
            {r.submit_on_allow
              ? "Automatic delivery requested for this entry."
              : "Review only."}
          </p>
          {r.error && <p>{r.error}</p>}
          {r.decision && (
            <>
              <p>
                OpenClaw verdict: <strong>{r.decision.outcome}</strong>
              </p>
              <p>{r.decision.reason}</p>
              <p>
                Buy {r.decision.approved_order.quantity}{" "}
                {r.decision.approved_order.symbol} · stop{" "}
                {r.decision.approved_order.stop_loss} · target{" "}
                {r.decision.approved_order.take_profit}
              </p>
              <p>
                Valid until {new Date(r.decision.expires_at).toLocaleString()}.
                The server checks current evidence before submission.
              </p>
              {r.intent_id ? (
                <p>
                  Linked demo entry: {r.intent_id}. Its status, fills and
                  position appear in the broker orders and journal.
                </p>
              ) : (
                r.decision.outcome === "allow" &&
                !r.submit_on_allow && (
                  <button
                    disabled={
                      blocked ||
                      new Date(r.decision.expires_at).getTime() <= Date.now()
                    }
                    onClick={() =>
                      void submit(r.id, {
                        request_id: crypto.randomUUID(),
                        reason:
                          "Owner submitted the exact OpenClaw-approved demo entry",
                      })
                    }
                  >
                    Submit this approved demo entry
                  </button>
                )
              )}
            </>
          )}
          {r.usage && (
            <p>
              Reported tokens: {String(r.usage.total_tokens ?? "unavailable")}
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
