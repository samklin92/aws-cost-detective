## AWS Cost Detective

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/714ffcb6-76e0-446b-bd56-486069ddb5c3" />

An AI-augmented AWS cost analysis tool that pulls real billing data from Cost Explorer, runs deterministic anomaly detection against it, and uses Claude to explain what it finds in plain English — including a narrated spend breakdown and, where anomalies exist, a specific hypothesis and recommended next step.

Unlike a raw Cost Explorer dashboard, this tool tells you *which* cost changes matter and *why they might have happened*, not just that the numbers changed. It also includes a tag-compliance auditor for cost attribution, a CI pipeline that tests the deterministic logic on every push, and (previously, now torn down after verification) a reactive Budgets-alerting system built on Lambda.

---

## Why this exists

AWS Cost Explorer shows you what you spent. It doesn't help you distinguish a meaningless pricing-tier rounding artifact from a forgotten dev instance accumulating charges. In a real environment with dozens of services, that distinction is exactly what an engineer needs at 2am, not a table of numbers.

This project's value-add is **triage**: surfacing the one or two findings that warrant action, explaining them in concrete terms, and leaving genuinely quiet periods alone rather than manufacturing urgency where none exists.

---

## Architecture

```
AWS Cost Explorer API (boto3)
        |
        v
cost_fetcher.py  (pulls daily cost-by-service, normalized)
        |
        v
analyzer.py  (deterministic: spend breakdown + anomaly detection)
        |
        v
triage.py  (Claude: narrates breakdown, explains anomalies)
        |
        v
main.py  (CLI orchestrator)
```

Two components extend this core without modifying it:

```
tag_fetcher.py -> tag_auditor.py           (tag compliance, separate concern)

AWS Budgets -> SNS -> Lambda (handler.py)  (reactive alerting, reuses the core
                                             pipeline unchanged - see below)
```

### Design principle: deterministic detection, AI narration

- **What changed and whether it's anomalous** is determined by rule-based, unit-tested code in `analyzer.py`. If a finding is wrongly flagged or missed, that is a code bug to fix directly.
- **Why it might have happened and what to do** is Claude's job in `triage.py`. If an explanation is unclear or unhelpful, that is a prompt issue.

