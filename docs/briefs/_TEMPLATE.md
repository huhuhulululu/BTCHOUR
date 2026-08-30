# 专员 brief 模板（复制到当次任务路径）

主控派单前填空。专员**先读**角色卡，再读本 brief。  
参考 [botdirectory.ai](https://botdirectory.ai) 与 `huhuhulululu/kalshi`：一角一责、完成定义写清、不可逆须批准。

```markdown
# brief — <ROLE> — <short-key>
日期: YYYY-MM-DD
角色: MON | RISK | ARCH | LEARN | ADV | ENG | QA | DOC | RPT
角色卡: docs/roles/<ROLE>.md
模型: <slug from TEAM.md §2>
模型家族对开: （仅 ADV）LEARN 用了 ___ → 本 brief 用 ___
负责人（A）: CMD
执行（R）: <本 ROLE>

## 目标（一句）


## 必读
- docs/roles/<ROLE>.md
- docs/TEAM.md §2（模型）与 §4（RACI）
- docs/GOALS.md
- docs/decisions.md
- （其他路径，如 catalog/rules/plays.md）

## 可写路径
- 

## 禁区（默认 + 本任务）
- 不切整条 loop live；`trading_active=true` 不是切 live
- 不新开第二张 live_one；已有 resting 不再挂
- 不写 data/*.sqlite；不提交 .env / keys / latest.json
- 不把成交门放到 0.45；不吃 taker；不放宽 p=0.30
- 不把回放绿写成达成
- 不改 btchour/（除非 ENG 且路径已批）
- 不对用户直接播报
- 不乱 pkill -f btchour

## 步骤
1.
2.

## 完成定义（Done）
- 

## 交付格式
- 以角色卡「输入→输出→完成定义」为准
- 最后一条消息：结论 + ≤3 关键数字 + 路径
- 给 CMD 的内部稿用中文即可；对外表格由 CMD/RPT 发

## 主控抽查点
- 

## 不可逆 / 须请示
- （无则写「无」）
```
