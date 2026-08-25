# Dynamic Risk Trigger Engine

**Event-driven customer risk reassessment for AML/KYC.** Instead of reassessing
customers on a fixed calendar, this engine reassesses them when something
happens — resolving the entity a signal refers to, deciding whether the signal is
material, and updating a Bayesian risk score whose every movement is explainable
and permanently recorded.

Phase 4 completes the prototype: a React analyst console, an append-only audit
trail enforced at the database level, JWT role-based access control, and an
evaluation harness that measures this engine against the incumbent control on a
labeled scenario.

---

## 1. The problem

In July 2025 the FCA fined Barclays a total of **£42 million** across two
separate financial-crime control failures. Both are failures of *continuous*
reassessment, not of onboarding.

**Barclays Bank UK plc — £3,093,600 (the WealthTek case).**
Barclays opened a client money account for WealthTek without checking it had
gathered enough information to understand the money-laundering risk. As the FCA
put it, one simple check would have been to look at the Financial Services
Register before opening the account — which would have shown that **WealthTek was
not permitted by the FCA to hold client money at all**. Barclays also made a
voluntary payment of £6,281,757 to WealthTek clients facing a shortfall.

**Barclays Bank plc — £39.3 million (the Stunt & Co case).**
Barclays failed to adequately manage the money-laundering risk of banking Stunt &
Co, which over little more than a year received **£46.8 million from Fowler
Oldfield**, a multimillion-pound money laundering operation. Adverse information
accumulated while the relationship continued.

