import {
  ScannerProvider,
  ScannerPanel,
  ScannerWorkspace,
} from "./scanner/Scanner";
import { StrategiesPage } from "./trading/StrategySettings";
import { BrokerModeBadge } from "./trading/LiveTrading";
import {
  PaperProvider,
  TradingSettings,
  PaperWorkbench,
  PaperPositions,
  PaperJournal,
  PaperGovernance,
} from "./trading/PaperTrading";
import { MarketProvider, useMarket } from "./market/MarketContext";
import { AnalysisPanel } from "./market/AnalysisPanel";
import { ModelConnections, SecuritySettings } from "./settings/OwnerSettings";
import { MarketConnections } from "./settings/MarketConnections";
import {
  connectionLabel,
  providerLabel,
  reviewModelLabel,
} from "./settings/aiLabels";
import { ChatConnections, OpenClawChat } from "./chat/Chat";
import { PortfolioSettings } from "./chat/PortfolioSettings";
import { ResearchPage } from "./research/Research";
import { KnowledgePage } from "./knowledge/Knowledge";
import { MarketIntelligence } from "./intelligence/MarketIntelligence";
import { NativeReportReader } from "./research/NativeReport";
import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Actions,
  DockLocation,
  Layout,
  Model,
  TabNode,
  type IJsonModel,
} from "flexlayout-react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  BookOpen,
  Boxes,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Database,
  Expand,
  History,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquare,
  PanelLeftClose,
  Plus,
  Radar,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Sparkles,
  Waves,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  api,
  ApiError,
  setSession,
  type AuditEvent,
  type LayoutVersion,
  type NativeReport,
  type Resource,
  type Scope,
  type Session,
  type Setting,
  type SettingVersion,
  type Workspace,
} from "./api/client";

const MarketChart = lazy(() =>
  import("./market/MarketChart").then((module) => ({
    default: module.MarketChart,
  })),
);

const PANELS: Record<string, { label: string; icon: LucideIcon }> = {
  chart: { label: "Market chart", icon: Waves },
  scanner: { label: "Scanner", icon: Radar },
  tauric: { label: "Tauric analysis", icon: Sparkles },
  openclaw: { label: "OpenClaw governance", icon: ShieldCheck },
  positions: { label: "Positions", icon: Boxes },
  orders: { label: "Orders", icon: ArrowDownToLine },
  journal: { label: "Journal", icon: BookOpen },
};

const message = (error: unknown) =>
  error instanceof Error ? error.message : "This request failed. Try again.";
const asLayout = (layout: Workspace["layout"]) =>
  layout as unknown as IJsonModel;
const layoutData = (model: Model) =>
  model.toJson() as unknown as Workspace["layout"];
const valueText = (value: unknown) =>
  typeof value === "boolean"
    ? value
      ? "On"
      : "Off"
    : String(value ?? "Inherited");
