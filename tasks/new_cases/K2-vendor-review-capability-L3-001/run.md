# K2-vendor-review-capability-L3-001

Run the task from the repository root with any of the four supported Harnesses. These commands use the task-compatible default reasoning setting and write all results under `output/case_0808`:

```bash
harnessbench run --task tasks/new_cases/K2-vendor-review-capability-L3-001 --harness codex --model deepseek --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808/k2_review_codex
harnessbench run --task tasks/new_cases/K2-vendor-review-capability-L3-001 --harness claudecode --model deepseek --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808/k2_review_claudecode
harnessbench run --task tasks/new_cases/K2-vendor-review-capability-L3-001 --harness openclaw --model deepseek --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808/k2_review_openclaw
harnessbench run --task tasks/new_cases/K2-vendor-review-capability-L3-001 --harness deepseek --model deepseek --timeout 3600 --mode docker --docker-auth model-registry --evaluation-mode run-and-score --model-registry ./model.toml --judge-model deepseek --output-dir output/case_0808/k2_review_deepseek
```
