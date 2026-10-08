import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  type BrokerConnection,
  type BrokerState,
  type BrokerJournal,
} from "../api/client";
import { BrokerSetupReview } from "./BrokerSetupReview";
import { BrokerRoutes } from "./BrokerRoutes";

const label = { oanda: "OANDA", alpaca: "Alpaca" };
const explain = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "The broker action could not be completed.";

export function BrokerExecution() {
  const [state, setState] = useState<BrokerState | null>(null);
  const [journal, setJournal] = useState<BrokerJournal | null>(null);
  const [closing, setClosing] = useState<string | null>(null);
  const [closeRetry, setCloseRetry] = useState<{
    entryId: string;
    body: Parameters<typeof api.closeBrokerPosition>[1];
  } | null>(null);
  const [connections, setConnections] = useState<
    Partial<Record<"oanda" | "alpaca", BrokerConnection>>
  >({});
  const [provider, setProvider] = useState<"oanda" | "alpaca">("oanda");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [orderType, setOrderType] = useState<"market" | "limit" | "stop">(
    "market",
  );
  const [history, setHistory] = useState<string>("");
  const [retry, setRetry] = useState<
    Parameters<typeof api.createBrokerOrder>[1] | null
  >(null);
  const policy = state?.policies.find((p) => p.provider === provider);
  const connection = connections[provider];
  const live =
    connection?.environment === "live" ||
    policy?.settings.environment === "live";
  async function load() {
    const [orders, positions] = await Promise.all([
      api.brokers(),
      api.brokerJournal(),
    ]);
    setState(orders);
    setJournal(positions);
  }
  useEffect(() => {
    const read = () => void load().catch((err) => setError(explain(err)));
    read();
    window.addEventListener("tauric-trading-changed", read);
    return () => window.removeEventListener("tauric-trading-changed", read);
  }, []);
  async function act(work: () => Promise<void>) {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await work();
      await load();
    } catch (err) {
      setError(explain(err));
    } finally {
      setPending(false);
    }
  }
  async function check() {
    await act(async () => {
      const result = await api.checkBroker(provider);
      setConnections((all) => ({ ...all, [provider]: result }));
      if (result.error) throw new Error(result.error);
      setNotice(`${label[provider]} account and current positions verified.`);
    });
  }
  async function toggle() {
    await act(async () => {
      await api.saveBrokerPolicy(provider, {
        request_id: crypto.randomUUID(),
        expected_revision: policy?.revision ?? 0,
        enabled: !policy?.settings.enabled,
        no_owner_limits: true,
        live_acknowledged: false,
        reason: policy?.settings.enabled
          ? "Owner stopped new broker orders"
          : "Owner enabled demo broker orders with Tauric review available",
      });
      setNotice(
        policy?.settings.enabled
          ? "New broker entries paused. Position protection and exits continue."
          : "Demo broker orders enabled. Broker buying-power and instrument rules apply.",
      );
    });
  }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const body: Parameters<typeof api.createBrokerOrder>[1] = {
      request_id: crypto.randomUUID(),
      expected_connection_revision: connection?.connection_revision ?? 0,
      symbol: String(form.get("symbol")).trim().toUpperCase(),
      side: String(form.get("side")) as "buy" | "sell",
      quantity: String(form.get("quantity")),
      order_type: orderType,
      time_in_force:
        provider === "oanda" && orderType === "market"
          ? "fok"
          : (String(form.get("time_in_force")) as "day" | "gtc"),
      position_effect: String(form.get("position_effect")) as "open" | "reduce",
      reason: String(form.get("reason")),
      limit_price:
        orderType === "limit" ? String(form.get("entry_price")) : null,
      stop_price: orderType === "stop" ? String(form.get("entry_price")) : null,
      stop_loss: String(form.get("stop_loss") ?? "") || null,
      take_profit: String(form.get("take_profit") ?? "") || null,
    };
    setRetry(body);
    await send(body);
  }
  async function send(body: Parameters<typeof api.createBrokerOrder>[1]) {
    await act(async () => {
      const result = await api.createBrokerOrder(provider, body);
      setRetry(null);
      setNotice(`Broker order ${result.status}. ${result.detail}`);
    });
  }
  async function sendClose(request: NonNullable<typeof closeRetry>) {
    await act(async () => {
      const result = await api.closeBrokerPosition(
        request.entryId,
        request.body,
      );
      setCloseRetry(null);
      setClosing(null);
      setNotice(`Position close ${result.status}. ${result.detail}`);
    });
  }
  async function submitClose(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!closing) return;
    const form = new FormData(e.currentTarget);
    const request = {
      entryId: closing,
      body: {
        request_id: crypto.randomUUID(),
        quantity: String(form.get("close_quantity")),
        reason: String(form.get("close_reason")),
      },
    };
    setCloseRetry(request);
    await sendClose(request);
  }
  return (
    <details className="paper-card">
      <summary>Broker trading and position protection</summary>
      <p>
        Inspect actual fills, protection and attributed exits for OANDA and
        Alpaca. Automatic routes check completed candles, calculate size from
        equity and the strategy stop, then require native Tauric approval and
        OpenClaw review.
      </p>
      <div className="paper-actions">
        <label>
          Broker
          <select
            aria-label="Broker"
            value={provider}
            disabled={pending || !!retry}
            onChange={(e) => {
              setProvider(e.target.value as "oanda" | "alpaca");
              setOrderType("market");
              setNotice("");
              setError("");
            }}
          >
            <option value="oanda">OANDA</option>
            <option value="alpaca">Alpaca</option>
          </select>
        </label>
        <button
          className="secondary"
          disabled={pending}
          onClick={() => void check()}
        >
          Check account & positions
        </button>
        <button
          className="secondary"
          disabled={
            pending ||
            (live && !policy?.settings.enabled) ||
            (!policy?.settings.enabled && !connection?.account?.trading_enabled)
          }
          onClick={() => void toggle()}
        >
          {policy?.settings.enabled
            ? "Pause new entries"
            : live
              ? "Activate through forward validation"
              : "Enable demo orders"}
        </button>
        <button
          className="secondary"
          disabled={pending}
          onClick={() => void act(load)}
        >
          Refresh orders
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {connection?.account && (
        <>
          <p>
            {label[provider]} · {connection.account.currency} · cash{" "}
            {connection.account.cash} · equity {connection.account.equity} ·{" "}
            {provider === "oanda" ? "margin available" : "buying power"}{" "}
            {connection.account.buying_power}
          </p>
          <p>
            Checked {new Date(connection.account.observed_at).toLocaleString()}.{" "}
            {policy?.settings.enabled
              ? `${connection.environment} execution enabled · Tauric governs reviewed entries.`
              : "New orders disabled."}
          </p>
          {(connection.positions ?? []).length ? (
            <table>
              <thead>
                <tr>
                  <th>Existing broker position</th>
                  <th>Side</th>
                  <th>Quantity</th>
                  <th>Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {(connection.positions ?? []).map((p) => (
                  <tr key={`${p.symbol}-${p.side}`}>
                    <td>{p.symbol}</td>
                    <td>{p.side}</td>
                    <td>{p.quantity}</td>
                    <td>{p.unrealized_pnl}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p>No open broker positions.</p>
          )}
        </>
      )}
      {live ? (
        <p>
          Live entries use the reviewed strategy routes below. Each requires
          Tauric approval, bounded OpenClaw review and verified protective
          stops.
        </p>
      ) : (
        <form className="paper-form-grid" onSubmit={(e) => void submit(e)}>
          <label>
            Instrument
            <input
              name="symbol"
              required
              placeholder={provider === "oanda" ? "EUR_USD" : "AAPL"}
            />
          </label>
          <label>
            Side
            <select name="side" aria-label="Side">
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </label>
          <label>
            Quantity
            <input name="quantity" required inputMode="decimal" />
          </label>
          <label>
            Order type
            <select
              aria-label="Order type"
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as typeof orderType)}
            >
              <option value="market">Market</option>
              <option value="limit">Limit</option>
              <option value="stop">Stop entry</option>
            </select>
          </label>
          {orderType !== "market" && (
            <label>
              Entry price
              <input name="entry_price" required inputMode="decimal" />
            </label>
          )}
          <label>
            Position action
            <select name="position_effect" aria-label="Position action">
              <option value="open">Open / add</option>
              <option value="reduce">Reduce existing position</option>
            </select>
          </label>
          <label>
            Duration
            <select name="time_in_force" aria-label="Duration">
              <option value="gtc">Until cancelled</option>
              {provider === "alpaca" && <option value="day">Day</option>}
            </select>
          </label>
          {orderType !== "stop" && (
            <>
              <label>
                Stop loss (optional)
                <input name="stop_loss" inputMode="decimal" />
              </label>
              <label>
                Take profit (optional)
                <input name="take_profit" inputMode="decimal" />
              </label>
            </>
          )}
          <label>
            Reason
            <input name="reason" required minLength={3} />
          </label>
          <button
            disabled={
              pending ||
              !!retry ||
              !policy?.settings.enabled ||
              !connection?.account?.trading_enabled
            }
            type="submit"
          >
            Submit to {label[provider]}
          </button>
        </form>
      )}
      {retry && (
        <p>
          A response was not received.{" "}
          <button disabled={pending} onClick={() => void send(retry)}>
            Recover this same request
          </button>{" "}
          Reusing its request ID prevents another submission.
        </p>
      )}
      {state?.orders.length ? (
        <table>
          <thead>
            <tr>
              <th>Broker / instrument</th>
              <th>State</th>
              <th>Filled quantity / price</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {state.orders.map((order) => (
              <tr key={order.id}>
                <td>
                  {label[order.provider]} · {String(order.request.symbol)}
                  <br />
                  <small>{order.client_order_id}</small>
                </td>
                <td>
                  {order.status}
                  <br />
                  <small>{order.detail}</small>
                </td>
                <td>
                  {order.latest_order?.filled_quantity ?? "0"} /{" "}
                  {order.latest_order?.average_fill_price ?? "—"}
                </td>
                <td>
                  <button
                    disabled={pending}
                    onClick={() =>
                      void act(async () => {
                        await api.reconcileBrokerOrder(order.id);
                      })
                    }
                  >
                    Reconcile
                  </button>
                  <button
                    disabled={
                      pending ||
                      ["filled", "cancelled", "rejected", "expired"].includes(
                        order.status,
                      )
                    }
                    onClick={() =>
                      void act(async () => {
                        await api.cancelBrokerOrder(order.id);
                      })
                    }
                  >
                    Cancel
                  </button>
                  <button
                    disabled={pending}
                    onClick={() =>
                      void act(async () => {
                        const rows = await api.brokerOrderHistory(order.id);
                        setHistory(
                          rows
                            .map(
                              (r) =>
                                `${new Date(r.created_at).toLocaleString()} · ${r.kind} · ${String(r.payload.detail)}`,
                            )
                            .join("\n"),
                        );
                      })
                    }
                  >
                    History
                  </button>
                  {order.request.position_effect === "open" &&
                    Number(order.latest_order?.filled_quantity ?? "0") > 0 && (
                      <button
                        disabled={pending}
                        onClick={() =>
                          void act(async () => {
                            await api.refreshBrokerPosition(order.id);
                          })
                        }
                      >
                        Check position
                      </button>
                    )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No broker orders recorded.</p>
      )}
      <h3>Broker position journal</h3>
      <p>
        Entry and exit quantities come from broker executions. Holdings include
        positions opened outside Tauric.
      </p>
      {journal?.positions.length ? (
        journal.positions.map((position) => (
          <article className="paper-card" key={position.entry_intent_id}>
            <h4>
              {label[position.provider]} · {position.symbol} · {position.side}
            </h4>
            <p>
              {position.status} · entered {position.entry_quantity} · closed{" "}
              {position.closed_quantity} · remaining{" "}
              {position.remaining_quantity}
            </p>
            <p>
              Broker holding {position.broker_holding_quantity} · coverage{" "}
              {position.holding_check} · checked{" "}
              {new Date(position.observed_at).toLocaleString()}
            </p>
            <p
              className={
                position.protection_status === "unprotected" ||
                position.protection_status === "incomplete"
                  ? "error"
                  : "muted"
              }
            >
              Protective stop: {position.protection_status.replaceAll("_", " ")}
            </p>
            <p>
              {position.provider === "oanda"
                ? `Broker realized P&L ${position.broker_realized_pnl_home ?? "—"} ${position.currency} · financing ${position.broker_financing_home ?? "—"} ${position.currency}`
                : `Attributed gross P&L ${position.realized_gross_pnl ?? "—"} ${position.currency}`}
            </p>
            <p>{position.detail}</p>
            {(position.protections ?? []).map((protection) => (
              <p key={protection.broker_order_id}>
                {protection.kind.replaceAll("_", " ")} · {protection.status} ·
                price {protection.price ?? "—"} · filled{" "}
                {protection.filled_quantity}
              </p>
            ))}
            <p>
              {(position.exit_fills ?? []).length} actual exit executions
              recorded.
            </p>
            <button
              disabled={pending}
              onClick={() =>
                void act(async () => {
                  await api.refreshBrokerPosition(position.entry_intent_id);
                })
              }
            >
              Refresh position
            </button>
            <button
              disabled={
                pending ||
                !!closeRetry ||
                position.holding_check !== "covered" ||
                !!journal.close_requests.find(
                  (request) =>
                    request.entry_intent_id === position.entry_intent_id &&
                    !["filled", "rejected"].includes(request.status),
                )
              }
              onClick={() => setClosing(position.entry_intent_id)}
            >
              Close quantity
            </button>
            {closing === position.entry_intent_id && (
              <form
                className="paper-form-grid"
                onSubmit={(e) => void submitClose(e)}
              >
                {position.provider === "alpaca" &&
                  (position.protections ?? []).some(
                    (protection) =>
                      !["filled", "cancelled", "expired", "rejected"].includes(
                        protection.status,
                      ),
                  ) && (
                    <p>
                      Closing cancels this entry’s attached protection first.
                      Tauric restores and verifies protection for any remaining
                      shares before marking the close complete.
                    </p>
                  )}
                <label>
                  Close quantity
                  <input
                    name="close_quantity"
                    required
                    inputMode="decimal"
                    defaultValue={position.remaining_quantity}
                  />
                </label>
                <label>
                  Close reason
                  <input name="close_reason" required minLength={3} />
                </label>
                <button type="submit" disabled={pending || !!closeRetry}>
                  Submit position close
                </button>
              </form>
            )}
          </article>
        ))
      ) : (
        <p>No attributed broker positions recorded.</p>
      )}
      {closeRetry && (
        <p>
          A close response was not received.{" "}
          <button disabled={pending} onClick={() => void sendClose(closeRetry)}>
            Recover this same close request
          </button>
        </p>
      )}
      {journal?.close_requests.map((request) => (
        <p key={request.id}>
          Close {request.quantity} · {request.status} · {request.detail}{" "}
          {!["filled", "rejected"].includes(request.status) && (
            <>
              <button
                disabled={pending}
                onClick={() =>
                  void act(async () => {
                    await api.reconcileBrokerClose(request.id);
                  })
                }
              >
                Reconcile close
              </button>
              {request.status === "unknown" &&
                journal.positions.find(
                  (p) => p.entry_intent_id === request.entry_intent_id,
                )?.protection_status === "unprotected" && (
                  <button
                    disabled={pending}
                    onClick={() =>
                      void act(async () => {
                        const result = await api.repairBrokerProtection(
                          request.id,
                          {
                            request_id: crypto.randomUUID(),
                            reason:
                              "Owner repaired confirmed failed remainder protection",
                          },
                        );
                        setNotice(
                          `Protection recovery ${result.status}. ${result.detail}`,
                        );
                      })
                    }
                  >
                    Repair confirmed failed protection
                  </button>
                )}
            </>
          )}
        </p>
      ))}
      {history && <pre style={{ whiteSpace: "pre-wrap" }}>{history}</pre>}
      <BrokerRoutes provider={provider} onEntry={load} />
      <BrokerSetupReview provider={provider} onEntry={load} />
    </details>
  );
}
