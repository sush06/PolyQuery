# LLM router and planner

The optional LLM planner maps a natural-language question to one catalog operation,
its parameters, and an eligible backend. It uses the OpenAI Responses API with
strict structured output, then validates the plan locally before execution.
The model receives the question, source identity, executable operation contracts,
and small entity domains. Full source-table schemas are available through
describe-source but are omitted from the routing prompt to keep small-model
context and inference overhead bounded. It does not receive data rows, local source paths, or
large LDBC ID domains. Use explicit person IDs for LDBC questions.

Install the SDK and set OPENAI_API_KEY in your environment. Select an explicit
model ID that supports Responses API structured output:

~~~powershell
python -m pip install -e ".[llm]"
python -m polyquery.cli query "What is the headcount of the research team?" --planner llm --model YOUR_MODEL_ID
python -m polyquery.cli query "What is the typical midpoint salary?" --planner llm --model YOUR_MODEL_ID --llm-fallback keyword
python -m polyquery.cli evaluate --planner llm --model YOUR_MODEL_ID --dataset benchmarks/demo.json --output benchmarks/llm-demo.json
~~~

The keyword planner remains the offline default. LLM routing supports
policy=router and policy=cost-aware. With cost-aware, the LLM identifies intent
and parameters, then the measured selector chooses a capable backend. The planner has a 30-second request timeout, no SDK retries, and
an 800-token output limit. Override request timeout with --llm-timeout (seconds).
Engine accepts llm_client for injected clients.
Requests set store=False. Returned planner_metadata records model, prompt hash,
API calls, response ID, and token usage; monetary cost remains unmeasured.

Provider failures, incomplete output, and invalid plans raise LLMPlanningError.
Use --llm-fallback keyword (or Engine(llm_fallback="keyword", ...)) to route
these failures through the source's existing keyword planner. Metadata records
the sanitized error and fallback. Missing credentials, SDK, and invalid
configuration fail at startup. Model abstentions and refusals never trigger fallback.

Plans remain single-step. Unsupported filters, compound requests, ambiguity,
and writes must abstain. The model cannot create SQL or Python execution code;
existing read-only templates and constrained adapters execute the validated plan.
Structural validation does not prove that the model preserved the question's
meaning, so an incorrect but well-formed plan is still possible.

LLM routing may improve paraphrase coverage and parameter extraction. Establish
that improvement using the same held-out questions and sources for keyword and
LLM planners, comparing answer accuracy, coverage, abstention, planning latency,
and token usage. Keep fallback disabled for a pure LLM comparison. Demo cases
and mocked API tests do not establish model quality. For real sources, compare
--planner llm adds an LLM run alongside existing execution policies:

~~~powershell
python -m polyquery.cli compare --source chinook --data benchmarks/Chinook_Sqlite.sqlite --dataset benchmarks/challenge-v1/chinook-test.json --planner llm --model YOUR_MODEL_ID --output benchmarks/llm-chinook.json
~~~

API contract: [official OpenAI structured outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Local small models

Use an OpenAI-compatible Chat Completions endpoint with JSON-schema output.
This path uses the Python standard library and needs no OpenAI SDK or key for
a loopback endpoint. Remote endpoints use POLYQUERY_API_KEY.

~~~powershell
python -m polyquery.cli query "What is the research team's headcount?" --planner llm --model qwen3:4b-instruct --llm-base-url http://localhost:11434/v1 --llm-api chat
python -m polyquery.cli compare --source chinook --data benchmarks/Chinook_Sqlite.sqlite --dataset benchmarks/challenge-v1/chinook-test.json --cost-profile benchmarks/challenge-v1/chinook-cost-profile.json --planner llm --model qwen3:4b-instruct --llm-base-url http://localhost:11434/v1 --llm-api chat --repeats 1 --warmup 0 --output benchmarks/experiments/qwen3-4b/chinook-test-report.json
~~~

Comparison includes keyword, fixed backends, keyword cost-aware, LLM, and LLM
cost-aware. Fallback defaults to none. Model output remains validated locally.
The local model must be installed and the server running before execution.
Chat requests use temperature=0; token usage is normalized into the same report
fields as Responses. Endpoint support for JSON schema is required.

See [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility) and
[structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

## AMD integrated-GPU experiment

The Qwen GPU test uses a separate Ollama server at localhost:11435 with
OLLAMA_VULKAN=1, OLLAMA_IGPU_ENABLE=1, and OLLAMA_CONTEXT_LENGTH=8192.
These are process settings; the existing localhost:11434 server is unaffected.
On this machine the server confirmed AMD Radeon Vulkan discovery and 37/37
Qwen layers offloaded. Verify actual offload with GET /api/ps and the server log;
GPU detection alone does not establish model offload or latency improvement.

Use the earlier comparison command with --llm-base-url http://localhost:11435/v1
and --llm-timeout 120 for this server. Keep failed setup measurements separate
from successful experiments. The initial CPU report hit the default 4096-token
context ceiling and cannot establish model accuracy. The bounded operation
prompt and larger GPU-server context resolve that setup issue.

GPU support reference: [Ollama hardware support](https://docs.ollama.com/gpu).
