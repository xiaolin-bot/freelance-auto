"""LLM 客户端：OpenAI 兼容接口（DeepSeek / OpenAI / 通义 / Kimi 等）。

v2: 增加多 LLM fallback —— 当主 LLM 余额不足时，自动切换到备用 LLM。
    不再因为一个 key 失效就停摆。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI

from .config import LLMSettings, load_llm_settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------------- errors


class LLMError(RuntimeError):
    pass


class LLMNoMoneyError(LLMError):
    """余额不足（402/429/insufficient_quota）。触发后上层可降级。"""
    pass


# ------------------------------------------------------------- 主客户端


class LLMClient:
    """LLM 客户端封装。

    用法：
        LLMClient()  # 用 .env 配置
        LLMClient(LLMClient.fallback("groq", api_key="gsk_..."))  # 显式备用
    """

    def __init__(self, settings: Optional[LLMSettings] = None):
        self.settings = settings or load_llm_settings()
        if not self.settings.api_key:
            raise LLMError(
                "未配置 LLM_API_KEY。请在 .env 中填写（可从 https://platform.deepseek.com 获取，"
                "或使用其他 OpenAI 兼容服务）。"
            )
        self._client = OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_sec,
        )
        self._last_was_no_money = False

    # ------------------------------------------------------------- 静态构造

    @staticmethod
    def fallback(name: str, api_key: str = "", model: str = "", base_url: str = "") -> "LLMClient":
        """从环境变量快速构造一个备用 LLM 客户端。

        已知 provider 的默认 base_url / model（无需配置也能用）：
        - groq:        https://api.groq.com/openai/v1,  llama-3.1-8b-instant
        - siliconflow: https://api.siliconflow.cn/v1,     Qwen/Qwen2.5-7B-Instruct
        - openrouter:  https://openrouter.ai/api/v1,       minimax/minimax-m3:free
        - huggingface: https://router.huggingface.co/v1,   meta-llama/Llama-3.1-8B-Instruct
        - doubao:      https://ark.cn-beijing.volces.com/api/v3,  doubao-1-5-pro-32k-250115
        - moonshot:    https://api.moonshot.cn/v1,         moonshot-v1-8k
        """
        import os

        # 已知 provider 的默认配置（用于 key-only 配置场景）
        _DEFAULTS: dict[str, dict[str, str]] = {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "model": "llama-3.1-8b-instant",
            },
            "siliconflow": {
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "Qwen/Qwen2.5-7B-Instruct",
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "model": "minimax/minimax-m3:free",
            },
            "huggingface": {
                "base_url": "https://router.huggingface.co/v1",
                "model": "meta-llama/Llama-3.1-8B-Instruct",
            },
            "doubao": {
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "doubao-1-5-pro-32k-250115",
            },
            "moonshot": {
                "base_url": "https://api.moonshot.cn/v1",
                "model": "moonshot-v1-8k",
            },
        }
        defaults = _DEFAULTS.get(name.lower(), {})

        key = api_key or os.environ.get(f"LLM_FALLBACK_{name.upper()}_KEY", "")
        if not key:
            raise LLMError(
                f"未配置备用 LLM {name} 的 key（环境变量 LLM_FALLBACK_{name.upper()}_KEY）"
            )
        resolved_base = (
            base_url
            or os.environ.get(f"LLM_FALLBACK_{name.upper()}_BASE", "")
            or defaults.get("base_url", "")
        )
        resolved_model = (
            model
            or os.environ.get(f"LLM_FALLBACK_{name.upper()}_MODEL", "")
            or defaults.get("model", "")
        )
        if not resolved_base:
            raise LLMError(
                f"备用 LLM {name} 未配置 base_url（设置 LLM_FALLBACK_{name.upper()}_BASE "
                f"或传入 base_url 参数）"
            )
        s = LLMSettings(api_key=key, base_url=resolved_base, model=resolved_model)
        c = LLMClient.__new__(LLMClient)
        c.settings = s
        c._client = OpenAI(api_key=s.api_key, base_url=s.base_url, timeout=s.timeout_sec)
        c._last_was_no_money = False
        return c

    # ------------------------------------------------------------- 余额探测

    def check_balance(self) -> dict[str, Any]:
        """查询余额（仅 DeepSeek 官方支持，返回原始 JSON）。其他 LLM 返回 {}。"""
        if "deepseek.com" not in (self.settings.base_url or ""):
            return {}
        try:
            import httpx
            r = httpx.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=10,
            )
            return r.json() if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001
            logger.debug("check_balance 失败: %s", e)
            return {}

    # ------------------------------------------------------------- 基础调用

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        """普通对话，返回文本。"""
        try:
            resp = self._client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            self._last_was_no_money = False
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            self._maybe_mark_no_money(e)
            logger.exception("LLM chat failed")
            raise LLMError(f"LLM 调用失败: {e}") from e

    def chat_json(self, system: str, user: str, temperature: float = 0.1) -> dict[str, Any]:
        """请求 JSON 输出（json mode 优先，失败则尝试解析）。"""
        try:
            resp = self._client.chat.completions.create(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": system + "\n\n必须只输出合法 JSON 对象，不要输出其他任何内容。"},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            self._last_was_no_money = False
            text = resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            self._maybe_mark_no_money(e)
            logger.exception("LLM chat_json failed")
            raise LLMError(f"LLM 调用失败: {e}") from e

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
            logger.error("LLM 返回非 JSON: %.500s", text)
            raise LLMError(f"LLM 返回非 JSON 输出: {text[:200]}")

    # ------------------------------------------------------------- 内部

    def _maybe_mark_no_money(self, exc: Exception) -> None:
        """把余额不足类的异常标记下来，供上层决定是否降级。"""
        msg = str(exc).lower()
        if any(s in msg for s in ("insufficient", "402", "quota", "balance", "credit")):
            self._last_was_no_money = True


# ------------------------------------------------------------- 带 fallback 的客户端


class FallbackLLMClient:
    """多 LLM fallback 链：主 LLM 失败 → 依次尝试备用。

    用法：
        client = FallbackLLMClient([
            LLMClient(),  # 主
            LLMClient.fallback("groq"),
            LLMClient.fallback("huggingface"),
        ])
        # 任何 chat / chat_json 调用都会自动 fallback
    """

    def __init__(self, clients: list[LLMClient]):
        if not clients:
            raise LLMError("FallbackLLMClient 至少需要 1 个 LLMClient")
        self.clients = clients

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        return self._run("chat", lambda c: c.chat(system, user, temperature))

    def chat_json(self, system: str, user: str, temperature: float = 0.1) -> dict[str, Any]:
        return self._run("chat_json", lambda c: c.chat_json(system, user, temperature))

    def _run(self, method: str, fn) -> Any:
        """依次调用每个 client，遇到余额不足/失败自动 fallback。"""
        last_err: Exception | None = None
        for i, c in enumerate(self.clients):
            try:
                return fn(c)
            except LLMNoMoneyError as e:
                logger.warning(
                    "LLM #%d (%s) 余额不足，降级到下一个: %s",
                    i, c.settings.base_url, e,
                )
                last_err = e
                continue
            except LLMError as e:
                # 非余额错误：也尝试降级（避免单点故障）
                logger.warning("LLM #%d (%s) 失败，降级: %s", i, c.settings.base_url, e)
                last_err = e
                continue
        raise LLMError(f"所有 LLM 都失败了，最后错误: {last_err}")

    def check_balance(self) -> list[dict[str, Any]]:
        """查询所有 LLM 的余额（仅主 DeepSeek 真正有返回）。"""
        return [c.check_balance() for c in self.clients]


# ------------------------------------------------------------- 工厂：自动 fallback


def get_llm() -> "LLMClient | FallbackLLMClient":
    """工厂函数：返回带自动 fallback 的 LLM 客户端。

    用法：把 `LLMClient()` 全部替换成 `get_llm()`。

    行为：
    - 默认读 .env 的主 LLM 配置；
    - 如果设置了以下任一环境变量，自动追加为 fallback：
        LLM_FALLBACK_GROQ_KEY + (LLM_FALLBACK_GROQ_BASE, LLM_FALLBACK_GROQ_MODEL)
        LLM_FALLBACK_SILICONFLOW_KEY + ...
        LLM_FALLBACK_OPENROUTER_KEY + ...
        LLM_FALLBACK_HUGGINGFACE_KEY + ...
        LLM_FALLBACK_DOUBAO_KEY + ...
    - 若主 LLM 失败（尤其是余额不足），自动切换到下一个；
    - 找不到任何 key → 抛 LLMError（与原 LLMClient 一致）。
    """
    import os

    # 关键：pydantic-settings 只读 LLM_* 注入 LLMSettings，不会注入 os.environ。
    # 手动读 .env 把 LLM_FALLBACK_* 灌进 os.environ，下面的 os.environ.get 才能拿到。
    _load_env_fallbacks()

    # 主
    try:
        primary = LLMClient()
    except LLMError as e:
        # 主没配 → 直接报错，避免静默用错
        raise

    # 收集 fallback
    fallbacks: list[LLMClient] = []
    for name in ("groq", "siliconflow", "openrouter", "huggingface", "doubao", "moonshot"):
        key = os.environ.get(f"LLM_FALLBACK_{name.upper()}_KEY", "").strip()
        if not key:
            continue
        try:
            c = LLMClient.fallback(name, api_key=key)
            fallbacks.append(c)
        except LLMError as e:
            logger.warning("fallback %s 初始化失败: %s", name, e)

    if not fallbacks:
        return primary
    return FallbackLLMClient([primary, *fallbacks])


def _load_env_fallbacks() -> None:
    """从 .env 读 LLM_FALLBACK_* 系列变量到 os.environ。

    简易解析（不依赖 python-dotenv），支持 KEY=value 格式，跳过 # 注释。
    已存在的环境变量不被覆盖（保持 shell 里 export 的优先级）。
    """
    import os
    from pathlib import Path

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            # 去掉行内注释
            if " #" in v:
                v = v.split(" #", 1)[0].strip()
            if v.startswith("#") or not v:
                continue
            if not k.startswith("LLM_FALLBACK_"):
                continue
            os.environ.setdefault(k, v)
    except OSError:
        # .env 读不到不影响主流程
        pass
