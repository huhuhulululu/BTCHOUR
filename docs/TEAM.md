# 常态化多角色编制（小时盘）

> **角色 × 模型 × 权责**的唯一入口。  
> 血统：`huhuhulululu/kalshi` 的 `docs/TEAM.md`（botdirectory：一角一卡、主控只路由、不可逆要批准）。  
> **客制**：场地是 `KXBTCD` 小时盘，不是 15 分钟 C1。编制更瘦。完整角色卡在 `docs/roles/`。

---

## 1. 为什么必须拆（以及为什么比 15m 仓库少）

| 单角色硬扛时 | 后果 |
|---|---|
| 托管 + 改门 + 改代码同一上下文 | 回放一绿就改门；红线遗忘 |
| 「顺手」看见 32–42¢ 就挂 / 整条切 live | `AUG2911` 那种蠢单；用户已否决整条 live |
| 一人既写假设又当对抗 | 选择性报告（回放当达成） |
| 全家同一快模型 | 门改浅、对抗无效 |

15m 仓库有 DATA-BULK / MODEL-WORKER / `live_c1 --arm` / 日均美元阶段。本仓库**没有那些东西**，不设对应常驻角色。

**提权（decisions 011 / 012）：** 15m 仓结论偏坐等；小时盘每小时一整条近 ATM 阶梯（常 100+ 档）。CMD 必须是最聪明、最灵活、产出最高的那张桌：用满阶梯打人手 clip，不能把「没机会 / 坐着」从那边抄过来，也不能靠噪声单刷量。

**原则：CMD 瘦身只路由；15 分钟核盘 CMD 自己跑 board（必须报活档数，不能只报空仓），不空转派专员；改门必须 LEARN→ADV→用户；模型按能力选型。**

---

## 2. 模型花名册（能力 → slug）

> `Task` 的 `model=` **必须**用下表。未列 slug 禁止 invent。

| 能力档 | 适合 | 首选 slug | 备选 | 不适合 |
|---|---|---|---|---|
| **峰值深推理** | 改门 / 换策略的边界 | `claude-fable-5-thinking-high` | `claude-fable-5-thinking-xhigh` | 15 分钟核盘、日志扫尾 |
| **深推理** | 同上降本 | `claude-opus-5-thinking-high` | `gpt-5.6-sol-xhigh` | 批量扫 journal |
| **深推理·快** | 同上降延迟 | `claude-opus-5-thinking-high-fast` | `gpt-5.6-sol-xhigh-fast` | 大回放网格 |
| **稳健分析** | 风控姿态、文档、QA | `claude-sonnet-5-thinking-high` | `gpt-5.6-sol-high` | 先结论后填数 |
| **稳健·加长想** | 难统计 | `claude-sonnet-5-thinking-xhigh` | `gpt-5.6-sol-xhigh` | 一行热修 |
| **工程精修** | `btchour/` + `tests/` | `composer-2.5` | Sonnet | 写门的方法论 |
| **工程·快** | 小补丁 | `composer-2.5-fast` | — | 跨模块改门 |
| **高吞吐工兵** | 监控、扫 journal | `cursor-grok-4.6-high-fast` | `cursor-grok-4.5-high-fast` | 改门终审 |
| **极速只读** | 粗扫 | `gemini-3.7-flash-high` | Grok-fast | 「可采纳」判决 |
| **主控** | 托管与用户频道 | `inherit` | — | 自己跑完整 sweep 网格当研究 |

**硬规则：** (1) LEARN 与 ADV **对开家族**（Fable/Opus/Sonnet 同属 Anthropic 侧，互不当对方的 ADV） (2) MON 默认 Grok，结论须 CMD 抽查 (3) ENG 默认 Composer (4) 峰值模型不当 15 分钟心跳工兵。

---

## 3. 编制一览 → 完整卡