This separation is the same principle used in the companion [Terraform Drift Detector](https://github.com/samklin92/terraform-drift-detector) project — keeping the two concerns in different layers means failures are immediately attributable to the right layer.

---

## Anomaly detection design

Two layers, both required:

**1. Minimum dollar floor** — a service's spend on a given day must exceed an absolute threshold (default: $1.00) before it is even considered for statistical analysis. This exists because percentage-based comparisons break down at near-zero baselines: a jump from $0.0000000016 to $0.01 is mathematically a massive percentage increase but financially meaningless. Verified against real account data — most services sit at exactly $0 or fractions of a cent most days.

**2. Statistical deviation** — for services that clear the dollar floor, flag a day where spend is more than N standard deviations above the trailing baseline average *for that same service* (default: 2 stddevs). Comparing within a service, not across services, matters because different services have wildly different normal cost shapes.

**3. Minimum absolute deviation floor** — even if the stddev threshold is exceeded, the dollar gap above baseline must itself clear a real threshold (default: $1.00). This was added to fix a real bug found during unit testing — see below.

---

## Tag compliance auditor

`tag_fetcher.py` + `tag_auditor.py` check every taggable resource in the account against a required-tags policy (`Project`, `Environment`, `ManagedBy`), answering the FinOps question "how do you enforce cost attribution in a multi-project account."

Two deliberate design decisions, both covered by unit tests:

- **Case-sensitive tag key matching** — `project` does not satisfy a requirement for `Project`. This matches how AWS and Cost Explorer's own cost-allocation tags behave; a lenient checker would report false compliance.
- **Empty-string values count as missing, not present** — a tag key that exists with `""` as its value isn't meaningfully attributed to anything. This is a real pattern from scripts that add a tag key without populating it.

Uses the Resource Groups Tagging API (`tag:GetResources`), which is free — unlike Cost Explorer, no per-call cost tracking needed for this module.

```bash
python cost_engine/tag_auditor.py
```

---

## CI/CD pipeline

GitHub Actions runs two jobs on every push:

**`test-tools`** (free, every push and PR) — 28 pytest tests across the anomaly detector, the cost fetcher's data model and documented API quirks, the triage layer's JSON parsing, and the tag auditor. Everything mocked — zero AWS or Anthropic calls, ~0.65s runtime.

**`smoke-test-live`** (costed, push to `main` only, gated behind `test-tools` passing) — runs the actual pipeline against real Cost Explorer and real Claude, using a dedicated IAM identity scoped to exactly `ce:GetCostAndUsage` and nothing else. Confirms the pipeline still works against the real APIs, not just against mocks — catches live schema drift that unit tests can't.

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

---

## Reactive Budgets alerting (built, verified, torn down)

A Terraform-managed stack (AWS Budgets → SNS → Lambda) was built to make this tool reactive instead of only scheduled or manually invoked: a real budget threshold breach triggers the exact same fetch → analyze → triage pipeline automatically, posting the result straight to Slack.

```
AWS Budgets (80% actual, 100% forecasted)
        |
        v
   SNS Topic
        |
        v
  Lambda (handler.py) --calls--> cost_fetcher + analyzer + triage (unchanged)
        |
        v
      Slack
```

**This was built with a $50/month test threshold, verified end-to-end with a real manual SNS trigger and a real message landing in Slack, then explicitly torn down** — `terraform destroy`, IAM user and keys deleted, SSM secrets deleted — since it was provisioned for verification, not to run unattended as production infrastructure indefinitely. The Terraform and Lambda source (`terraform/`, `lambda/`) remain in this repo; the live AWS resources do not currently exist. Re-deploying requires re-running `terraform apply` after recreating the IAM user and SSM secrets it depends on.

---

## Cost of running this tool

Every `get-cost-and-usage` API call against the primary billing view costs **$0.01**, and paginated results multiply that cost per page. The CLI tool issues exactly one API call per run — no local caching, so repeated runs for the same date range re-incur the charge each time.

Total AWS API spend across the original build and verification process: **$0.07** (7 calls × $0.01), tracked explicitly throughout. Since then, the CI pipeline's `smoke-test-live` job adds one additional Cost Explorer call (plus Claude token cost) on every merge to `main` — an ongoing, automatic cost, not a one-time build cost.

---

## Real bugs found during verification

### Bug 1 — Tight-baseline false positives (found via unit testing)

A pure stddev-based anomaly detector is sensitive to artificially tight baselines. If a service happens to be extremely stable for a few days (e.g. $5.00, $5.10, $4.90, $5.00), its baseline stddev becomes very small — so even a trivial absolute deviation (e.g. $5.20 instead of $5.00) can compute as "many standard deviations above baseline" despite being financially meaningless.

**Found by:** a specifically-designed synthetic unit test with a tight-variance baseline, not by live data. The live data on this account is too quiet to surface this pattern naturally.

**Fix:** `MIN_ABSOLUTE_DEVIATION` requires the dollar gap above baseline to also clear a real threshold, not just a statistical one. All 5 unit test scenarios pass after this fix.

### Bug 2 — File corruption from repeated heredoc paste

`cost_fetcher.py` ended up with its entire content duplicated, with a broken shell command embedded at the seam between the two copies. The file ran correctly when invoked directly but failed immediately when another module tried to import it, since Python loads the full module on import.

**Found by:** re-cloning the repository months later and re-verifying a previously "fixed" bug against the actual pushed state — git history showed the corrupted version was the only version ever committed. The local fix never made it into git.

**Fix:** confirmed the two halves of the file were byte-identical, then trimmed to the first clean copy and verified the import succeeded before committing.

### Bug 3 — Test fixture testing the wrong code branch

The first draft of the tight-baseline unit test used three **identical** baseline values (`$5.00, $5.00, $5.00`), which produces an exact-zero stddev — this hits the zero-baseline "new spend" branch, not the tight-wobble branch the test was meant to exercise. Two tests failed against known-correct code.

**Found by:** the test suite itself failing in a way that didn't match the code's actual behavior — a signal to check the test, not the implementation.

**Fix:** used the documented wobble example (`$5.00, $5.10, $4.90`) instead of round, identical numbers.

### Bug 4 — Wrong function signature assumed (Lambda handler)

The reactive Lambda handler was first written calling `triage_cost_report(report, anomalies, mode="slack")` — but that `mode` parameter belongs to the Terraform Drift Detector's `triage.py`, a structurally similar but separate project. This project's `triage_cost_report()` takes only `(report, anomalies)`.

**Found by:** re-reading the actual file instead of trusting memory of a similar-looking project.

**Fix:** removed the invalid keyword argument; added a unit test asserting the function is always called with exactly two positional arguments.

### Bug 5 — Secret-retrieval ordering bug (Lambda handler)

The handler originally called `triage_cost_report()` before setting `ANTHROPIC_API_KEY` in the process environment — but that function reads the key from `os.environ` internally. Would have failed with `KeyError` on the first real invocation.

**Found by:** code review before deployment, then locked in with a test asserting the secret is resolved before triage runs.

**Fix:** reordered so SSM secret retrieval happens before any call that depends on it.

### Bug 6 — Cross-platform packaging bug (Lambda deployment)

`pip install anthropic` run on a Windows machine fetched Windows-compiled wheels for `pydantic_core` (a compiled Rust extension, a dependency of `anthropic` via `pydantic`). Lambda runs Linux — the Windows binary doesn't load there.

**Found by:** `Runtime.ImportModuleError: No module named 'pydantic_core._pydantic_core'` in real CloudWatch logs on the first live invocation attempt — this only surfaces at actual Lambda execution, not at package-build time.

**Fix:** forced manylinux wheels explicitly in the packaging script, regardless of host OS:
```bash
pip install anthropic python-dotenv \
  --target ./package \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all:
```

---

## Verified API behavior (before writing any code against assumptions)

Confirmed against a single real Cost Explorer query before writing `cost_fetcher.py`:

- `ResultsByTime` is a list, one entry per day for `DAILY` granularity
- `Groups[].Metrics.UnblendedCost.Amount` is a **string**, not a float — requires explicit `float()` conversion
- Services with zero cost still appear in `Groups` — zero-cost entries must be filtered deliberately, not assumed absent
- The set of services is **not consistent across days** — a service may appear on one day and be absent the next if its cost was exactly zero
- Every period was marked `"Estimated": true` — Cost Explorer has a documented ~24 hour data lag, so very recent days' figures are not final

---

## Setup

```bash
pip install -r requirements.txt
```

For running tests locally, install dev dependencies instead:

```bash
pip install -r requirements-dev.txt
```

Create a `.env` file in the project root (never commit this — already in `.gitignore`):

```
ANTHROPIC_API_KEY=your-key-here
```

AWS credentials are picked up from your standard AWS CLI configuration. The IAM user or role needs `ce:GetCostAndUsage` for the core tool, and `tag:GetResources` if using the tag auditor — nothing broader than that.

---

## Usage

```bash
python cost_engine/main.py --start 2026-06-21 --end 2026-06-28
```

`--end` is exclusive, matching Cost Explorer's own date-range convention.

### Example output — quiet account

```
=== AWS Cost Report ===

Total spend for the week is extremely low at roughly $0.08, driven entirely
by EC2 - Other (data transfer, EBS volumes, Elastic IPs, NAT Gateway charges)
while S3 rounds to zero. This is a very light-footprint sandbox account.

No anomalies detected.

--- Summary ---
This was a quiet, stable week with negligible cloud spend and no detected
anomalies. No action is required from a cost-management perspective.
```

### Example output — with a real anomaly

```
=== AWS Cost Report ===

Spend is heavily dominated by Amazon EC2 at $115.00, almost entirely driven
by a single-day spike rather than steady daily usage.

--- Anomalies ---

  2026-06-07 | Amazon EC2
    EC2 spend hit $85.00 against a baseline average of $5.00 - a 17x jump.
    Likely cause: A large instance or cluster may have been launched for a
    one-off job or load test and not terminated promptly.
    Recommended action: Check CloudTrail for RunInstances events on June 7th.

--- Summary ---
The EC2 anomaly warrants immediate attention. Resolving whether those
instances are still running should be the first priority.
```

---

## Project structure

```
cost_engine/
├── cost_fetcher.py   # Pulls Cost Explorer data, normalizes to clean structs
├── analyzer.py        # Deterministic: spend breakdown + anomaly detection
├── triage.py           # Claude: narrates breakdown, explains anomalies
├── tag_fetcher.py      # Resource Groups Tagging API calls
├── tag_auditor.py      # Deterministic tag-compliance checking
└── main.py             # CLI orchestrator

tests/                  # 28 pytest tests, fully mocked, zero AWS/Anthropic cost
├── conftest.py
├── test_analyzer.py
├── test_cost_fetcher.py
├── test_triage.py
└── test_tag_auditor.py

scripts/
└── smoke_test_live.py  # Real end-to-end check, runs only in CI on push to main

lambda/                 # Reactive Budgets-alerting Lambda (source kept, infra torn down)
├── handler.py
├── package_lambda.sh
└── test_lambda_handler.py

terraform/              # Budget, SNS, Lambda, IAM - all as code (currently destroyed)

iam/
└── ci-smoke-test-policy.json

.github/workflows/
└── ci.yml              # test-tools (free) + smoke-test-live (costed, main only)
```

---

## Configurable thresholds

All thresholds are constants in `analyzer.py`, overridable per-call.

| Constant | Default | Purpose |
|---|---|---|
| `DEFAULT_DOLLAR_FLOOR` | `$1.00` | Minimum daily spend to consider |
| `DEFAULT_STDDEV_THRESHOLD` | `2.0` | Standard deviations above baseline to flag |
| `MIN_DAYS_FOR_BASELINE` | `3` | Minimum prior days needed for a baseline |
| `MIN_ABSOLUTE_DEVIATION` | `$1.00` | Minimum dollar gap above baseline |

---

## Limitations

- Claude's `likely_cause` is an inference from cost numbers alone, not from CloudTrail or deployment history. Treat it as a hypothesis to verify.
- No local caching — every run costs $0.01 in Cost Explorer API charges.
- Anomaly detection is per-service. Multi-service correlated spikes produce separate findings rather than a single correlated finding.
- Tested against a sandbox account. Thresholds may need tuning for accounts with higher, more variable baseline spend.
- The reactive Budgets-alerting infrastructure is not currently live (see above) — the CLI tool and CI pipeline are the only components running today.

---

## Author

**Ogaji Igwe Samuel** — Cloud & DevOps Engineer
- GitHub: [https://github.com/samklin92](https://github.com/samklin92)
- Email: samklinofficial91@gmail.com
