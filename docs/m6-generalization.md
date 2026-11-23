# M6 end-to-end generalization benchmark

M6 evaluates the frozen Qwen3 4B Instruct direct typed-DAG planner,
deterministic verifier, and existing physical selector. Production
defaults and security checks are unchanged. See the [freeze record](../benchmarks/m6-freeze-v1.json)
for the Git revision, model digest, prompt hashes, full schema and catalog,
fixture fingerprints, profile hash, and inference settings.

## Corpus and review

The [versioned manifest](../benchmarks/m6-manifest-v1.json) specifies 80
cases, with ten cases per category in each 40-case partition. Development
and final have disjoint family labels and normalized answerable operation
and dependency shapes. The four categories are single operations, typed
DAG bindings, paraphrases/compositions, and requests that require refusal.
Gold was computed over raw Chinook and LDBC fixtures independently of
Qwen. Each reviewed reference DAG was also checked node by node against
the reference computation before inference. The [gold file](../benchmarks/m6-gold-v1.json)
records those answers. Authored labels and questions were reviewed in this
workflow. No outside human reviewer participated.

Six canonical single-operation wordings also appeared in earlier
repository benchmarks. An [exact-wording audit](../benchmarks/m6-wording-overlap-v1.json)
identified them before final inference. The report includes these easy
controls in the requested full metrics and separately reports the
remaining new-wording cases. This check cannot establish model-training
novelty. Many graph questions are variations in a shared structural family.
Family-cluster intervals account for this grouping more directly than
per-case intervals, though both are descriptive for a small authored set.

## Execution and scoring

Keyword and deterministic DAG run on the same questions. One Qwen
response per question is shared across fixed SQL Server, learned routing,
and a diagnostic physical oracle. Authorization refusals can incur zero
model calls. Oracle regret compares measured eligible implementations,
only where the learned result and the oracle result were both correct.
Reviewed reference DAGs are executed and reported as a separate control.
These reference results never enter model input or candidate selection.

Per-question logs retain a redacted structured proposal, the verified
decision or refusal, structural and binding diagnostics, node routing
traces, timing, token usage, and independent correctness. A catalog-valid
proposal matching the full reviewed shape that fails semantic preflight
is a high-confidence false-rejection candidate. A different shape is
flagged for further review rather than asserted incorrect, since
equivalent plans may exist. A wrong accepted answer is flagged as a
false-acceptance candidate. Semantic preflight timing is reported when
reached. Planning includes inference, decoding and local checks, and
oracle probe time stays outside oracle end-to-end timing.

The live comparison uses an existing database-only restricted SQL Server
lab reader. The direct Windows reader login remains unverified. The
fixed and learned runs reuse the same data and accepted plan. They are
executed sequentially, so cache effects can bias small timing differences.
The selector profile is frozen and trained outside this benchmark.

## Reproduce

Use the [frozen selector profile](../benchmarks/m6-selector-profile-v1.json)
and configure the SQL Server reader using the [routing guide](intelligent-routing.md). The benchmark requires
local Ollama, the frozen model digest, the original data snapshots, and
the same profile fingerprint. Keep existing journals when rerunning a
separate experiment.

```powershell
python -m pip install -e ".[planner-benchmark]"
python -m benchmarks.m6_generalization run --server
python -m benchmarks.report_m6_generalization
```

The runner appends a journal record after each question. Restarting
the same invocation resumes at unrecorded IDs and refuses a mismatched
freeze or server setting. The reviewer can summarize a partial journal
with the same report command. Raw per-question records and summaries are
under `logs/m6/`.

## Results

All 80 questions were recorded. The main table includes all 40 final
cases, including the three canonical wording controls.

| Final arm | Correct answers | Correct refusals | Accepted plans | End-to-end p50 / p95 |
| --- | ---: | ---: | ---: | ---: |
| Keyword | 6/30 | 10/10 | 6 | 0.37 / 3.13 ms |
| Deterministic DAG | 7/30 | 10/10 | 7 | 0.32 / 2.13 ms |
| Qwen fixed SQL Server | 6/30 | 3/10 | 6 | 22.03 / 48.78 s |
| Qwen learned | 6/30 | 3/10 | 6 | 22.03 / 48.78 s |

Qwen answered 7/30 development and 6/30 final answerable questions.
Excluding the previously seen exact wordings, it answered 5/27 and 4/27,
respectively. Final answer coverage was 20.0% (95% Wilson interval
9.5–37.3%, family-bootstrap interval 7.1–47.1%). Correct refusal coverage
was 30.0% (95% Wilson interval 10.8–60.3%). The three correct Qwen
refusals in each split were the pre-inference security guards. The other
refusal cases were blocked by later validation and did not receive
credit as correct refusals. Qwen made 37 calls per split and used 32,988
development and 33,734 final tokens. On the 13 final proposals reaching
semantic verification, the verification p50/p95 was 0.66/0.90 ms.

The 31 final plan failures broke down into 17 binding errors, two
parameter failures, five structural failures, and seven semantic blocks.
Three semantic blocks rejected a catalog-valid proposal matching the
reviewed reference shape (final IDs 04, 05, and 23). No accepted Qwen
answer was wrong on this corpus. No binding question produced a verified
answer in either split. All 13 successful generated answers across the
two partitions were Chinook questions.

The fixed and learned arms produced the same correct answers on
identical generated plans. The learned selector matched the measured
oracle on 6/6 comparable final backend choices with zero median regret.
In the separate **reviewed-plan** control, learned completed 30/30 final
references and fixed SQL Server completed 26/30. Its four failed
references timed out. This control shows what the execution layers can
do with reviewed plans. It does not establish an intelligent-routing
gain on generated plans.

The full suite reported **347 passed and one skipped**. The skipped
case requires the direct Windows reader login.

[Machine-readable results and family breakdowns](../benchmarks/m6-summary-v1.json)
