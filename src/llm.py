from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import load_env


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    api_key: str
    model: str
    timeout: int = 60
    max_tokens: int = 4096

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"


class ModelConfigError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, settings: ModelSettings | None = None) -> None:
        self.settings = settings or load_model_settings()

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.settings.max_tokens,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.settings.chat_completions_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"模型 API 请求失败：HTTP {error.code} {detail[:500]}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"模型 API 连接失败：{error}") from error

        return _extract_content(data)


def load_model_settings() -> ModelSettings:
    load_env()
    base_url = os.getenv("AI_BASE_URL", "").strip()
    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    timeout = _int_env("AI_TIMEOUT", 60)
    max_tokens = _int_env("AI_MAX_TOKENS", 4096)
    missing = []
    if not base_url:
        missing.append("AI_BASE_URL")
    if not api_key:
        missing.append("AI_API_KEY")
    if not model:
        missing.append("AI_MODEL")
    if missing:
        raise ModelConfigError("缺少模型配置：" + "、".join(missing) + "。请复制 .env.example 为 .env 后填写。")
    return ModelSettings(base_url=base_url, api_key=api_key, model=model, timeout=timeout, max_tokens=max_tokens)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _extract_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(first.get("text"), str):
                return first["text"].strip()
    raise RuntimeError("模型 API 返回中未找到 choices[0].message.content。")
