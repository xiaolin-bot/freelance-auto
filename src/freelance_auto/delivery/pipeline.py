"""接单后的交付流水线：项目创建 → LLM 拆任务 → LLM 逐个产出交付物 → 自动语法检查 → 人工质检关卡 → 标记交付。

安全边界（硬要求）：
- 所有 LLM 调用一律包 try/except LLMError：失败记日志、任务保持 todo、函数返回 0/None，绝不崩溃。
  无 LLM_API_KEY 时整条流水线必须优雅跳过。
- LLM 产出的代码绝不直接执行，最多通过 `python -m py_compile` 做语法校验。
- 交付前必须经过人工 approve（review_task）。
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import Database
from ..llm import LLMClient, LLMError
from ..models import (
    Notification,
    NotificationType,
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
)
from ..utils import truncate

logger = logging.getLogger(__name__)

# 交付类型常量（用于任务分类与文件扩展名）
_KIND_PYTHON = "python"
_KIND_JS = "js"
_KIND_MARKDOWN = "markdown"


# ---------------------------------------------------------------- helpers


def _now() -> str:
    """当前时间（秒精度 ISO 格式），与 models._now 保持一致。"""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _row_to_project(row: Any) -> Project:
    """把 projects 表的一行转换为 Project 模型。"""
    return Project(
        id=row["id"], order_id=row["order_id"], customer_id=row["customer_id"],
        name=row["name"], status=ProjectStatus(row["status"]),
        price=row["price"], deadline=row["deadline"], notes=row["notes"] or "",
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _get_project(db: Database, project_id: int) -> Project | None:
    """按 ID 取项目。

    绕开 db.get_project()：该方法当前引用了未导入的 ProjectStatus（db.py 缺陷），
    直接用底层连接查询。
    """
    row = db._conn.execute(
        "SELECT * FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    return _row_to_project(row) if row else None


def _find_project_by_order(db: Database, order_id: int) -> Project | None:
    """按订单 ID 查找已存在的项目（一个订单只对应一个项目）。

    直接查询底层连接：db.list_projects() 当前存在缺陷（引用了未导入的
    ProjectStatus），且这里只需按 order_id 精确查询。
    """
    row = db._conn.execute(
        "SELECT * FROM projects WHERE order_id=?", (order_id,)
    ).fetchone()
    return _row_to_project(row) if row else None


def _get_task(db: Database, task_id: int) -> Task | None:
    """按任务 ID 取任务（db.py 未提供 get_task，直接查底层连接）。"""
    row = db._conn.execute(
        "SELECT * FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    if not row:
        return None
    return Task(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        description=row["description"] or "",
        status=TaskStatus(row["status"]),
        artifacts=json.loads(row["artifacts"]) if row["artifacts"] else [],
        llm_output=row["llm_output"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _list_tasks(db: Database, project_id: int) -> list[Task]:
    """列出项目下的所有任务（按 id 升序）。

    绕开 db.list_tasks()：该方法当前引用了未导入的 TaskStatus（db.py 缺陷），
    直接用底层连接查询，保证流水线可正常运行。
    """
    rows = db._conn.execute(
        "SELECT * FROM tasks WHERE project_id=? ORDER BY id", (project_id,)
    ).fetchall()
    return [
        Task(
            id=r["id"], project_id=r["project_id"], title=r["title"],
            description=r["description"] or "",
            status=TaskStatus(r["status"]),
            artifacts=json.loads(r["artifacts"]) if r["artifacts"] else [],
            llm_output=r["llm_output"] or "",
            created_at=r["created_at"], updated_at=r["updated_at"],
        )
        for r in rows
    ]


def _update_task_deliverable(
    db: Database, task_id: int, llm_output: str, artifacts: list[str]
) -> None:
    """写入任务的交付物字段（db.py 未提供更新方法，直接使用底层连接）。"""
    db._conn.execute(
        "UPDATE tasks SET llm_output=?, artifacts=?, updated_at=? WHERE id=?",
        (llm_output, json.dumps(artifacts, ensure_ascii=False), _now(), task_id),
    )
    db._conn.commit()


def _append_task_note(db: Database, task_id: int, note: str) -> None:
    """把日志性备注追加到任务描述中（review 打回时记录原因）。"""
    task = _get_task(db, task_id)
    if task is None:
        return
    desc = (task.description + "\n" + note).strip()
    db._conn.execute(
        "UPDATE tasks SET description=?, updated_at=? WHERE id=?",
        (desc, _now(), task_id),
    )
    db._conn.commit()


def _classify_task(task: Task) -> tuple[str, str]:
    """根据任务标题/描述判断交付类型，返回 (kind, 建议扩展名)。

    - 含 爬虫/spider/接口/api/脚本/函数/算法/测试/开发/实现 等关键词 → 代码类
      （含 前端/node/react/vue/js 时按 JavaScript，否则按 Python）
    - 含 文档/说明/报告/方案 等关键词 → Markdown 文档类
    - 默认按 Python 处理
    """
    text = f"{task.title} {task.description}".lower()
    code_kw = (
        "爬虫", "spider", "crawl", "scrape", "脚本", "函数", "接口", "api",
        "后端", "算法", "实现", "开发", "代码", "自动化", "测试", "test", "function",
        "class ", "def ", "import ",
    )
    if any(k in text for k in code_kw):
        if any(k in text for k in ("javascript", "前端", "react", "vue", "node", "js")):
            return _KIND_JS, ".js"
        return _KIND_PYTHON, ".py"
    if any(k in text for k in ("文档", "说明", "readme", "报告", "方案", "doc", "document")):
        return _KIND_MARKDOWN, ".md"
    return _KIND_PYTHON, ".py"


def _sanitize_filename(name: str, fallback: str) -> str:
    """清理文件名：去掉 Windows 非法字符、折叠空白、限长。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    cleaned = cleaned[:80].rstrip("._ ")
    return cleaned or fallback


