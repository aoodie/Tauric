import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";

type Receipt = components["schemas"]["AssistantReceipt"];
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];

export function AssistantReceiptCard({ receipt }: { receipt: Receipt }) {
  const proposal = receipt.operation === "prepare_upgrade";
  const note = proposal || receipt.operation === "save_research_note";
  const result = receipt.result;
  const patch = typeof result.patch === "string" ? result.patch : "";
  const [copyNotice, setCopyNotice] = useState("");
  return (
    <article className="assistant-receipt" aria-label={receipt.title}>
      <header>
        <strong>{receipt.title}</strong>
        <span className={`badge ${proposal ? "" : "cyan"}`}>
          {proposal ? "Review required" : "Saved"}
        </span>
      </header>
      <p className="muted">{new Date(receipt.created_at).toLocaleString()}</p>
      {note && (
        <>
          <p>{String(result.hypothesis ?? "")}</p>
          <details>
            <summary>Evidence and counter-evidence</summary>
            <p>Evidence</p>
            <ul>
              {strings(result.evidence).map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
            <p>Counter-evidence</p>
            <ul>
              {strings(result.counter_evidence).map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
          </details>
          <p>Planned tests</p>
          <ol className="planned-tests">
            {strings(result.test_plan).map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ol>
        </>
      )}
      {proposal && (
        <>
          <p className="workflow-authority">
            Draft for your review. Code has not been applied and planned tests
            have not been run.
          </p>
          <ul className="proposal-files">
            {(Array.isArray(result.files) ? result.files : []).map((v, i) => (
              <li key={i}>
                <code>{String((v as { path?: string }).path ?? "")}</code>
              </li>
            ))}
          </ul>
          <ul>
            {strings(result.changes).map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
          {patch && (
            <details>
              <summary>Proposed code changes</summary>
              <pre>{patch}</pre>
              <button
                className="secondary small"
                onClick={() =>
                  void navigator.clipboard
                    .writeText(patch)
                    .then(() => setCopyNotice("Patch copied."))
                    .catch(() =>
                      setCopyNotice("Select the patch text to copy it."),
                    )
                }
              >
                Copy patch
              </button>
              {copyNotice && <p role="status">{copyNotice}</p>}
            </details>
          )}
        </>
      )}
      <details>
        <summary>Saved receipt details</summary>
        <p className="muted">Receipt {receipt.id}</p>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </article>
  );
}

export function AssistantControls({
  draft,
}: {
  draft: (value: string) => void;
}) {
  const [state, setState] =
    useState<Awaited<ReturnType<typeof api.assistant>>>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const panel = useRef<HTMLDetailsElement>(null);
  function prepare(value: string) {
    draft(value);
    if (panel.current) panel.current.open = false;
    document
      .getElementById("openclaw-message")
      ?.scrollIntoView({ block: "nearest" });
    document.getElementById("openclaw-message")?.focus({ preventScroll: true });
  }
  async function reload() {
    setPending(true);
    try {
      setState(await api.assistant());
      setError("");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Action history is unavailable.",
      );
    } finally {
      setPending(false);
    }
  }
  useEffect(() => {
    let active = true;
    void api
      .assistant()
      .then((value) => {
        if (active) setState(value);
      })
      .catch((e) => {
        if (active)
          setError(
            e instanceof Error ? e.message : "Action history is unavailable.",
          );
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <details className="assistant-controls" ref={panel}>
      <summary>Project controls &amp; action history</summary>
      <div className="assistant-control-heading">
        <strong>OpenClaw manages research. Tauric controls approvals.</strong>
        <button
          className="secondary small"
          disabled={pending}
          onClick={() => void reload()}
        >
          {pending ? "Refreshing…" : "Refresh history"}
        </button>
      </div>
      <p className="muted">
        Ask OpenClaw to manage scans, save strategy versions, run backtests, or
        prepare a code upgrade for review.
      </p>
      <div className="assistant-actions">
        <button
          className="secondary small"
          onClick={() =>
            prepare(
              "Audit my scanners and suggest a simpler configuration. Preserve my current automation settings and research allowances.",
            )
          }
        >
          Review scanners
        </button>
        <button
          className="secondary small"
          onClick={() =>
            prepare(
              "Review my strategies. Save a quant research note with a falsifiable hypothesis, baseline, evidence, counter-evidence and test plan. Leave deployed routes unchanged.",
            )
          }
        >
          Develop a strategy
        </button>
        <button
          className="secondary small"
          onClick={() =>
            prepare(
              "Inspect the project source and prepare a concrete code upgrade proposal for my review, including current file checksums, proposed changes and planned tests. Keep Tauric's evidence and approval checks authoritative.",
            )
          }
        >
          Prepare an upgrade
        </button>
      </div>
      <p className="muted">
        These buttons prepare a message. Send it when you are ready.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {state?.receipts.map((receipt) => (
        <AssistantReceiptCard key={receipt.id} receipt={receipt} />
      ))}
      {state && !state.receipts.length && (
        <p className="empty-copy">
          Saved actions, research notes and upgrade drafts will appear here.
        </p>
      )}
    </details>
  );
}
