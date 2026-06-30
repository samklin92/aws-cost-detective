\# AWS Cost Detective



An AI-augmented AWS cost analysis tool that pulls real billing data from Cost

Explorer, runs deterministic anomaly detection against it, and uses Claude to

explain what it finds in plain English — including a narrated spend breakdown

and, where anomalies exist, a specific hypothesis and recommended next step.



Unlike a raw Cost Explorer dashboard, this tool tells you \*which\* cost changes

matter and \*why they might have happened\*, not just that the numbers changed.



\---



\## Why this exists



AWS Cost Explorer shows you what you spent. It doesn't help you distinguish a

meaningless pricing-tier rounding artifact from a forgotten dev instance

accumulating charges. In a real environment with dozens of services, that

distinction is exactly what an engineer needs at 2am, not a table of numbers.



This project's value-add is \*\*triage\*\*: surfacing the one or two findings that

warrant action, explaining them in concrete terms, and leaving genuinely quiet

periods alone rather than manufacturing urgency where none exists.



\---



\## Architecture



```

AWS Cost Explorer API (boto3)

&#x20;       |

&#x20;       v

cost\_fetcher.py  (pulls daily cost-by-service, normalized)

&#x20;       |

&#x20;       v

analyzer.py  (deterministic: spend breakdown + anomaly detection)

&#x20;       |

&#x20;       v

triage.py  (Claude: narrates breakdown, explains anomalies)

&#x20;       |

&#x20;       v

main.py  (CLI orchestrator)

```



\### Design principle: deterministic detection, AI narration



\- \*\*What changed and whether it's anomalous\*\* is determined by rule-based,

&#x20; unit-tested code in `analyzer.py`. If a finding is wrongly flagged or

&#x20; missed, that is a code bug to fix directly.

\- \*\*Why it might have happened and what to do\*\* is Claude's job in `triage.py`.

&#x20; If an explanation is unclear or unhelpful, that is a prompt issue.



This separation is the same principle used in the companion

