import { connectionLabel } from "../settings/aiLabels";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  MessageSquare,
  Plus,
  Send,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { api, type Resource } from "../api/client";
import type { components } from "../api/schema";
import { PortfolioPanel, PortfolioSelector } from "./Portfolios";
import "./chat.css";
import { AssistantControls, AssistantReceiptCard } from "./AssistantControls";
import { ReportText } from "../research/NativeReport";

type Config = components["schemas"]["ChatConfigView"];
type Change = components["schemas"]["ChatConfigChange"];
type Turn = components["schemas"]["ChatTurnView"];
type Conversation = components["schemas"]["ConversationView"];
const message = (e: unknown) =>
  e instanceof Error ? e.message : "Chat is unavailable.";

export function ChatConnections() {
  const [config, setConfig] = useState<Config>();
  const [draft, setDraft] = useState<Change>();
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  function apply(value: Config) {
    setConfig(value);
    setDraft({
      expected_revision: value.revision,
      profile: value.profile,
      daily_research_allowance: value.daily_research_allowance,
      telegram: {
        enabled: value.telegram.enabled,
        owner_id: value.telegram.owner_id,
        token_action: "keep",
        token: "",
      },
      discord: {
        enabled: value.discord.enabled,
        owner_id: value.discord.owner_id,
        token_action: "keep",
        token: "",
      },
    });
  }
  useEffect(() => {
    let active = true;
    api
      .chatSettings()
      .then((value) => {
        if (active) apply(value);
      })
      .catch((e) => {
        if (active) setError(message(e));
      });
    return () => {
      active = false;
    };
  }, []);
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      apply(await api.saveChatSettings(draft));
      setSaved(true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="chat-connections">
      <h2>OpenClaw chat connections</h2>
      <p className="muted">
        Use the same assistant on the web, Telegram and Discord. Each keeps its
        own conversation history. Bot tokens are stored encrypted and never
        displayed again.
      </p>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
      {saved && (
        <p role="status" className="success-banner">
          Saved. OpenClaw applies the connections shortly. Send your bot a DM to
          check delivery.
        </p>
      )}
      {draft && config && (
        <form onSubmit={save}>
          <label className="chat-field">
            AI connection
            <select
              aria-label="Chat AI connection"
              value={draft.profile}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  profile: e.target.value as Change["profile"],
                })
              }
            >
              <option value="local">{connectionLabel("local")}</option>
              <option value="cloud">{connectionLabel("cloud")}</option>
            </select>
          </label>
          <p className="muted">
            Uses the model and reply budgets selected in AI connections →
            OpenClaw review settings. Web chat{" "}
            {config.gateway_ready && config.worker_ready && config.route_ready
              ? "is ready"
              : "is waiting for its model or services"}
            .
          </p>
          <label className="chat-field">
            New research runs per UTC day
            <input
              aria-label="Chat daily research allowance"
              type="number"
              min={0}
              max={50}
              required
              value={draft.daily_research_allowance}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  daily_research_allowance: Number(e.target.value),
                })
              }
            />
          </label>
          <p className="muted">
            {config.research_used_today} runs used today. Web, Telegram and
            Discord share this allowance. Matching requests reuse the same daily
            run. Set 0 to stop new chat research. This limits runs, not monetary
            cost.
          </p>
          <div className="chat-channel-grid">
            {(["telegram", "discord"] as const).map((key) => (
              <fieldset key={key} disabled={busy} className="chat-channel">
                <legend>{key === "telegram" ? "Telegram" : "Discord"}</legend>
                <p className="muted">{config[key].detail}</p>
                <label className="chat-check">
                  <input
                    type="checkbox"
                    checked={draft[key].enabled}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        [key]: { ...draft[key], enabled: e.target.checked },
                      })
                    }
                  />
                  Enable {key === "telegram" ? "Telegram" : "Discord"} owner DMs
                </label>
                <label className="chat-field">
                  Your numeric user ID
                  <input
                    aria-label={`${key} owner user ID`}
                    inputMode="numeric"
                    value={draft[key].owner_id}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        [key]: { ...draft[key], owner_id: e.target.value },
                      })
                    }
                  />
                </label>
                <label className="chat-field">
                  Bot token
                  <select
                    aria-label={`${key} token action`}
                    value={draft[key].token_action}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        [key]: {
                          ...draft[key],
                          token_action: e.target.value as
                            "keep" | "replace" | "clear",
                          token: "",
                        },
                      })
                    }
                  >
                    <option value="keep">
                      {config[key].has_token
                        ? "Keep saved token"
                        : "No token saved"}
                    </option>
                    <option value="replace">Enter new token</option>
                    <option value="clear">Clear saved token</option>
                  </select>
                </label>
                {draft[key].token_action === "replace" && (
                  <label className="chat-field">
                    New bot token
                    <input
                      aria-label={`${key} new bot token`}
                      type="password"
                      autoComplete="off"
                      value={draft[key].token}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          [key]: { ...draft[key], token: e.target.value },
                        })
                      }
                    />
                  </label>
                )}
                {key === "telegram" ? (
                  <p className="muted">
                    Create a bot with Telegram’s @BotFather. Enter its token and
                    your personal numeric Telegram ID, then DM your bot. Use a
                    dedicated bot; another polling service must not use its
                    token.
                  </p>
                ) : (
                  <p className="muted">
                    Create a bot in the Discord Developer Portal, enable Message
                    Content Intent, and invite it to your server with the bot
                    scope. In Discord enable Developer Mode, right-click your
                    own profile → Copy User ID, then DM the bot. Server channel
                    messages are disabled.
                  </p>
                )}
              </fieldset>
            ))}
          </div>
          <p className="muted">
            <ShieldCheck size={14} /> Only your configured IDs can chat. Group
            messages and remote configuration commands are disabled.
          </p>
          <button
            type="submit"
            className="primary"
            disabled={busy || !config.storage_ready}
          >
            {busy ? "Saving…" : "Save chat connections"}
          </button>
        </form>
      )}
    </section>
  );
}

