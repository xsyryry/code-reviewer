<div align="center">

<h1>Code Reviewer</h1>

<p><strong>面向真实 Issue 与 Candidate PR 的 Agentic Code Review 系统</strong></p>

<p>不只是阅读 Diff。Reviewer 会主动探索代码仓库、定位 Root Cause、执行测试与行为验证，再给出 Approve / Request Changes 决策，并通过 Verifier 自动评估审查结果。</p>

<p>Explore Repository · Trace Root Cause · Validate Patch · Make Decision · Verify</p>

</div>

<p align="center">
  <img src="assets/readme/hero-architecture.svg" alt="Code Reviewer architecture" width="1000" />
</p>

| 真实 PR 样本 | 审查决策准确率 | 正确 PR 识别率 | 长上下文 |
|:---:|:---:|:---:|:---:|
| **131** | **64.1%** | **79.4%** | **131K** |
| Unique PRs | Decision Accuracy | Resolved PR Accuracy | Context Window |

> **Code Reviewer 的核心不是“让大模型看更多代码”，而是让 Reviewer 主动决定需要什么证据。**
> 它可以搜索仓库、阅读调用链、复现问题、执行测试，再基于代码与运行结果判断一个 Patch 是否真正解决了 Issue。

## How It Works

Code Reviewer 将每个 Issue / Candidate PR 放入隔离的代码仓库环境，由 OpenHands 驱动 Reviewer 通过工具主动读取源码、搜索调用链、执行命令和测试。Reviewer 最终输出结构化 Review，再由 Verifier 根据 benchmark ground truth 判断审查决策是否正确。

<p align="center">
  <img src="assets/readme/system-architecture.svg"
       alt="Code Reviewer system architecture"
       width="1000" />
</p>

### Review Lifecycle

1. **加载任务** — 读取 Issue、Candidate Patch 和仓库环境
2. **独立理解问题** — 在查看 Patch 前定位相关代码与 Root Cause
3. **探索与验证** — 通过 Read / Search / Shell / Test 收集证据
4. **评估 Patch** — 应用并检查 Candidate Patch 是否解决问题
5. **形成 Review** — 输出结构化 Approve / Request Changes
6. **自动验证** — Verifier 对照 Ground Truth 计算 Reward

## One Review in Action

下面是一条真实 Review 轨迹。Reviewer 并没有直接相信 Candidate Patch，而是先独立定位问题、复现 Bug，再检查 Patch 并执行验证。

| Instance | Decision | Confidence | Steps | Runtime | Reward |
|:---|:---:|:---:|:---:|:---:|:---:|
| `astropy__astropy-7166` | ✅ Approve | 0.95 | 32 | 12m18s | 1 |

Repository: `astropy/astropy` · Base commit: `26d147868f8a891a6009a25cd6a8576d2e1bd747`

下面不是一条预先定义好的工作流，而是一条真实的 Agent Event Trace。Reviewer 会在多轮「模型推理 → 工具调用 → Observation → 上下文更新」中逐步累积证据，动态决定下一步行动。

<sub>This is a real agent trace, not a predefined workflow.</sub>

<p align="center">
  <img src="assets/readme/case-study-astropy-7166.svg"
       alt="Astropy code review case study"
       width="1000" />
</p>

### 🐛 The Bug

Astropy 的 `InheritDocstrings` metaclass 可以让 subclass method 自动继承父类 docstring，但 property 不行。

Root Cause 很小：原实现只检查 `inspect.isfunction(val)`，而 Python property 的类型是 `<class 'property'>`，所以 property 根本不会进入 docstring inheritance 分支。

```text
Method:
Base.method.__doc__
      ↓
Derived.method.__doc__ ✅

Property:
Base.prop.__doc__
      ↓
Derived.prop.__doc__ ❌ None
```

### 🔧 The Candidate Patch

核心修改来自真实 `predicted.json`，这里只展示最关键的 diff：

```diff
         for key, val in dct.items():
-            if (inspect.isfunction(val) and
-                is_public_member(key) and
-                val.__doc__ is None):
-                for base in cls.__mro__[1:]:
-                    super_method = getattr(base, key, None)
-                    if super_method is not None:
-                        val.__doc__ = super_method.__doc__
-                        break
+            if is_public_member(key):
+                if inspect.isfunction(val):
+                    if val.__doc__ is None:
+                        for base in cls.__mro__[1:]:
+                            super_method = getattr(base, key, None)
+                            if super_method is not None:
+                                val.__doc__ = super_method.__doc__
+                                break
+                elif isinstance(val, property):
+                    if val.__doc__ is None:
+                        for base in cls.__mro__[1:]:
+                            super_property = getattr(base, key, None)
+                            if super_property is not None:
+                                val.__doc__ = super_property.__doc__
+                                break
```