\[Terraform Drift Detector](https://github.com/samklin92/terraform-drift-detector)

project — keeping the two concerns in different layers means failures are

immediately attributable to the right layer.



\---



\## Anomaly detection design



Two layers, both required:



\*\*1. Minimum dollar floor\*\* — a service's spend on a given day must exceed an

absolute threshold (default: $1.00) before it is even considered for statistical

analysis. This exists because percentage-based comparisons break down at

near-zero baselines: a jump from $0.0000000016 to $0.01 is mathematically a

massive percentage increase but financially meaningless. Verified against real

account data — most services sit at exactly $0 or fractions of a cent most days.



\*\*2. Statistical deviation\*\* — for services that clear the dollar floor, flag

a day where spend is more than N standard deviations above the trailing

baseline average \*for that same service\* (default: 2 stddevs). Comparing within

a service, not across services, matters because different services have wildly

different normal cost shapes.



\*\*3. Minimum absolute deviation floor\*\* — even if the stddev threshold is

exceeded, the dollar gap above baseline must itself clear a real threshold

(default: $1.00). This was added to fix a real bug found during unit testing

— see below.



\---



\## Cost of running this tool



Every `get-cost-and-usage` API call against the primary billing view costs

\*\*$0.01\*\*, and paginated results multiply that cost per page. This tool is

designed to issue exactly one API call per run. Running it repeatedly for the

same date range re-incurs the $0.01 charge each time — there is no local

caching in this version.



Total AWS API spend across the entire build and verification process for this

project: \*\*$0.07\*\* (7 calls x $0.01), tracked explicitly throughout.



\---



\## Real bugs found during verification



\### Bug 1 — Tight-baseline false positives (found via unit testing)



A pure stddev-based anomaly detector is sensitive to artificially tight

baselines. If a service happens to be extremely stable for a few days (e.g.

$5.00, $5.10, $4.90, $5.00), its baseline stddev becomes very small — so

even a trivial absolute deviation (e.g. $5.20 instead of $5.00) can compute

as "many standard deviations above baseline" despite being financially

meaningless.



\*\*Found by:\*\* a specifically-designed synthetic unit test with a tight-variance

baseline, not by live data. The live data on this account is too quiet to

surface this pattern naturally.



\*\*Fix:\*\* `MIN\_ABSOLUTE\_DEVIATION` requires the dollar gap above baseline to

also clear a real threshold, not just a statistical one. All 5 unit test

scenarios pass after this fix.



\### Bug 2 — File corruption from repeated heredoc paste



`cost\_fetcher.py` ended up with its entire content duplicated, with a broken

shell command embedded at the seam between the two copies. The file ran

correctly when invoked directly but failed immediately when another module

tried to import it, since Python loads the full module on import.



\*\*Found by:\*\* `analyzer.py` failing on import with a `NameError` — a different

failure mode than direct execution, which is exactly why integration testing

catches this class of bug.



\*\*Fix:\*\* full overwrite of the file, verified at 120 lines before re-running.



\---



\## Verified API behavior (before writing any code against assumptions)



Confirmed against a single real Cost Explorer query before writing

`cost\_fetcher.py`:



\- `ResultsByTime` is a list, one entry per day for `DAILY` granularity

\- `Groups\[].Metrics.UnblendedCost.Amount` is a \*\*string\*\*, not a float —

&#x20; requires explicit `float()` conversion

\- Services with zero cost still appear in `Groups` — zero-cost entries must

&#x20; be filtered deliberately, not assumed absent

\- The set of services is \*\*not consistent across days\*\* — a service may

&#x20; appear on one day and be absent the next if its cost was exactly zero

\- Every period was marked `"Estimated": true` — Cost Explorer has a documented

&#x20; \~24 hour data lag, so very recent days' figures are not final



\---



\## Setup



```bash

pip install boto3 anthropic python-dotenv

```



Create a `.env` file in the project root (never commit this — already in

`.gitignore`):



```

ANTHROPIC\_API\_KEY=your-key-here

```



AWS credentials are picked up from your standard AWS CLI configuration.

The IAM user or role needs `ce:GetCostAndUsage` permission — the minimum

required, nothing broader.



\---



\## Usage



```bash

python cost\_engine/main.py --start 2026-06-21 --end 2026-06-28

```



`--end` is exclusive, matching Cost Explorer's own date-range convention.



\### Example output — quiet account



```

=== AWS Cost Report ===



Total spend for the week is extremely low at roughly $0.08, driven entirely

by EC2 - Other (data transfer, EBS volumes, Elastic IPs, NAT Gateway charges)

while S3 rounds to zero. This is a very light-footprint sandbox account.



No anomalies detected.



\--- Summary ---

This was a quiet, stable week with negligible cloud spend and no detected

anomalies. No action is required from a cost-management perspective.

```



\### Example output — with a real anomaly



```

=== AWS Cost Report ===



Spend is heavily dominated by Amazon EC2 at $115.00, almost entirely driven

by a single-day spike rather than steady daily usage.



\--- Anomalies ---



&#x20; 2026-06-07 | Amazon EC2

&#x20;   EC2 spend hit $85.00 against a baseline average of $5.00 — a 17x jump.

&#x20;   Likely cause: A large instance or cluster may have been launched for a

&#x20;   one-off job or load test and not terminated promptly.

&#x20;   Recommended action: Check CloudTrail for RunInstances events on June 7th.



\--- Summary ---

The EC2 anomaly warrants immediate attention. Resolving whether those

instances are still running should be the first priority.

```



\---



\## Project structure



```

cost\_engine/

├── cost\_fetcher.py   # Pulls Cost Explorer data, normalizes to clean structs

├── analyzer.py        # Deterministic: spend breakdown + anomaly detection

├── triage.py           # Claude: narrates breakdown, explains anomalies

└── main.py             # CLI orchestrator

```



\---



\## Configurable thresholds



All thresholds are constants in `analyzer.py`, overridable per-call.



| Constant | Default | Purpose |

|---|---|---|

| `DEFAULT\_DOLLAR\_FLOOR` | `$1.00` | Minimum daily spend to consider |

| `DEFAULT\_STDDEV\_THRESHOLD` | `2.0` | Standard deviations above baseline to flag |

| `MIN\_DAYS\_FOR\_BASELINE` | `3` | Minimum prior days needed for a baseline |

| `MIN\_ABSOLUTE\_DEVIATION` | `$1.00` | Minimum dollar gap above baseline |



\---



\## Limitations



\- Claude's `likely\_cause` is an inference from cost numbers alone, not from

&#x20; CloudTrail or deployment history. Treat it as a hypothesis to verify.

\- No local caching — every run costs $0.01 in Cost Explorer API charges.

\- Anomaly detection is per-service. Multi-service correlated spikes produce

&#x20; separate findings rather than a single correlated finding.

\- Tested against a sandbox account. Thresholds may need tuning for accounts

&#x20; with higher, more variable baseline spend.



\---



\## Author



\*\*Ogaji Igwe Samuel\*\* — Cloud \& DevOps Engineer

\- GitHub: \[https://github.com/samklin92](https://github.com/samklin92)

\- Email: samklinofficial91@gmail.com

