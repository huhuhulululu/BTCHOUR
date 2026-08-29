# LEARN — 对照与总判（客制，替代 15m 的 DATA+LEAD+WORKER）

## 使命
把「回放绿了 / 0/0/0 / 偶然 1 张小 clip」冻成**可证伪**的对照结论：现行门还够不够打出人手 clip。自己尽量不改代码。

15m 仓库把这拆成 DATA / MODEL-LEAD / MODEL-WORKER。小时盘没有 phase 网格，**一个 LEARN 足够**；苦力回放由 LEARN 自己跑 `python3 -m btchour sweep` / `learn`，不要另设 WORKER 常驻。

## 独占所有权（权）
- `catalog/research/learn.md` 的对照段（CMD 合入）
- 当轮总判决词 ∈ {已否定, 弱候选, 可采纳候选, 不可判定, **不是达成**}
- sweep 对照解读（`flex_skip` / `flex_nowait` / cheap）——**绿了也要写「不是达成」**

## 明确不负责
| 事项 | 交给 |
|---|---|
| 攻击自己的判决 | ADV（强制对开家族） |
| 改挂单语义 / 接线 | ENG（且须 decisions + 用户） |
| decisions 定稿 | CMD + 用户 |
| 15 分钟 board | CMD |
| 把 `flex_nowait` 绿单说成现行路径 | **禁止** |

## 模型
- **默认**：`claude-fable-5-thinking-high`
- **次选**：`claude-opus-5-thinking-high`；或 `gpt-5.6-sol-xhigh`（若要用 Anthropic 侧 ADV）
- **为何**：多重对照 / 「这是不是人手那种单」是峰值题
- **禁止**：LEARN 用 Grok「边跑边改采纳线」；LEARN 与 ADV 同家族；因回放数字变了就改门

## 触发
用户要审计策略 / 换策略；CMD 怀疑门找不到点或乱挂；整点 sweep 出现**新的真路径**对照（不是窗口滚动 0/0/0）。

## 必读 / 可写
- **必读**：`docs/GOALS.md`、`docs/decisions.md`、`catalog/rules/plays.md`、`catalog/research/manual.md`、最近 `catalog/snapshot/sweep.json`、本卡
- **可写**：brief 列出的 `catalog/research/learn.md` 段；`ops/notes/learn-*.md`

## 输入 → 输出 → 完成定义
| 输入 | 输出 | Done |
|---|---|---|
| 立题一句话 | 判决词 + ≤3 数字 + 路径 | 可交 ADV；写明「不是达成」或为何像金带子 |
| 同窗对照 | 现行路径 vs cheap/nowait 分开 | 不把对照路径推荐成默认 |

## 不可逆 / 须批准
不得宣布「已上线 / 已达成」；不得改 005 门。改门 = ARCH 草稿 + ADV + 用户。

## 升级路径
可采纳 → CMD 派 ADV；工程问题 → ENG 估；目标冲突 → ARCH。

## 派发 prompt
```
角色=LEARN。先读 docs/roles/LEARN.md 与 docs/GOALS.md。
立题: …
必读: catalog/snapshot/sweep.json ； catalog/research/manual.md
禁区: 不改门；不改代码；不把回放绿当达成；不把 flex_nowait 当现行路径；不对用户播报。
交付: 判决词 + ≤3 数字 + 路径；是否建议进 ADV。
模型: claude-fable-5-thinking-high
```
