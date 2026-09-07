# phantom-aggregator

## Local LLM Intelligence Enrichment (Optional)

`phantom-aggregator` can optionally pass raw incoming news, cyber threat feeds, and weather reports through a local LLM running on `Phantom-Cloud` to generate concise, bulleted summaries tailored for low-bandwidth Reticulum display.

### 1. Prerequisites
Ensure a local LLM server (such as [Ollama](https://github.com/ollama/ollama) or `llama.cpp`) is running on the host system or on the local network. We recommend `Qwen 2.5 14B` or `Llama 3.1 8B` for fast CPU execution and high summarization quality.

```bash
# Example: Running Ollama on the Phantom-Cloud host
ollama run qwen2.5:14b
