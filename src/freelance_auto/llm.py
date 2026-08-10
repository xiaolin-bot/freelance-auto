"""LLM 客户端：OpenAI 兼容接口（DeepSeek / OpenAI / 通义 / Kimi）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI

from .config import LLMSettings, load_llm_settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
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

    # ------------------------------------------------------------ basic

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
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
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
            text = resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            logger.exception("LLM chat_json failed")
            raise LLMError(f"LLM 调用失败: {e}") from e

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 ```json ... ``` 块
            m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
            logger.error("LLM 返回非 JSON: %.500s", text)
            raise LLMError(f"LLM 返回非 JSON 输出: {text[:200]}")