export function OpenClawChat(props: {
  initialDraft?: string;
  setup: () => void;
  open: (type: Resource["resource"]["type"], id: string) => void;
}) {
  const [portfolios, setPortfolios] = useState<
    components["schemas"]["PortfolioView"][]
  >([]);
  const [portfolioId, setPortfolioId] = useState(
    () => new URLSearchParams(location.search).get("portfolio") ?? "",
  );
  const [error, setError] = useState("");
  const refresh = () => {
    void api
      .portfolios()
      .then(setPortfolios)
      .catch((e) => setError(message(e)));
  };
  useEffect(refresh, []);
  return (
    <ConversationChat
      key={portfolioId || "general"}
      {...props}
      portfolioId={portfolioId}
      picker={
        <>
          <PortfolioSelector
            value={portfolioId}
            portfolios={portfolios}
            onChange={(id) => {
              const url = new URL(location.href);
              if (id) url.searchParams.set("portfolio", id);
              else url.searchParams.delete("portfolio");
              history.replaceState(null, "", url);
              setPortfolioId(id);
            }}
          />
          {error && <p role="alert">{error}</p>}
          {!portfolios.length && !error && (
            <p className="muted">
              Create a portfolio allocation in Trading settings to open a
              portfolio conversation.
            </p>
          )}
          {portfolioId && (
            <PortfolioPanel
              key={portfolioId}
              id={portfolioId}
              portfolios={portfolios}
              changed={refresh}
              open={props.open}
            />
          )}
        </>
      }
    />
  );
}

