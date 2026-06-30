"""Small provider adapter for the content pipelines.

Supports an automatic provider fallback chain: if the primary provider
(e.g. Gemini) is unavailable or rate limited even after retries, the client
transparently retries the request against any other provider that has an API
key configured. This keeps the pipeline running through transient outages
such as Gemini's HTTP 503 "high demand" / UNAVAILABLE responses.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests


class AIProviderError(RuntimeError):
    """Raised when the selected AI provider cannot complete a request."""


# Errors that are permanent for a given provider — no point retrying the same
# provider, but a *different* provider may still succeed, so we fall back.
_FATAL_MARKERS = (
    "credit balance is too low",
    "insufficient_quota",
    "billing",
    "authentication_error",
    "invalid api key",
    "invalid_api_key",
    "invalid x-api-key",
    "permission denied",
    "permission_denied",
    "permission_error",
    "api key not valid",
)


@dataclass
class ProviderConfig:
    provider: str
    model: str
    api_key: str


@dataclass
class AIClient:
    provider: str
    model: str
    api_key: str
    timeout: int = 90
    fallbacks: List[ProviderConfig] = field(default_factory=list)
    max_retries: int = 4
    backoff_base: int = 5
    _anthropic_clients: Dict[str, Any] = field(default_factory=dict)

    def complete(self, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None) -> str:
        """Try the primary provider (with retries), then each fallback provider."""
        chain = [ProviderConfig(self.provider, self.model, self.api_key)] + list(self.fallbacks)
        last_exc: Optional[Exception] = None

        for i, cfg in enumerate(chain):
            try:
                return self._complete_with_retries(cfg, prompt, max_tokens, response_mime_type)
            except Exception as e:  # noqa: BLE001 — we deliberately try the next provider
                last_exc = e
                if i < len(chain) - 1:
                    nxt = chain[i + 1]
                    print(
                        f"  [AI Client] Provider '{cfg.provider}' ({cfg.model}) failed: "
                        f"{str(e)[:160]}\n  [AI Client] Falling back to '{nxt.provider}' ({nxt.model})..."
                    )
                else:
                    print(f"  [AI Client] Provider '{cfg.provider}' failed and no fallback remains.")

        assert last_exc is not None
        raise last_exc

    def _complete_with_retries(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int, response_mime_type: Optional[str]
    ) -> str:
        for attempt in range(self.max_retries):
            try:
                if cfg.provider == "openai":
                    return self._complete_openai(cfg, prompt, max_tokens, response_mime_type)
                if cfg.provider == "gemini":
                    return self._complete_gemini(cfg, prompt, max_tokens, response_mime_type)
                if cfg.provider == "anthropic":
                    return self._complete_anthropic(cfg, prompt, max_tokens, response_mime_type)
                raise AIProviderError(f"Unsupported AI_PROVIDER: {cfg.provider}")
            except Exception as e:  # noqa: BLE001
                err_msg = str(e).lower()

                # Permanent failures for this provider — don't burn retries, let
                # the caller fall back to a different provider immediately.
                if any(marker in err_msg for marker in _FATAL_MARKERS):
                    raise

                is_rate_limit = (
                    "429" in err_msg
                    or "quota" in err_msg
                    or "rate limit" in err_msg
                    or "resource_exhausted" in err_msg
                )
                # Transient server-side errors (overloaded / temporarily unavailable).
                # Gemini returns HTTP 503 UNAVAILABLE ("high demand") under load.
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
                if (is_rate_limit or is_transient_server) and attempt < self.max_retries - 1:
                    sleep_time = (2 ** attempt) * self.backoff_base
                    reason = "Rate limit (429/quota)" if is_rate_limit else "Transient server error (5xx)"
                    print(
                        f"  [AI Client] {reason} from '{cfg.provider}'. Retrying in {sleep_time}s... "
                        f"(Attempt {attempt + 1}/{self.max_retries})"
                    )
                    time.sleep(sleep_time)
                else:
                    raise

        # Unreachable, but keeps type checkers happy.
        raise AIProviderError(f"{cfg.provider} exhausted retries")

    def _complete_openai(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None
    ) -> str:
        payload: Dict[str, Any] = {
            "model": cfg.model,
            "input": prompt,
            "max_output_tokens": max_tokens,
        }
        # In OpenAI, if response_mime_type is JSON, set response_format
        if response_mime_type == "application/json":
            payload["response_format"] = {"type": "json_object"}

        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
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

    def _complete_gemini(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None
    ) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{cfg.model}:generateContent"
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
            params={"key": cfg.api_key},
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

    def _complete_anthropic(
        self, cfg: ProviderConfig, prompt: str, max_tokens: int, response_mime_type: Optional[str] = None
    ) -> str:
        client = self._anthropic_clients.get(cfg.api_key)
        if client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise AIProviderError(
                    "anthropic package not installed. Run: pip install anthropic"
                ) from exc
            client = Anthropic(api_key=cfg.api_key)
            self._anthropic_clients[cfg.api_key] = client

        response = client.messages.create(
            model=cfg.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()


# Default model per provider when not explicitly configured.
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-3.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
}

_ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _model_for(provider: str, is_primary: bool) -> str:
    """Resolve the model name for a provider. AI_MODEL only applies to the primary."""
    if is_primary:
        explicit = os.environ.get("AI_MODEL")
        if explicit:
            return explicit
    per_provider = os.environ.get(f"{provider.upper()}_MODEL")
    if per_provider:
        return per_provider
    return _DEFAULT_MODELS[provider]


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

    if provider not in _ENV_KEYS:
        raise AIProviderError("AI_PROVIDER must be one of: openai, gemini, anthropic")

    api_key = os.environ.get(_ENV_KEYS[provider], "")
    model = _model_for(provider, is_primary=True)

    if not api_key:
        raise AIProviderError(f"{_ENV_KEYS[provider]} is not set for AI_PROVIDER={provider}")

    # Build the fallback chain from any *other* providers that have a key set.
    # Preference order keeps things deterministic and puts the cheaper/faster
    # general-purpose providers first.
    fallback_order = [p for p in ("gemini", "openai", "anthropic") if p != provider]
    fallbacks: List[ProviderConfig] = []
    for fp in fallback_order:
        fk = os.environ.get(_ENV_KEYS[fp], "")
        if fk:
            fallbacks.append(ProviderConfig(provider=fp, model=_model_for(fp, is_primary=False), api_key=fk))

    return AIClient(provider=provider, model=model, api_key=api_key, fallbacks=fallbacks)
