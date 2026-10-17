# EVALUATIONS

## Method and current results

All results below are local, deterministic checks run against synthetic fixtures on Python 3.13 and the repository's 50 generated inbox examples. They establish behavior of the current code, not production accuracy. The Python test suites and TypeScript suite pass. The exact evaluation cases are executable under each task's `evaluation` folder.

| Task | Cases | Passed | What is checked |
|---|---:|---:|---|
| Knowledge Brain | 20 | 20 | 15 supported fixture questions cite the expected article; 5 unsupported/sensitive questions abstain |
| Inbox Agent | 50 | 50 | Category labels across how-to, troubleshooting, project, refund, complaint, quote, follow-up, and general mail |
| Meeting to Scope | 20 | 20 | Request extraction, stable IDs, timeline and integration labels, and non-request abstention |

Task 1 eval output includes elapsed local retrieval time; Task 3 includes elapsed local extraction time. These values measure local SQLite/rule execution only. No live crawl or Claude API request was part of these runs. Current measured model cost is therefore not applicable, not a claim that future usage is free. No two-model comparison has been run; model-selection metrics remain pending.

## Safety and reliability checks

- Task 1 returns insufficient evidence for unsupported product claims and private credential requests.
- A verified article fixture returns its URL and related image reference.
- An expert resolution becomes a searchable, separately sourced answer.
- Unchanged sitemap metadata avoids a fetch; a failed page is isolated from others.
- Task 2 does not draft when Task 1 is unavailable or evidence is insufficient.
- The autonomy ledger compares normalized final and generated text, requires a quality-passed send, and excludes high-risk categories.
- Task 3 does not create requirements from conversational statements with no request; only a human-approved proposal updates the baseline. Follow-up removal and out-of-scope additions are tested against that baseline.

## Known gaps

Fixtures are synthetic; none of the retrieval results measure Brilliant Directories article recall, screenshot understanding, or hidden-test performance. The TypeScript classifier's 50/50 result is against hand-authored labels and does not measure reply quality. Feedback correction recall is tested locally, but no before/after model quality improvement has been measured. Scope estimates are placeholder planning ranges and have not been compared with engineer estimates. No Claude model versions were compared, no per-request token costs were measured, and no long-tail latency results are available. Replace pending measurements with actual evaluation records before making product claims.
