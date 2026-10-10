# Choosing a model and profile

Measured on Colab A100-SXM4-40GB with vLLM 0.31.0 (2026-10-10). Numbers are from one session each;
treat them as orders of magnitude, not benchmarks. Details in `phase0-findings.md`.

| Profile | Model | Weights | Cold start* | Gen tok/s | Prefill tok/s | KV @32k | Notes |
|---|---|---|---|---|---|---|---|
| `a100-qwen3.8-27b` (default) | `Qwen/Qwen3.8-27B-FP8` | 27.8 GB | ~12 min | ~40 | ~2,400 | 64k tokens | Dense, Apache-2.0, thinking disabled per request. FP8 runs weight-only (Marlin) on Ampere. |
| `a100-qwen3-coder-30b-awq` | `cyankiwi/Qwen3-Coder-30B-A3B-Instruct-AWQ-4bit` | 18.1 GB | ~7 min (est.) | ~128 | ~6,900 | 193k tokens | MoE 3B active; community 4-bit quant. 3x faster; quality on real tasks not yet compared head-to-head. |
| `t4-qwen2.5-coder-7b` | `Qwen/Qwen2.5-Coder-7B-Instruct-AWQ` | ~5 GB | not measured | — | — | — | Starting point for T4; untested. |

\* Cold start = `colab new` + pip install vLLM (~190 s) + model download + engine start. The dependency
install repeats on every new VM because Colab disks are ephemeral.

Rules of thumb
- Keep `max_model_len: 32768` (verified). 64k is possible on paper (KV cache sizes above) but was not
  tested; raise `gpu_memory_utilization` or `--kv-cache-memory` if you try.
- Pin `engine.revision` to a commit SHA. Model repos change.
- `max_num_seqs: 1`: the delegate runs one agent loop at a time; batching is pointless here.
- Keep `idle_minutes` close to the measured cold start of your profile (8-10 min for A100 profiles).
- A100 40GB costs ~5.2 compute units/hour on Google AI Pro / Colab Pro (measured); 200 units ≈ 38 hours
  of allocation including setup and idle.

To add a profile: copy a YAML from `colab_delegate/profiles/`, change `engine.model`/`revision`,
set `tool_call_parser` (`qwen3_coder` for Qwen3 family, `hermes` for many others) and
`reasoning_parser` if the model emits `<think>` blocks.
