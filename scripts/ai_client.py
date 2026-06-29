"""Small provider adapter for the content pipelines."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


class AIProviderError(RuntimeError):
    """Raised when the selected AI provider cannot complete a request."""


@dataclass
class AIClient:
    provider: str
    model: str
    api_key: str
    timeout: int = 90
    _anthropic_client: Optional[Any] = None

    def complete(self, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None) -> str:
        import time
        max_retries = 5
        backoff_factor = 2

        for attempt in range(max_retries):
            try:
                if self.provider == "openai":
                    return self._complete_openai(prompt, max_tokens, response_mime_type)
                if self.provider == "gemini":
                    return self._complete_gemini(prompt, max_tokens, response_mime_type)
                if self.provider == "anthropic":
                    return self._complete_anthropic(prompt, max_tokens, response_mime_type)
                raise AIProviderError(f"Unsupported AI_PROVIDER: {self.provider}")
            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = (
                    "429" in err_msg
                    or "quota" in err_msg
                    or "rate limit" in err_msg
                    or "resource_exhausted" in err_msg
                )
                # Transient server-side errors (overloaded / temporarily unavailable).
                # Gemini returns HTTP 503 UNAVAILABLE ("high demand") under load;
                # these are retriable and should not fail the whole pipeline run.
                is_transient_server = (
                    "503" in err_msg
                    or "500" in err_msg
                    or "502" in err_msg
                    or "504" in err_msg
                    or "unavailable" in err_msg
                    or "overloaded" in err_msg
                    or "high demand" in err_msg
                    or "internal error" in err_msg
                    or "timed out" in err_msg
                    or "timeout" in err_msg
                )
                if (is_rate_limit or is_transient_server) and attempt < max_retries - 1:
                    sleep_time = (backoff_factor ** attempt) * 5
                    reason = "Rate limit (429/quota)" if is_rate_limit else "Transient server error (5xx)"
                    print(f"  [AI Client] {reason}. Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(sleep_time)
                else:
                    raise e

    def _complete_openai(self, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": max_tokens,
        }
        # In OpenAI, if response_mime_type is JSON, set response_format
        if response_mime_type == "application/json":
            payload["response_format"] = {"type": "json_object"}

        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise AIProviderError(
                f"OpenAI API HTTP {response.status_code}: {response.text[:600]}"
            )

        data = response.json()
        if data.get("output_text"):
            return data["output_text"].strip()

        parts = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    parts.append(content["text"])
        text = "\n".join(parts).strip()
        if not text:
            raise AIProviderError(f"OpenAI response did not contain text: {data}")
        return text

    def _complete_gemini(self, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model}:generateContent"
        )
        generation_config: Dict[str, Any] = {"maxOutputTokens": max_tokens}
        if response_mime_type:
            generation_config["responseMimeType"] = response_mime_type

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        response = requests.post(
            url,
            params={"key": self.api_key},
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise AIProviderError(
                f"Gemini API HTTP {response.status_code}: {response.text[:600]}"
            )

        data = response.json()
        parts = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("text"):
                    parts.append(part["text"])
        text = "\n".join(parts).strip()
        if not text:
            raise AIProviderError(f"Gemini response did not contain text: {data}")
        return text

    def _complete_anthropic(self, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None) -> str:
        if self._anthropic_client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise AIProviderError(
                    "anthropic package not installed. Run: pip install anthropic"
                ) from exc
            self._anthropic_client = Anthropic(api_key=self.api_key)

        response = self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


def create_ai_client() -> AIClient:
    provider = os.environ.get("AI_PROVIDER", "").strip().lower()
    if not provider:
        if os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("GEMINI_API_KEY"):
            provider = "gemini"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            provider = "gemini"

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = os.environ.get("AI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    elif provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("AI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash"
    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = (
            os.environ.get("AI_MODEL")
            or os.environ.get("ANTHROPIC_MODEL")
            or "claude-haiku-4-5-20251001"
        )
    else:
        raise AIProviderError("AI_PROVIDER must be one of: openai, gemini, anthropic")

    if not api_key:
        env_name = {
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }[provider]
        raise AIProviderError(f"{env_name} is not set for AI_PROVIDER={provider}")

    return AIClient(provider=provider, model=model, api_key=api_key)
