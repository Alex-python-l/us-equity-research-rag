# US Equity Research RAG

Private source repository for the existing financial research workspace. Research output is not an order or trading instruction.

## Setup

Use Python 3.12. Clone with `--recurse-submodules`, install `requirements.txt`, then run `python scripts/restore_vendor.py` to apply the reviewed TradingAgents patches. The upstream commit remains pinned; patches are separately versioned.

The existing workstation uses `tech_rag/integration/.venv`; do not replace that environment. Start with `启动RAG问答.cmd`, or run `python tech_rag/chat_launcher.py` in a configured environment.

## Local configuration and data

- Copy `tech_rag/.env.example` to `tech_rag/.env` and configure only the providers you use.
- LLM credentials belong in the ignored root `apikey.env`; use `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-flash`, `LLM_BASE_URL=https://api.deepseek.com` and `DEEPSEEK_API_KEY` locally.
- The existing knowledge archive (`tech_rag/raw`, `text`, `output`), model cache and runtime state are **not in this repository**. Mount your existing archive before running knowledge-dependent features. A source-only clone does not contain the research evidence.
- Set `FIN_RAG_MODEL_PATH` to an existing BGE-M3 ONNX snapshot when dense retrieval is required. No automatic model download is performed.
- Runtime ledgers must be preserved when restoring the application; cloning source is not authorization to reset usage or enable recurring costs.

## Version management

`baseline-2026-11-01` preserves the pre-change application. Development occurs on `feat/rag-validation-monitoring`, with separate commits for evaluation, frozen RAG handoff, knowledge maintenance, scheduling and event analysis.

Before pushing, run `python scripts/repo_security.py`. Tests requiring knowledge, model caches or real providers run locally. CI uses synthetic fixtures and must never call a paid model.