| 代号 | 完整角色卡 | 默认模型 | 一句话权责 |
|---|---|---|---|
| **CMD** | [`roles/CMD.md`](roles/CMD.md) | `inherit` | 唯一前台；board（含阶梯活档）；路由；抽查 |
| **ARCH** | [`roles/ARCH.md`](roles/ARCH.md) | Fable / Opus / GPT-xhigh | 改门 ADR 草稿；不跑数 |
| **MON** | [`roles/MON.md`](roles/MON.md) | Grok-fast | 进程/滞留根因；不修代码；不重启 |
| **RISK** | [`roles/RISK.md`](roles/RISK.md) | Sonnet | 还该不该按现行门继续挂；不擅自改门 |
| **LEARN** | [`roles/LEARN.md`](roles/LEARN.md) | Fable / Opus | sweep/learn 总判；回放绿不是达成 |
| **ADV** | [`roles/ADV.md`](roles/ADV.md) | 与 LEARN 对开 | 进 decisions 前攻漏洞 |
| **ENG** | [`roles/ENG.md`](roles/ENG.md) | Composer | 已批准路径落地；有补丁才重启 loop |
| **QA** | [`roles/QA.md`](roles/QA.md) | Sonnet（非 Composer） | 工程复核 |
| **DOC** | [`roles/DOC.md`](roles/DOC.md) | Sonnet | catalog / docs 与代码对齐 |
| **RPT** | [`roles/RPT.md`](roles/RPT.md) | 常 CMD 兼 | 对外表格（中文 / 纽约时间） |

Schema：[`roles/_SCHEMA.md`](roles/_SCHEMA.md)。

不设：DATA-BULK、MODEL-WORKER、`live_c1` 武装员。账本深挖并进 LEARN。

```
                    ┌──────── CMD（inherit）────────┐
                    │  board · sweep整点 · 用户频道 · timer │
                    └───┬─────┬─────┬─────┬─────┬───┘
                        │     │     │     │     │
                   MON/RISK  ARCH  LEARN  ENG  DOC/RPT
                                      │    │
                                     ADV  QA
```

MON = loop 还在扫吗；**RISK = 还该不该按现行门挂**。

---

## 4. RACI

图例：R=执行 A=拍板 C=协商 I=知情。每一行 **A 有且仅有一个**。

| 工作项 | CMD | ARCH | MON | RISK | LEARN | ADV | ENG | QA | DOC | RPT |
|---|---|---|---|---|---|---|---|---|---|---|
| 15 分钟核盘 / 播报 | **A/R** | | C | I | | | | | | C |
| 整点 sweep + 提交 snapshot | **A/R** | | | I | C | | | | I | I |
| 线上滞留/死进程 | A | | **R** | I | | | I | | | |
| **风险姿态 / 是否继续挂** | A | C | I | **R** | I | | | | | I |
| sweep/learn 总判 | A | C | | I | **R** | C | | | | I |
| 对抗复核 | A | | | I | I | **R** | | | | |
| decisions 定稿 | **A** | R草稿 | | C | C | C | | | I | |
| 改 `btchour/`（有决定） | A | | | | | | **R** | C | I | |
| 工程可否合并 | A | | | | | | C | **R** | | |
| 文档对齐 | A | C | | | | | C | | **R** | |
| 用户表格 | **A** | | | C | C | | | | | **R** |
| **实盘单复盘（010）** | **A** | I | I | **R**姿态 | **R**对照 | I | | | | C |
| 升级清单请示用户 | **A/R** | I | | **C起草理由** | C | | | | | I |

**动钱：** 任何专员都不是下单者。唯一可碰交易所写接口的是已在跑的 paper loop 里的 `live_one`（CMD 托管，不新开武装进程）。CMD 不另开第二 loop。

---

## 5. 标准流水线

### 5.1 15 分钟心跳（默认，不派专员）

```
timer → CMD 看队列 → python3 -m btchour board
  → 看 tmux btchour-paper-flex（滞留>60s 且现在还滞留才重启）
  → 过整点则 sweep，只提交 sweep.json + replay.json
  → 表格播报（中文 / America/New_York）
  → 更新 timer prompt（先 unsubscribe 再 subscribe）
```

不因此派 RISK / LEARN / ENG。

### 5.2 可能改门

`CMD 立题 → ARCH? → LEARN 冻 brief + 总判 → ADV（对开）→ decisions 草稿 → 用户批准 → ENG → QA → DOC → 有引擎补丁才重启 loop`

回放数字变了**不是**立题理由。

### 5.3 线上故障

`滞留/死了 → MON → CMD → ENG(+QA) 或问用户`

MON **不** `pkill -f btchour`。已恢复的旧 gap 不重启。429 / `exchange_hold` 自己恢复。

### 5.5 每笔实盘单（用户 2026-08-29，decisions 010）

用户只跟 CMD 说话。CMD 负责调用。

