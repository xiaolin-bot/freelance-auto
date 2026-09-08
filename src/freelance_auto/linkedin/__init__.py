"""LinkedIn 求职自动化模块。

三层决策架构：
  LEVEL 1: 纯规则过滤（0 LLM）
  LEVEL 2: 轻量规则评分（0 LLM）
  LEVEL 3: LLM 深度判断（仅对 50-70 分岗位）

状态机：
  DISCOVERED → RULE_FILTERED → AI_EVALUATED → APPROVED →
  APPLICATION_STARTED → FORM_FILLING → SUBMITTED
"""
