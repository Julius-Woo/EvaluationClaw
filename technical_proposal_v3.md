# EvaluationClaw 技术方案 v3

## 1. 产品定位

**EvaluationClaw = 基于 nanobot 的通用自动化 Benchmark 构建 + 测评助手**

核心能力：从模糊的评测需求 → 自动调研、生成、质检 benchmark 数据 → 执行评测 → 出报告 → 自我改进。

不是线性 pipeline，是**带三个反馈闭环的智能系统**。

---

## 2. 架构总览

```
Pipeline: Plan (Loop 1) → Generate (DeepResearch) → Quality Check (Loop 2) → Run → Report & Self-improve (Loop 3)

                    ┌──────────────────────────────────────────────────────────────┐
                    │                      EvaluationClaw                          │
                    │                   (nanobot AgentRunner)                      │
                    │                                                              │
  用户输入           │  ┌─────────┐   ┌───────────┐   ┌─────────┐   ┌───────────┐  │
  (概念描述)  ──────▶│  │ Planner │──▶│ Generator │──▶│ QC Gate │──▶│  Runner   │  │
                    │  │ (Loop1) │   │           │   │ (Loop2) │   │           │  │
                    │  └────┬────┘   └─────┬─────┘   └────┬────┘   └─────┬─────┘  │
                    │       │↻             │              │↻             │         │
                    │  自消歧迭代      DeepResearch     cold-start      执行评测    │
                    │                + Self-generate    + IRT 筛选                  │
                    │                                                              │
                    │                              ┌──────────────┐                │
                    │                              │   Reporter   │                │
                    │                              │ + Self-improve│               │
                    │                              │   (Loop 3)   │                │
                    │                              └──────────────┘                │
                    └──────────────────────────────────────────────────────────────┘
```

---

## 3. 模块详细设计

### 3.1 Planner (Loop 1) — 需求消歧与方案规划

**输入：** 用户的模糊评测需求（如"评测 GPT-5 的数学推理能力"）

**输出：** 结构化 eval_spec（JSON），包含：
```json
{
  "test_objective": "评估模型在竞赛级数学问题上的推理能力",
  "test_subject": {"type": "llm_api", "models": ["gpt-5", "claude-opus-4"]},
  "test_format": "open-ended generation with verifiable answers",
  "test_content": {
    "dimensions": ["algebra", "geometry", "number_theory", "combinatorics"],
    "difficulty_distribution": {"L1": 0.1, "L2": 0.2, "L3": 0.3, "L4": 0.25, "L5": 0.15},
    "potential_issues": ["歧义表述", "多解问题", "符号歧义"]
  },
  "test_scale": 200,
  "metrics": ["accuracy", "pass@1", "solve_rate_by_difficulty"]
}
```

**Loop 1 机制：**
- Planner 使用 DeepResearch（web_search + domain-specific RAG）调研该领域的评测标准
- 生成 eval_spec 草案
- Self-critique：用 checklist 验证 6 项（目标/主体/形式/内容/规模/指标）是否明确无歧义
- 不满足 → 迭代修改。满足 → 退出循环
- **退出条件：** 6 项 checklist 全部 pass 且 self-critique 得分 ≥ 4/5，或达到 max_iterations=5

**技术实现：**
- nanobot AgentRunner（主 agent，200 轮上限）
- 初始知识库：benchmark taxonomy（v2 的 `taxonomy.py`）

```python
# evalclaw/taxonomy.py (from v2, 作为 Planner 的初始知识)
BENCHMARK_TAXONOMY = {
    "math_reasoning": {
        "benchmarks": ["gsm8k", "math", "aime_2024", "minerva_math"],
        "metrics": ["exact_match", "pass@1"],
        "difficulty_range": ["elementary", "competition"],
    },
    "code_generation": {
        "benchmarks": ["humaneval", "mbpp", "swe-bench", "bigcodebench"],
        "metrics": ["pass@1", "pass@10"],
    },
    # ... 20-30 维度
}
```

**参考方法：** AutoDetect 的 Examiner（构建 taxonomy）、Measurement to Meaning 的 validity 框架（区分 criterion/construct claim）

---

### 3.2 Generator — Benchmark 数据获取与生成

**输入：** eval_spec（来自 Planner）

**输出：** 标准化 benchmark 数据集（统一 YAML 格式）

**两条路径：**

#### 路径 A: DeepResearch — 检索已有数据
1. **搜索候选：** HuggingFace Hub API（按 task_categories + keywords）+ lm-eval-harness task registry + web_search
2. **评估适配度：** 对每个候选下载小样本（`datasets.load_dataset(split="test[:10]")`），LLM 判断与 eval_spec 的匹配度
3. **决策：** 完全匹配 → 直接用；部分匹配 → 用 + 补充生成；不匹配 → 跳过