```
live_one 成交或撤单 → CMD 写 brief
  → LEARN（像不像 AUG2520/AUG2608；判决词；不是达成）
  ∥ RISK（绿|黄|红；该不该挂；菜单）
  → CMD 抽查 ≥1 数字对 sqlite raw
  → 对用户只出复盘表
  → 若要改门：ADV → 用户批准 → ENG
```

15 分钟空仓心跳仍不派专员。有新的实盘结果才派。

### 5.4 市场/门是否还让挂

```
事件或用户问「是不是很蠢」→ RISK（绿|黄|红 + 调整菜单）
  → 绿：维持 952cb97
  → 黄：空仓加强观察（不改门）
  → 红：建议停挂 / ESCALATE（改门、切 live、放宽 p、吃 taker）
  → 需新证据：LEARN（不把回放当达成）
```

---

## 6. 派发协议（CMD 必守）

1. 先写 brief（`docs/briefs/_TEMPLATE.md`），**必填**模型 slug + 角色卡路径。
2. `Task(..., model="<slug>")`；prompt 首行令专员先读对应角色卡。
3. 并行：MON 可与 RISK 并行；同一 `btchour/` 的 ENG 串行。
4. 抽查：LEARN 至少一个关键数字对原始 `sweep.json` / sqlite。
5. 对外频道只有 CMD/RPT。中文、纽约时间、只用表格。
6. 无用户请求 / 无故障 / 无改门议题 → **不空转多角色**；不另开第二 timer。
7. 改 timer prompt 必须先 `unsubscribe` 再 `subscribe`。

### 派发骨架

```
Task(
  subagent_type="generalPurpose",  # MON 可用 explore
  model="<TEAM §2 slug>",
  description="<ROLE>-<key>",
  prompt="先读 docs/roles/<ROLE>.md（权责与完成定义），再读 brief：…\n"
         "禁区与交付以角色卡为准。不对用户直接播报。"
)
```

---

## 7. 与现有系统的边界

| 系统 | 关系 |
|---|---|
| timer `btchour-auto-loop` `*/15 * * * *` | CMD 心跳 |
| tmux `btchour-paper-flex` | 唯一 paper loop（supervisor + run） |
| `live_one` | 唯一真单通道；不是切 live |
| `catalog/rules/` | 策略正文；DOC 对齐这里，不另写第二套门 |
| `catalog/snapshot/sweep.json` | 整点对照；LEARN 读，CMD 提交 |
| `huhuhulululu/kalshi` | 15m 研究仓库；角色 schema 来源；**不**把 C1/日均美元搬过来；**不**把「没边就坐着」搬过来。小时盘阶梯操作权更高（011） |

---

## 8. 落地清单

- [x] TEAM 索引 + `docs/roles/*` 细卡 + 模型矩阵 + RACI（从 kalshi 客制）
- [x] brief 模板强制模型 + 角色卡路径
- [x] GOALS / decisions 记小时盘门，不抄 15m 美元阶段
- [x] 实战：010 每笔 live_one 派 LEARN∥RISK（372/373/374 首批）
- [x] 制度：011 小时盘阶梯提权；board 报整点档 / $600 内 / 32–42¢ 活档
- [x] 实战：LEARN 用满阶梯提案（`ops/notes/learn-20260829-011-ladder.md`，可采纳候选；改 005 须用户）
- [ ] 实战：改门走 LEARN→ADV，不在心跳里改
- [ ] 实战：滞留只派 MON

---

## 9. 反模式

- CMD 把核盘思考外包；用坐等或噪声单冒充「产出最高」
- 把小时盘当 15m 坐等；只报空仓不报整点档 / $600 内 / 32–42¢ 活档
- 专员越权改 live / 对用户抢播报 / 把成交门放到 0.45
- 专员自己 push（交付写 `ops/notes/`，合入由 CMD；ENG 仅在 brief 允许时提交代码）
- LEARN 与 ADV 同家族
- 全家 Grok；或用 Fable 跑 15 分钟 board
- 15 分钟心跳每次都派 RISK（噪音）
- 回放绿 / `flex_nowait` 绿就改门
- 无 decisions / 无用户批准让 ENG 改挂单语义
- 主控塞满 sweep 原始 JSON
- 乱 `pkill -f btchour`；已恢复的 gap 再重启
- 泄露 `.env` / `kalshi.pem`
- 说每笔都赚 20%
