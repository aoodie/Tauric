# Connected AI verification

Current development 0.9.0 update: real bound OpenClaw rejection and isolated daily BTC approval-to-paper-entry/close paths are verified, including independent journal reconstruction and API-restart persistence; see [connected paper verification](REAL_PAPER_WORKFLOW_VERIFICATION.md) and [earlier rejection verification](REAL_GOVERNANCE_VERIFICATION.md). The original 0.6.0 connectivity record below preserves the initial checks and remaining limits at that stage.

Recorded 3 October 2026 for development 0.6.0. This is real provider connectivity evidence, separate from the earlier labelled model fixtures. The full platform is not ready for server installation.

## Connection and owner choice

The existing external-local endpoint was configured as Google protocol. Its real pinned-SDK probe failed with a model-not-found error; an OpenAI-compatible probe using `ag/gemini-3.8-flash-high` succeeded. Model discovery returned HTTP 403 and was not treated as a successful connection check. The saved endpoint protocol was corrected to `openai_compatible`, retaining the encrypted credential without displaying it.

The old deep model `ag/gemini-3.5-flash-high` returned HTTP 503. The owner explicitly chose `ag/gemini-3.8-flash-high` for both quick and deep roles. That choice is saved as local connection revision 3. Cloud remains unconfigured. Keys and raw provider error bodies were not printed. No owner paper account, policy, balance or deployment was invented.

## Real requests and retained evidence

| Check | Result |
|---|---|
| Pinned OpenAI-compatible SDK quick-model diagnostic | Exact `TAURIC_OK` response |
| Native analysis job | `ef941723-e5d0-45ca-bd9e-760ec1482b35`, succeeded in approximately 313 seconds |
| Instrument/context | `binance:spot:BTCUSDT`, current daily native research context |
| Original native artifact | `d00bc9cc-d635-4bcd-9042-c30a8d6a8ed1`; saved payload SHA-256 verified |
| Native graph | Unmodified TradingAgents 0.5.2, pinned revision `8b22d43d01d9ddda5d686d093d5385884622f3de` |
| Captured effective models | OpenAI-compatible; quick and deep both `ag/gemini-3.8-flash-high` |
| Report completeness | All nine expected original report fields present and nonempty |
| Original authority separation | Governance absent on original artifact; execution authority false |
| Real private OpenClaw diagnostic | `governor-local` returned exact `TAURIC_OK` with no tools requested |
| OpenClaw diagnostic reported usage | Prompt 4,183; completion 161; total 4,344 tokens, including separately reported reasoning usage |
| Audit | Append-only `model_connection.verified` links connection revision, job, artifact, checksum, model choices and diagnostic usage |
| Broker order calls | Zero |

The nine original fields are market, sentiment, news and fundamentals reports; investment plan; trader investment plan; final trade decision; research debate; and risk debate. Open the saved [native report](http://127.0.0.1:8080/#resource/report/d00bc9cc-d635-4bcd-9042-c30a8d6a8ed1) while signed in to the local workstation.

## Limits

Report completeness and transport success do not certify factual accuracy, provider-data quality, point-in-time isolation or profitable decisions. Native daily research uses its pinned data-provider context; Binance charts/paper quotes use USDT spot and may use an intraday interval. The app retains that distinction. Intermediate native report progress was not emitted in this run, although the final artifact was complete.

The real OpenClaw check requested no tools. Bound paper-context tool calling, strict real-model permission validation and a complete original analysis → governance → deterministic plan → paper fill workflow still need real-model verification after the owner supplies explicit account/allocation/risk policies. Fixture tests cover those mechanics independently. No live execution, broker integration, automatic stop monitoring, restart recovery certification or server release is established by these checks. Real requests can incur provider costs; reported usage is not a billing guarantee, and provider SDKs may retry transport requests.

Development 0.7.0 update: a real bound OpenClaw context-tool review succeeded with outcome reject, and planning was blocked. This closes the previously pending real tool/rejection check; it does not certify an affirmative approval-to-fill path. See [real governance verification](REAL_GOVERNANCE_VERIFICATION.md).
