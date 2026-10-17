"""Gemini client: structured JSON output, schema validation with one retry, model fallback."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import settings

log = logging.getLogger("operateguard.llm")
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


def _is_model_missing(e: Exception) -> bool:
    s = str(e).lower()
    return ("not found" in s or "404" in s or "not_found" in s or "is not supported" in s
            or "does not have access" in s or "unsupported model" in s)


class GeminiClient:
    """Thin wrapper over google-genai. Works with Vertex AI (ADC) or a Gemini API key."""

    def __init__(self) -> None:
        from google import genai
        from google.genai import types
        self.types = types
        if settings.use_vertex:
            self.client = genai.Client(vertexai=True, project=settings.project, location=settings.gemini_location)
        else:
            if not settings.gemini_api_key:
                raise LLMError("No Gemini credentials: set GOOGLE_CLOUD_PROJECT (Vertex AI) or GEMINI_API_KEY")
            self.client = genai.Client(api_key=settings.gemini_api_key)
        self._resolved: dict[str, str] = {}
        self._thinking_ok = bool(settings.thinking_level)
        self._schema_ok = True

    def _candidates(self, model: str) -> list[str]:
        if model in self._resolved:
            return [self._resolved[model]]
        seen, out = set(), []
        for m in [model] + settings.model_fallbacks:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out

    def _config(self, schema: Type[BaseModel], system: str, temperature: float):
        t = self.types
        kw: dict[str, Any] = dict(system_instruction=system, temperature=temperature, response_mime_type="application/json")
        if self._schema_ok:
            kw["response_schema"] = schema
        if self._thinking_ok:
            kw["thinking_config"] = t.ThinkingConfig(thinking_level=settings.thinking_level)
        return t.GenerateContentConfig(**kw)

    async def _call(self, model: str, contents: list, schema, system: str, temperature: float):
        import asyncio
        last: Optional[Exception] = None
        for m in self._candidates(model):
            for attempt in range(4):  # transient retries + config downgrades without burning the model candidate
                try:
                    resp = await self.client.aio.models.generate_content(
                        model=m, contents=contents, config=self._config(schema, system, temperature))
                    self._resolved[model] = m
                    return m, resp
                except Exception as e:  # noqa: BLE001 - classify and downgrade
                    last = e
                    s = str(e).lower()
                    if any(k in s for k in ("429", "resource_exhausted", "503", "unavailable", "deadline")) and attempt < 3:
                        # Rate limits (common on free Gemini API keys) back off longer than transient 503s.
                        wait = (4, 8, 15)[attempt] if ("429" in s or "resource_exhausted" in s) else 1.5 * (attempt + 1)
                        log.warning("transient Gemini error on %s, retrying in %ss: %s", m, wait, e)
                        await asyncio.sleep(wait)
                        continue
                    if self._thinking_ok and "think" in s:
                        log.warning("thinking config rejected by %s, retrying without: %s", m, e)
                        self._thinking_ok = False
                        continue
                    if self._schema_ok and "schema" in s and "400" in s:
                        log.warning("response_schema rejected by %s, using prompt-only JSON: %s", m, e)
                        self._schema_ok = False
                        continue
                    break
            if last is not None and _is_model_missing(last):
                log.warning("model %s unavailable (%s); trying next fallback", m, last)
                continue
            break
        raise LLMError(f"Gemini call failed: {last}")

    async def generate_json(self, *, model: str, schema: Type[T], system: str, parts: list,
                            temperature: float = 0.1) -> tuple[T, dict]:
        """Ask for JSON matching ``schema``. Invalid output -> one retry with the error appended (D4)."""
        t0 = time.perf_counter()
        types = self.types
        contents = [types.Content(role="user", parts=list(parts))]
        attempts = 0
        err_text = ""
        while attempts < 2:
            attempts += 1
            used_model, resp = await self._call(model, contents, schema, system, temperature)
            raw = getattr(resp, "text", None) or ""
            try:
                data = json.loads(raw)
                obj = schema.model_validate(data)
                usage = getattr(resp, "usage_metadata", None)
                meta = {"model": used_model, "attempts": attempts, "latency_ms": int((time.perf_counter() - t0) * 1000),
                        "input_tokens": getattr(usage, "prompt_token_count", None),
                        "output_tokens": getattr(usage, "candidates_token_count", None)}
                return obj, meta
            except (json.JSONDecodeError, ValidationError) as e:
                err_text = str(e)[:1500]
                log.warning("schema validation failed (attempt %s): %s", attempts, err_text)
                contents = contents + [
                    types.Content(role="model", parts=[types.Part.from_text(text=raw[:8000])]),
                    types.Content(role="user", parts=[types.Part.from_text(
                        text="Your previous answer did not validate against the required JSON schema. "
                             f"Validation error:\n{err_text}\nReturn only corrected JSON.")]),
                ]
        raise LLMError(f"Model output failed schema validation twice: {err_text}")

    async def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        import asyncio
        t = self.types

        async def one(x: str):
            r = await self.client.aio.models.embed_content(
                model=settings.embedding_model, contents=x, config=t.EmbedContentConfig(task_type=task_type))
            return list(r.embeddings[0].values)

        return list(await asyncio.gather(*[one(x) for x in texts]))

    def pdf_part(self, data: bytes):
        return self.types.Part.from_bytes(data=data, mime_type="application/pdf")

    def text_part(self, text: str):
        return self.types.Part.from_text(text=text)


_client: Optional[Any] = None


def get_llm():
    global _client
    if _client is None:
        _client = GeminiClient()
    return _client


def set_llm(client) -> None:  # tests inject a fake; never used on the live path
    global _client
    _client = client
