import { useEffect, useState, type FormEvent } from "react";
import { BookOpen, Copy, Plus, RefreshCw } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type KnowledgeDocument, type MemoryState } from "../api/client";
import { WorkflowTabs } from "../ui/WorkflowTabs";
import "./knowledge.css";

const labels: Record<KnowledgeDocument["kind"], string> = {
  strategy: "Strategies",
  chat: "Chats",
  research: "Research",
  preference: "Preferences",
  wiki: "Wiki notes",
};
const sourceLabels: Record<string, string> = {
  strategy: "Saved strategy",
  chat: "Recorded conversation",
  research: "Saved experiment",
  assistant: "OpenClaw research note",
  obsidian: "Obsidian Inbox",
  note: "Your note",
};
const privateMarkdown = {
  img: ({ alt }: { alt?: string }) => (
    <span className="muted">
      Image reference: {alt || "image"} (automatic loading is off)
    </span>
  ),
};
const explain = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Could not load your knowledge. Try again.";

export function KnowledgePage({
  navigate,
  ask,
}: {
  navigate: (page: string) => void;
  ask: (text: string) => void;
}) {
  const [tab, setTab] = useState("library");
  const [state, setState] = useState<MemoryState>();
  const [controls, setControls] = useState<MemoryState["settings"]>();
  const [items, setItems] = useState<KnowledgeDocument[]>([]);
  const [more, setMore] = useState(false);
  const [selected, setSelected] = useState<KnowledgeDocument>();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<KnowledgeDocument["kind"] | "">("");
  const [hidden, setHidden] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editor, setEditor] = useState<{
    document?: KnowledgeDocument;
    kind: "wiki" | "preference";
  } | null>(null);

  async function loadState() {
    const next = await api.knowledgeSettings();
    setState(next);
    setControls(next.settings);
    return next;
  }
  useEffect(() => {
    let active = true;
    api
      .knowledgeSettings()
      .then((next) => {
        if (active) {
          setState(next);
          setControls(next.settings);
        }
      })
      .catch((err) => {
        if (active) setError(explain(err));
      });
    return () => {
      active = false;
    };
  }, [refresh]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .knowledge({
          query,
          kind: tab === "preferences" ? "preference" : kind || undefined,
          include_hidden: hidden,
        })
        .then((next) => {
          if (active) {
            setItems(next.items);
            setMore(next.has_more);
          }
        })
        .catch((err) => {
          if (active) setError(explain(err));
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query, kind, tab, hidden, refresh]);
  function selectTab(next: string) {
    setTab(next);
    setSelected(undefined);
    setEditor(null);
    setQuery("");
    setKind("");
    setError("");
    setNotice("");
  }
  async function run(action: () => Promise<void>) {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(explain(err));
    } finally {
      setPending(false);
    }
  }
  async function saveNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await run(async () => {
      const next = await api.saveKnowledgeNote(
        {
          request_id: crypto.randomUUID(),
          expected_revision: editor?.document?.revision ?? 0,
          kind: editor?.kind ?? "wiki",
          title: String(data.get("title")),
          body: String(data.get("body")),
        },
        editor?.document?.id,
      );
      setEditor(null);
      setSelected(next);
      setQuery("");
      setKind("");
      setRefresh((v) => v + 1);
      setNotice(
        "Note saved to your local library. Obsidian updates on the next sync.",
      );
    });
  }
  async function visibility() {
    if (!selected) return;
    await run(async () => {
      const next = await api.knowledgeVisibility(selected.id, {
        expected_revision: selected.revision,
        excluded: !selected.excluded,
      });
      setSelected(next);
      setRefresh((v) => v + 1);
      setNotice(
        next.excluded
          ? "Hidden from recall. Honcho rebuilds its local memory on the next sync; the original source is preserved."
          : "Restored to recall. Honcho will rebuild on the next sync.",
      );
    });
  }
  const total = Object.values(state?.counts ?? {}).reduce(
    (sum, n) => sum + n,
    0,
  );
  const sourcePage = selected?.provenance.original_page;
  const originalPage =
    typeof sourcePage === "string" &&
    ["strategies", "research", "chat"].includes(sourcePage)
      ? sourcePage
      : undefined;

  return (
    <section className="workflow-page knowledge-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">YOUR RESEARCH MEMORY</span>
          <h1>Knowledge</h1>
          <p>
            Keep useful ideas, strategies and conversations in one searchable
            place.
          </p>
        </div>
        <button
          className="secondary"
          disabled={pending}
          onClick={() =>
            void run(async () => {
              if (state?.settings.enabled) await api.collectKnowledge();
              setRefresh((v) => v + 1);
              setNotice(
                "Library refreshed. Honcho and Obsidian sync in the background.",
              );
            })
          }
        >
          <RefreshCw size={16} />
          Refresh library
        </button>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="knowledge-notice" role="status">
          {notice}
        </p>
      )}
      <WorkflowTabs
        value={tab}
        select={selectTab}
        label="Knowledge sections"
        options={[
          { id: "library", label: "Library", count: total },
          {
            id: "preferences",
            label: "Your preferences",
            count: state?.counts.preference ?? 0,
          },
          { id: "memory", label: "Memory controls" },
        ]}
      />
      <div
        role="tabpanel"
        id={`workflow-panel-${tab}`}
        aria-labelledby={`workflow-${tab}`}
      >
        {tab === "memory" ? (
          <div className="knowledge-settings">
            <form
              className="settings-card"
              aria-label="Memory controls"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  if (!controls || !state) return;
                  const next = await api.saveKnowledgeSettings({
                    ...controls,
                    expected_revision: state.revision,
                  });
                  setState(next);
                  setControls(next.settings);
                  setRefresh((v) => v + 1);
                  setNotice("Memory controls saved.");
                });
              }}
            >
              <h2>What to remember</h2>
              <p className="muted">
                These controls apply to memory copies. Your original chats,
                strategy versions and research stay in Tauric.
              </p>
              {controls &&
                (
                  [
                    [
                      "enabled",
                      "Record and sync local memory",
                      "Pause to stop collecting new records and exporting notes.",
                    ],
                    [
                      "record_chats",
                      "Save completed chats",
                      "Honcho learns patterns from your own messages, with assistant replies as context.",
                    ],
                    [
                      "record_strategies",
                      "Save strategy versions",
                      "Keep the idea and rules alongside the original version.",
                    ],
                    [
                      "record_research",
                      "Save experiments and research notes",
                      "Keep conclusions and planned tests for later use.",
                    ],
                    [
                      "honcho_enabled",
                      "Learn preferences with local Honcho",
                      "Share selected copies with the model running on this machine. Learning takes time.",
                    ],
                    [
                      "use_in_chat",
                      "Use selected memory in OpenClaw",
                      "Recall still depends on the privacy setting below and the actual chat model.",
                    ],
                    [
                      "allow_external_context",
                      "Allow memory in remote OpenClaw chat",
                      "When enabled, selected notes and preferences may leave this machine for your configured chat service.",
                    ],
                  ] as const
                ).map(([key, label, detail]) => (
                  <label className="knowledge-switch" key={key}>
                    <input
                      type="checkbox"
                      checked={controls[key]}
                      onChange={(e) =>
                        setControls({ ...controls, [key]: e.target.checked })
                      }
                    />
                    <span>
                      <strong>{label}</strong>
                      <small>{detail}</small>
                    </span>
                  </label>
                ))}
              <button className="primary" disabled={pending || !controls}>
                Save memory controls
              </button>
            </form>
            <div className="knowledge-service-cards">
              <article className="settings-card">
                <span className="eyebrow">PRIVATE RECALL</span>
                <h2>
                  {state?.chat_recall_allowed
                    ? "Available to OpenClaw"
                    : "Memory stays local"}
                </h2>
                <p>
                  {state?.chat_recall_detail ??
                    "Checking the current chat connection…"}
                </p>
              </article>
              <article className="settings-card">
                <span className="eyebrow">HONCHO</span>
                <h2>
                  {state?.honcho_configured
                    ? "Local service configured"
                    : "Awaiting local setup"}
                </h2>
                <p>{state?.honcho_detail}</p>
                <p className="muted">
                  {state?.last_honcho_at
                    ? `Last sync: ${new Date(state.last_honcho_at).toLocaleString()}`
                    : "First sync has not completed yet."}
                </p>
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    void run(async () => {
                      await loadState();
                      setNotice("Memory status refreshed.");
                    })
                  }
                >
                  Check memory status
                </button>
              </article>
              <article className="settings-card">
                <span className="eyebrow">OBSIDIAN</span>
                <h2>Your editable notebook</h2>
                <p>
                  Open the folder below as an Obsidian vault. Write Markdown
                  notes in <strong>Inbox</strong> to add them to the wiki.
                  Exported source files preserve the original record; edits to
                  those files are kept for you and shown as a sync conflict.
                </p>
                {state?.vault_location && (
                  <div className="knowledge-vault-path">
                    <code>{state.vault_location}</code>
                    <button
                      className="icon-button"
                      type="button"
                      aria-label="Copy Obsidian folder path"
                      onClick={() =>
                        void run(async () => {
                          await navigator.clipboard.writeText(
                            state.vault_location,
                          );
                          setNotice("Obsidian folder path copied.");
                        })
                      }
                    >
                      <Copy size={16} />
                    </button>
                  </div>
                )}
                <p className="muted">
                  {state?.worker_ready
                    ? "Collector is running."
                    : "Collector has not checked in recently."}{" "}
                  {state?.vault_conflicts
                    ? `${state.vault_conflicts} edited exports were preserved. Copy them into Inbox to make them searchable.`
                    : ""}
                </p>
              </article>
            </div>
          </div>
        ) : (
          <>
            {tab === "preferences" && (
              <article className="knowledge-profile settings-card">
                <span className="eyebrow">LEARNED PATTERNS · CAN BE WRONG</span>
                <h2>What Honcho has inferred</h2>
                {state?.honcho_representation ? (
                  <Markdown
                    remarkPlugins={[remarkGfm]}
                    components={privateMarkdown}
                  >
                    {state.honcho_representation}
                  </Markdown>
                ) : (
                  <p className="muted">
                    No learned preferences are available yet. Honcho processes
                    recorded chats locally in the background. Preferences you
                    write below are explicit and take precedence.
                  </p>
                )}
              </article>
            )}
            <div className="knowledge-tools">
              <label>
                Search your knowledge
                <input
                  aria-label="Search your knowledge"
                  maxLength={300}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search an idea, strategy or topic…"
                />
              </label>
              {tab === "library" && (
                <label>
                  Category
                  <select
                    aria-label="Knowledge category"
                    value={kind}
                    onChange={(e) => setKind(e.target.value as typeof kind)}
                  >
                    <option value="">All categories</option>
                    {Object.entries(labels).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={hidden}
                  onChange={(e) => setHidden(e.target.checked)}
                />
                Include hidden notes
              </label>
              <button
                className="primary"
                onClick={() => {
                  setSelected(undefined);
                  setEditor({
                    kind: tab === "preferences" ? "preference" : "wiki",
                  });
                }}
              >
                <Plus size={16} />
                {tab === "preferences" ? "Add preference" : "New note"}
              </button>
            </div>
            {editor && (
              <form
                key={editor.document?.id ?? editor.kind}
                className="settings-card knowledge-editor"
                aria-label="Knowledge note editor"
                onSubmit={(e) => void saveNote(e)}
              >
                <h2>
                  {editor.document
                    ? "Edit your note"
                    : editor.kind === "preference"
                      ? "Write an explicit preference"
                      : "Add a research note"}
                </h2>
                <label>
                  Note title
                  <input
                    autoFocus
                    required
                    name="title"
                    aria-label="Note title"
                    minLength={3}
                    maxLength={160}
                    defaultValue={editor.document?.title ?? ""}
                  />
                </label>
                <label>
                  Note text
                  <textarea
                    required
                    name="body"
                    aria-label="Note text"
                    minLength={3}
                    maxLength={20000}
                    rows={7}
                    defaultValue={editor.document?.body ?? ""}
                    placeholder={
                      editor.kind === "preference"
                        ? "For example: Explain the hypothesis and risks before suggesting a test."
                        : "Hypothesis, evidence, counter-evidence and the next test…"
                    }
                  />
                </label>
                <div className="assistant-actions">
                  <button className="primary" disabled={pending}>
                    Save note
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setEditor(null)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
            <div className="knowledge-library">
              <div
                className="knowledge-list"
                aria-label="Knowledge results"
                aria-busy={loading}
              >
                {!loading && items.length === 0 && (
                  <div className="settings-card knowledge-empty">
                    <BookOpen size={24} />
                    <h2>
                      {query ? "No matching notes" : "Your library starts here"}
                    </h2>
                    <p className="muted">
                      {query
                        ? "Try a shorter keyword or another category."
                        : "Add a note now, or enable recording in Memory controls to collect completed chats, saved strategies and research."}
                    </p>
                  </div>
                )}
                {items.map((item) => (
                  <button
                    key={item.id}
                    className={`knowledge-result ${selected?.id === item.id ? "selected" : ""}`}
                    aria-pressed={selected?.id === item.id}
                    onClick={() => {
                      setSelected(item);
                      setEditor(null);
                    }}
                  >
                    <span className="knowledge-result-kind">
                      {labels[item.kind]}
                      {item.excluded ? " · Hidden" : ""}
                    </span>
                    <strong>{item.title}</strong>
                    <span className="muted">
                      {sourceLabels[item.source_kind] ?? "Saved source"} ·{" "}
                      {new Date(item.updated_at).toLocaleDateString()}
                    </span>
                    <p>{item.body.replace(/[#`*]/g, "").slice(0, 150)}</p>
                  </button>
                ))}
                {more && (
                  <button
                    className="text-button"
                    disabled={pending}
                    onClick={() =>
                      void run(async () => {
                        const next = await api.knowledge({
                          query,
                          kind:
                            tab === "preferences"
                              ? "preference"
                              : kind || undefined,
                          include_hidden: hidden,
                          offset: items.length,
                        });
                        setItems((prior) => [...prior, ...next.items]);
                        setMore(next.has_more);
                      })
                    }
                  >
                    Load more notes
                  </button>
                )}
              </div>
              {selected && (
                <article
                  className="settings-card knowledge-reader"
                  aria-label="Selected knowledge note"
                >
                  <span className="eyebrow">
                    {sourceLabels[selected.source_kind] ?? "SAVED SOURCE"} ·{" "}
                    {selected.excluded
                      ? "HIDDEN FROM RECALL"
                      : "MEMORY CONTEXT"}
                  </span>
                  <h2>{selected.title}</h2>
                  <div className="assistant-actions">
                    {selected.source_kind === "note" && (
                      <button
                        className="secondary"
                        onClick={() =>
                          setEditor({
                            document: selected,
                            kind:
                              selected.kind === "preference"
                                ? "preference"
                                : "wiki",
                          })
                        }
                      >
                        Edit note
                      </button>
                    )}
                    <button
                      className="secondary"
                      disabled={pending}
                      onClick={() => void visibility()}
                    >
                      {selected.excluded
                        ? "Restore to recall"
                        : "Hide from recall"}
                    </button>
                    {originalPage && (
                      <button
                        className="text-button"
                        onClick={() => navigate(originalPage)}
                      >
                        Open source page
                      </button>
                    )}
                  </div>
                  <div className="knowledge-markdown">
                    <Markdown
                      remarkPlugins={[remarkGfm]}
                      components={privateMarkdown}
                    >
                      {selected.body}
                    </Markdown>
                  </div>
                  <details className="workflow-advanced">
                    <summary>Source details</summary>
                    <p>
                      Revision {selected.revision} · {selected.id}
                    </p>
                    <p className="muted">
                      Source reference:{" "}
                      {String(
                        selected.provenance.source_sha256 ?? selected.checksum,
                      )}
                    </p>
                  </details>
                  <button
                    className="secondary"
                    disabled={!state?.chat_recall_allowed || selected.excluded}
                    onClick={() =>
                      ask(
                        `Read my saved knowledge note ${selected.id} with tauric_project_read. Explain the hypothesis, evidence, counter-evidence and next test. Use fresh original Tauric evidence for trading decisions.`,
                      )
                    }
                  >
                    Discuss with OpenClaw
                  </button>
                  {!state?.chat_recall_allowed && (
                    <p className="muted">{state?.chat_recall_detail}</p>
                  )}
                </article>
              )}
            </div>
          </>
        )}
      </div>
      <p className="workflow-authority">
        Saved notes and learned preferences help with context. Tauric’s original
        evidence, trading checks and your current instructions remain
        authoritative.
      </p>
    </section>
  );
}
