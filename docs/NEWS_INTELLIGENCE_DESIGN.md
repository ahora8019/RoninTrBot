# RoninTrBot News Intelligence — Design Spec

Status: proposed design; paper-trading only. No live execution.

## 1. Goal

Add an event-driven news research layer that collects official announcements and reputable crypto/financial media, deduplicates and validates stories, estimates their likely relevance to configured assets, and emits explainable candidate signals for the existing strategy/risk pipeline. In v1 this layer may open and close simulated paper positions only.

News sentiment is not a standalone guarantee of direction. A headline can be stale, misinterpreted, already priced in, or contradicted by market action. News alone must never bypass risk controls.

## 2. Non-goals and hard constraints

- No live orders, private exchange endpoints, API trading credentials, or automatic deployment.
- No paid LLM or paid news API required for the initial version.
- No arbitrary webpage scraping where RSS, official feeds, or publisher APIs are available.
- No order intent from unverified or timestamp-ambiguous content.
- No model output may override risk limits, stale-data checks, position limits, or the paper-only execution boundary.
- News module must not import or call an exchange execution client.
- All event processing must be deterministic/replayable when given the same archived inputs and configuration.

## 3. Proposed modules

    app/news/
      models.py          # NewsItem, SourcePolicy, EventAssessment
      sources.py         # RSS/Atom and official announcement feed adapters
      normalize.py       # canonical URLs, timestamps, text normalization
      deduplicate.py     # canonical URL + normalized-title/content fingerprints
      classify.py        # deterministic v1 rules: asset, event type, polarity, impact
      scoring.py         # source reliability + freshness + corroboration + uncertainty
      store.py           # persistence interface; SQLite locally, existing DB adapter later
      pipeline.py        # collect -> normalize -> deduplicate -> assess -> emit candidate
    app/paper/
      portfolio.py       # simulated cash, positions, fills, fees and slippage
      execution.py       # paper-only fill simulation; no exchange calls
    app/validation/
      event_backtest.py  # event-time replay, strategy comparison, OOS and walk-forward
    tests/
      test_news_*.py
      test_paper_*.py
      test_event_backtest.py

The names are a target layout, not a claim that these files already exist.

## 4. Source policy

Start with a small allowlist and store source metadata per item:

- Official project/exchange announcements and official status/security incident notices.
- Regulatory sources, including the SEC press-release feed.
- Macro policy sources, including Federal Reserve RSS feeds.
- Reputable crypto media RSS feeds, e.g. CoinDesk and Cointelegraph; treat media reports as secondary until corroborated.

Every item records source ID, publisher, canonical URL, original publication timestamp (UTC), first-seen timestamp (UTC), fetch timestamp (UTC), title, summary/content hash, mentioned assets, and parser version. If original publication time is missing or untrustworthy, mark the item timestamp quality as unknown and prohibit it from historical signal generation until reviewed.

RSS is a transport, not a truth guarantee. Publisher allowlisting, corroboration, duplicate detection and source-health monitoring remain required.

## 5. Event schema

NewsItem:
- item_id, source_id, canonical_url
- published_at, first_seen_at, fetched_at (UTC-aware)
- title, summary, content_hash
- timestamp_quality: verified | publisher_reported | unknown
- verification_state: unreviewed | corroborated | official | retracted | disputed
- language, parser_version

EventAssessment:
- event_id, linked item IDs, asset_scope
- event_type: listing | delisting | exploit | outage | regulation | macro | tokenomics | partnership | court | other
- polarity: positive | negative | mixed | unknown
- impact_score (0–100), confidence (0–1), novelty (0–1)
- evidence, uncertainty_reasons, assessed_at
- candidate_direction: long | short | none; default none

Keep confidence separate from impact. A high-impact but unconfirmed report should produce a hold/no-trade state, not a larger position.

## 6. Deterministic v1 scoring

Do not begin with a paid model. Use transparent rules and versioned configuration:
1. Identify asset mentions using a curated symbol/name map; avoid ambiguous tickers without context.
2. Map headlines to a limited event taxonomy using phrase patterns.
3. Set provisional polarity and impact from reviewed event rules.
4. Add source-reliability tier, freshness, novelty and corroboration signals.
5. Penalize duplicate reports and conflicting sources.
6. Emit none if source is unapproved, timestamp is unknown, confidence is below threshold, asset mapping is ambiguous, reports conflict, or market data is stale.

Do not use a simplistic positive-word/negative-word score as the final trade decision. Sentiment can be logged as a feature but must be evaluated empirically.

## 7. Candidate generation and paper-only gates

