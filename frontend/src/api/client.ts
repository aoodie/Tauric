import createClient from "openapi-fetch";
import type { paths, components } from "./schema";

export type Setting = components["schemas"]["SettingView"];
export type SettingVersion = components["schemas"]["SettingVersionView"];
export type Workspace = components["schemas"]["WorkspaceView"];
export type LayoutVersion = components["schemas"]["LayoutVersionView"];
export type AuditEvent = components["schemas"]["AuditView"];
export type Resource = components["schemas"]["ResourceView"];
export type Session = components["schemas"]["SessionView"];
export type Scope = "global" | "user";
export type Instrument = components["schemas"]["InstrumentView"];
export type Market = components["schemas"]["MarketView"];
export type DataProvider = components["schemas"]["DataProviderView"];
export type MarketConnection = components["schemas"]["MarketConnectionView"];
export type MarketConnectionChange =
  components["schemas"]["MarketConnectionChange"];
export type MarketConnectionCheck =
  components["schemas"]["MarketConnectionCheckView"];
export type Candle = components["schemas"]["Candle"];
export type Interval = NonNullable<
  components["schemas"]["MarketRefresh"]["interval"]
>;
export type AnalysisStatus = components["schemas"]["AnalysisStatus"];
export type Analysis = components["schemas"]["AnalysisView"];
export type NativeReport = components["schemas"]["AnalysisArtifactView"];
export type ModelConnection = components["schemas"]["ModelConnectionView"];
export type ModelConnectionChange =
  components["schemas"]["ModelConnectionChange"];

export type ScannerState = components["schemas"]["ScannerState"];
export type ScannerProfile = components["schemas"]["ScannerProfileView"];
export type ScannerParameters = components["schemas"]["ScannerParameters"];
export type ScannerCandidate = components["schemas"]["CandidateView"];
export type StrategyVersion = components["schemas"]["StrategyVersionView"];
export type StrategyParameters = components["schemas"]["StrategyParameters"];
export type StrategyRun = components["schemas"]["StrategyRunView"];
export type CapitalScope = components["schemas"]["CapitalView"];
export type CapitalLimits = components["schemas"]["CapitalLimits"];
export type CapitalVersion = components["schemas"]["CapitalVersionView"];
export type BrokerState = components["schemas"]["BrokerExecutionState"];
export type BrokerConnection = components["schemas"]["BrokerConnectionStatus"];
export type BrokerJournal = components["schemas"]["BrokerJournalView"];
export type BrokerSetupPreview = components["schemas"]["BrokerSetupPreview"];
export type BrokerReview = components["schemas"]["BrokerReviewView"];
export type BrokerSetup = components["schemas"]["BrokerSetupView"];
export type BrokerRoutes = components["schemas"]["BrokerRoutesState"];
export type TradingReadiness = components["schemas"]["TradingReadinessView"];
export type ReadinessRecord = components["schemas"]["ReadinessRecordView"];
export type BrokerRoute = components["schemas"]["BrokerRouteView"];
export type PaperState = components["schemas"]["PaperState"];
export type PaperAccount = components["schemas"]["PaperAccountView"];
export type TradingRecord = components["schemas"]["TradingRecordView"];
export type PaperPolicy = components["schemas"]["PaperPolicy"];
export type GovernanceStatus = components["schemas"]["GovernanceStatusView"];
export type KnowledgeDocument = components["schemas"]["KnowledgeView"];
export type MemoryState = components["schemas"]["MemoryState"];

let csrfToken = "";
export const client = createClient<paths>({
  baseUrl: "",
  credentials: "same-origin",
});
client.use({
  onRequest({ request }) {
    if (!["GET", "HEAD"].includes(request.method) && csrfToken)
      request.headers.set("X-CSRF-Token", csrfToken);
    return request;
  },
  onResponse({ request, response }) {
    if (
      response.status === 401 &&
      (request.method !== "POST" ||
        new URL(request.url).pathname !== "/api/v1/session")
    )
      window.dispatchEvent(new Event("tauric-session-ended"));
  },
});

export function setSession(session: Session | null) {
  csrfToken = session?.csrf_token ?? "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (!result.response.ok) {
    const detail = (
      result.error as { detail?: string | { msg: string }[] } | undefined
    )?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((x) => x.msg).join("; ")
          : "The workstation could not complete this request. Try again.";
    throw new ApiError(message, result.response.status);
  }
  return result.data as T;
}

