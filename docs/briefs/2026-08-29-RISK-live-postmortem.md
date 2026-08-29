# brief — RISK — live-postmortem-372-374
日期: 2026-08-29
角色: RISK
角色卡: docs/roles/RISK.md
模型: claude-sonnet-5-thinking-high
负责人（A）: CMD
执行（R）: RISK

## 目标（一句）
按现行 005 门判断这 3 笔 live_one 该不该挂，给出绿|黄|红和调整菜单。不改门。

## 必读
- docs/roles/RISK.md
- docs/decisions.md 005–008、010
- catalog/rules/plays.md
- 本 brief 里的 sqlite 事实

## 可写路径
- ops/notes/risk-20260829-live.md

## 禁区
- 不下单、不改代码、不改门、不切 live
- 不对用户直接播报

## 完成定义
姿态绿|黄|红（可按笔再给总姿态）；菜单 1–5；是否 ESCALATE；每笔「该不该挂 / 该不该成交」。