function formattedTime(timestamp: string, timezone: string) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(timestamp));
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "brand-compact" : ""}`}>
      <svg viewBox="0 0 48 48" width="42" height="42" aria-hidden="true">
        <path
          d="M4 8c1 12 8 14 15 13l-3-7C10 16 7 14 4 8Zm40 0c-1 12-8 14-15 13l3-7c6 2 9 0 12-6Z"
          fill="currentColor"
        />
        <path
          d="m16 22 3 14 5 6 5-6 3-14-8-5Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        <path
          d="m17 24 6 3m8-3-6 3m-5 8h8"
          stroke="currentColor"
          strokeWidth="2"
        />
      </svg>
      {!compact && (
        <div>
          <strong>TAURIC</strong>
          <span>TRADING INTELLIGENCE</span>
        </div>
      )}
    </div>
  );
}

function Modal({
  title,
  children,
  close,
  wide = false,
  eyebrow = "RESOURCE INSPECTOR",
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
  eyebrow?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      className={`drawer ${wide ? "drawer-wide" : ""}`}
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      aria-label={title}
    >
      <div className="drawer-head">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close details"
          onClick={close}
        >
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

function Login({
  done,
  notice = "",
}: {
  done: (session: Session) => void;
  notice?: string;
}) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      done(
        await api.login(
          String(form.get("username")),
          String(form.get("password")),
        ),
      );
    } catch (err) {
      setError(message(err));
    } finally {
      setPending(false);
    }
  }
  return (
    <main className="login-screen">
      <div className="login-visual">
        <Brand />
        <div className="login-thesis">
          <span className="eyebrow">YOUR PRIVATE TRADING WORKSTATION</span>
          <h1>
            Every decision.
            <br />A clear record.
          </h1>
          <p>
            Research, governance and execution.
            <br />
            One place to see how they connect.
          </p>
          <div className="login-lineage">
            <span>Evidence</span>
            <ArrowRight size={16} />
            <span>Analysis</span>
            <ArrowRight size={16} />
            <span>Outcome</span>
          </div>
        </div>
        <small>TAURIC / WORKSTATION FOUNDATION</small>
      </div>
      <div className="login-form-wrap">
        <form onSubmit={submit} className="login-form">
          <span className="eyebrow">
            <LockKeyhole size={14} /> OWNER ACCESS
          </span>
          <h2>Open your workstation</h2>
          <p>Sign in with the owner account created during setup.</p>
          {notice && (
            <p role="status" className="settings-success">
              {notice}
            </p>
          )}
          <label>
            Username
            <input
              name="username"
              autoComplete="username"
              required
              maxLength={80}
              autoFocus
            />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={1024}
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" disabled={pending} type="submit">
            {pending ? "Signing in…" : "Sign in"}
            <ArrowRight size={16} />
          </button>
          <div className="setup-note">
            <CircleHelp size={18} />
            <p>
              First visit? Create your owner account on the host using the setup
              guide. Public account registration is disabled.
            </p>
          </div>
        </form>
      </div>
    </main>
  );
}

function Empty({
  icon: Icon,
  title,
  text,
  action,
}: {
  icon: LucideIcon;
  title: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Icon size={25} strokeWidth={1.3} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}

function AgentPanel({
  governor,
  setup,
}: {
  governor: boolean;
  setup: () => void;
}) {
  const steps = governor
    ? ["Portfolio context", "Governance review", "Execution permission"]
    : [
        "Analysts",
        "Research debate",
        "Trader",
        "Risk debate",
        "Portfolio manager",
      ];
  return (
    <div className="agent-panel">
      <div className="panel-kicker">
        {governor ? "SYSTEM GOVERNOR" : "NATIVE ANALYSIS GRAPH"}
        <span className="badge neutral">NOT CONNECTED</span>
      </div>
      <div className="agent-intro">
        <div className="agent-symbol">
          {governor ? <ShieldCheck size={28} /> : <Sparkles size={28} />}
        </div>
        <h3>
          {governor
            ? "A separate governance record"
            : "Analysis with its evidence intact"}
        </h3>
        <p>
          {governor
            ? "OpenClaw reviews portfolio and system context. Its decisions remain separate from Tauric’s original analysis."
            : "Tauric’s analysts, debates and final decision will appear here when the native runtime is connected."}
        </p>
      </div>
      <ol className="agent-steps">
        {steps.map((step) => (
          <li key={step}>
            <span className="step-dot" />
            <span>{step}</span>
            <small>Awaiting runtime</small>
          </li>
        ))}
      </ol>
      <button className="text-button" onClick={setup}>
        View system status
        <ArrowRight size={14} />
      </button>
    </div>
  );
}

function AuditList({
  events,
  timezone,
  open,
  more,
  loading,
}: {
  events: AuditEvent[];
  timezone: string;
  open: (type: string, id: string) => void;
  more?: () => void;
  loading?: boolean;
}) {
  if (!events.length)
    return (
      <Empty
        icon={History}
        title="No history yet"
        text="Changes to your settings and workspaces will be recorded here."
      />
    );
  return (
    <>
      <div className="audit-table">
        <div className="audit-columns">
          <span>Time</span>
          <span>Activity</span>
          <span>Actor</span>
        </div>
        {events.map((event) => (
          <button
            className="audit-row"
            key={event.id}
            onClick={() => open("audit", event.id)}
          >
            <time>{formattedTime(event.created_at, timezone)}</time>
            <span>
              <strong>
                {event.action.replaceAll(".", " / ").replaceAll("_", " ")}
              </strong>
              <small>{event.reason}</small>
            </span>
            <span>
              {event.actor}
              <ChevronRight size={14} />
            </span>
          </button>
        ))}
      </div>
      {more && (
        <button
          className="secondary load-more"
          disabled={loading}
          onClick={more}
        >
          {loading ? "Loading…" : "Load older activity"}
        </button>
      )}
    </>
  );
}

function SettingRow({
  setting,
  changed,
  inspect,
}: {
  setting: Setting;
  changed: () => Promise<void>;
  inspect: (setting: Setting, scope: Scope) => void;
}) {
  const spec = {
    ...setting.definition,
    label: reviewModelLabel(setting.definition.key, setting.definition.label),
  };
  const reviewTier = [
    "governance.cloud_model_tier",
    "governance.local_model_tier",
  ].includes(spec.key);
  const [scope, setScope] = useState<Scope>(
    spec.scopes.includes("user") ? "user" : "global",
  );
  const [value, setValue] = useState<string | number | boolean>(
    setting.effective_value,
  );
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => {
    setValue(
      scope === "global" ? setting.global_value : setting.effective_value,
    );
  }, [scope, setting]);
  async function save(inherit = false) {
    setPending(true);
    setError("");
    try {
      await api.changeSetting(spec.key, {
        scope,
        value: inherit ? null : value,
        inherit,
        expected_revision:
          scope === "global" ? setting.global_revision : setting.user_revision,
        reason: reason.trim(),
      });
      setReason("");
      await changed();
    } catch (err) {
      setError(message(err));
    } finally {
      setPending(false);
    }
  }
  return (
    <article className="setting-row">
      <div className="setting-copy">
        <h3>{spec.label}</h3>
        <p>
          {reviewTier
            ? `Choose the quick or deep model saved in ${connectionLabel(spec.key === "governance.cloud_model_tier" ? "cloud" : "local")} above. Applies to new OpenClaw reviews; changes invalidate pending reviews.`
            : spec.description}
        </p>
        <div className="setting-meta">
          <code>{spec.key}</code>
          {spec.restart_required && (
            <span>Restart required when workers exist</span>
          )}
        </div>
        <div className="inheritance">
          <span>
            Effective <strong>{valueText(setting.effective_value)}</strong>
          </span>
          <span>
            From <strong>{setting.source_scope}</strong> · v
            {setting.source_revision}
          </span>
          <span>
            Global <strong>{valueText(setting.global_value)}</strong>
          </span>
        </div>
      </div>
      <div className="setting-controls">
        <div className="field-pair">
          <label>
            Scope
            <select
              aria-label={`${spec.label} scope`}
              value={scope}
              onChange={(e) => {
                setScope(e.target.value as Scope);
                setError("");
              }}
            >
              {spec.scopes.map((option) => (
                <option key={option} value={option}>
                  {option === "user" ? "My preference" : "Global"}
                </option>
              ))}
            </select>
          </label>
          <label>
            {spec.label}
            {spec.kind === "enum" ? (
              <select
                aria-label={spec.label}
                value={String(value)}
                onChange={(e) => setValue(e.target.value)}
              >
                {spec.options.map((option) => (
                  <option key={option} value={option}>
                    {reviewTier
                      ? option === "quick"
                        ? "Quick model (saved above)"
                        : "Deep model (saved above)"
                      : option}
                  </option>
                ))}
              </select>
            ) : spec.kind === "boolean" ? (
              <select
                aria-label={spec.label}
                value={String(value)}
                onChange={(e) => setValue(e.target.value === "true")}
              >
                <option value="true">On</option>
                <option value="false">Off</option>
              </select>
            ) : (
              <input
                aria-label={spec.label}
                type={spec.kind === "integer" ? "number" : "text"}
                value={String(value)}
                min={spec.minimum ?? undefined}
                max={spec.maximum ?? undefined}
                onChange={(e) =>
                  setValue(
                    spec.kind === "integer"
                      ? Number(e.target.value)
                      : e.target.value,
                  )
                }
              />
            )}
          </label>
        </div>
        <label className="reason-label">
          Reason for change
          <input
            aria-label={`${spec.label} reason`}
            placeholder="Describe why you’re changing this"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
        <div className="setting-actions">
          <button
            className="primary small"
            disabled={pending || reason.trim().length < 3}
            onClick={() => void save()}
          >
            Save change
          </button>
          <button
            className="text-button"
            disabled={pending || reason.trim().length < 3}
            onClick={() => void save(true)}
          >
            {scope === "user" ? "Reset to inherited" : "Reset to default"}
          </button>
          <button
            className="text-button"
            onClick={() => inspect(setting, scope)}
          >
            <History size={14} />
            History
          </button>
        </div>
        {error && (
          <div>
            <p className="error" role="alert">
              {error}
            </p>
            <button
              className="text-button"
              disabled={pending}
              onClick={async () => {
                setPending(true);
                try {
                  await changed();
                  setError("");
                } catch (err) {
                  setError(message(err));
                } finally {
                  setPending(false);
                }
              }}
            >
              <RefreshCw size={14} />
              Reload current value
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function SettingsPage({
  open,
  settings,
  changed,
  inspect,
  section,
  selectSection,
  passwordChanged,
  scanners,
  strategies,
}: {
  settings: Setting[];
  changed: () => Promise<void>;
  inspect: (setting: Setting, scope: Scope) => void;
  section: string;
  selectSection: (section: string) => void;
  passwordChanged: () => void;
  scanners: () => void;
  strategies: () => void;
  open: (type: string, id: string) => void;
}) {
  return (
    <div className="page-scroll">
      <div className="page-heading">
        <div>
          <span className="eyebrow">CONFIGURATION</span>
          <h1>Settings</h1>
          <p>
            Manage preferences, AI connections, market data, paper trading and
            account security.
          </p>
        </div>
        <span className="badge cyan">VERSIONED</span>
      </div>
      <div
        className="settings-tabs"
        role="tablist"
        aria-label="Settings sections"
      >
        {[
          ["preferences", "Preferences"],
          ["models", "AI connections"],
          ["market", "Market data"],
          ["trading", "Trading"],
          ["scanners", "Scanners"],
          ["chat", "Chat connections"],
          ["security", "Security"],
        ].map(([id, label]) => (
          <button
            key={id}
            id={`settings-tab-${id}`}
            role="tab"
            aria-selected={section === id}
            aria-controls={`settings-section-${id}`}
            tabIndex={section === id ? 0 : -1}
            onClick={() => selectSection(id)}
            onKeyDown={(event) => {
              const ids = [
                "preferences",
                "models",
                "market",
                "trading",
                "scanners",
                "chat",
                "security",
              ];
              const index = ids.indexOf(id);
              const next =
                event.key === "ArrowRight"
                  ? ids[(index + 1) % ids.length]
                  : event.key === "ArrowLeft"
                    ? ids[(index + ids.length - 1) % ids.length]
                    : event.key === "Home"
                      ? ids[0]
                      : event.key === "End"
                        ? ids.at(-1)
                        : undefined;
              if (next) {
                event.preventDefault();
                selectSection(next);
                document.getElementById(`settings-tab-${next}`)?.focus();
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`settings-section-${section}`}
        aria-labelledby={`settings-tab-${section}`}
      >
        {section === "models" ? (
          <>
            <ModelConnections />
            <h2>OpenClaw review settings</h2>
            <p className="muted">
              Choose which saved model each API connection uses for reviews and
              set review budgets. Saving applies automatically to new reviews
              and does not call a model.
            </p>
            <div className="settings-list">
              {settings
                .filter((s) => s.definition.key.startsWith("governance."))
                .map((setting) => (
                  <SettingRow
                    key={setting.definition.key}
                    setting={setting}
                    changed={changed}
                    inspect={inspect}
                  />
                ))}
            </div>
          </>
        ) : section === "market" ? (
          <MarketConnections />
        ) : section === "trading" ? (
          <>
            <PortfolioSettings open={open} />
            <TradingSettings strategies={strategies} />
          </>
        ) : section === "scanners" ? (
          <div className="paper-intro">
            <div>
              <h2>Scanner setup</h2>
              <p>
                Manage your saved scans and opportunities from the Scanners
                page.
              </p>
              <button className="secondary" onClick={scanners}>
                Open scanners
              </button>
            </div>
          </div>
        ) : section === "chat" ? (
          <ChatConnections />
        ) : section === "security" ? (
          <SecuritySettings done={passwordChanged} />
        ) : (
          <>
            <div className="settings-intro">
              <ShieldCheck size={20} />
              <p>
                Every save creates a new revision. Restoring an earlier value
                keeps the original history intact.
              </p>
            </div>
            <div className="settings-list">
              {settings
                .filter((s) => !s.definition.key.startsWith("governance."))
                .map((setting) => (
                  <SettingRow
                    key={setting.definition.key}
                    setting={setting}
                    changed={changed}
                    inspect={inspect}
                  />
                ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SettingHistory({
  setting,
  scope,
  timezone,
  close,
  changed,
}: {
  setting: Setting;
  scope: Scope;
  timezone: string;
  close: () => void;
  changed: () => Promise<void>;
}) {
  const [items, setItems] = useState<SettingVersion[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [reason, setReason] = useState("");
  const [loaded, setLoaded] = useState(false);
  const load = async () => {
    try {
      setItems(await api.settingHistory(setting.definition.key, scope));
      setLoaded(true);
    } catch (err) {
      setError(message(err));
    }
  };
  useEffect(() => {
    void load();
  }, []);
  async function restore(version: SettingVersion) {
    setPending(true);
    setError("");
    try {
      await api.restoreSetting(
        setting.definition.key,
        scope,
        version.revision,
        scope === "user" ? setting.user_revision : setting.global_revision,
        reason.trim(),
      );
      await changed();
      close();
    } catch (err) {
      setError(message(err));
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal
      title={reviewModelLabel(setting.definition.key, setting.definition.label)}
      close={close}
    >
      <div className="drawer-body">
        <span className="badge cyan">{scope.toUpperCase()} HISTORY</span>
        <dl className="resource-facts">
          <div>
            <dt>Effective value</dt>
            <dd>{valueText(setting.effective_value)}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>
              {setting.source_scope} · v{setting.source_revision}
            </dd>
          </div>
          <div>
            <dt>Inherited global value</dt>
            <dd>{valueText(setting.global_value)}</dd>
          </div>
        </dl>
        <label>
          Reason for restoration
          <input
            value={reason}
            aria-label="Restoration reason"
            placeholder="Required to restore a revision"
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="history-list">
          {items.map((version) => (
            <article key={version.id}>
              <div className="history-head">
                <span>v{version.revision}</span>
                <strong>
                  {version.inherited ? "Inherited" : valueText(version.value)}
                </strong>
                <button
                  className="text-button"
                  disabled={pending || reason.trim().length < 3}
                  onClick={() => void restore(version)}
                >
                  Restore
                </button>
              </div>
              <p>{version.reason}</p>
              <time>{formattedTime(version.created_at, timezone)}</time>
            </article>
          ))}
        </div>
        {loaded && !items.length && (
          <Empty
            icon={History}
            title="Using the inherited value"
            text="There are no changes at this scope yet. Your first save will create revision 1."
          />
        )}
        {items.length > 0 && (
          <button
            className="text-button"
            onClick={async () => {
              try {
                const older = await api.settingHistory(
                  setting.definition.key,
                  scope,
                  items.length,
                );
                setItems([...items, ...older]);
                if (!older.length) setError("All revisions are shown.");
              } catch (err) {
                setError(message(err));
              }
            }}
          >
            Load older revisions
          </button>
        )}
      </div>
    </Modal>
  );
}

function ResourceDrawer({
  type,
  id,
  close,
  timezone,
}: {
  type: string;
  id: string;
  close: () => void;
  timezone: string;
}) {
  const [resource, setResource] = useState<Resource | null>(null);
  const [error, setError] = useState("");
  const [wide, setWide] = useState(false);
  useEffect(() => {
    let active = true;
    setResource(null);
    setError("");
    api
      .resource(type, id)
      .then((next) => {
        if (active) setResource(next);
      })
      .catch((err) => {
        if (active) setError(message(err));
      });
    return () => {
      active = false;
    };
  }, [type, id]);
  return (
    <Modal
      title={
        type === "report"
          ? "Tauric research report"
          : type === "model_connection"
            ? `${connectionLabel(id)} connection`
            : (resource?.title ?? "Loading resource…")
      }
      close={close}
      wide={type === "report" || wide}
      eyebrow={type === "report" ? "SAVED RESEARCH" : undefined}
    >
      <div className="drawer-body">
        {type !== "report" && (
          <div className="resource-toolbar">
            <span className="badge cyan">{type.toUpperCase()}</span>
            <button className="text-button" onClick={() => setWide(!wide)}>
              <Expand size={14} />
              {wide ? "Drawer view" : "Full resource view"}
            </button>
          </div>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {resource && type === "report" ? (
          <NativeReportReader
            key={id}
            report={resource.data as NativeReport}
            timezone={timezone}
          />
        ) : (
          resource && (
            <dl className="resource-facts">
              {Object.entries(resource.data)
                .filter(([key]) => key !== "layout" && key !== "definition")
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>
                      {typeof value === "object" ? (
                        <pre>{JSON.stringify(value, null, 2)}</pre>
                      ) : key === "created_at" ? (
                        formattedTime(String(value), timezone)
                      ) : (
                        valueText(value)
                      )}
                    </dd>
                  </div>
                ))}
            </dl>
          )
        )}
        {type !== "report" && <p className="resource-id">{id}</p>}
      </div>
    </Modal>
  );
}

function WorkspaceHistory({
  workspace,
  timezone,
  close,
  restored,
}: {
  workspace: Workspace;
  timezone: string;
  close: () => void;
  restored: (workspace: Workspace) => void;
}) {
  const [items, setItems] = useState<LayoutVersion[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => {
    api
      .layoutHistory(workspace.id)
      .then(setItems)
      .catch((err) => setError(message(err)));
  }, []);
  return (
    <Modal title={`${workspace.name} layout history`} close={close}>
      <div className="drawer-body">
        <p>
          Restoration creates a new version. Your current saved layout stays in
          the history.
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="history-list">
          {items.map((version) => (
            <article key={version.id}>
              <div className="history-head">
                <span>v{version.revision}</span>
                <strong>{version.reason}</strong>
                <button
                  className="text-button"
                  disabled={pending}
                  onClick={async () => {
                    setPending(true);
                    try {
                      restored(
                        await api.restoreLayout(
                          workspace.id,
                          version.revision,
                          workspace.revision,
                        ),
                      );
                      close();
                    } catch (err) {
                      setError(message(err));
                    } finally {
                      setPending(false);
                    }
                  }}
                >
                  Restore
                </button>
              </div>
              <time>{formattedTime(version.created_at, timezone)}</time>
            </article>
          ))}
        </div>
        {items.length >= 50 && (
          <button
            className="text-button"
            onClick={async () => {
              try {
                setItems([
                  ...items,
                  ...(await api.layoutHistory(workspace.id, items.length)),
                ]);
              } catch (err) {
                setError(message(err));
              }
            }}
          >
            Load older revisions
          </button>
        )}
      </div>
    </Modal>
  );
}

function Clock({ timezone }: { timezone: string }) {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="header-clock">
      <Clock3 size={13} />
      {new Intl.DateTimeFormat("en-GB", {
        timeZone: timezone,
        weekday: "short",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(time)}
      <small>{timezone.split("/").at(-1)?.replaceAll("_", " ")}</small>
    </span>
  );
}

function pageFromUrl() {
  const view = new URLSearchParams(location.search).get("view");
  return view &&
    [
      "workspace",
      "intelligence",
      "scanners",
      "strategies",
      "paper",
      "research",
      "knowledge",
      "chat",
      "audit",
      "settings",
      "system",
    ].includes(view)
    ? view
    : "workspace";
}

function Workstation({
  session,
  signedOut,
}: {
  session: Session;
  signedOut: (notice?: string) => void;
}) {
  const market = useMarket();
  const [, setLayoutTick] = useState(0);
  const [page, setPage] = useState(() =>
    new URLSearchParams(window.location.search).has("tradingview")
      ? "settings"
      : pageFromUrl(),
  );
  const [settingsSection, setSettingsSection] = useState(() =>
    new URLSearchParams(window.location.search).has("tradingview")
      ? "market"
      : "preferences",
  );
  const [settings, setSettings] = useState<Setting[]>([]);
  const [chatDraft, setChatDraft] = useState("");
  const [chatConnection, setChatConnection] =
    useState<Awaited<ReturnType<typeof api.chatSettings>>>();
  useEffect(() => {
    const url = new URL(location.href);
    if (page === "workspace") url.searchParams.delete("view");
    else url.searchParams.set("view", page);
    history.replaceState(null, "", url);
  }, [page]);
  useEffect(() => {
    const readPage = () => setPage(pageFromUrl());
    window.addEventListener("popstate", readPage);
    return () => window.removeEventListener("popstate", readPage);
  }, []);
  function navigate(next: string) {
    if (next === page) return;
    const url = new URL(location.href);
    if (next === "workspace") url.searchParams.delete("view");
    else url.searchParams.set("view", next);
    history.pushState(null, "", url);
    setPage(next);
  }
  function askOpenClaw(draft: string) {
    const url = new URL(location.href);
    url.searchParams.delete("portfolio");
    history.replaceState(null, "", url);
    setChatDraft(draft);
    navigate("chat");
  }
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [model, setModel] = useState<Model | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | undefined>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [ready, setReady] = useState<boolean | null>(null);
  const [collapsedNav, setCollapsedNav] = useState(false);
  const [mobilePanel, setMobilePanel] = useState("chart");
  const [resource, setResource] = useState<{ type: string; id: string } | null>(
    null,
  );
  const [historySetting, setHistorySetting] = useState<{
    setting: Setting;
    scope: Scope;
  } | null>(null);
  const [layoutHistory, setLayoutHistory] = useState(false);
  const [workspaceDialog, setWorkspaceDialog] = useState<
    "create" | "clone" | null
  >(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceError, setWorkspaceError] = useState("");
  const timezone = String(
    settings.find((s) => s.definition.key === "ui.timezone")?.effective_value ??
      "Europe/London",
  );
  const density = String(
    settings.find((s) => s.definition.key === "ui.density")?.effective_value ??
      "compact",
  );
  const confirmUnsaved =
    settings.find((s) => s.definition.key === "workspace.confirm_unsaved")
      ?.effective_value !== false;

  function applyWorkspace(next: Workspace) {
    setWorkspace(next);
    setModel(Model.fromJson(asLayout(next.layout)));
    setDirty(false);
    setWorkspaces((current) =>
      current.map((item) => (item.id === next.id ? next : item)),
    );
  }
  function mayDiscard() {
    return (
      !dirty ||
      !confirmUnsaved ||
      window.confirm("Discard unsaved changes to this layout?")
    );
  }
  async function loadAudit() {
    const result = await api.audit();
    setEvents(result.items);
    setCursor(result.next_cursor ?? undefined);
  }
  async function refreshSettings() {
    setSettings(await api.settings());
    await loadAudit();
  }
  async function boot() {
    setError("");
    try {
      const [preferences, spaces, activity] = await Promise.all([
        api.settings(),
        api.workspaces(),
        api.audit(),
      ]);
      setSettings(preferences);
      setWorkspaces(spaces);
      if (spaces.length) applyWorkspace(spaces[0]);
      setEvents(activity.items);
      setCursor(activity.next_cursor ?? undefined);
    } catch (err) {
      setError(message(err));
    }
  }
  useEffect(() => {
    void boot();
  }, []);
  useEffect(() => {
    let active = true;
    const check = () =>
      fetch("/health/ready")
        .then((r) => {
          if (active) setReady(r.ok);
        })
        .catch(() => {
          if (active) setReady(false);
        });
    void check();
    const timer = setInterval(check, 15_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    let active = true;
    const check = () =>
      api
        .chatSettings()
        .then((state) => {
          if (active) setChatConnection(state);
        })
        .catch(() => {
          if (active) setChatConnection(undefined);
        });
    void check();
    const timer = setInterval(check, 15_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (dirty && confirmUnsaved) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, confirmUnsaved]);
  useEffect(() => {
    if (!model) return;
    const listener = () => {
      setLayoutTick((value) => value + 1);
      setDirty(
        JSON.stringify(layoutData(model)) !== JSON.stringify(workspace?.layout),
      );
    };
    model.addChangeListener(listener);
    return () => model.removeChangeListener(listener);
  }, [model, workspace]);
  useEffect(() => {
    const read = () => {
      const match = location.hash.match(/^#resource\/([^/]+)\/(.+)$/);
      if (match)
        setResource({
          type: decodeURIComponent(match[1]),
          id: decodeURIComponent(match[2]),
        });
      else setResource(null);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  function open(type: string, id: string) {
    location.hash = `resource/${encodeURIComponent(type)}/${encodeURIComponent(id)}`;
  }
  function closeResource() {
    history.replaceState(null, "", location.pathname + location.search);
    setResource(null);
  }
  function openPanel(component: string) {
    setPage("workspace");
    setMobilePanel(component);
    if (!model) return;
    let tab: TabNode | undefined;
    model.visitNodes((node) => {
      if (node instanceof TabNode && node.getComponent() === component)
        tab = node;
    });
    if (tab) model.doAction(Actions.selectTab(tab.getId()));
    else {
      const target = model.getActiveTabset() ?? model.getFirstTabSet();
      if (target)
        model.doAction(
          Actions.addTab(
            {
              type: "tab",
              id: crypto.randomUUID(),
              name: PANELS[component].label,
              component,
            },
            target.getId(),
            DockLocation.CENTER,
            -1,
            true,
          ),
        );
    }
  }
  async function updateLayout(reset = false) {
    if (!workspace || !model || (reset && !mayDiscard())) return;
    setPending(true);
    setError("");
    try {
      const next = reset
        ? await api.resetLayout(workspace.id, workspace.revision)
        : await api.saveLayout(
            workspace.id,
            layoutData(model),
            workspace.revision,
          );
      applyWorkspace(next);
      await loadAudit();
      setNotice(
        reset
          ? "Layout reset. Previous versions remain in history."
          : `Layout saved · v${next.revision}`,
      );
    } catch (err) {
      setError(message(err));
    } finally {
      setPending(false);
    }
  }
  async function reloadWorkspace() {
    if (!workspace || !mayDiscard()) return;
    try {
      applyWorkspace(await api.workspace(workspace.id));
      setError("");
      await loadAudit();
    } catch (err) {
      setError(message(err));
    }
  }
  function renderPanel(component: string) {
    if (component === "chart")
      return (
        <Suspense fallback={<p className="muted">Opening market chart…</p>}>
          <MarketChart open={open} analyze={() => openPanel("tauric")} />
        </Suspense>
      );
    if (component === "tauric")
      return (
        <AnalysisPanel
          open={open}
          setup={() => {
            setSettingsSection("models");
            setPage("settings");
          }}
        />
      );
    if (component === "openclaw")
      return <PaperGovernance show={() => setPage("paper")} />;
    if (component === "positions")
      return <PaperPositions open={open} compact />;
    if (component === "orders") return <PaperJournal open={open} fillsOnly />;
    if (component === "journal") return <PaperJournal open={open} />;
    if (component === "scanner")
      return (
        <ScannerPanel
          open={open}
          setup={() => {
            navigate("scanners");
          }}
          chart={() => openPanel("chart")}
        />
      );
    return (
      <div className="execution-panel">
        <div className="table-empty-header">
          {(component === "positions"
            ? ["Instrument", "Account", "Strategy", "Quantity", "P&L"]
            : ["Instrument", "Account", "Type", "Quantity", "Status"]
          ).map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
        <Empty
          icon={PANELS[component]?.icon ?? Boxes}
          title={
            component === "positions"
              ? "No connected positions"
              : "No connected orders"
          }
          text="Broker accounts and execution are not connected in this foundation. No trades are being placed."
        />
      </div>
    );
  }
  const factory = (node: TabNode) => {
    if (node.getConfig()?.collapsed)
      return (
        <Empty
          icon={PANELS[node.getComponent()!].icon}
          title="Panel collapsed"
          text="Expand to show this panel’s contents."
          action={
            <button
              className="secondary"
              onClick={() =>
                model?.doAction(
                  Actions.updateNodeAttributes(node.getId(), {
                    config: { collapsed: false },
                  }),
                )
              }
            >
              Expand panel
            </button>
          }
        />
      );
    return renderPanel(node.getComponent()!);
  };
  const nav = [
    { id: "workspace", label: "Workspace", icon: LayoutDashboard },
    { id: "intelligence", label: "Market intelligence", icon: Waves },
    { id: "scanners", label: "Scanners", icon: Radar },
    { id: "strategies", label: "Strategies", icon: Sparkles },
    { id: "paper", label: "Trading", icon: Boxes },
    { id: "research", label: "Research & analytics", icon: BookOpen },
    { id: "knowledge", label: "Knowledge", icon: Database },
    { id: "chat", label: "OpenClaw", icon: MessageSquare },
    { id: "audit", label: "Activity", icon: History },
    { id: "settings", label: "Settings", icon: Settings2 },
    { id: "system", label: "System", icon: Activity },
  ];

  return (
    <div
      className={`app density-${density} ${collapsedNav ? "nav-collapsed" : ""}`}
    >
      <aside className="sidebar">
        <Brand compact={collapsedNav} />
        <div className="nav-label">WORKSTATION</div>
        <nav>
          {nav.map((item) => (
            <button
              key={item.id}
              title={item.label}
              className={page === item.id ? "active" : ""}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
              {page === item.id && <span className="nav-mark" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-context">
          <span className="eyebrow">REAL-MONEY TRADING</span>
          <div>
            <span className="status-dot idle" />
            Off
          </div>
          <p>
            Paper simulation, broker validation and governed live execution are
            available.
          </p>
        </div>
        <div className="sidebar-bottom">
          <button
            title="Collapse navigation"
            aria-label="Collapse navigation"
            onClick={() => setCollapsedNav(!collapsedNav)}
          >
            <PanelLeftClose size={18} />
            <span>Collapse</span>
          </button>
          <span>DEVELOPMENT · 0.12.0</span>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title">
            <button
              className="mobile-menu icon-button"
              onClick={() => setCollapsedNav(!collapsedNav)}
              aria-label="Toggle navigation"
            >
              <Menu size={18} />
            </button>
            <span>Tauric workstation</span>
            <span className="topbar-slash">/</span>
            <strong>{nav.find((item) => item.id === page)?.label}</strong>
          </div>
          <Clock timezone={timezone} />
          <span className="owner-avatar" title={session.username}>
            {session.username.slice(0, 2).toUpperCase()}
          </span>
          <button
            className="icon-button"
            title="Sign out"
            aria-label="Sign out"
            onClick={async () => {
              if (!mayDiscard()) return;
              try {
                await api.logout();
                signedOut();
              } catch (err) {
                setError(message(err));
              }
            }}
          >
            <LogOut size={17} />
          </button>
        </header>
        <div className="statusbar">
          <span>
            <span className={`status-dot ${ready ? "good" : "idle"}`} />
            {ready === null
              ? "Checking storage"
              : ready
                ? "Storage ready"
                : "Storage unavailable"}
          </span>
          <span>
            <span
              className={`status-dot ${market.feed?.state === "fresh" ? "good" : "idle"}`}
            />
            {market.feed?.state === "fresh"
              ? "Market data received"
              : market.feed?.state === "stale"
                ? "Market data stale"
                : "No market snapshot"}
          </span>
          <span>
            <span
              className={`status-dot ${market.status?.worker_ready ? "good" : "idle"}`}
            />
            {market.status?.worker_ready
              ? "Tauric worker ready"
              : "Tauric worker offline"}
          </span>
          <span>
            <span className="status-dot idle" />
            OpenClaw · assistant &amp; approvals
          </span>
          <span className="status-execution">
            <LockKeyhole size={12} />
            <BrokerModeBadge />
          </span>
        </div>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button
              className="text-button"
              onClick={() => (workspace ? void reloadWorkspace() : void boot())}
            >
              <RefreshCw size={14} />
              Reload saved data
            </button>
            <button
              className="icon-button"
              aria-label="Dismiss error"
              onClick={() => setError("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {page === "workspace" && (
          <div className="workspace-page">
            <div className="workspace-heading">
              <div>
                <span className="eyebrow">TRADING WORKSPACE</span>
                <h1>
                  Your view of the markets<span className="heading-dot">.</span>
                </h1>
              </div>
              <div className="workspace-selector">
                <label htmlFor="workspace-choice">Workspace</label>
                <select
                  id="workspace-choice"
                  value={workspace?.id ?? ""}
                  onChange={async (e) => {
                    const id = e.target.value;
                    if (!mayDiscard()) return;
                    try {
                      applyWorkspace(await api.workspace(id));
                    } catch (err) {
                      setError(message(err));
                    }
                  }}
                >
                  {workspaces.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="workspace-toolbar">
              <div className="toolbar-start">
                <span className={`save-state ${dirty ? "unsaved" : ""}`}>
                  {dirty
                    ? "Unsaved layout"
                    : workspace
                      ? `Saved · v${workspace.revision}`
                      : "No workspace"}
                </span>
                <span className="toolbar-rule" />
                <span className="workspace-hint">
                  Drag tabs. Resize dividers. Make it yours.
                </span>
              </div>
              <div className="toolbar-actions">
                <details className="panel-menu">
                  <summary>
                    <Plus size={14} />
                    Panels
                    <ChevronDown size={12} />
                  </summary>
                  <div>
                    {Object.entries(PANELS).map(([key, panel]) => (
                      <button
                        key={key}
                        onClick={(e) => {
                          openPanel(key);
                          e.currentTarget
                            .closest("details")
                            ?.removeAttribute("open");
                        }}
                      >
                        <panel.icon size={14} />
                        {panel.label}
                      </button>
                    ))}
                  </div>
                </details>
                <button
                  className="text-button"
                  onClick={() => {
                    setWorkspaceDialog("create");
                    setWorkspaceName("");
                    setWorkspaceError("");
                  }}
                >
                  <Plus size={14} />
                  New
                </button>
                <button
                  className="text-button"
                  disabled={!workspace}
                  onClick={() => {
                    setWorkspaceDialog("clone");
                    setWorkspaceName(`${workspace?.name} copy`);
                    setWorkspaceError("");
                  }}
                >
                  Clone
                </button>
                <button
                  className="text-button"
                  disabled={!workspace}
                  onClick={() => void updateLayout(true)}
                >
                  <RefreshCw size={14} />
                  Reset
                </button>
                <button
                  className="icon-button"
                  title="Layout history"
                  aria-label="Layout history"
                  disabled={!workspace}
                  onClick={() => {
                    if (mayDiscard()) setLayoutHistory(true);
                  }}
                >
                  <History size={15} />
                </button>
                <button
                  className="primary small"
                  disabled={pending || !workspace || !dirty}
                  onClick={() => void updateLayout()}
                >
                  <Save size={14} />
                  {pending ? "Saving…" : "Save layout"}
                </button>
              </div>
            </div>
            {model ? (
              <>
                <div className="desktop-layout">
                  <Layout
                    model={model}
                    factory={factory}
                    realtimeResize
                    supportsPopout={false}
                    onRenderTab={(node, values) => {
                      const Icon = PANELS[node.getComponent()!]?.icon;
                      if (Icon) values.leading = <Icon size={13} />;
                    }}
                    onRenderTabSet={(node, values) => {
                      const selected =
                        "getSelectedNode" in node
                          ? node.getSelectedNode()
                          : undefined;
                      if (selected instanceof TabNode)
                        values.buttons.push(
                          <button
                            key="collapse"
                            className="icon-button panel-collapse"
                            aria-label={`Collapse ${selected.getName()}`}
                            title="Collapse panel contents"
                            onClick={() =>
                              model.doAction(
                                Actions.updateNodeAttributes(selected.getId(), {
                                  config: {
                                    collapsed: !selected.getConfig()?.collapsed,
                                  },
                                }),
                              )
                            }
                          >
                            <ChevronDown size={14} />
                          </button>,
                        );
                    }}
                  />
                </div>
                <div className="mobile-layout">
                  <label>
                    Panel
                    <select
                      aria-label="Active panel"
                      value={mobilePanel}
                      onChange={(e) => openPanel(e.target.value)}
                    >
                      {Object.entries(PANELS).map(([id, panel]) => (
                        <option key={id} value={id}>
                          {panel.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="mobile-panel">{renderPanel(mobilePanel)}</div>
                </div>
              </>
            ) : (
              <Empty
                icon={LayoutDashboard}
                title="Create your first workspace"
                text="Save an arrangement of chart, analysis and activity panels."
                action={
                  <button
                    className="primary"
                    onClick={() => setWorkspaceDialog("create")}
                  >
                    Create workspace
                  </button>
                }
              />
            )}
            <div className="lineage-rail">
              <span className="eyebrow">DECISION LINEAGE</span>
              {[
                "Evidence",
                "Tauric",
                "Governance",
                "Calculation",
                "Order",
                "Fill",
                "Outcome",
              ].map((step, i) => (
                <span className="lineage-step" key={step}>
                  {i > 0 && <ChevronRight size={12} />}
                  <span>{step}</span>
                </span>
              ))}
              <span className="lineage-note">No trade selected</span>
            </div>
          </div>
        )}
        {page === "intelligence" && (
          <MarketIntelligence
            timezone={timezone}
            open={open}
            chart={(id) => {
              market.setInstrumentId(id);
              market.setIntervalValue("1d");
              openPanel("chart");
            }}
            scanners={() => openPanel("scanner")}
            setup={() => {
              setSettingsSection("market");
              setPage("settings");
            }}
            ask={askOpenClaw}
          />
        )}
        {page === "paper" && (
          <PaperWorkbench
            open={open}
            setup={() => {
              setSettingsSection("trading");
              setPage("settings");
            }}
          />
        )}
        {page === "scanners" && (
          <ScannerWorkspace
            open={open}
            chart={() => openPanel("chart")}
            ask={askOpenClaw}
          />
        )}
        {page === "strategies" && (
          <StrategiesPage
            ask={askOpenClaw}
            trading={() => navigate("paper")}
            research={() => navigate("research")}
          />
        )}
        {page === "chat" && (
          <OpenClawChat
            initialDraft={chatDraft}
            open={open}
            setup={() => {
              setSettingsSection("chat");
              setPage("settings");
            }}
          />
        )}
        {page === "research" && (
          <ResearchPage open={(resource) => open(resource.type, resource.id)} />
        )}
        {page === "knowledge" && (
          <KnowledgePage navigate={navigate} ask={askOpenClaw} />
        )}
        {page === "settings" && (
          <SettingsPage
            open={open}
            settings={settings}
            changed={refreshSettings}
            inspect={(setting, scope) => setHistorySetting({ setting, scope })}
            section={settingsSection}
            selectSection={setSettingsSection}
            passwordChanged={() =>
              signedOut("Password changed. Sign in with your new password.")
            }
            scanners={() => navigate("scanners")}
            strategies={() => navigate("strategies")}
          />
        )}
        {page === "audit" && (
          <div className="page-scroll">
            <div className="page-heading">
              <div>
                <span className="eyebrow">APPEND-ONLY RECORD</span>
                <h1>Activity</h1>
                <p>Settings, layouts and owner sessions, as they happened.</p>
              </div>
              <button
                className="secondary"
                onClick={() =>
                  void loadAudit().catch((err) => setError(message(err)))
                }
              >
                <RefreshCw size={14} />
                Refresh
              </button>
            </div>
            <AuditList
              events={events}
              timezone={timezone}
              open={open}
              loading={pending}
              more={
                cursor
                  ? async () => {
                      setPending(true);
                      try {
                        const result = await api.audit(cursor);
                        setEvents([...events, ...result.items]);
                        setCursor(result.next_cursor ?? undefined);
                      } catch (err) {
                        setError(message(err));
                      } finally {
                        setPending(false);
                      }
                    }
                  : undefined
              }
            />
          </div>
        )}
        {page === "system" && (
          <div className="page-scroll">
            <div className="page-heading">
              <div>
                <span className="eyebrow">CONNECTIONS & READINESS</span>
                <h1>System status</h1>
                <p>Only verified connections are shown as ready.</p>
              </div>
              <span className="badge gold">DEVELOPMENT BUILD</span>
            </div>
            <div className="system-list">
              {[
                {
                  label: "Workstation",
                  text: "Owner login, settings and saved layouts",
                  state: "Available",
                  ok: true,
                  icon: LayoutDashboard,
                },
                {
                  label: "Storage",
                  text: "Configuration, workspace and audit records",
                  state: ready ? "Ready" : "Unavailable",
                  ok: !!ready,
                  icon: Database,
                },
                {
                  label: "Market data",
                  text: "Binance public spot candles; no account access",
                  state:
                    market.feed?.state === "fresh"
                      ? "Received"
                      : market.feed?.state === "stale"
                        ? "Stale"
                        : "No snapshot",
                  ok: market.feed?.state === "fresh",
                  icon: Waves,
                },
                {
                  label: "Tauric",
                  text: "Pinned native graph; direct provider APIs and 9router",
                  state: market.status?.worker_ready
                    ? "Worker ready"
                    : "Worker offline",
                  ok: !!market.status?.worker_ready,
                  icon: Sparkles,
                },
                {
                  label: "OpenClaw",
                  text: "Independent governance and chat runtime",
                  state: !chatConnection
                    ? "Checking connection"
                    : chatConnection.gateway_ready &&
                        chatConnection.worker_ready &&
                        chatConnection.route_ready
                      ? "Ready for chat"
                      : !chatConnection.gateway_ready
                        ? "Gateway offline"
                        : !chatConnection.worker_ready
                          ? "Chat worker offline"
                          : "AI connection required",
                  ok: !!(
                    chatConnection?.gateway_ready &&
                    chatConnection.worker_ready &&
                    chatConnection.route_ready
                  ),
                  icon: ShieldCheck,
                },
                {
                  label: "Trading",
                  text: "Owner review, Decimal sizing and attributed simulated fills",
                  state: "Available",
                  ok: true,
                  icon: Boxes,
                },
                {
                  label: "Broker accounts",
                  text: "OANDA · IBKR · Binance · Alpaca",
                  state: "Not connected",
                  ok: false,
                  icon: Boxes,
                },
              ].map((item) => (
                <article key={item.label}>
                  <item.icon size={21} />
                  <div>
                    <h3>{item.label}</h3>
                    <p>{item.text}</p>
                  </div>
                  <span className={`badge ${item.ok ? "green" : "neutral"}`}>
                    {item.state}
                  </span>
                </article>
              ))}
            </div>
            <div className="model-profile-grid">
              {market.status?.profiles.map((profile) => (
                <article key={profile.id}>
                  <span className="eyebrow">{connectionLabel(profile.id)}</span>
                  <span
                    className={`badge ${profile.configured ? "cyan" : "neutral"}`}
                  >
                    {profile.configured ? "CONFIGURED" : "SETUP NEEDED"}
                  </span>
                  <h3>
                    {profile.provider
                      ? providerLabel(profile.provider)
                      : "Choose a provider"}
                  </h3>
                  <p>{profile.detail}</p>
                  {profile.quick_model && (
                    <p>
                      Quick: {profile.quick_model}
                      <br />
                      Deep: {profile.deep_model}
                    </p>
                  )}
                  <p>
                    Manage this connection in Settings. Saved keys stay private.
                  </p>
                  <button
                    className="text-button"
                    onClick={() => {
                      setSettingsSection("models");
                      setPage("settings");
                    }}
                  >
                    Edit AI connections <ArrowRight size={13} />
                  </button>
                </article>
              ))}
            </div>
            <div className="system-note">
              <LockKeyhole size={19} />
              <div>
                <h3>Trading is not enabled</h3>
                <p>
                  This milestone stores workstation preferences and history.
                  Public market data and the native analysis connection are
                  available. Broker accounts, governance and execution remain
                  disabled.
                </p>
              </div>
            </div>
          </div>
        )}
        <footer className="app-footer">
          <span>
            <span className="status-dot good" />
            Owner session active
          </span>
          <span>Private workstation · Paper simulation</span>
          <span>TAURIC 0.12.0</span>
        </footer>
      </div>
      {notice && (
        <div className="toast" role="status">
          <ShieldCheck size={17} />
          {notice}
        </div>
      )}
      {resource && (
        <ResourceDrawer
          {...resource}
          close={closeResource}
          timezone={timezone}
        />
      )}
      {historySetting && (
        <SettingHistory
          {...historySetting}
          timezone={timezone}
          close={() => setHistorySetting(null)}
          changed={refreshSettings}
        />
      )}
      {layoutHistory && workspace && (
        <WorkspaceHistory
          workspace={workspace}
          timezone={timezone}
          close={() => setLayoutHistory(false)}
          restored={(next) => {
            applyWorkspace(next);
            void loadAudit();
            setNotice(`Layout restored · v${next.revision}`);
          }}
        />
      )}
      {workspaceDialog && (
        <Modal
          title={
            workspaceDialog === "clone"
              ? "Clone saved workspace"
              : "Create workspace"
          }
          close={() => setWorkspaceDialog(null)}
        >
          <form
            className="drawer-body"
            onSubmit={async (e) => {
              e.preventDefault();
              setPending(true);
              setWorkspaceError("");
              try {
                const next = await api.createWorkspace(
                  workspaceName.trim(),
                  workspaceDialog === "clone" ? workspace?.id : undefined,
                );
                setWorkspaces((current) => [...current, next]);
                if (mayDiscard()) applyWorkspace(next);
                setWorkspaceDialog(null);
                await loadAudit();
                setNotice("Workspace created.");
              } catch (err) {
                setWorkspaceError(message(err));
              } finally {
                setPending(false);
              }
            }}
          >
            <label>
              Workspace name
              <input
                autoFocus
                required
                minLength={1}
                maxLength={80}
                value={workspaceName}
                onChange={(e) => setWorkspaceName(e.target.value)}
              />
            </label>
            <p>
              {workspaceDialog === "clone"
                ? "Clones the last saved layout. Unsaved changes are not included."
                : "Starts with a chart, analysis dock and execution panels."}
            </p>
            {workspaceError && (
              <p className="error" role="alert">
                {workspaceError}
              </p>
            )}
            <button
              className="primary"
              disabled={pending || !workspaceName.trim()}
              type="submit"
            >
              {pending ? "Creating…" : "Create workspace"}
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}

export function App() {
  const [session, updateSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState("");
  const [loginNotice, setLoginNotice] = useState("");
  const signedIn = (next: Session) => {
    setSession(next);
    updateSession(next);
    setBootError("");
    setLoginNotice("");
  };
  const signedOut = (notice?: string) => {
    if (notice !== undefined) setLoginNotice(notice);
    setSession(null);
    updateSession(null);
    history.replaceState(null, "", location.pathname);
  };
  async function check() {
    setLoading(true);
    setBootError("");
    try {
      signedIn(await api.session());
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401))
        setBootError(message(err));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void check();
    const listener = () => signedOut();
    window.addEventListener("tauric-session-ended", listener);
    return () => window.removeEventListener("tauric-session-ended", listener);
  }, []);
  if (loading)
    return (
      <div className="loading-screen">
        <Brand />
        <p>Opening your workstation…</p>
      </div>
    );
  if (bootError)
    return (
      <div className="loading-screen">
        <Brand />
        <p role="alert">{bootError}</p>
        <button className="secondary" onClick={() => void check()}>
          Try again
        </button>
      </div>
    );
  return session ? (
    <MarketProvider>
      <PaperProvider>
        <ScannerProvider>
          <Workstation session={session} signedOut={signedOut} />
        </ScannerProvider>
      </PaperProvider>
    </MarketProvider>
  ) : (
    <Login done={signedIn} notice={loginNotice} />
  );
}