A candidate may be considered only if all gates pass:
- source is allowlisted and event is not retracted/disputed;
- original publication time is valid and item was available to the system at decision time;
- event is not a duplicate and has not already triggered a trade;
- source/event confidence, novelty and asset mapping pass configured thresholds;
- market data is fresh, liquid enough, and not outside spread/volatility limits;
- technical strategy does not explicitly conflict, or a documented event strategy policy permits it;
- existing risk engine approves size, stop, target and exposure;
- paper portfolio has capacity and no cooldown/circuit breaker is active.

Default behavior on missing data, exceptions, parser changes, clock skew or disagreement is NO_TRADE.

## 8. Paper execution model

Paper trading must be a separate execution implementation with no exchange order client dependency. Use next eligible market bar after the event was first available to the system; never fill at the event publication price unless a reliable timestamped quote proves it was actually available then.

Simulate:
- adverse slippage and fees on entry and exit;
- conservative stop/target ambiguity (stop first if both are touched in one OHLC bar);
- position and notional caps, max concurrent positions, cooldown after event;
- gaps, rejected/late signals and insufficient simulated cash;
- event-driven exits only as explicit, versioned rules.

Record every decision including no-trade reasons. Store immutable inputs/config version so every paper result can be reproduced.

## 9. Look-ahead bias prevention

Every replay decision at time t may consume only data whose first_seen_at is at or before t and whose publication timestamp is not after t. A story published at 10:00 but first retrieved at 10:04 is unavailable to the strategy before 10:04.

Additional requirements:
- Use archived snapshots, not today's edited headline/summary, for historical replay.
- Separate chronological train/development/OOS periods; never tune thresholds on OOS results.
- Use walk-forward folds and preserve a final untouched holdout.
- Apply fees, slippage, spread assumptions and latency before scoring performance.
- Do not let duplicate syndicated articles count as independent corroboration.
- Report the number of usable events and timestamp-quality exclusions.
- Compare against baselines: technical-only, news-only diagnostic (not deployment candidate), and technical+news.

## 10. Risk controls

Initial paper defaults (configurable and conservative):
- max risk per trade: 0.5% of simulated equity for news-triggered positions;
- hard cap per trade: 1%;
- leverage: 1x in initial paper mode;
- one position per asset and small global concurrent-position cap;
- daily loss circuit breaker and mandatory cooldown after a large gap/slippage event;
- no averaging down, no martingale, no increasing risk because confidence is high;
- block trade when spread, volatility, data freshness, event confidence or risk calculation is outside limits.

These are initial simulation parameters to test, not claims of profitability or a recommendation to trade real funds.

## 11. Evaluation and acceptance criteria

Unit tests:
- valid/invalid RSS parsing and timezone normalization;
- duplicate URLs, syndicated titles and near-duplicate headlines;
- unknown or conflicting timestamps;
- unapproved source, retraction and corroboration states;
- ambiguous asset ticker and conflicting reports;
- signal is not generated before first-seen time;
- duplicate event cannot create duplicate positions;
- fees/slippage, gaps, stop-first ambiguity, cash/exposure limits;
- parser failure and stale market data fail closed;
- no imports or calls to live execution interfaces from news/paper modules.

Replay/validation:
- chronological OOS and walk-forward evaluation;
- compare technical-only vs technical+news with identical risk and cost assumptions;
- net PnL, drawdown, expectancy, profit factor, win rate, turnover, exposure, average holding time and confidence calibration;
- results reported with sample size and uncertainty; no performance threshold is treated as sufficient alone.

Do not call the feature accepted until tests run in CI, historical replay is reproducible, and paper-trading telemetry demonstrates correct fail-closed behavior. No live trading is in scope for this design.

## 12. Delivery sequence

1. Data contracts + source allowlist + RSS parser tests.
2. Normalization, deduplication, archival and source-health metrics.
3. Deterministic event classifier and explainable scoring.
4. Candidate signals that only write audit records.
5. Separate paper portfolio/execution simulator.
6. Event-time replay, OOS and walk-forward comparison.
7. Telegram reporting: top verified events, score rationale, open paper positions and no-trade reasons.
8. Security review and acceptance report.

## 13. Initial low-cost source references

- SEC press releases: https://www.sec.gov/newsroom/press-releases
- Federal Reserve RSS feed directory: https://www.federalreserve.gov/feeds/feeds.htm
- CoinDesk RSS: https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml
- Cointelegraph RSS: https://cointelegraph.com/rss

Verify each feed's current terms, format and availability during implementation; keep the source list configurable and do not treat all sources as equally authoritative.