**三级策略（from v2）：**

| 优先级 | 策略 | 成本 |
|--------|------|------|
| 1 | 复用 lm-eval-harness 已有 task | 免费 |
| 2 | 下载 HuggingFace dataset + 适配 | 低 |
| 3 | Self-generate | 中-高 |

#### 路径 B: Self-generate — 自动生成数据
- **方法：** ZSB 的 meta prompt 方式
  - 构造 meta prompt（含 placeholder 属性：topic, subtopic, difficulty, format, style...）
  - LLM 批量生成（一次 10-20 题，再逐条验证）
  - Multiple trials：用 2-3 个不同 designer 模型生成，比较质量
- **质量保证：**
  - BenchMaker 的 SC 验证（Self-Consistency，10 次独立回答）
  - BenchBench 的 quota 控制（子领域覆盖 + 难度分布 + 格式比例）
  - Deficit-driven top-up：不够的子领域/难度自动补生成

**技术实现：**
- 路径 A：需要新增 HuggingFace API tool（`hf_dataset_search`, `hf_dataset_sample`）
- 路径 B：AgentRunner 主 agent（200 轮，批量生成够用）
- Multiple trials：可用 sub-agent 并行（每个 trial 一个 sub-agent），或 AgentRunner 顺序跑

#### Synthesize — 数据融合
- 统一输出格式：lm-eval-harness 兼容的 YAML task config（from v2）
- 不同来源的数据保留 source 标签，方便后续分析
- 难度标定：暂用声明难度（L1-L5），QC 阶段用 IRT 做 post-hoc 校准

```yaml
# 统一输出格式 (from v2, lm-eval-harness 兼容)
task: evalclaw_math_reasoning_v1
dataset_path: ./generated/math_reasoning.jsonl
test_split: test
doc_to_text: "Problem: {{question}}\nSolution:"
doc_to_target: "{{answer}}"
output_type: generate_until
metric_list:
  - metric: exact_match
    aggregation: mean
    higher_is_better: true
metadata:
  source: ["gsm8k", "self_generated", "hf:hendrycks/competition_math"]
  eval_spec_version: "v1"
```

---

### 3.3 Quality Check Gate (Loop 2) — 质量控制

**两层 QC：**

#### 静态 QC（不跑模型，低成本）
- 格式校验：JSON schema 验证必填字段
- 去重：语义相似度（embedding cosine > 0.95 视为重复）
- 规则检查：MCQ 选项互斥？答案在选项中？开放题有参考答案？
- LLM 自检：题目清晰？无歧义？答案正确？

#### 动态 QC / Cold-start（跑模型，控制成本）
- 抽样 10-20 题，跑 2-3 个基线模型（GPT-4o-mini + Claude Sonnet）
- 检查：
  - 分数分布是否合理（不是全对或全错）
  - IRT 估计 discrimination（区分度 ≈ 0 的题标记为低质量）
  - CAD 检测（Benchmark²）：强模型错但弱模型对 → 标记为可疑
  - 客观评分优先：exact → numeric → symbolic → LLM judge（BenchBench 策略）

**Loop 2 机制：**
- 静态 QC 过滤 → 动态 QC 抽样检查
- 如果动态 QC 发现 >20% 低质量题 → 反馈给 Generator 重新生成该子领域
- 满足质量标准 → 人工确认 → 进入 Runner
- **退出条件：** 低质量题比例 < 10% 且 discrimination 均值 > 0.3，或达到 max_qc_iterations=3

**参考方法：** PSN-IRT（IRT 4 参数分析）、Benchmark²（CBRC+DS+CAD）、BenchBench（panel validation + objective-first scoring）

---

### 3.4 Runner — 评测执行引擎

**分级执行：**

| Tier | 任务类型 | 引擎 | 状态 |
|------|---------|------|------|
| 1 | MCQ / T-F / QA | lm-eval-harness CLI | ✅ MVP |
| 2 | Open-ended generation | ZSB judgment prompt（Likert 6 分制） | ✅ MVP |
| 3 | 代码执行 | bwrap sandbox / Inspect AI | 🟡 v2 |
| 4 | Agent / scenario / multi-turn | Inspect AI Docker sandbox + Agent Bridge | 🔴 v2+ |

**执行要求：**
- 忠实执行：用户指定测几个模型就测几个，不偷懒
- 进度监控：nanobot checkpoint_callback 回报进度
- 并发控制：respect API rate limits，可配置 max_concurrent_requests
- Position swap：使用 LLM judge 时必须交换答案位置评两次

