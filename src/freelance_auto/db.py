"""SQLite 存储层：schema 初始化 + CRUD。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    Customer,
    Notification,
    NotificationType,
    Order,
    OrderStatus,
    Project,
    ProjectStatus,
    Proposal,
    ProposalStatus,
    Task,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    content     TEXT,
    url         TEXT,
    author      TEXT,
    budget_min  INTEGER,
    budget_max  INTEGER,
    tags        TEXT,
    posted_at   TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    score       REAL,
    raw_json    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_source ON orders(source);

CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,
    title       TEXT,
    body        TEXT NOT NULL,
    price_min   INTEGER,
    price_max   INTEGER,
    days        INTEGER,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,
    customer_id INTEGER,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    price       REAL,
    deadline    TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'todo',
    artifacts   TEXT,
    llm_output  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    contact     TEXT,
    platform    TEXT,
    platform_username TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    channel     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    content     TEXT,
    related_id  INTEGER,
    sent_at     TEXT NOT NULL,
    success     INTEGER NOT NULL DEFAULT 1,
    error       TEXT
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------ orders

    def insert_order(self, o: Order) -> int:
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO orders
               (source, source_id, title, content, url, author, budget_min,
                budget_max, tags, posted_at, status, score, raw_json,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                o.source, o.source_id, o.title, o.content, o.url, o.author,
                o.budget_min, o.budget_max, json.dumps(o.tags, ensure_ascii=False),
                o.posted_at, o.status.value, o.score, o.raw_json,
                o.created_at, o.updated_at,
            ),
        )
        self._conn.commit()
        # 注意：INSERT OR IGNORE 冲突被忽略时 lastrowid 不会重置（仍非 0），
        # 必须用 rowcount 判断是否真的插入了新行。
        # 语义：返回 >0 = 新插入的行 id；返回 0 = 已存在（非新增）。
        if cur.rowcount == 1:
            return cur.lastrowid
        return 0

    def get_order(self, order_id: int) -> Optional[Order]:
        row = self._conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return self._row_to_order(row) if row else None

    def get_order_by_source(self, source: str, source_id: str) -> Optional[Order]:
        row = self._conn.execute(
            "SELECT * FROM orders WHERE source=? AND source_id=?", (source, source_id)
        ).fetchone()
        return self._row_to_order(row) if row else None

    def list_orders(
        self,
        status: Optional[OrderStatus] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> list[Order]:
        sql = "SELECT * FROM orders"
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status.value)
        if source:
            where.append("source=?")
            params.append(source)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_order(r) for r in rows]

    def update_order(self, o: Order) -> None:
        self._conn.execute(
            """UPDATE orders SET title=?, content=?, url=?, author=?, budget_min=?,
               budget_max=?, tags=?, status=?, score=?, updated_at=?
               WHERE id=?""",
            (
                o.title, o.content, o.url, o.author, o.budget_min, o.budget_max,
                json.dumps(o.tags, ensure_ascii=False), o.status.value, o.score,
                o.updated_at, o.id,
            ),
        )
        self._conn.commit()

    def set_order_status(self, order_id: int, status: OrderStatus, score: float | None = None) -> None:
        import datetime
        now = datetime.datetime.now().isoformat(timespec="seconds")
        if score is not None:
            self._conn.execute(
                "UPDATE orders SET status=?, score=?, updated_at=? WHERE id=?",
                (status.value, score, now, order_id),
            )
        else:
            self._conn.execute(
                "UPDATE orders SET status=?, updated_at=? WHERE id=?",
                (status.value, now, order_id),
            )
        self._conn.commit()

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        return Order(
            id=row["id"], source=row["source"], source_id=row["source_id"],
            title=row["title"], content=row["content"] or "", url=row["url"] or "",
            author=row["author"] or "", budget_min=row["budget_min"],
            budget_max=row["budget_max"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            posted_at=row["posted_at"] or "", status=OrderStatus(row["status"]),
            score=row["score"], raw_json=row["raw_json"] or "",
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ---------------------------------------------------------- proposals

    def insert_proposal(self, p: Proposal) -> int:
        cur = self._conn.execute(
            """INSERT INTO proposals (order_id, title, body, price_min, price_max,
               days, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (p.order_id, p.title, p.body, p.price_min, p.price_max, p.days,
             p.status.value, p.created_at, p.updated_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_proposals(self, status: Optional[ProposalStatus] = None) -> list[Proposal]:
        sql = "SELECT * FROM proposals"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status.value)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Proposal(
                id=r["id"], order_id=r["order_id"], title=r["title"] or "",
                body=r["body"], price_min=r["price_min"], price_max=r["price_max"],
                days=r["days"], status=ProposalStatus(r["status"]),
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def set_proposal_status(self, proposal_id: int, status: ProposalStatus) -> None:
        import datetime
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            "UPDATE proposals SET status=?, updated_at=? WHERE id=?",
            (status.value, now, proposal_id),
        )
        self._conn.commit()

    # ----------------------------------------------------------- projects

    def insert_project(self, p: Project) -> int:
        cur = self._conn.execute(
            """INSERT INTO projects (order_id, customer_id, name, status, price,
               deadline, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (p.order_id, p.customer_id, p.name, p.status.value, p.price,
             p.deadline, p.notes, p.created_at, p.updated_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_project(self, project_id: int) -> Optional[Project]:
        row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        return Project(
            id=row["id"], order_id=row["order_id"], customer_id=row["customer_id"],
            name=row["name"], status=ProjectStatus(row["status"]),
            price=row["price"], deadline=row["deadline"], notes=row["notes"] or "",
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_projects(self, status: Optional[str] = None) -> list[Project]:
        sql = "SELECT * FROM projects"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Project(
                id=r["id"], order_id=r["order_id"], customer_id=r["customer_id"],
                name=r["name"], status=ProjectStatus(r["status"]),
                price=r["price"], deadline=r["deadline"], notes=r["notes"] or "",
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def set_project_status(self, project_id: int, status: str) -> None:
        import datetime
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            "UPDATE projects SET status=?, updated_at=? WHERE id=?",
            (status, now, project_id),
        )
        self._conn.commit()

    # -------------------------------------------------------------- tasks

    def insert_task(self, t: Task) -> int:
        cur = self._conn.execute(
            """INSERT INTO tasks (project_id, title, description, status, artifacts,
               llm_output, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (t.project_id, t.title, t.description, t.status.value,
             json.dumps(t.artifacts, ensure_ascii=False), t.llm_output,
             t.created_at, t.updated_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_tasks(self, project_id: int) -> list[Task]:
        rows = self._conn.execute(
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

    def set_task_status(self, task_id: int, status: str) -> None:
        import datetime
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self._conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (status, now, task_id),
        )
        self._conn.commit()

    # ---------------------------------------------------------- customers

    def upsert_customer(self, c: Customer) -> int:
        if c.platform and c.platform_username:
            row = self._conn.execute(
                "SELECT id FROM customers WHERE platform=? AND platform_username=?",
                (c.platform, c.platform_username),
            ).fetchone()
            if row:
                return row["id"]
        cur = self._conn.execute(
            """INSERT INTO customers (name, contact, platform, platform_username,
               notes, created_at) VALUES (?,?,?,?,?,?)""",
            (c.name, c.contact, c.platform, c.platform_username, c.notes, c.created_at),
        )
        self._conn.commit()
        return cur.lastrowid

    # ------------------------------------------------------- notifications

    def insert_notification(self, n: Notification) -> int:
        cur = self._conn.execute(
            """INSERT INTO notifications (type, channel, subject, content,
               related_id, sent_at, success, error) VALUES (?,?,?,?,?,?,?,?)""",
            (n.type.value, n.channel, n.subject, n.content, n.related_id,
             n.sent_at, int(n.success), n.error),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_notifications(self, limit: int = 50) -> list[Notification]:
        rows = self._conn.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Notification(
                id=r["id"], type=NotificationType(r["type"]),
                channel=r["channel"], subject=r["subject"], content=r["content"] or "",
                related_id=r["related_id"], sent_at=r["sent_at"],
                success=bool(r["success"]), error=r["error"] or "",
            )
            for r in rows
        ]

    # ------------------------------------------------------------ helpers

    def count_orders(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    def pending_orders(self, status: OrderStatus, limit: int = 100) -> list[Order]:
        """按状态取订单（最新优先）。"""
        return self.list_orders(status=status, limit=limit)

    def commit(self) -> None:
        self._conn.commit()