def _parse_decomposed_tasks(data: Any) -> list[tuple[str, str]]:
    """解析 LLM 拆解结果：{"tasks": [{"title": ..., "description": ...}]}，最多取 8 个。"""
    if not isinstance(data, dict):
        return []
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return []
    out: list[tuple[str, str]] = []
    for item in tasks[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        desc = str(item.get("description", "")).strip()
        if title:
            out.append((title, desc))
    return out


def _send_delivery_notification(
    db: Database, config: AppConfig, project: Project
) -> None:
    """发送“项目已交付”通知。

    优先使用 ..notify.notify 的 send_notification；该模块尚未实现时降级为
    直接写入 notifications 表，保证 finalize_project 绝不因通知失败而报错。
    """
    subject = f"项目已交付: {project.name}"
    content = (
        f"项目「{project.name}」(id={project.id}) 的全部任务已完成并通过质检，"
        f"已标记为 delivered，可导出交付物。"
    )
    try:
        from ..notify.notify import send_notification

        send_notification(
            db,
            config,
            NotificationType.DELIVERY_REVIEW,
            subject,
            content,
            related_id=project.id,
        )
        return
    except Exception:  # noqa: BLE001  notify 模块缺失或调用失败 → 降级
        logger.warning("notify.send_notification 不可用，降级为直接写入通知记录")

    try:
        db.insert_notification(
            Notification(
                type=NotificationType.DELIVERY_REVIEW,
                channel="none",
                subject=subject,
                content=content,
                related_id=project.id,
                success=False,
                error="send_notification unavailable",
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("写入交付通知失败（不影响交付状态）")


# ---------------------------------------------------------------- 拆解 prompt


_DECOMPOSE_SYSTEM = (
    "你是一名资深的自由职业项目交付经理。客户通过平台下单，你需要把订单拆解成 "
    "3-8 个可直接执行的子任务，覆盖从开发到测试、文档的完整交付链路。"
    "任务标题必须包含任务类型，例如『实现爬虫』『编写测试』『撰写说明文档』。"
)


# ---------------------------------------------------------------- 对外入口


def start_project(
    db: Database,
    config: AppConfig,
    order_id: int,
    customer_name: str = "",
    customer_contact: str = "",
) -> int | None:
    """为订单创建项目（status=pending），并用 LLM 拆解为 3-8 个任务入库。

    - 若该订单已有项目，直接返回已有 project_id（幂等，不重复创建）。
    - 成功返回 project_id；失败（含 LLMError / 无 API key）返回 None 并记日志，
      项目保持 pending，由外部重试。
    """
    existing = _find_project_by_order(db, order_id)
    if existing is not None:
        logger.info("订单 %s 已有项目 #%s，直接复用", order_id, existing.id)
        return existing.id

    order = db.get_order(order_id)
    name = f"订单#{order_id}" if order is None else (order.title or f"订单#{order_id}")
    project_id = db.insert_project(
        Project(
            order_id=order_id,
            name=name,
            status=ProjectStatus.PENDING,
            notes="",
        )
    )
    logger.info("已为订单 %s 创建项目 #%s（%s）", order_id, project_id, name)

    order_text = ""
    if order is not None:
        order_text = (
            f"订单标题: {order.title}\n"
            f"订单详情: {truncate(order.content, 3000)}\n"
            f"预算: {order.budget_text}"
        )

    user = (
        f"客户名称: {customer_name or '未知'}\n"
        f"客户联系方式: {customer_contact or '未知'}\n"
        f"{order_text}\n\n"
        '请输出 JSON 对象，格式: {"tasks": [{"title": "任务名", "description": "详细要求"}]}，'
        "共 3-8 个任务。"
    )

    try:
        client = LLMClient()
        data = client.chat_json(_DECOMPOSE_SYSTEM, user)
        parsed = _parse_decomposed_tasks(data)
    except LLMError:
        logger.exception("订单 %s 任务拆解失败（LLM 不可用），项目保持 pending", order_id)
        return None

    if not parsed:
        logger.error("订单 %s 任务拆解结果为空，项目保持 pending", order_id)
        return None
    if len(parsed) < 3:
        logger.warning(
            "订单 %s 仅拆解出 %d 个任务（要求 3-8），按实际入库", order_id, len(parsed)
        )

    for title, desc in parsed:
        db.insert_task(
            Task(
                project_id=project_id,
                title=title,
                description=desc,
                status=TaskStatus.TODO,
            )
        )
    logger.info("项目 #%s 拆解出 %d 个任务", project_id, len(parsed))
    return project_id


def run_task_generation(db: Database, config: AppConfig, project_id: int) -> int:
    """对项目下所有 status=todo 的任务，逐个调用 LLM 生成交付物。

    - 交付物全文写入 task.llm_output，建议文件名写入 task.artifacts。
    - 任务状态：config.delivery.require_review=True → review（待人工质检）；
      False → done。
    - 单个任务生成失败（LLMError）时任务保持 todo，记日志继续下一个；
      LLM 完全不可用时返回 0。返回实际处理的任务数。
    """
    project = _get_project(db, project_id)
    if project is None:
        logger.error("项目 %s 不存在，无法生成任务交付物", project_id)
        return 0

    tasks = [t for t in _list_tasks(db, project_id) if t.status == TaskStatus.TODO]
    if not tasks:
        logger.info("项目 %s 没有待处理（todo）的任务", project_id)
        return 0

    try:
        client = LLMClient()
    except LLMError:
        logger.exception("LLM 不可用，项目 %s 的任务生成跳过", project_id)
        return 0

    processed = 0
    for task in tasks:
        try:
            output, filename = _generate_deliverable(client, task)
        except LLMError:
            logger.exception("任务 #%s「%s」生成失败，保持 todo", task.id, task.title)
            continue
        except Exception:  # noqa: BLE001  防御：任何异常都不阻断流水线
            logger.exception("任务 #%s「%s」生成异常，保持 todo", task.id, task.title)
            continue

        try:
            _update_task_deliverable(db, task.id, output, [filename])
            if config.delivery.require_review:
                db.set_task_status(task.id, TaskStatus.REVIEW.value)
            else:
                db.set_task_status(task.id, TaskStatus.DONE.value)
            processed += 1
            logger.info(
                "任务 #%s「%s」已生成交付物 %s → %s",
                task.id, task.title, filename,
                TaskStatus.REVIEW.value if config.delivery.require_review else TaskStatus.DONE.value,
            )
        except Exception:  # noqa: BLE001
            logger.exception("任务 #%s 交付物入库失败，保持 todo", task.id)

    logger.info("项目 %s 本轮处理 %d 个任务", project_id, processed)
    return processed


def _generate_deliverable(client: LLMClient, task: Task) -> tuple[str, str]:
    """调用 LLM 为单个任务生成完整交付物，返回 (content, 建议文件名)。

    根据任务类型选择 prompt（Python/JS 代码类、Markdown 文档类），
    要求 LLM 输出 {"filename": "...", "content": "..."}，content 必须完整、无省略。
    """
    kind, default_ext = _classify_task(task)

    if kind == _KIND_PYTHON:
        system = (
            "你是一名资深 Python 开发工程师。请输出一个完整、可直接运行、不含省略号的 "
            "Python 文件。必须只输出合法 JSON 对象："
            '{"filename": "建议文件名.py", "content": "完整源码全文"}。'
        )
    elif kind == _KIND_JS:
        system = (
            "你是一名资深前端 / Node.js 开发工程师。请输出一个完整、可直接运行、不含省略号的 "
            "JavaScript 文件。必须只输出合法 JSON 对象："
            '{"filename": "建议文件名.js", "content": "完整源码全文"}。'
        )
    else:
        system = (
            "你是一名资深技术文档工程师。请为任务撰写一份结构清晰、内容完整的 Markdown 文档。"
            "必须只输出合法 JSON 对象："
            '{"filename": "建议文件名.md", "content": "文档全文"}。'
        )

    user = (
        f"任务标题: {task.title}\n"
        f"任务要求: {truncate(task.description or '（无附加说明）', 3000)}\n\n"
        "要求：代码/文档必须完整、可直接使用，禁止使用省略号或占位注释；"
        f"filename 给出建议文件名（默认扩展名 {default_ext}）。"
    )

    data = client.chat_json(system, user, temperature=0.2)
    if not isinstance(data, dict):
        raise LLMError(f"LLM 返回结构异常（任务 #%s）: {str(data)[:200]}" % task.id)
    content = str(data.get("content", "")).strip()
    if not content:
        raise LLMError(f"LLM 未返回交付物内容（任务 #{task.id}）")

    raw_name = str(data.get("filename", "")).strip() or f"deliverable_{task.id}{default_ext}"
    filename = _sanitize_filename(raw_name, f"deliverable_{task.id}{default_ext}")
    if not Path(filename).suffix:  # 无扩展名时补上类型扩展名
        filename += default_ext
    return content, filename


def review_task(db: Database, config: AppConfig, task_id: int, approved: bool) -> None:
    """人工质检关卡入口。

    - approved=True  → 任务置为 done（允许交付）。
    - approved=False → 任务打回 todo，并在描述中附加打回日志。
    """
    task = _get_task(db, task_id)
    if task is None:
        logger.error("质检失败：任务 %s 不存在", task_id)
        return

    if approved:
        db.set_task_status(task_id, TaskStatus.DONE.value)
        logger.info("任务 #%s「%s」通过质检 → done", task_id, task.title)
    else:
        db.set_task_status(task_id, TaskStatus.TODO.value)
        _append_task_note(
            db,
            task_id,
            f"[质检打回 {_now()}] 人工审核未通过，任务回到待办，请重新生成。",
        )
        logger.warning("任务 #%s「%s」未通过质检 → 回到 todo", task_id, task.title)


def auto_check(db: Database, config: AppConfig, task_id: int) -> list[str]:
    """对任务的 llm_output 做安全自动检查，返回问题列表。

    - 仅当 config.delivery.auto_test=True 且输出疑似 Python 代码（含 "def " 或
      "import "）时执行 `python -m py_compile` 语法校验。
    - 绝不执行 LLM 生成的脚本逻辑，仅做语法层面校验；检查完删除临时文件。
    - 其他类型直接返回空列表（跳过）。
    """
    problems: list[str] = []
    if not config.delivery.auto_test:
        return problems

    task = _get_task(db, task_id)
    if task is None:
        logger.error("自动检查失败：任务 %s 不存在", task_id)
        problems.append(f"任务 {task_id} 不存在")
        return problems

    output = task.llm_output or ""
    # 非 Python 代码（如 Markdown 文档）跳过语法检查
    if "def " not in output and "import " not in output:
        logger.info("任务 #%s 的交付物不是 Python 代码，跳过语法检查", task_id)
        return problems

    tmp_path = Path(tempfile.gettempdir()) / f"fa_auto_check_{task_id}_{uuid.uuid4().hex[:8]}.py"
    try:
        tmp_path.write_text(output, encoding="utf-8")
        # Windows 编码处理：errors='replace' 避免捕获到非法字节时抛 UnicodeDecodeError
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            problems.append(f"Python 语法错误: {err[:500]}")
            logger.warning("任务 #%s 语法检查未通过: %s", task_id, err[:300])
    except subprocess.TimeoutExpired:
        problems.append("Python 语法检查超时（>30s）")
        logger.warning("任务 #%s 语法检查超时", task_id)
    except Exception as exc:  # noqa: BLE001  找不到 python / 文件写入失败等
        problems.append(f"语法检查执行失败: {exc}")
        logger.exception("任务 #%s 语法检查执行失败", task_id)
    finally:
        # 无论如何删除临时文件
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    return problems


def finalize_project(db: Database, config: AppConfig, project_id: int) -> bool:
    """所有任务 done 后把项目置为 delivered 并推送通知；未完成返回 False。"""
    project = _get_project(db, project_id)
    if project is None:
        logger.error("交付失败：项目 %s 不存在", project_id)
        return False

    tasks = _list_tasks(db, project_id)
    if not tasks:
        logger.warning("交付失败：项目 %s 没有任何任务", project_id)
        return False

    pending = [t for t in tasks if t.status != TaskStatus.DONE]
    if pending:
        logger.info(
            "项目 #%s 还有 %d 个任务未完成（todo/review），无法交付",
            project_id, len(pending),
        )
        return False

    db.set_project_status(project_id, ProjectStatus.DELIVERED.value)
    _send_delivery_notification(db, config, project)
    logger.info("项目 #%s「%s」已交付", project_id, project.name)
    return True


def export_project(
    db: Database, config: AppConfig, project_id: int, dest_dir: str | None = None
) -> Path:
    """把任务的 artifacts/llm_output 按任务名写成文件（task_<序号>_<标题>.py / .md）。

    - 输出目录：dest_dir 指定时用 dest_dir，否则用 config.delivery.workdir
      （默认 C:\\freelance-auto\\data\\workspaces）下的 project_<id> 项目目录。
    - 扩展名优先取任务 artifacts 里的文件名扩展名，否则按任务类型推断。
    - 返回项目目录 Path。
    """
    project = _get_project(db, project_id)
    if project is None:
        raise ValueError(f"项目 {project_id} 不存在，无法导出")

    base = Path(dest_dir).expanduser() if dest_dir else config.workspaces_dir()
    proj_dir = base / f"project_{project_id}"
    proj_dir.mkdir(parents=True, exist_ok=True)

    tasks = _list_tasks(db, project_id)
    if not tasks:
        logger.warning("导出项目 #%s：没有任何任务", project_id)

    for idx, task in enumerate(tasks, start=1):
        # 扩展名优先取 artifacts 中的文件名，否则按类型推断
        ext = ""
        if task.artifacts:
            ext = Path(task.artifacts[0]).suffix
        if not ext:
            _, ext = _classify_task(task)

        title_slug = _sanitize_filename(task.title, f"task_{idx}")
        fname = _sanitize_filename(f"task_{idx}_{title_slug}", f"task_{idx}") + ext
        fpath = proj_dir / fname

        content = task.llm_output or (
            f"# {task.title}\n\n（该任务尚未生成交付物）\n\n{task.description}"
        )
        fpath.write_text(content, encoding="utf-8")
        logger.info("已导出交付物: %s", fpath)

    return proj_dir
