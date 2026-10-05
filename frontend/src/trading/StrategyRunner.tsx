import { api, type PaperState, type StrategyRun } from "../api/client";
import { usePaper } from "./PaperTrading";

export function StrategyRunner({
  deployment,
  run,
  open,
}: {
  deployment: PaperState["deployments"][number];
  run: StrategyRun | undefined;
  open: (type: string, id: string) => void;
}) {
  const { state, act, pending } = usePaper();
  const current =
    run && new Date(String(run.payload.expires_at)).getTime() > Date.now();
  const positionOpen = state?.positions.some(
    (p) => p.id === run?.payload.position_id,
  );
  const proposed = state?.records.some(
    (r) =>
      r.kind === "proposal" &&
      r.deployment_id === deployment.id &&
      r.payload.strategy_signal_key === run?.payload.signal_key,
  );
  const history =
    state?.strategy_runs
      .filter((r) => r.deployment_id === deployment.id)
      .slice(0, 10) ?? [];
  return (
    <div className="strategy-runner">
      <p className="muted">
        Rules use closed candles. Entry signals require review and sizing; exit
        signals close only this route’s attributed position at the current paper
        price. Enable a monitoring schedule for background checks, or evaluate
        now.
      </p>
      <button
        className="secondary small"
        type="button"
        disabled={pending}
        onClick={() =>
          void act(
            `rules-${deployment.id}-${deployment.control_revision}`,
            (id) =>
              api.runStrategy({
                request_id: id,
                deployment_id: deployment.id,
                reason: "Owner requested saved paper strategy evaluation",
              }),
            "Strategy rules evaluated. Inspect the signal and evidence below.",
          )
        }
      >
        Evaluate strategy rules
      </button>
      {run && (
        <div aria-label="Strategy rule result" role="region" aria-live="polite">
          <p>
            <strong>{run.action.toUpperCase()}</strong> ·{" "}
            {current ? "Current signal" : "Expired — evaluate again"}
          </p>
          <p>{(run.payload.reasons as string[]).join(" ")}</p>
          {run.action === "enter" && proposed && (
            <p className="muted">
              This candle already has a recorded proposal. Choose it under
              Review the proposal.
            </p>
          )}
          {!!run.payload.bar_close_time && (
            <p className="muted">
              Candle closed{" "}
              {new Date(String(run.payload.bar_close_time)).toLocaleString(
                "en-GB",
                { timeZone: "Europe/London" },
              )}{" "}
              · signal expires{" "}
              {new Date(String(run.payload.expires_at)).toLocaleTimeString(
                "en-GB",
                { timeZone: "Europe/London" },
              )}{" "}
              London
            </p>
          )}
          <button
            className="secondary small"
            type="button"
            onClick={() => open("strategy_run", run.id)}
          >
            Inspect rule evidence
          </button>
          {run.action === "exit" && (
            <button
              className="primary small"
              type="button"
              disabled={pending || !current || !positionOpen}
              onClick={() =>
                void act(
                  `rule-exit-${run.id}`,
                  async (id) => {
                    await api.refreshMarket(deployment.instrument_id, "1m");
                    return api.closeStrategySignal(run.id, {
                      request_id: id,
                      reason:
                        "Owner applied the saved paper strategy exit signal",
                    });
                  },
                  "Rule exit applied to the attributed paper position.",
                )
              }
            >
              Close signalled paper position
            </button>
          )}
        </div>
      )}
      {!!history.length && (
        <details className="capital-history">
          <summary>Rule evaluation history</summary>
          {history.map((r) => (
            <div key={r.id}>
              <strong>
                {r.action.toUpperCase()} ·{" "}
                {new Date(r.created_at).toLocaleString("en-GB", {
                  timeZone: "Europe/London",
                })}
              </strong>
              <span>{(r.payload.reasons as string[]).join(" ")}</span>
              <button
                type="button"
                className="secondary small"
                onClick={() => open("strategy_run", r.id)}
              >
                Inspect evaluation
              </button>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}
