# Qwen3 4B GPU experiment

Model: qwen3:4b-instruct, Q4_K_M; AMD Radeon integrated GPU via Vulkan.
Server confirmed all 37/37 layers offloaded, with 3,873,366,343 bytes allocated.
Endpoint: http://localhost:11435/v1; Ollama 0.35.0; context 8192.
Fallback disabled. Single pass over 20 frozen authored Chinook challenge cases.
Model and runtime provenance: run-manifest.json. Raw results: gpu-chinook-test-report.json.

| Policy | Accuracy | Coverage | Paraphrase accuracy | Total latency p50 | Total latency p95 |
| --- | --- | --- | --- | --- | --- |
| router | 35% | 15% | 0% | 0.03 ms | 1.25 ms |
| cost-aware | 35% | 15% | 0% | 0.03 ms | 0.55 ms |
| fixed-sql | 35% | 15% | 0% | 0.03 ms | 1.03 ms |
| fixed-code | 35% | 15% | 0% | 0.02 ms | 0.47 ms |
| llm | 70% | 75% | 100% | 10177.71 ms | 24217.36 ms |
| llm-cost-aware | 70% | 75% | 100% | 9394.26 ms | 11737.34 ms |

For Qwen alone, planning p50 is 9374.03 ms and p95 is 24677.82 ms over 19 non-error runs. Total latency includes the rejected plan. Qwen plus cost selection has planning p50 9321.49 ms and p95 10979.41 ms. Sequential order and prompt caching can affect these timings; the difference is not evidence that cost selection speeds model inference.

Qwen got all 3 canonical and 9 paraphrase cases right, but only 2 of 4 required abstentions right. It incorrectly treated ambiguous requests as customer count or invoice mean, and returned country-grouped revenue instead of filtering to USA. One invalid plan was rejected before execution. Two other unsupported capability-gap questions were correctly abstained under the current catalog but scored incorrect against dataset task answers. Both LLM policies show the same accuracy and 5% contract-error rate.

The original chinook-test-report.json was a failed CPU setup run: its prompt exceeded the 4096-token context ceiling. It is not a model-quality measurement. The failed early GPU request timed out during startup; gpu-smoke.json records the succeeding early-run request (18.27 s planning). The routing prompt now uses bounded executable contracts instead of full source schemas.

Limitations: one small authored dataset, one pass, no independent gold review, no causal GPU-vs-CPU comparison, no monetary cost measurement. Keyword total medians include many cheap abstentions. Backend execution comparisons must account for differing coverage. Do not use these results as a general model ranking.

Next priorities: evaluate ambiguity and constraint preservation on development cases before changing prompts; add explicit typed filters; measure shorter explanations, validated plan caching, and a deterministic fast path on a separate validation run. Keep this report unchanged for comparison.