Sources:
- [FCA fines Barclays £42 million for poor handling of financial crime risks](https://www.fca.org.uk/news/press-releases/fca-fines-barclays-42-million-poor-handling-financial-crime-risks)
- [Final Notice: Barclays Bank UK plc (2025)](https://www.fca.org.uk/publication/final-notices/barclays-bank-uk-plc-2025.pdf)
- [Final Notice: Barclays Bank plc (2025)](https://www.fca.org.uk/publication/final-notices/barclays-bank-plc-2025.pdf)

### Why periodic review is the wrong shape for this

The standard control is **periodic review**: reassess low-risk customers every
three years, high-risk every year, on a schedule. The schedule is the problem.

1. **It is indexed on the calendar, not on evidence.** A firm losing its client
   money permission, a director change that hands control to someone new, a
   sanctions designation — none of these wait for the review date.
2. **Point-in-time evidence expires.** A news story is superseded; an unusual
   payment pattern ages out of the monitoring window. A review 60 days later
   cannot see what a review on the day would have seen.
3. **Most reviews find nothing.** On the evaluation scenario below, **53.5% of
   scheduled reviews turned up no material event at all** — analyst capacity
   spent confirming that nothing happened, which is capacity not spent on the
   cases where something did.

Measured on the labeled scenario in §5, the periodic control caught **66.1%** of
material events at a **median 32 days** after they occurred. This engine catches
**95.0%** at a **median 2.0 hours**.

---

## 2. Architecture

```
                        EXTERNAL SIGNAL SOURCES
   ┌──────────────┬──────────────┬──────────────┬─────────────────────┐
   │ Companies    │ OpenSanctions│ FCA Register │ NewsAPI /           │
   │ House        │              │              │ Txn monitoring      │
   └──────┬───────┴──────┬───────┴──────┬───────┴──────────┬──────────┘
          │              │              │                  │
          └──────────────┴──────┬───────┴──────────────────┘
                                ▼
                   ┌────────────────────────┐
                   │  Redis Streams queue   │   at-least-once delivery
                   │  risk_events_stream    │   (in-memory fallback)
                   └───────────┬────────────┘
                               ▼
  ╔══════════════════════════════════════════════════════════════════════╗
  ║                       WORKER PIPELINE (app/worker.py)                ║
  ║                                                                      ║
  ║  ① ENTITY RESOLUTION            Phase 1  app/entity_resolution.py    ║
  ║     exact → normalised → fuzzy (Jaro-Winkler + token-sort)           ║
  ║     against customer names AND registered aliases                    ║
  ║                    │ matched_customer_id, confidence, method         ║
  ║                    ▼                                                 ║
  ║  ② SIGNAL CLASSIFICATION        Phase 2  app/classifier.py           ║
  ║     event_type → (category, severity) from a JSON rule table         ║
  ║                    │                                                 ║
  ║                    ▼                                                 ║
  ║  ③ MATERIALITY GATE             Phase 2  app/materiality.py          ║
  ║     domain relevance · match confidence ≥ threshold ·                ║
  ║     deduplication window · minimum-severity floor                    ║
  ║                    │ SUPPRESSED ─────────────► stop (audited)        ║
  ║                    ▼ MATERIAL                                        ║
  ║  ④ BAYESIAN RISK UPDATE         Phase 3  app/risk_engine.py          ║
  ║     log-odds_new = log-odds_prev + ln(LR)                            ║
  ║                    │                                                 ║
  ║                    ▼                                                 ║
  ║  ⑤ TIER BOUNDARY CHECK          Phase 3                              ║
  ║     crossed a boundary, or HIGH/CRITICAL severity? → raise alert     ║
  ╚═══════════════════════════════╤══════════════════════════════════════╝
                                  │
        every step, without exception, appends to ▼
        ┌──────────────────────────────────────────────────────────┐
        │  APPEND-ONLY AUDIT TRAIL          Phase 4  app/audit.py  │
        │  SHA-256 hash chain · PostgreSQL triggers refuse         │
        │  UPDATE / DELETE / TRUNCATE, including to the owner      │
        └──────────────────────────┬───────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  FastAPI  +  JWT RBAC (Analyst · Manager · Auditor)               │
   └───────────────────────────────┬───────────────────────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  ANALYST CONSOLE — React + Tailwind + Recharts                    │
   │  Alert queue │ Investigation │ Audit trail │ Live pipeline │ Eval  │
   └───────────────────────────────────────────────────────────────────┘
```

### Repository layout

| Path | What lives there |
|---|---|
| `app/entity_resolution.py` | Phase 1 — multi-tier matching against names and aliases |
| `app/classifier.py`, `app/classifier_rules.json` | Phase 2 — category/severity rule table |
| `app/materiality.py` | Phase 2 — the materiality gate |
| `app/risk_engine.py` | Phase 3 — prior model + Bayesian log-odds update + alerting |
| `app/anomaly_detector.py` | Phase 3 — IsolationForest transaction anomalies |
| `app/audit.py` | Phase 4 — hash-chained append-only audit logger |
| `app/db_constraints.py` | Phase 4 — the PostgreSQL triggers that enforce immutability |
| `app/auth.py` | Phase 4 — JWT, password hashing, role/permission matrix |
| `app/evaluation/` | Phase 4 — labeled scenario generator and the two-arm harness |
| `app/reset_demo.py` | Phase 4 — reset the demo to a clean, reproducible state |
| `frontend/` | Phase 4 — the analyst console |

---

## 3. The algorithm: Bayesian log-odds

Risk is modelled as **P(customer is high risk)**, held internally as log-odds.
Log-odds is the right representation because in that space **evidence is
additive**: each new signal contributes a fixed amount, independent of what came
before, and the running total is a single number that can be audited.

### Step 0 — the onboarding prior

A logistic model over onboarding attributes (`app/risk_engine.py`,
`PriorRiskModel`):

```
logit₀ = β₀ + β_ind·x_ind + β_cty·x_cty + β_pep·x_pep
            + β_sanc·x_sanc + β_corp·x_corp + β_turnover·ln(turnover ratio)
```

| Term | Weight | Fires when |
|---|---|---|
| `β₀` intercept | −3.20 | always — a base rate of ~3.9% |
| `β_ind` high-risk industry | +1.20 | crypto, defence, precious metals, real estate, gaming, commodities |
| `β_cty` high-risk country | +1.20 | KY, VG, PA, AE, IR, KP, RU, SY, MM |
| `β_pep` PEP | +1.80 | politically exposed person |
| `β_sanc` sanctioned | +3.50 | already on a sanctions list |
| `β_corp` corporate | +0.40 | corporate rather than individual |
| `β_turnover` | +0.50 × ln(ratio) | actual turnover exceeds the agreed profile |

### Step n — one event, one update

Each material event carries a **likelihood ratio**:

```
LR = P(observing this event | customer IS high risk)
     ────────────────────────────────────────────────
     P(observing this event | customer is NOT high risk)
```

Bayes' theorem in odds form is a multiplication, so in log space it is an
addition — which is the whole reason for working in log-odds:

```
odds_new     = odds_prev × LR
log-odds_new = log-odds_prev + ln(LR)
P(high risk) = 1 / (1 + e^(−log-odds))
```

An LR of 1 is uninformative and moves nothing. Configured LRs
(`DEFAULT_LIKELIHOOD_RATIOS`) include:

| Evidence | LR | ln(LR) |
|---|---|---|
| Sanctions match | 50.0 | +3.912 |
| Regulatory permission revoked | 30.0 | +3.401 |
| Law enforcement action | 25.0 | +3.219 |
| PEP flagged | 10.0 | +2.303 |
| Adverse media, high severity | 8.0 | +2.079 |
| Transaction anomaly | 6.0 | +1.792 |
| Director change | 3.0 | +1.099 |
| Address change | 1.2 | +0.182 |

### Tiers

| Tier | P(high risk) |
|---|---|
| LOW | < 0.20 |
| MEDIUM | 0.20 – 0.50 |
| HIGH | 0.50 – 0.80 |
| CRITICAL | ≥ 0.80 |

An alert is raised when a **tier boundary is crossed**, or when the event is
HIGH/CRITICAL severity. Lower-severity material events update the score silently
— they change the risk position without consuming analyst attention.

### A worked example, straight from the demo database

WealthTek Ltd, as `GET /customers/{id}/risk-timeline` returns it:

| Step | Evidence | LR | ln(LR) | log-odds | P(high risk) | Tier |
|---|---|---|---|---|---|---|
| 0 | Onboarding prior | — | — | −2.8000 | 5.7% | LOW |
| 1 | `CLIENT_MONEY_REVOCATION` | 30 | +3.4012 | +0.6012 | 64.6% | **HIGH** |
| 2 | `FRAUD_ALLEGATION` (via the alias "Vertem Asset Management") | 8 | +2.0794 | +2.6806 | 93.6% | **CRITICAL** |

Two boundary crossings, two alerts, and every number reproducible from the two
LRs and the prior. This is the property that matters for a regulator: the score
is not a black box output, it is a running sum whose terms are all named.

### Honest note on the model

The weights and likelihood ratios are **expert-set, not fitted** — there is no
labeled outcome data here to fit them to. They are held in one configuration
table specifically so they can be replaced with fitted values when such data
exists. The engine's *structure* (additive log-odds, explainable increments) is
the contribution; the specific constants are a starting calibration.

---

## 4. Compliance controls (Phase 4)

### 4.1 The append-only audit trail

Every state change — event ingested, entity matched or not matched, materiality
decision, risk score updated, alert generated, analyst decision, permission
denied, audit export — appends one record to `audit_logs` via `app/audit.py` and
by no other route.

**Two independent guarantees.**

**(a) Append-only, enforced by the database.** `app/db_constraints.py` installs
row-level `BEFORE UPDATE` and `BEFORE DELETE` triggers plus a statement-level
`BEFORE TRUNCATE` trigger, each raising an exception. PostgreSQL applies triggers
to the table owner too, so this holds against the account the application itself
connects as:

```
ERROR: audit_logs is append-only: UPDATE is not permitted on this table
       (attempted by role "aml_user"). Append a correcting record instead.
```

`GET /audit/immutability-proof` demonstrates this rather than asserting it: it
issues a real UPDATE and a real DELETE inside transactions that are always rolled
back, and returns what the database said. The application refuses to start
quietly if the guarantee is missing — startup verifies it and says so either way.

**(b) Tamper-evident, enforced by cryptography.** Each record stores the SHA-256
hash of its own canonical content — sequence number, actor, action, details,
timestamp — plus its predecessor's hash. `GET /audit/verify-chain` recomputes the
whole chain and names the first record that fails. Verified in the test suite by
disabling the trigger inside a rolled-back transaction, altering a record, and
asserting that verification points at exactly that record.

The two are complementary: the trigger stops the easy attack, the chain detects
the one that gets underneath it. The only sanctioned way to change history is to
append a correcting record.

Because there is no SQLite fallback any more, that guarantee cannot silently
disappear: `DATABASE_URL` must be PostgreSQL or the application fails to start.

### 4.2 Role-based access control

JWT bearer tokens (HS256), PBKDF2-HMAC-SHA256 password hashing at 260k
iterations. Three roles, mapping to how a financial-crime function is actually
separated:

| Permission | ANALYST | MANAGER | AUDITOR |
|---|:--:|:--:|:--:|
| View alert queue and customers | ✓ | ✓ | ✓ |
| Confirm / dismiss / request info | ✓ | ✓ | — |
| Escalate to MLRO | — | ✓ | — |
| Ingest events | ✓ | ✓ | — |
| View audit trail | ✓ | ✓ | ✓ |
| Export audit trail | — | ✓ | ✓ |
| Verify chain / immutability proof | — | ✓ | ✓ |
| Run the evaluation harness | — | ✓ | — |

The Auditor being unable to act is the point, not an oversight: an assurance
function that can dispose of the alerts it reviews is not independent. Gating is
enforced in the API, not merely hidden in the UI — and **a refused attempt is
itself audited**, because a denial is evidence a control operated.

Demo accounts (local only; override with `DEMO_*_PASSWORD`):

| Role | Username | Password |
|---|---|---|
| Analyst | `a.chen` | `analyst123` |
| Manager | `r.okafor` | `manager123` |
| Auditor | `j.lindqvist` | `auditor123` |

---

## 5. Evaluation: baseline vs engine

`app/evaluation/` generates a **labeled synthetic scenario where the ground truth
is fixed before either system sees the data**, then runs two policies over the
identical event stream.

- **Baseline** — static 90-day periodic review, anchored to each customer's
  onboarding anniversary, regardless of what happens in between.
- **Engine** — event-driven continuous reassessment, this system.

**Scenario:** 5,000 events across 1,000 customers over 365 days. Seed 42, so the
numbers below reproduce exactly. 1,506 events (30.1%) are material by ground
truth; 416 name an entity that is not on the customer book at all.

### 5.1 Headline results

| Metric | Baseline (90-day review) | **This engine** | Change |
|---|---|---|---|
| **Material event detection rate (recall)** | 66.1% | **95.0%** | **+28.9 pp** |
| **False positive rate** | 50.4% | **38.9%** | **−11.5 pp** |
| Precision | 36.1% | **51.3%** | +15.2 pp |
| F1 | 0.467 | **0.666** | +0.199 |
| **Median detection latency** | 32.2 days | **2.0 hours** | **388× faster** |
| Mean detection latency | 36.5 days | 4.3 hours | |
| 90th-percentile latency | 75.2 days | 1.0 day | |
| Worst-case latency | 90.0 days | 1.0 day | |
| Material events caught | 995 | **1,431** | **+436** |
| Material events missed | 511 | **75** | −436 |
| **Analyst touches** | 4,068 reviews | **2,079 alerts** | **−48.9%** |
| Touches per material event caught | 4.09 | **1.45** | −65% |
| Reviews that found nothing | 53.5% | n/a | |

In-process compute latency (matching + classification + gate + scoring, excluding
I/O): **median 0.37 ms**, p99 9.6 ms. Detection latency is dominated by feed
delivery, not compute — which is why the two are reported separately.

### 5.2 Entity resolution (Phase 1)

| Metric | At the production threshold (65) | At the recommended threshold (85) |
|---|---|---|
| Precision | 87.5% | **93.7%** |
| Recall | 95.5% | 94.7% |
| F1 | 0.913 | **0.942** |
| Off-book decoys correctly rejected | **0.0%** | **76.7%** |
| Matched to the wrong customer | 208 | 193 |
| Left unmatched | 0 | 48 |

**The evaluation's most useful finding is a defect in Phase 1.** At the shipped
fuzzy threshold of 65, the resolver accepts a customer match for **every single
one of the 416 off-book entities**. It never returns "no match", because with
1,000 customers something always scores above 65. That single fault is the
largest driver of the engine's false positives (245 of 1,360) *and* the largest
driver of its misses (73 of 75 — a material event scored against the wrong
customer is not a detection).

The threshold has been **left at its shipped value and the whole trade-off curve
published** (in the report and via `GET /evaluation/latest`) rather than quietly
retuned. Picking the threshold that flatters this scenario and then reporting the
result as validation would be fitting the system to its own test. The
recommendation — move to ~85 — is verifiable in one call:

```bash
curl -X POST localhost:8000/evaluation/run -H "Authorization: Bearer $MANAGER" \
  -H 'Content-Type: application/json' \
  -d '{"num_customers":1000,"num_events":5000,"horizon_days":365,
       "baseline_review_interval_days":90,"random_seed":42,
       "fuzzy_match_threshold":85}'
```

At 85: recall 94.4% (−0.7 pp), FPR 33.3% (−5.6 pp), precision 55.0% (+3.7 pp),
F1 0.695. A clear net improvement, and the top remediation item.

### 5.3 Where each system goes wrong

**Engine — why it missed 75 of 1,506 material events**

| Cause | Count |
|---|---|
| Matched to the wrong customer (Phase 1) | 73 |
| Suppressed by the 24h deduplication window | 2 |

**Engine — why it raised 1,360 false positives**

| Cause | Count |
|---|---|
| Gate passed `CORPORATE_CHANGE` / MEDIUM (routine director changes) | 421 |
| Gate passed `ADVERSE_MEDIA` / MEDIUM (minor local coverage) | 361 |
| Gate passed `TRANSACTION_ANOMALY` / MEDIUM (variance within tolerance) | 272 |
| Matched an entity not on the book (Phase 1) | 245 |
| Matched the wrong customer (Phase 1) | 45 |
| Gate passed `GEOGRAPHIC_EXPOSURE` / MEDIUM | 16 |

The engine's false positive rate is its weakest number and the report says so.
The mechanism is specific: the materiality gate's severity floor sits at LOW, so
MEDIUM-severity routine signal clears it. Raising the floor to MEDIUM would cut
roughly 1,070 false positives at the cost of the genuinely material
MEDIUM-severity events (`PEP_ASSOCIATE`, `HIGH_RISK_JURISDICTION_EXPOSURE`) — a
policy decision for a compliance function to make, not one to bury in a
threshold.

**Baseline — why it missed 511 of 1,506**

| Cause | Count |
|---|---|
| Evidence no longer discoverable by the review date | 328 |
| No scheduled review before the horizon ended | 183 |

Both are structural to a fixed-schedule policy, not tuning choices.

### 5.4 Method, and how it avoids flattering the engine

**The engine arm runs the production components, not a reimplementation.**
`SignalClassifier` for classification, `EntityResolver.resolve_against` for
matching (the same function the live pipeline calls), `MaterialityGate.evaluate`
for the gate policy, `BayesianRiskEngine` for scoring. Only two things are
substituted, both I/O rather than policy: deduplication is answered from an
in-memory index via the gate's `duplicate_lookup` seam, and scoring runs with
`persist=False` so 5,000 events do not write 5,000 rows into the live tables.

**Ground truth is defined from the obligation, not from the engine's rules.**
Materiality follows the MLR 2017 / JMLSG notion of a trigger event: a change in
circumstances capable of altering the customer's risk profile or the adequacy of
existing due diligence. Defining it as "whatever the severity filter passes"
would have made the evaluation circular and guaranteed a perfect score.

**Hard cases are kept in, not filtered out.** 1,212 of the 5,000 events are
archetypes deliberately chosen to sit across the engine's decision boundaries —
routine director changes it over-flags, minor media coverage it over-flags,
in-jurisdiction counterparty exposure it has no test for, and off-book decoy
names. They are the reason the engine's false positive rate is 38.9% rather than
something prettier.

**Stated assumptions.** Every modelling choice that moves a number is written
into the report's `assumptions` block and rendered in the console:

- *Evidence persistence* — point-in-time evidence (adverse media 21–30 days,
  transaction patterns 45 days) stops being discoverable; register filings and
  sanctions designations are permanent. This is the mechanism by which a periodic
  review genuinely misses things rather than an arbitrary penalty against it.
- *Feed lag* — the engine cannot act before its feed delivers, so per-source lag
  is charged against it: sanctions 2h, FCA Register 6h, news 1h, Companies House
  24h, internal transaction monitoring 0.25h. This is why its median latency is
  2 hours rather than zero.
- *Baseline false positives* — a periodic review is **indiscriminate rather than
  inaccurate**: it sweeps whatever is in the file, so it picks up noise and
  signal alike. Its distinguishing weakness is latency and wasted effort, not
  recall. Both are reported rather than collapsed into one number.

---

## 6. Running it

### Prerequisites

Docker (for PostgreSQL and Redis), Python 3.10+, Node 20+.

### Backend

```bash
cp .env.example .env          # then edit if you want live connector keys

docker compose up -d postgres redis
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate elsewhere
pip install -r requirements.txt

# Clean, reproducible demo state: 2,006 customers, 12 events through the real
# pipeline, and a populated evaluation report.
python -m app.reset_demo --yes --evaluate

uvicorn app.main:app --reload --port 8000
```

Or run the API in Docker too: `docker compose up -d` (it reads `.env`).

API docs at http://localhost:8000/docs. Readiness at `/health/ready`.

### Console

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies `/api` to the backend, so the browser stays same-origin.
Sign in with any account from §4.2 — click one on the sign-in screen to fill the
form.

Optionally run the console in compose instead:
`docker compose --profile console up -d console`.

### Tests

```bash
python -m pytest app/tests/ -q      # 28 passed
```

Tests run against throwaway PostgreSQL **schemas**, not SQLite — the audit chain
serialises appends with `pg_advisory_xact_lock` and immutability is a Postgres
trigger, so a SQLite double would test neither. Note the search path is set to
the test schema *only*: with `public` also on the path, `create_all` finds the
live tables, skips creating the test copies, and the tests write into the demo
database.

---

## 7. The end-to-end demo

Five minutes, following one event the whole way through.

1. **Sign in as `a.chen` / `analyst123`** (Analyst). Note the role in the sidebar.

2. **Alert queue.** Eight open alerts, six CRITICAL, sorted worst-tier-first then
   by the size of the score movement. Columns are customer, previous tier,
   current tier, primary trigger, and timestamp.

3. **Live pipeline → "FCA client-money revocation" → Inject event.** Watch the
   stages appear with their own timings:

   ```
   ENTITY_RESOLUTION   MATCHED      WealthTek Ltd, EXACT, 100%
   CLASSIFICATION      CLASSIFIED   REGULATORY_CHANGE / CRITICAL
   PERSIST_EVENT       PERSISTED    event #N
   MATERIALITY_GATE    MATERIAL     with the gate's own stated reasons
   ALERT_GENERATION    GENERATED    HIGH → CRITICAL
   AUDIT_LOG           WRITTEN      5 hash-chained records
   ```

   Then try **"Routine filing (control case)"** — suppressed by the gate, no
   alert. A system that alerts on everything is a system nobody reads.

   And **"Adverse media, arriving under a trading name"** — the feed says "Vertem
   Asset Management", the resolver returns WealthTek Ltd via its registered
   alias.

4. **Investigate** from the alert. The risk score evolution chart with tier
   bands; the "why" panel listing each event's likelihood ratio and log-odds
   contribution, summing exactly to the current score.

5. **Confirm reassessment** with a note. Then try **Escalate to MLRO** — refused,
   because escalation needs a Manager. The refusal is recorded.

6. **Audit trail.** Every step, oldest first, system actions and analyst
   decisions distinguished by icon and label. Expand any record for its details
   and its position in the hash chain. Sign in as `r.okafor` (Manager) or
   `j.lindqvist` (Auditor) to see the integrity panels and export as JSON or CSV
   — the export is itself audited.

7. **Evaluation.** The closing proof: baseline versus engine on detection rate,
   false positive rate, latency, and analyst workload, with the cumulative
   detection curve, delay by category, the entity-resolution threshold sweep, and
   a table view of every number.

---

## 8. Known limitations

Stated plainly, because a prototype that hides its edges is not a useful one.

1. **Entity resolution over-matches at the shipped threshold.** §5.2. The single
   highest-value fix, and the evaluation quantifies exactly what it buys.
2. **The materiality gate's severity floor is too low.** MEDIUM-severity routine
   signal clears it, producing ~1,070 of the 1,360 false positives. Raising the
   floor is a policy decision with a real recall cost.
3. **Likelihood ratios and prior weights are expert-set, not fitted.** No labeled
   outcome data exists here to fit them to. They live in one config table for
   exactly that reason.
4. **The materiality gate calls the risk engine.** A layering inversion: the gate
   should decide materiality and the caller should score. It is why the
   `on_decision` callback exists — to get the audit records into causal order
   without restructuring Phase 2 late in the build. Worth fixing properly.
5. **Evaluation uses synthetic data with a hand-built ground truth.** Realistic in
   shape and honest in construction, but no substitute for a back-test against
   real alert outcomes.
6. **The worker runs synchronously behind the HTTP request** so the console can
   show the trace. `run_worker_loop` exists for real asynchronous consumption;
   production would use it.
7. **JWTs are not revocable before expiry** — no refresh tokens, no denylist.
   Fine for a prototype, not for production.
8. **The engine's false positive rate is 38.9%.** Better than the baseline's
   50.4%, but not good. The report attributes every one of them to a cause rather
   than reporting a single number and moving on.