function ConversationChat({
  setup,
  open,
  portfolioId,
  picker,
  initialDraft,
}: {
  initialDraft?: string;
  setup: () => void;
  open: (type: Resource["resource"]["type"], id: string) => void;
  portfolioId: string;
  picker: import("react").ReactNode;
}) {
  const [config, setConfig] = useState<Config>();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversation, setConversation] = useState<string>(() =>
    crypto.randomUUID(),
  );
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState(initialDraft ?? "");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const request = useRef<
    { id: string; text: string; conversation: string } | undefined
  >(undefined);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const [state, list, items] = await Promise.all([
          api.chatSettings(),
          api.conversations(portfolioId || undefined),
          api.chatTurns(conversation, portfolioId || undefined),
        ]);
        if (active) {
          setConfig(state);
          setConversations(list);
          setTurns(items);
        }
      } catch (e) {
        if (active) setError(message(e));
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [conversation, portfolioId]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, turns.at(-1)?.status]);
  const waiting = turns.some(
    (t) => t.status === "queued" || t.status === "running",
  );
  const ready = !!(
    config?.gateway_ready &&
    config.worker_ready &&
    config.route_ready
  );
  async function send(e: FormEvent) {
    e.preventDefault();
    if (!text.trim() || waiting || sending) return;
    setSending(true);
    setError("");
    if (
      !request.current ||
      request.current.text !== text ||
      request.current.conversation !== conversation
    )
      request.current = { id: crypto.randomUUID(), text, conversation };
    try {
      const row = await api.sendChat({
        request_id: request.current.id,
        conversation_id: conversation,
        portfolio_id: portfolioId || null,
        text,
      });
      setTurns((old) =>
        old.some((t) => t.id === row.id) ? old : [...old, row],
      );
      setText("");
      request.current = undefined;
    } catch (e) {
      setError(message(e));
    } finally {
      setSending(false);
    }
  }
  return (
    <div className="page-scroll chat-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">YOUR TAURIC ASSISTANT</span>
          <h1>OpenClaw</h1>
          <p>
            Manage scans, develop strategies, and prepare project upgrades for
            review.
          </p>
        </div>
        <button className="secondary" onClick={setup}>
          <Settings2 size={15} /> Chat connections
        </button>
      </div>
      {picker}
      {!portfolioId && <AssistantControls draft={setText} />}
      <div className="chat-workspace">
        <aside className="chat-history">
          <button
            className="secondary"
            onClick={() => {
              setConversation(crypto.randomUUID());
              setTurns([]);
              setText("");
              setError("");
              request.current = undefined;
            }}
          >
            <Plus size={15} /> New conversation
          </button>
          <span className="eyebrow">WEB CONVERSATIONS</span>
          {conversations.map((item) => (
            <button
              key={item.id}
              className={item.id === conversation ? "selected" : ""}
              onClick={() => {
                setConversation(item.id);
                setTurns([]);
                setError("");
              }}
            >
              {item.title}
            </button>
          ))}
          <p className="muted">
            Telegram and Discord histories stay in their own channels.
          </p>
        </aside>
        <section className="chat-main" aria-label="OpenClaw conversation">
          <div className="chat-state">
            <MessageSquare size={17} />
            <strong>OpenClaw</strong>
            <span className={`badge ${ready ? "cyan" : ""}`}>
              {ready ? "Ready" : "Waiting for connection"}
            </span>
            <span className="muted">
              {portfolioId
                ? "Portfolio conversation · permitted paper controls"
                : "Project manager & research assistant"}{" "}
              · live trading disabled
            </span>
          </div>
          <div className="chat-messages" role="log" aria-live="polite">
            {!turns.length && (
              <div className="chat-empty">
                <MessageSquare size={32} />
                <h2>What would you like to improve?</h2>
                <p className="muted">
                  {portfolioId
                    ? "Ask about this portfolio or request its research."
                    : "Try “Simplify my scanners”, “Test a strategy idea”, or “Prepare a code upgrade for review”."}
                </p>
              </div>
            )}
            {turns.map((turn) => (
              <div className="chat-turn" key={turn.id}>
                <article className="chat-bubble user">
                  <span className="eyebrow">YOU</span>
                  <p>{turn.user_text}</p>
                </article>
                {(turn.action_receipts ?? []).map((receipt) => (
                  <details className="chat-research" key={receipt.id}>
                    <summary>
                      Saved portfolio action: {receipt.action} ·{" "}
                      {new Date(receipt.created_at).toLocaleString()}
                    </summary>
                    <p>
                      Receipt {receipt.id} · portfolio {receipt.portfolio_id}
                    </p>
                    <pre>{JSON.stringify(receipt.result, null, 2)}</pre>
                  </details>
                ))}
                {(turn.project_receipts ?? []).map((receipt) => (
                  <AssistantReceiptCard key={receipt.id} receipt={receipt} />
                ))}
                <article className="chat-bubble assistant">
                  <span className="eyebrow">OPENCLAW</span>
                  {turn.assistant_text ? (
                    <ReportText>{turn.assistant_text}</ReportText>
                  ) : turn.error ? (
                    <p role="alert">{turn.error}</p>
                  ) : (
                    <p className="muted">
                      {turn.status === "queued"
                        ? "Waiting for OpenClaw…"
                        : "OpenClaw is replying…"}
                    </p>
                  )}
                </article>
                {(turn.research_jobs ?? []).map((job) => (
                  <section
                    className="chat-research"
                    key={job.id}
                    aria-label={`${job.instrument_id} research run`}
                  >
                    <div className="chat-research-heading">
                      <strong>
                        {job.instrument_id.split(":").at(-1)} daily research
                      </strong>
                      <span className="badge cyan">{job.status}</span>
                    </div>
                    <p className="muted">
                      {job.error ||
                        (job.status === "queued"
                          ? "Waiting for the native research worker."
                          : job.status === "running"
                            ? job.progress.length
                              ? `Completed sections: ${job.progress.join(", ")}`
                              : "Native research is running. No completed sections reported yet."
                            : job.status === "succeeded"
                              ? "Original native reports are available."
                              : "This run stopped. No automatic retry was made.")}
                    </p>
                    <div className="chat-research-actions">
                      <button
                        className="secondary"
                        onClick={() => open("analysis", job.id)}
                      >
                        View research run
                      </button>
                      {job.artifact_id && (
                        <button
                          className="secondary"
                          onClick={() => open("report", job.artifact_id!)}
                        >
                          Open original report
                        </button>
                      )}
                    </div>
                  </section>
                ))}
              </div>
            ))}
            <div ref={end} />
          </div>
          {error && (
            <p role="alert" className="error-banner">
              {error}
            </p>
          )}
          <form className="chat-compose" onSubmit={send}>
            <label className="sr-only" htmlFor="openclaw-message">
              Message OpenClaw
            </label>
            <textarea
              id="openclaw-message"
              aria-label="Message OpenClaw"
              placeholder="Ask OpenClaw…"
              maxLength={4000}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={sending}
            />
            <button
              className="primary"
              type="submit"
              disabled={!ready || waiting || sending || !text.trim()}
            >
              <Send size={16} />
              {sending ? "Sending…" : "Send"}
            </button>
          </form>
          <p className="chat-footnote muted">
            Daily research uses the allowance in Chat connections. Chat cannot
            place orders. Do not put passwords or API keys in messages.
          </p>
        </section>
      </div>
    </div>
  );
}