The patch handles properties explicitly while preserving the existing method inheritance path.

### Evidence Collected

#### ① Reproduced the bug

```text
Method docstring: Base method docstring
Property docstring: None
```

#### ② Verified the patch

```text
Method correct: True
Property correct: True
```

#### ③ Ran tests

```text
test_inherit_docstrings
→ 1 passed

test_inherit_docstrings_property
→ 1 passed

-k "inherit"
→ 2 passed
```

Additional property edge cases also passed.

### Final Decision

✅ **APPROVE**

Confidence: **0.95**

- 修复发生在 Root Cause 位置；
- property 的 docstring inheritance 被正确补齐；
- 原有 method 行为保持；
- targeted tests 与 edge cases 均通过。

```text
Candidate Patch
→ Approve
→ Ground Truth: Resolved
→ Verifier: Correct
→ Reward: 1
```

<details>
<summary>Raw artifacts</summary>

```text
outputs/official100-new/retry50/tasks/astropy__astropy-7166/environment/data/problem_statement.txt
outputs/official100-new/retry50/tasks/astropy__astropy-7166/environment/data/predicted.json
outputs/official100-new/retry50/results-final/2026-09-12__22-49-35/astropy__astropy-7166__2C2kQKs/agent/trajectory.json
outputs/official100-new/retry50/results-final/2026-09-12__22-49-35/astropy__astropy-7166__2C2kQKs/agent/openhands_sdk.txt
outputs/official100-new/retry50/results-final/2026-09-12__22-49-35/astropy__astropy-7166__2C2kQKs/verifier/review_report.json
outputs/official100-new/retry50/results-final/2026-09-12__22-49-35/astropy__astropy-7166__2C2kQKs/verifier/report.json
outputs/official100-new/retry50/results-final/2026-09-12__22-49-35/astropy__astropy-7166__2C2kQKs/verifier/reward.txt
```

</details>

<sub>32 agent steps · 12m18s · 893K total tokens</sub>

## Benchmark Results

在 131 个互不重复的 SWE-Review-Bench PR 样本上，Code Reviewer 达到 **64.1% Decision Accuracy**。

但总体准确率并不能完整描述 Reviewer 行为：模型对已经正确修复的 PR 判断较好，却明显倾向于放过仍然存在问题的 PR，表现出显著的 over-approval bias。

> 当前 131 个样本来自 `glm5_500` 的固定子集，不代表完整 500 / 1384-instance SWE-Review-Bench 官方结果。

| 样本数 | Decision Accuracy | Resolved Accuracy | Unresolved Accuracy |
|:---:|:---:|:---:|:---:|
| **131** | **64.1%** | **79.4%** | **20.6%** |
| Unique PRs | Decision Accuracy | Resolved PR Accuracy | Unresolved PR Accuracy |

<p align="center">
  <img src="assets/readme/benchmark-results.svg"
       alt="Code Reviewer benchmark results"
       width="1000" />
</p>

### What We Learned

- 131 个样本上的 Decision Accuracy 为 **64.1%**。
- 97 个 resolved PR 中，**77 个正确 Approve**。
- 34 个 unresolved PR 中，仅 **7 个正确 Request Changes**，其余 **27 个被错误放行**。
- 当前 Reviewer 的主要错误类型是 **False Accept**。

### General LLM vs Review-SFT

这里只比较完全相同的 **31 个 paired PR**，不和上面的 131-instance aggregate 混用。

| Metric | GPT-5.6 Luna | SWE-Review-8B |
|---|---:|---:|
| Decision Accuracy | 58.1% | **64.5%** |
| Resolved PR Accuracy | 42.9% | **85.7%** |
| Unresolved PR Accuracy | **90.0%** | 20.0% |
| False Reject Rate | 57.1% | **14.3%** |
| False Accept Rate | **10.0%** | 80.0% |

同样的 31 个 PR 上，Review-SFT 模型显著缓解了通用模型的 over-rejection，但决策偏置转向另一端，出现明显 over-approval。

Paired transition: Luna 的 12 个 False Reject 中，**11 个**转为 True Accept；但 Luna 原本正确识别的 bad PR 中，**7 个**转为 False Accept。

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
