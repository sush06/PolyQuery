# Semantic planner model benchmark

This benchmark leaves the production planner, verifier, and physical
selector unchanged. It uses the direct typed DAG contract, independently
computed reference results, and distinct development and final
operation/dependency families. The final questions were authored before
running the model comparison. The earlier 12-question manifest is
outside model selection and final testing.

## Run

```powershell
python -m pip install -e ".[planner-benchmark]"
python -m benchmarks.planner_model_benchmark
python -m benchmarks.replay_ordered_plan
python -m benchmarks.report_planner_model_benchmark
```

The default candidates are local Qwen3 4B Instruct and Qwen3 8B. The
runner checks the Ollama inventory and records unavailable models. The
larger candidate uses the published model tag and default quantization.
No hosted inference is required. Supply local model names with
`--models` to run against a different Ollama inventory.

The OpenAI-compatible and native Ollama endpoints both receive the full
typed JSON schema. The existing compatible request already asks for
structured output. This compares two schema delivery interfaces, rather
than comparing unconstrained sampling with constrained sampling. The
model, prompt, schema, seed, temperature, completion-token cap, and
requested thinking setting remain fixed for the decoding comparison.
There is one request per model case. The benchmark applies no repair or
canonicalization.

The report distinguishes valid JSON, provider-schema conformance, local
catalog and binding checks, semantic acceptance, and independent answer
or refusal correctness. A binding can be untested if an earlier check
failed. The total latency includes synchronous inference and execution.
Raw rows record runner residency before each request. Native Ollama
durations are in nanoseconds. Compatible calls expose client round-trip
latency, which includes transport and local validation overhead. Token
counts are reported without a dollar conversion for local inference.

## Offline verifier replay

A saved ordered Chinook proposal with the expected operations and
literals still abstained on the original wording. Replaying the same
typed proposal with a recognized ordered formulation accepted it and
matched independent gold. The replay adds only the canonical ordering
edge to the redacted prior trace and makes no model calls. This isolates
an intent-recognition coverage gap from output formatting and model
capability. The replay uses earlier audit evidence and is excluded from
candidate ranking and the final test.

## Development result

Both Qwen variants used Q4_K_M on the same Windows host under Ollama,
with 16 logical CPUs. Observed loaded runners reported 4,096-token
contexts and zero VRAM usage. No hosted model was included.

| Metric | Qwen3 4B compatible | Qwen3 8B compatible | Qwen3 4B native |
| --- | ---: | ---: | ---: |
| Provider calls | 3 | 3 | 3 |
| Provider-schema valid | 3/3 | 3/3 | 3/3 |
| Locally catalog valid | 2/3 | 2/3 | 2/3 |
| Accepted plans / correct answers | 1 / 1 | 0 / 0 | 1 / 1 |
| Correct refusals | 1 | 1 | 1 |
| Input / output tokens | 2,167 / 339 | 2,191 / 542 | 2,167 / 435 |
| End-to-end p50 / p95 | 20.3 / 40.7 s | 57.6 / 85.0 s | 18.4 / 148.5 s |

All nine responses parsed as JSON and satisfied the submitted provider
schema. Each arm still had one local shape rejection. Semantic checks
blocked one catalog-valid proposal in each 4B arm and two in 8B.
All three security refusals bypassed inference. The native lane matched
the compatible 4B lane on every correctness outcome. Its measured
median was lower and its tail was much higher. The trials mix runner
residency states, and three calls per lane do not support a latency
generalization.

Model selection ranked correct answers plus correct refusals first, then
correct answers separately, followed by p50 and total tokens. This
selected 4B and, on the interface tie, native Ollama. An initial process
began selection while 8B was still downloading. It was stopped before
any final outcome was saved or inspected. The resumed run kept completed
development rows, added 8B, and froze the final configuration.

## Independent final test

Frozen native 4B accepted one of four plans and answered one of two
answerable requests correctly. It correctly refused one of two refusal
cases. The other two outcomes were an incorrect dependency binding and
a semantic rejection of a catalog-valid proposal. All three generated
responses passed the submitted JSON schema. Total usage was 2,153 input
tokens and 400 output tokens over three calls. Warm end-to-end p50/p95
was 15.1/28.0 seconds across the four cases.

The results point to failures after schema conformance. The larger model
did not improve this sample, and native schema delivery did not change
development correctness. The replay establishes a separate verifier
coverage gap. The fresh final set is independent of model selection and
uses independent reference computations, but was authored in the same
repository rather than by an external reviewer. Keep Qwen3 4B as the
experimental baseline and treat native schema delivery as a measured
option. These small samples do not establish a decoding or routing gain.
[Machine-readable results](../benchmarks/planner-model-summary.json)
