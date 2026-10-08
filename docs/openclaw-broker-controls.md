# OpenClaw broker controls

The owner's requested starting mandate is OANDA and Alpaca live accounts, with a 5% per-trade risk ceiling and a 15% maximum observed account drawdown. These values can be revised later. This document records the requested configuration; it does not activate an account or certify a deployment.

The project tools expose account state, automatic routes, attributed positions, setup assessments and broker reviews. Owner-bound actions support account checks, policy and route changes, assessment and review requests, reviewed entry submission, cancellation, reconciliation, position closing and protection repair. Both providers use their existing broker services, durable request identities and immutable assistant receipts. A request receipt is not a fill confirmation.

Read `broker_state` and the `save_broker_policy` schema before changing policy. Set `no_owner_limits` to `false`, `maximum_risk_percent` to `"5"`, and `maximum_drawdown_percent` to `"15"`, with the current expected revision. Use the existing policy values on subsequent changes unless the owner's mandate authorizes an adjustment. Policies can be saved while disabled. Enabling a live policy still requires a verified live connection, explicit acknowledgment and passing prospective evidence. Never replace a demo connection with a live endpoint without saving the actual live account credentials through Settings.

The risk ceiling bounds estimated account-currency loss at the assessed stop. Broker increments, available buying power, fresh quotes and any lower forward-validated ceiling continue to apply. Raw setup quantities are also checked against policy sizing. New live entries pause when recorded maximum marked drawdown reaches the policy threshold. The existing valuation service maintains observed equity peaks; this is sampled broker NAV, not continuous monitoring or cash-flow-adjusted performance. The threshold remains breached after a recovery until an explicitly authorized policy adjustment; changing the threshold does not rewrite history. Closing positions and repairing protection remain available.

UI enable and pause actions preserve saved account limits. Changing an account policy invalidates old bound setup evidence and route bindings; request fresh evidence and re-save affected routes.

These controls do not expand the strategy engine or candle registry. Automatic routes currently use supported closed-bar long breakout rules. Additional strategies, short entries, arbitrary/custom timeframes and executing/debugging staged code require further implementation. Research Lab experiments remain research evidence and cannot directly qualify a live entry. Native assessments, broker review permission, account identity checks and live qualification continue to govern submission.

Verification uses offline broker fixtures and disposable databases. No live account is activated or real order submitted by the implementation tests.
