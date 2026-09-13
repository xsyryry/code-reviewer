<h1 align="center"> Code Reviewer: Closing the Loop on Issue Resolution with Agentic Code Review </h1>

<p align="center">
<a href="https://arxiv.org/abs/2607.06065" > 📖 Paper</a>
•
<a href="https://swe-lego.github.io/SWE-Review/" > 🌐 Project Page</a>
•
<a href="https://huggingface.co/datasets/Lego-X/SWE-Review-Bench" > 📊 Benchmark</a>
•
<a href="https://huggingface.co/collections/Lego-X/swe-review" > 🤗 Datasets & Models</a>
•
<a href="https://github.com/LegoX/cc-swe-review" > 🔌 Claude Code Plugin</a>
</p>

<p align="center">
    <br>
    <img src="assets/pipeline_overview.png" width="1000"/>
    <br>
</p>

**Code Reviewer** turns one-shot PR generation into closed-loop issue resolution with agentic code review. Given an AI-generated PR, a reviewer agent explores the repository, decides whether the PR should be accepted, and provides structured feedback for revision.

Key results on SWE-bench Verified:
- **Agentic review continuously improves PRs**: resolve rate rises from 27.5% to 56.9% (Qwen3-30B-A3B) through iterative review-revision
- **Agentic > single-turn review**: outperforms fixed-context baselines in both Decision Accuracy and Resolve Rate after Revision
- **Review trajectories improve issue resolution**: mixed training raises resolve rate by up to 5.6pp and enables self-contained review-revise loops (+10.6pp)
- **Effective test-time scaling**: review-guided iterative revision reaches 38.4% with only 2.44 samples on average

## Demo

> ▶ Code Reviewer in action — resolving a real GitHub issue with the generate–review–revise loop, powered by the [Claude Code plugin](https://github.com/LegoX/cc-swe-review).

<p align="center">
    <img src="assets/demo.gif" width="1000"/>
</p>

## Released Resources

| Resource | Link | Description |
|----------|------|-------------|
| SWE-Review-Bench | [HuggingFace](https://huggingface.co/datasets/Lego-X/SWE-Review-Bench) | 1,384 AI-generated PRs across 3 quality tiers |
| SWE-Review-Traj | [HuggingFace](https://huggingface.co/datasets/Lego-X/SWE-Review-Traj) | 8,914 decision-correct + 5,242 decision-incorrect review trajectories |
| SWE-Review-8B | [HuggingFace](https://huggingface.co/Lego-X/SWE-Review-8B) | Qwen3-8B fine-tuned reviewer |
| SWE-Review-30B-A3B | [HuggingFace](https://huggingface.co/Lego-X/SWE-Review-30B-A3B) | Qwen3-30B-A3B fine-tuned reviewer |
| Claude Code Plugin | [GitHub](https://github.com/LegoX/cc-swe-review) | Use Code Reviewer directly in Claude Code |

## Repo Structure

```
code-reviewer/
├── SWE-Review-Bench/       # Benchmark evaluation (scripts, metrics, README)
├── scripts/
│   ├── train/              # Training scripts (serve, sft, review, eval)
│   └── data_pipeline/      # Data download and preparation
├── harbor/                 # Agent orchestration framework (local install)
├── configs/                # LLaMA-Factory training configs
├── prompts/                # All prompts used in the paper (verbatim)
└── SWE-bench/              # SWE-bench evaluation harness (local install)
```