export const api = {
  tradingReadiness: async () =>
    unwrap(await client.GET("/api/v1/trading-readiness")),
  tradingOperations: async () =>
    unwrap(await client.GET("/api/v1/trading-readiness/operations")),
  captureValuation: async (body: components["schemas"]["ReadinessAction"]) =>
    unwrap(await client.POST("/api/v1/trading-readiness/valuation", { body })),
  registerForward: async (body: components["schemas"]["ForwardRegister"]) =>
    unwrap(await client.POST("/api/v1/trading-readiness/forward", { body })),
  evaluateForward: async (
    protocol_id: string,
    body: components["schemas"]["ReadinessAction"],
  ) =>
    unwrap(
      await client.POST(
        "/api/v1/trading-readiness/forward/{protocol_id}/evaluate",
        {
          params: { path: { protocol_id } },
          body,
        },
      ),
    ),
  repairBrokerProtection: async (
    close_id: string,
    body: components["schemas"]["BrokerReviewedEntrySubmit"],
  ) =>
    unwrap(
      await client.POST("/api/v1/brokers/closes/{close_id}/repair-protection", {
        params: { path: { close_id } },
        body,
      }),
    ),
  repairBrokerEntryProtection: async (
    intent_id: string,
    body: components["schemas"]["BrokerReviewedEntrySubmit"],
  ) =>
    unwrap(
      await client.POST(
        "/api/v1/brokers/orders/{intent_id}/repair-protection",
        {
          params: { path: { intent_id } },
          body,
        },
      ),
    ),
  knowledgeSettings: async () =>
    unwrap(await client.GET("/api/v1/knowledge/settings")),
  saveKnowledgeSettings: async (
    body: components["schemas"]["MemorySettingsChange"],
  ) => unwrap(await client.PUT("/api/v1/knowledge/settings", { body })),
  knowledge: async (query: {
    query?: string;
    kind?: KnowledgeDocument["kind"];
    include_hidden?: boolean;
    offset?: number;
  }) => unwrap(await client.GET("/api/v1/knowledge", { params: { query } })),
  collectKnowledge: async () =>
    unwrap(await client.POST("/api/v1/knowledge/collect")),
  knowledgeDocument: async (document_id: string) =>
    unwrap(
      await client.GET("/api/v1/knowledge/{document_id}", {
        params: { path: { document_id } },
      }),
    ),
  saveKnowledgeNote: async (
    body: components["schemas"]["KnowledgeNote"],
    document_id?: string,
  ) =>
    document_id
      ? unwrap(
          await client.PUT("/api/v1/knowledge/{document_id}", {
            params: { path: { document_id } },
            body,
          }),
        )
      : unwrap(await client.POST("/api/v1/knowledge/notes", { body })),
  knowledgeVisibility: async (
    document_id: string,
    body: components["schemas"]["KnowledgeVisibility"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/knowledge/{document_id}/visibility", {
        params: { path: { document_id } },
        body,
      }),
    ),
  refreshIntelligenceCatalogs: async () =>
    unwrap(await client.POST("/api/v1/intelligence/catalogs/refresh")),
  registerIntelligenceSymbol: async (
    body: components["schemas"]["IntelligenceRegister"],
  ) => unwrap(await client.POST("/api/v1/intelligence/register", { body })),
  intelligence: async (query: {
    market: components["schemas"]["IntelligenceDashboard"]["market"];
    offset?: number;
    query?: string;
    watched?: boolean;
  }) => unwrap(await client.GET("/api/v1/intelligence", { params: { query } })),
  refreshIntelligence: async (
    body: components["schemas"]["IntelligenceRefresh"],
  ) => unwrap(await client.POST("/api/v1/intelligence/refresh", { body })),
  saveIntelligenceWatchlist: async (
    body: components["schemas"]["IntelligenceWatchlistChange"],
  ) => unwrap(await client.PUT("/api/v1/intelligence/watchlist", { body })),
  intelligenceReceipt: async (receipt_id: string) =>
    unwrap(
      await client.GET("/api/v1/intelligence/receipts/{receipt_id}", {
        params: { path: { receipt_id } },
      }),
    ),
  intelligenceReports: async (instrument_id: string) =>
    unwrap(
      await client.GET("/api/v1/intelligence/reports/{instrument_id}", {
        params: { path: { instrument_id } },
      }),
    ),
  brokerRoutes: async () => unwrap(await client.GET("/api/v1/brokers/routes")),
  saveBrokerRoute: async (
    route_id: string,
    body: components["schemas"]["BrokerRouteChange"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/brokers/routes/{route_id}", {
        params: { path: { route_id } },
        body,
      }),
    ),
  brokerRouteHistory: async (route_id: string) =>
    unwrap(
      await client.GET("/api/v1/brokers/routes/{route_id}/history", {
        params: { path: { route_id } },
      }),
    ),
  brokerReviews: async () =>
    unwrap(await client.GET("/api/v1/brokers/reviews")),
  requestBrokerReview: async (
    body: components["schemas"]["BrokerReviewRequest"],
  ) => unwrap(await client.POST("/api/v1/brokers/reviews", { body })),
  submitReviewedBrokerEntry: async (
    job_id: string,
    body: components["schemas"]["BrokerReviewedEntrySubmit"],
  ) =>
    unwrap(
      await client.POST("/api/v1/brokers/reviews/{job_id}/submit", {
        params: { path: { job_id } },
        body,
      }),
    ),
  brokerSetupPreview: async (
    body: components["schemas"]["BrokerSetupRequest"],
  ) => unwrap(await client.POST("/api/v1/brokers/setup-preview", { body })),
  requestBrokerSetup: async (
    body: components["schemas"]["BrokerSetupRequest"],
  ) => unwrap(await client.POST("/api/v1/brokers/setup-assessments", { body })),
  brokerSetups: async () =>
    unwrap(await client.GET("/api/v1/brokers/setup-assessments")),
  strategyVersions: async () => unwrap(await client.GET("/api/v1/strategies")),
  brokers: async () => unwrap(await client.GET("/api/v1/brokers")),
  checkBroker: async (provider: "oanda" | "alpaca") =>
    unwrap(
      await client.POST("/api/v1/brokers/{provider}/check", {
        params: { path: { provider } },
      }),
    ),
  saveBrokerPolicy: async (
    provider: "oanda" | "alpaca",
    body: components["schemas"]["BrokerPolicyChange"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/brokers/{provider}/policy", {
        params: { path: { provider } },
        body,
      }),
    ),
  createBrokerOrder: async (
    provider: "oanda" | "alpaca",
    body: components["schemas"]["BrokerOrderCreate"],
  ) =>
    unwrap(
      await client.POST("/api/v1/brokers/{provider}/orders", {
        params: { path: { provider } },
        body,
      }),
    ),
  reconcileBrokerOrder: async (intent_id: string) =>
    unwrap(
      await client.POST("/api/v1/brokers/orders/{intent_id}/reconcile", {
        params: { path: { intent_id } },
      }),
    ),
  cancelBrokerOrder: async (intent_id: string) =>
    unwrap(
      await client.POST("/api/v1/brokers/orders/{intent_id}/cancel", {
        params: { path: { intent_id } },
      }),
    ),
  brokerOrderHistory: async (intent_id: string) =>
    unwrap(
      await client.GET("/api/v1/brokers/orders/{intent_id}/history", {
        params: { path: { intent_id } },
      }),
    ),
  brokerJournal: async () =>
    unwrap(await client.GET("/api/v1/brokers/journal")),
  refreshBrokerPosition: async (intent_id: string) =>
    unwrap(
      await client.POST("/api/v1/brokers/orders/{intent_id}/position", {
        params: { path: { intent_id } },
      }),
    ),
  closeBrokerPosition: async (
    intent_id: string,
    body: components["schemas"]["BrokerCloseAction"],
  ) =>
    unwrap(
      await client.POST("/api/v1/brokers/orders/{intent_id}/close", {
        params: { path: { intent_id } },
        body,
      }),
    ),
  reconcileBrokerClose: async (close_id: string) =>
    unwrap(
      await client.POST("/api/v1/brokers/closes/{close_id}/reconcile", {
        params: { path: { close_id } },
      }),
    ),
  saveAutonomy: async (
    deployment_id: string,
    body: components["schemas"]["AutonomyPolicyChange"],
  ) =>
    unwrap(
      await client.PUT(
        "/api/v1/strategies/deployments/{deployment_id}/autonomy",
        { params: { path: { deployment_id } }, body },
      ),
    ),
  autonomyHistory: async (deployment_id: string) =>
    unwrap(
      await client.GET(
        "/api/v1/strategies/deployments/{deployment_id}/autonomy/history",
        { params: { path: { deployment_id } } },
      ),
    ),
  saveStrategySchedule: async (
    deployment_id: string,
    body: components["schemas"]["StrategyScheduleChange"],
  ) =>
    unwrap(
      await client.PUT(
        "/api/v1/strategies/deployments/{deployment_id}/schedule",
        {
          params: { path: { deployment_id } },
          body,
        },
      ),
    ),
  strategyScheduleHistory: async (deployment_id: string) =>
    unwrap(
      await client.GET(
        "/api/v1/strategies/deployments/{deployment_id}/schedule/history",
        {
          params: { path: { deployment_id } },
        },
      ),
    ),
  runStrategy: async (body: components["schemas"]["StrategyRunRequest"]) =>
    unwrap(await client.POST("/api/v1/strategies/runs", { body })),
  closeStrategySignal: async (
    run_id: string,
    body: components["schemas"]["PaperAction"],
  ) =>
    unwrap(
      await client.POST("/api/v1/strategies/runs/{run_id}/close", {
        params: { path: { run_id } },
        body,
      }),
    ),
  chatSettings: async () => unwrap(await client.GET("/api/v1/chat/settings")),
  projectTasks: async () => unwrap(await client.GET("/api/v1/assistant/tasks")),
  controlProjectTask: async (
    task_id: string,
    body: components["schemas"]["TaskControl"],
  ) =>
    unwrap(
      await client.POST("/api/v1/assistant/tasks/{task_id}/control", {
        params: { path: { task_id } },
        body,
      }),
    ),
  heartbeatStatus: async () =>
    unwrap(
      await client.GET("/api/v1/chat/heartbeat", {
        signal: AbortSignal.timeout(5000),
      }),
    ),
  assistant: async () => unwrap(await client.GET("/api/v1/assistant")),
  assistantActivities: async (cursor?: string) =>
    unwrap(
      await client.GET("/api/v1/assistant/activities", {
        params: { query: { limit: 50, cursor } },
      }),
    ),
  saveChatSettings: async (body: components["schemas"]["ChatConfigChange"]) =>
    unwrap(await client.PUT("/api/v1/chat/settings", { body })),
  portfolioResearch: async (portfolio_id: string) =>
    unwrap(
      await client.GET("/api/v1/portfolios/{portfolio_id}/research", {
        params: { path: { portfolio_id } },
      }),
    ),
  savePortfolioResearch: async (
    portfolio_id: string,
    body: components["schemas"]["PortfolioResearchSave"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/portfolios/{portfolio_id}/research", {
        params: { path: { portfolio_id } },
        body,
      }),
    ),
  runPortfolioResearch: async (portfolio_id: string) =>
    unwrap(
      await client.POST("/api/v1/portfolios/{portfolio_id}/research/runs", {
        params: { path: { portfolio_id } },
        body: {
          request_id: crypto.randomUUID(),
          reason: "Owner requested portfolio research",
          evidence: "",
        },
      }),
    ),
  portfolioResearchHistory: async (portfolio_id: string) =>
    unwrap(
      await client.GET("/api/v1/portfolios/{portfolio_id}/research/history", {
        params: { path: { portfolio_id } },
      }),
    ),
  admissionBudget: async () =>
    unwrap(await client.GET("/api/v1/research/admission-budget")),
  saveAdmissionBudget: async (
    body: components["schemas"]["SharedAdmissionBudgetSave"],
  ) => unwrap(await client.PUT("/api/v1/research/admission-budget", { body })),
  portfolios: async () => unwrap(await client.GET("/api/v1/portfolios")),
  portfolio: async (portfolio_id: string) =>
    unwrap(
      await client.GET("/api/v1/portfolios/{portfolio_id}", {
        params: { path: { portfolio_id } },
      }),
    ),
  savePortfolio: async (
    portfolio_id: string,
    body: components["schemas"]["PortfolioSave"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/portfolios/{portfolio_id}", {
        params: { path: { portfolio_id } },
        body,
      }),
    ),
  portfolioHistory: async (portfolio_id: string) =>
    unwrap(
      await client.GET("/api/v1/portfolios/{portfolio_id}/history", {
        params: { path: { portfolio_id } },
      }),
    ),
  conversations: async (portfolio_id?: string) =>
    unwrap(
      await client.GET("/api/v1/chat/conversations", {
        params: { query: { portfolio_id } },
      }),
    ),
  chatTurns: async (conversation_id: string, portfolio_id?: string) =>
    unwrap(
      await client.GET("/api/v1/chat/conversations/{conversation_id}", {
        params: { path: { conversation_id }, query: { portfolio_id } },
      }),
    ),
  sendChat: async (body: components["schemas"]["ChatSend"]) =>
    unwrap(await client.POST("/api/v1/chat/messages", { body })),
  scanners: async () => unwrap(await client.GET("/api/v1/scanners")),
  observeActivities: async (identities: string[]) =>
    unwrap(
      await client.POST("/api/v1/assistant/activities/observe", {
        body: { identities },
      }),
    ),
  candidatePaperReadiness: async (candidate_id: string) =>
    unwrap(
      await client.GET(
        "/api/v1/scanners/candidates/{candidate_id}/paper-readiness",
        { params: { path: { candidate_id } } },
      ),
    ),
  stageCandidatePaper: async (
    candidate_id: string,
    body: components["schemas"]["CandidatePaperStage"],
  ) =>
    unwrap(
      await client.POST(
        "/api/v1/scanners/candidates/{candidate_id}/paper-plan",
        { params: { path: { candidate_id } }, body },
      ),
    ),
  saveScanner: async (body: components["schemas"]["ScannerSave"]) =>
    unwrap(await client.PUT("/api/v1/scanners/profiles", { body })),
  runScanner: async (body: components["schemas"]["ScannerRunRequest"]) =>
    unwrap(await client.POST("/api/v1/scanners/runs", { body })),
  scannerHistory: async (profile_id: string) =>
    unwrap(
      await client.GET("/api/v1/scanners/profiles/{profile_id}/history", {
        params: { path: { profile_id } },
      }),
    ),
  candidateAction: async (
    candidate_id: string,
    body: components["schemas"]["CandidateAction"],
  ) =>
    unwrap(
      await client.POST("/api/v1/scanners/candidates/{candidate_id}/actions", {
        params: { path: { candidate_id } },
        body,
      }),
    ),
  escalateCandidate: async (
    candidate_id: string,
    body: components["schemas"]["CandidateEscalate"],
  ) =>
    unwrap(
      await client.POST("/api/v1/scanners/candidates/{candidate_id}/analysis", {
        params: { path: { candidate_id } },
        body,
      }),
    ),
  createStrategy: async (body: components["schemas"]["StrategyCreate"]) =>
    unwrap(await client.POST("/api/v1/strategies", { body })),
  reviseStrategy: async (
    strategy_id: string,
    body: components["schemas"]["StrategyRevise"],
  ) =>
    unwrap(
      await client.POST("/api/v1/strategies/{strategy_id}/versions", {
        params: { path: { strategy_id } },
        body,
      }),
    ),
  deploymentControl: async (
    deployment_id: string,
    body: components["schemas"]["DeploymentControl"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/paper/deployments/{deployment_id}/control", {
        params: { path: { deployment_id } },
        body,
      }),
    ),
  governance: async () => unwrap(await client.GET("/api/v1/governance")),
  requestGovernance: async (body: components["schemas"]["GovernanceEnqueue"]) =>
    unwrap(await client.POST("/api/v1/governance/jobs", { body })),
  paper: async () => unwrap(await client.GET("/api/v1/paper")),
  createPaperAccount: async (
    body: components["schemas"]["PaperAccountCreate"],
  ) => unwrap(await client.POST("/api/v1/paper/accounts", { body })),
  savePaperPolicy: async (
    account_id: string,
    body: components["schemas"]["PaperPolicyChange"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/paper/accounts/{account_id}/policy", {
        params: { path: { account_id } },
        body,
      }),
    ),
  haltPaper: async (
    account_id: string,
    body: components["schemas"]["PaperHalt"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/paper/accounts/{account_id}/halt", {
        params: { path: { account_id } },
        body,
      }),
    ),
  createCapital: async (body: components["schemas"]["CapitalCreate"]) =>
    unwrap(await client.POST("/api/v1/paper/capital", { body })),
  saveCapital: async (
    scope_id: string,
    body: components["schemas"]["CapitalChange"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/paper/capital/{scope_id}", {
        params: { path: { scope_id } },
        body,
      }),
    ),
  capitalHistory: async (scope_id: string) =>
    unwrap(
      await client.GET("/api/v1/paper/capital/{scope_id}/history", {
        params: { path: { scope_id } },
      }),
    ),
  createDeployment: async (body: components["schemas"]["DeploymentCreate"]) =>
    unwrap(await client.POST("/api/v1/paper/deployments", { body })),
  proposePaper: async (body: components["schemas"]["ProposalCreate"]) =>
    unwrap(await client.POST("/api/v1/paper/proposals", { body })),
  reviewPaper: async (
    proposal_id: string,
    body: components["schemas"]["GovernanceReview"],
  ) =>
    unwrap(
      await client.POST("/api/v1/paper/proposals/{proposal_id}/review", {
        params: { path: { proposal_id } },
        body,
      }),
    ),
  planPaper: async (
    decision_id: string,
    body: components["schemas"]["PaperAction"],
  ) =>
    unwrap(
      await client.POST("/api/v1/paper/decisions/{decision_id}/plan", {
        params: { path: { decision_id } },
        body,
      }),
    ),
  executePaper: async (
    plan_id: string,
    body: components["schemas"]["PaperAction"],
  ) =>
    unwrap(
      await client.POST("/api/v1/paper/plans/{plan_id}/execute", {
        params: { path: { plan_id } },
        body,
      }),
    ),
  closePaper: async (
    position_id: string,
    body: components["schemas"]["PaperAction"],
  ) =>
    unwrap(
      await client.POST("/api/v1/paper/positions/{position_id}/close", {
        params: { path: { position_id } },
        body,
      }),
    ),
  modelConnections: async () =>
    unwrap(await client.GET("/api/v1/model-connections")),
  saveModelConnection: async (
    profile: "cloud" | "local",
    body: ModelConnectionChange,
  ) =>
    unwrap(
      await client.PUT("/api/v1/model-connections/{profile}", {
        params: { path: { profile } },
        body,
      }),
    ),
  changePassword: async (
    current_password: string,
    new_password: string,
    confirm_password: string,
  ) =>
    unwrap(
      await client.POST("/api/v1/owner/password", {
        body: { current_password, new_password, confirm_password },
      }),
    ),
  instruments: async () => unwrap(await client.GET("/api/v1/instruments")),
  discoverInstruments: async (
    provider: "oanda" | "alpaca",
    query = "",
    offset = 0,
  ) =>
    unwrap(
      await client.GET("/api/v1/instrument-discovery/{provider}", {
        params: { path: { provider }, query: { query, offset, limit: 50 } },
      }),
    ),
  addInstrument: async (body: components["schemas"]["InstrumentAdd"]) =>
    unwrap(await client.POST("/api/v1/instruments", { body })),
  marketProviders: async () =>
    unwrap(await client.GET("/api/v1/market-providers")),
  marketConnections: async () =>
    unwrap(await client.GET("/api/v1/market-connections")),
  saveMarketConnection: async (
    provider: "oanda" | "alpaca",
    body: MarketConnectionChange,
  ) =>
    unwrap(
      await client.PUT("/api/v1/market-connections/{provider}", {
        params: { path: { provider } },
        body,
      }),
    ),
  testMarketConnection: async (
    provider: "oanda" | "alpaca",
    expected_revision: number,
  ) =>
    unwrap(
      await client.POST("/api/v1/market-connections/{provider}/test", {
        params: { path: { provider } },
        body: { expected_revision },
      }),
    ),
  market: async (instrument_id: string, interval: Interval) =>
    unwrap(
      await client.GET("/api/v1/market/{instrument_id}", {
        params: { path: { instrument_id }, query: { interval } },
      }),
    ),
  refreshMarket: async (instrument_id: string, interval: Interval) =>
    unwrap(
      await client.POST("/api/v1/market/{instrument_id}/refresh", {
        params: { path: { instrument_id } },
        body: { interval },
      }),
    ),
  analysisStatus: async () =>
    unwrap(await client.GET("/api/v1/analysis/status")),
  analyses: async (offset = 0, limit = 30) =>
    unwrap(
      await client.GET("/api/v1/analysis", {
        params: { query: { offset, limit } },
      }),
    ),
  analysis: async (job_id: string) =>
    unwrap(
      await client.GET("/api/v1/analysis/{job_id}", {
        params: { path: { job_id } },
      }),
    ),
  requestAnalysis: async (
    instrument_id: string,
    profile: "cloud" | "local",
    snapshot_id?: string,
  ) =>
    unwrap(
      await client.POST("/api/v1/analysis", {
        body: {
          request_id: crypto.randomUUID(),
          instrument_id,
          profile,
          snapshot_id,
        },
      }),
    ),
  nativeReport: async (artifact_id: string) =>
    unwrap(
      await client.GET("/api/v1/analysis-artifacts/{artifact_id}", {
        params: { path: { artifact_id } },
      }),
    ),
  session: async () => unwrap(await client.GET("/api/v1/session")),
  login: async (username: string, password: string) =>
    unwrap(
      await client.POST("/api/v1/session", { body: { username, password } }),
    ),
  logout: async () => unwrap(await client.DELETE("/api/v1/session")),
  settings: async () => unwrap(await client.GET("/api/v1/settings")),
  settingHistory: async (key: string, scope: Scope, offset = 0) =>
    unwrap(
      await client.GET("/api/v1/settings/{key}/history", {
        params: { path: { key }, query: { scope, offset } },
      }),
    ),
  changeSetting: async (
    key: string,
    body: components["schemas"]["SettingChange"],
  ) =>
    unwrap(
      await client.PUT("/api/v1/settings/{key}", {
        params: { path: { key } },
        body,
      }),
    ),
  restoreSetting: async (
    key: string,
    scope: Scope,
    revision: number,
    expected_revision: number,
    reason: string,
  ) =>
    unwrap(
      await client.POST("/api/v1/settings/{key}/restore/{revision}", {
        params: { path: { key, revision }, query: { scope } },
        body: { expected_revision, reason },
      }),
    ),
  workspaces: async () => unwrap(await client.GET("/api/v1/workspaces")),
  workspace: async (workspace_id: string) =>
    unwrap(
      await client.GET("/api/v1/workspaces/{workspace_id}", {
        params: { path: { workspace_id } },
      }),
    ),
  createWorkspace: async (name: string, clone_from?: string) =>
    unwrap(
      await client.POST("/api/v1/workspaces", { body: { name, clone_from } }),
    ),
  saveLayout: async (
    workspace_id: string,
    layout: Workspace["layout"],
    expected_revision: number,
  ) =>
    unwrap(
      await client.PUT("/api/v1/workspaces/{workspace_id}/layout", {
        params: { path: { workspace_id } },
        body: {
          layout,
          expected_revision,
          reason: "Saved workspace arrangement",
        },
      }),
    ),
  resetLayout: async (workspace_id: string, expected_revision: number) =>
    unwrap(
      await client.POST("/api/v1/workspaces/{workspace_id}/reset", {
        params: { path: { workspace_id } },
        body: {
          expected_revision,
          reason: "Reset workspace to default arrangement",
        },
      }),
    ),
  layoutHistory: async (workspace_id: string, offset = 0) =>
    unwrap(
      await client.GET("/api/v1/workspaces/{workspace_id}/history", {
        params: { path: { workspace_id }, query: { offset } },
      }),
    ),
  restoreLayout: async (
    workspace_id: string,
    revision: number,
    expected_revision: number,
  ) =>
    unwrap(
      await client.POST(
        "/api/v1/workspaces/{workspace_id}/restore/{revision}",
        {
          params: { path: { workspace_id, revision } },
          body: {
            expected_revision,
            reason: "Restored saved workspace revision",
          },
        },
      ),
    ),
  audit: async (cursor?: string) =>
    unwrap(
      await client.GET("/api/v1/audit", { params: { query: { cursor } } }),
    ),
  resource: async (resource_type: string, resource_id: string) =>
    unwrap(
      await client.GET("/api/v1/resources/{resource_type}/{resource_id}", {
        params: { path: { resource_type, resource_id } },
      }),
    ),
};
