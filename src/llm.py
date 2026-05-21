from __future__ import annotations

import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import load_env


DEFAULT_BASE_URL = "https://xfx.plus"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_WIRE_API = "responses"
DEFAULT_REASONING_EFFORT = "medium"


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    api_key: str
    model: str
    wire_api: str = DEFAULT_WIRE_API
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    timeout: int = 60
    max_tokens: int = 4096

    @property
    def endpoint_url(self) -> str:
        base = self.base_url.rstrip("/")
        if self.wire_api == "responses":
            if base.endswith("/responses"):
                return base
            if base.endswith("/v1"):
                return f"{base}/responses"
            return f"{base}/v1/responses"
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
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.settings.endpoint_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        data = self._post_json(request)
        return _extract_content(data)

    def _post_json(self, request: urllib.request.Request) -> dict[str, Any]:
        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    return json.loads(response.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"模型 API 请求失败：HTTP {error.code} {detail[:500]}") from error
            except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError, ssl.SSLError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
        raise RuntimeError(f"模型 API 连接失败，已重试 3 次：{last_error}") from last_error

    def vision(self, *, image_url: str, prompt: str, max_tokens: int | None = 1200) -> str:
        if self.settings.wire_api == "responses":
            return self.chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": image_url},
                        ],
                    }
                ],
                temperature=0.1,
                max_tokens=max_tokens,
            )
        return self.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            temperature=0.1,
            max_tokens=max_tokens,
        )

    def _payload(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        if self.settings.wire_api == "responses":
            payload: dict[str, Any] = {
                "model": self.settings.model,
                "input": messages,
                "max_output_tokens": max_tokens or self.settings.max_tokens,
            }
            if self.settings.reasoning_effort:
                payload["reasoning"] = {"effort": self.settings.reasoning_effort}
            return payload
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or self.settings.max_tokens,
        }
        if self.settings.reasoning_effort:
            payload["reasoning_effort"] = self.settings.reasoning_effort
        return payload


def load_model_settings() -> ModelSettings:
    load_env()
    base_url = os.getenv("AI_BASE_URL", DEFAULT_BASE_URL).strip()
    api_key = (os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    model = os.getenv("AI_MODEL", DEFAULT_MODEL).strip()
    wire_api = os.getenv("AI_WIRE_API", DEFAULT_WIRE_API).strip().lower()
    reasoning_effort = os.getenv("AI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip()
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
        raise ModelConfigError(
            "缺少模型配置：" + "、".join(missing) + "。请复制 .env.example 为 .env 后填写。"
        )
    if wire_api not in {"responses", "chat"}:
        raise ModelConfigError("AI_WIRE_API 只支持 responses 或 chat。")
    return ModelSettings(
        base_url=base_url,
        api_key=api_key,
        model=model,
        wire_api=wire_api,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
        max_tokens=max_tokens,
    )


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _extract_content(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = data.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        if parts:
            return "\n".join(part.strip() for part in parts if part.strip()).strip()

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
