"""配置加载：config.yaml（非敏感）+ .env（敏感，LLM keys 等）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


# ---------------------------------------------------------------- sections


class SourceConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    topic_id: int = 0
    board: str = ""
    categories: str = ""  # 逗号分隔；WWR 等多分类源用


class RadarConfig(BaseModel):
    request_interval_sec: float = 3.0
    max_pages: int = 3
    order_ttl_days: int = 7
    eleduck: SourceConfig = SourceConfig(base_url="https://eleduck.com", topic_id=19)
    v2ex: SourceConfig = SourceConfig(base_url="https://www.v2ex.com", board="freelancer")
    yaojiedan: SourceConfig = SourceConfig(base_url="https://www.yaojiedan.com")
    wwr: SourceConfig = SourceConfig(
        base_url="https://weworkremotely.com",
        categories="remote-full-stack-programming-jobs,remote-programming-jobs,remote-devops-sysadmin-jobs",
    )


class ScreenerConfig(BaseModel):
    daily_cap: int = 20
    min_score: float = 60
    top_n: int = 5
    profile: str = ""


class ProposalConfig(BaseModel):
    default_price_min: int = 500
    default_price_max: int = 5000
    default_days: int = 5
    require_approval: bool = True


class DeliveryConfig(BaseModel):
    workdir: str = "data/workspaces"
    auto_test: bool = True
    require_review: bool = True


class CrmConfig(BaseModel):
    followup_days: int = 2
    deadline_alert_days: int = 1
    payment_reminder_days: int = 3


class ServerChanConfig(BaseModel):
    send_key: str = ""


class DingTalkConfig(BaseModel):
    webhook: str = ""


class EmailConfig(BaseModel):
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = Field(default_factory=list)


class NotifyConfig(BaseModel):
    channel: Literal["serverchan", "dingtalk", "email", "none"] = "none"
    serverchan: ServerChanConfig = ServerChanConfig()
    dingtalk: DingTalkConfig = DingTalkConfig()
    email: EmailConfig = EmailConfig()
    on_new_orders: bool = True
    on_shortlist: bool = True
    on_proposal_ready: bool = True
    on_delivery_review: bool = True


class SchedulerConfig(BaseModel):
    radar_interval_min: int = 60
    screen_interval_min: int = 60
    proposal_interval_min: int = 120
    crm_interval_min: int = 180


class AppConfig(BaseModel):
    radar: RadarConfig = RadarConfig()
    screener: ScreenerConfig = ScreenerConfig()
    proposal: ProposalConfig = ProposalConfig()
    delivery: DeliveryConfig = DeliveryConfig()
    crm: CrmConfig = CrmConfig()
    notify: NotifyConfig = NotifyConfig()
    scheduler: SchedulerConfig = SchedulerConfig()

    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    def db_path(self) -> Path:
        return PROJECT_ROOT / "data" / "freelance.db"

    def workspaces_dir(self) -> Path:
        return PROJECT_ROOT / self.delivery.workdir


# ---------------------------------------------------------------- LLM env


class LLMSettings(BaseSettings):
    """从环境变量 / .env 读取，前缀 LLM_。"""

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=str(DEFAULT_ENV_PATH), extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    timeout_sec: float = 120.0


# ---------------------------------------------------------------- loaders


def load_config(path: Path | str | None = None) -> AppConfig:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def load_llm_settings(env_path: Path | str | None = None) -> LLMSettings:
    return LLMSettings(_env_file=env_path or DEFAULT_ENV_PATH)