**技术实现：**
- Tier 1：nanobot ExecTool 调用 `lm-eval --model openai-chat --tasks <task> --output_path <path>`
- Tier 2：AgentRunner 逐题调用 target model API + judge model API
- 结果存储：结构化 JSON（每道题的 input/output/score/metadata）

---

### 3.5 Reporter & Self-improve (Loop 3)

#### Report 生成
- 指标计算 + 置信区间
- 模型对比表（按维度）
- 难度分布分析（IRT 能力估计，参考 AutoJudger）
- 样例展示（对/错案例）
- Benchmark 质量自检：CBRC + DS（Benchmark² 指标）
- 输出格式：Markdown（默认）+ JSON

#### Self-improve
- **结果分析：** 如果结果很差（accuracy < 预期阈值）→ 诊断模块分析原因
  - 题目质量差？→ 只重做 Generator（不 loop 全流程）
  - 评测 protocol 有偏差？→ 调整 Runner 参数
  - Benchmark 不匹配目标？→ 反馈给 Planner 调整 eval_spec
- **写 memory：** 记录"这类任务用什么 benchmark/参数效果好"
- **更新 skill：** 如果发现了更好的生成策略/QC 方法，更新对应 skill

**Loop 3 退出条件：** 报告质量满意（人工判断）或自动判定 benchmark 质量指标达标。MVP 阶段不自动 loop，人工决定是否重做。

---

## 4. 技术栈

| 组件 | 技术选择 | 理由 |
|------|---------|------|
| **编排层** | nanobot AgentRunner | 独立执行引擎，200 轮，hook/callback 完备 |
| **LLM 调用** | LiteLLM（通过 nanobot provider） | 统一接口，支持所有主流 provider |
| **评测引擎 Tier 1** | lm-eval-harness | HF Leaderboard 后端，200+ benchmark |
| **评测引擎 Tier 2** | ZSB judgment prompt | 轻量，两个 prompt 搞定 |
| **数据检索** | HuggingFace Hub API + web_search | 最大数据源 |
| **质量分析** | PSN-IRT（开源） | IRT 4 参数题目诊断 |
| **沙箱** | nanobot bwrap（简单）/ Inspect AI Docker（复杂） | 分级 |
| **数据格式** | lm-eval-harness YAML task config | 标准化，200+ benchmark 兼容 |
| **报告** | Jinja2 模板 → Markdown | 轻量 |

---

## 5. nanobot 集成方案

### 需要新增的 nanobot tools/skills

| Tool/Skill | 功能 | 优先级 |
|-----------|------|--------|
| `hf_dataset_search` | 搜索 HuggingFace Hub datasets | P0 |
| `hf_dataset_sample` | 下载 dataset 小样本分析 | P0 |
| `lm_eval_run` | 调用 lm-eval-harness CLI | P0 |
| `benchmark_generate` | ZSB 风格批量生成题目 | P0 |
| `irt_analyze` | IRT 4 参数分析 | P1 |
| `quality_check` | 静态 QC 规则检查 | P1 |
| `report_generate` | Markdown 报告生成 | P1 |

### AgentRunner 编排

```python
# evalclaw 主流程（伪代码）
async def run_evalclaw(user_input: str):
    # Loop 1: Plan
    eval_spec = await planner_loop(user_input, max_iterations=5)
    
    # Generate
    existing_data = await deep_research(eval_spec)
    generated_data = await self_generate(eval_spec, existing_data)
    benchmark = await synthesize(existing_data, generated_data, eval_spec)
    
    # Loop 2: Quality Check
    benchmark = await quality_check_loop(benchmark, eval_spec, max_iterations=3)
    
    # Human confirmation point
    await present_to_human(benchmark, eval_spec)
    
    # Run
    results = await runner(benchmark, eval_spec)
    
    # Report & Self-improve
    report = await generate_report(results, benchmark, eval_spec)
    await self_improve(report, eval_spec)
    
    return report
```

---

## 6. MVP 范围（v0.1）

| 模块 | MVP 包含 | 不包含 |
|------|---------|--------|
| **Planner** | Taxonomy 匹配 + 1 轮 self-critique | DeepResearch、多轮 Loop |
| **Generator** | lm-eval 已有 task + ZSB 自生成 | HF 检索、multiple trials |
| **Synthesize** | 单来源直通 | 多来源融合、难度校准 |
| **QC** | 静态规则检查 | 动态 cold-start、IRT |
| **Runner** | Tier 1（lm-eval CLI）+ Tier 2（ZSB judge） | Tier 3-4 |
| **Reporter** | Markdown 指标表 | 可视化、质量自检指标 |
| **Self-improve** | 写 memory | 自动 Loop 3 |

**预估：** ~1,500 行 Python + 4-5 个 nanobot skills，5-7 天