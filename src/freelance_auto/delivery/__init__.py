"""交付流水线：接单后 项目创建 → LLM 拆任务 → LLM 产出交付物 → 自动检查 → 人工质检 → 交付。

对外暴露的入口函数：
- start_project        为订单创建项目并用 LLM 拆解任务
- run_task_generation  逐个任务调用 LLM 生成交付物
- review_task          人工质检关卡（approve / 打回）
- auto_check           对 LLM 产出做安全自动检查（py_compile 语法校验）
- finalize_project     全部任务 done 后标记项目交付并推送通知
- export_project       把交付物导出为文件
"""

from .pipeline import (
    auto_check,
    export_project,
    finalize_project,
    review_task,
    run_task_generation,
    start_project,
)

__all__ = [
    "auto_check",
    "export_project",
    "finalize_project",
    "review_task",
    "run_task_generation",
    "start_project",
]
