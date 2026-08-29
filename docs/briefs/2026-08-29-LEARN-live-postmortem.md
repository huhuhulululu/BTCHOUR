# brief — LEARN — live-postmortem-372-374
日期: 2026-08-29
角色: LEARN
角色卡: docs/roles/LEARN.md
模型: claude-fable-5-thinking-high
负责人（A）: CMD
执行（R）: LEARN

## 目标（一句）
把现有 3 笔 live_one 对照人手金带子，给出判决词；不要改门。

## 必读
- docs/roles/LEARN.md
- docs/GOALS.md
- docs/decisions.md
- catalog/rules/plays.md
- catalog/research/manual.md
- 本 brief 里的 sqlite 事实（以事实为准）

## 可写路径
- ops/notes/learn-20260829-live.md

## 禁区
- 不改门、不改代码、不切 live
- 不把回放绿 / 374 小赚写成达成
- 不对用户直接播报

## 完成定义
`ops/notes/learn-20260829-live.md`：每笔一张表 + 总判决词 ∈ {已否定, 弱候选, 可采纳候选, 不可判定, 不是达成}；是否建议进 ADV（是|否）。
