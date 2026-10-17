import random
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

from wellwell.config import Settings, get_settings

log = structlog.get_logger()


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    latency_ms: int


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> Completion: ...


class GemmaOnGemini:
    """Models on Google's Gemini API (Gemini Flash-Lite by default, Gemma 4 as fallback), with retries across them."""

    def __init__(self, settings: Settings):
        from google import genai
        from google.genai import types

        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=settings.gemini_api_key,
                                    http_options=types.HttpOptions(timeout=settings.llm_call_timeout_s * 1000))
        self._types = types
        chain = [m.strip() for m in settings.llm_models.split(",") if m.strip()] or [settings.gemma_model]
        primary = chain[0]
        # The first model takes most attempts; the others get a turn when it keeps failing.
        pattern = [primary, primary, *chain[1:2], primary, *chain[2:3], *chain[1:2], *chain[2:3]][:7]
        self._models = pattern + [m for m in chain if m not in pattern]

    def complete(self, system: str, prompt: str) -> Completion:
        contents = [self._types.Content(role="user", parts=[self._types.Part(text=f"{system}\n\n{prompt}")])]
        config = self._types.GenerateContentConfig(temperature=0.1, max_output_tokens=4096)

        def call(model: str) -> Completion:
            started = time.perf_counter()
            response = self._client.models.generate_content(model=model, contents=contents, config=config)
            parts = response.candidates[0].content.parts if response.candidates else []
            text = "".join(p.text or "" for p in parts if not getattr(p, "thought", False))
            if not text.strip():
                raise TransientError("empty response")
            return Completion(text, model, int((time.perf_counter() - started) * 1000))

        return with_backoff(lambda model: hedged(call, model), self._models, label="gemini")


class TransientError(RuntimeError):
    pass


def hedged(call, model: str, hedge_after_s: float = 7.0, wait_s: float = 75.0) -> Completion:
    """Sends a second identical request if the first is slow, and keeps whichever answers first.

    The free Gemma endpoint has a long tail: most calls take about 6 s, some hang or fail late.
    """
    pool = ThreadPoolExecutor(max_workers=2)
    pending = {pool.submit(call, model)}
    hedge_sent = False
    deadline = time.monotonic() + wait_s
    last_error: Exception | None = None
    try:
        while pending and time.monotonic() < deadline:
            timeout = hedge_after_s if not hedge_sent else max(deadline - time.monotonic(), 0.1)
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    return future.result()
                except Exception as error:
                    last_error = error
            if not hedge_sent:
                hedge_sent = True
                log.info("llm.hedge", model=model)
                pending.add(pool.submit(call, model))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise last_error or TransientError("timed out waiting for the model")


_RETRY_DELAY = re.compile(r"retry(?:Delay|[ _-]?after)[\"':= ]+(\d+(?:\.\d+)?)s?", re.IGNORECASE)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, TransientError):
        return True
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    text = str(error)
    if code in (408, 429, 500, 502, 503, 504) or any(
            marker in text for marker in ("INTERNAL", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE", "timed out",
                                          "Timeout", "Connection")):
        return True
    return False


_EXHAUSTED: set[str] = set()


def with_backoff(call, models: list[str], label: str, max_attempts: int = 7, budget_s: float = 100.0,
                 base_s: float = 1.0, cap_s: float = 20.0) -> Completion:
    """Exponential backoff with full jitter, alternating models, honouring a server-suggested retry delay.

    Client errors such as a bad request are not retried; server errors, rate limits, timeouts and empty
    replies are, until the attempts or the time budget run out.
    """
    deadline = time.monotonic() + budget_s
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        model = models[min(attempt, len(models) - 1)] if len(models) >= max_attempts else models[attempt % len(models)]
        if model in _EXHAUSTED and any(m not in _EXHAUSTED for m in models):
            model = next(m for m in models if m not in _EXHAUSTED)
        try:
            return call(model)
        except Exception as error:
            last_error = error
            if not _is_retryable(error):
                raise LLMError(f"{model} rejected the request: {str(error)[:300]}") from error
            hinted = _RETRY_DELAY.search(str(error))
            delay = float(hinted.group(1)) if hinted else random.uniform(0, min(cap_s, base_s * 2 ** attempt))
            remaining = deadline - time.monotonic()
            if hinted and delay > 120:
                # A quota that resets in hours: stop asking this model and move on to the next one now.
                _EXHAUSTED.add(model)
                delay = 0.0
            log.warning("llm.backoff", provider=label, model=model, attempt=attempt + 1, delay_s=round(delay, 1),
                        error=str(error)[:160])
            if attempt == max_attempts - 1 or remaining <= delay:
                break
            time.sleep(delay)
    raise LLMError(f"Gemma is unavailable right now after retries: {str(last_error)[:300]}")


class OpenAICompatible:
    """Ollama (local Gemma 4) or OpenRouter, through the OpenAI-style chat API."""

    def __init__(self, base_url: str, model: str, api_key: str = "ollama", timeout_s: int = 120):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout_s

    def complete(self, system: str, prompt: str) -> Completion:
        def call(model: str) -> Completion:
            started = time.perf_counter()
            try:
                response = httpx.post(self._url, headers=self._headers, timeout=self._timeout, json={
                    "model": model, "temperature": 0.1,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                })
            except httpx.HTTPError as error:
                raise TransientError(f"connection problem: {error}") from error
            if response.status_code >= 400:
                error = RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
                error.code = response.status_code
                raise error
            text = response.json()["choices"][0]["message"]["content"] or ""
            if not text.strip():
                raise TransientError("empty response")
            return Completion(text, model, int((time.perf_counter() - started) * 1000))

        return with_backoff(call, [self._model], label=self._url)


class FallbackChain:
    """Tries each Gemma 4 endpoint in order until one answers."""

    def __init__(self, providers: list[tuple[str, LLM]]):
        self._providers = providers

    def complete(self, system: str, prompt: str) -> Completion:
        errors = []
        for name, provider in self._providers:
            try:
                return provider.complete(system, prompt)
            except LLMError as error:
                log.warning("llm.provider_failed", provider=name, error=str(error)[:200])
                errors.append(f"{name}: {error}")
        raise LLMError(" | ".join(errors))


def _ollama(settings: Settings) -> LLM:
    return OpenAICompatible(settings.ollama_base_url + "/v1", settings.ollama_model or "gemma4:e4b", timeout_s=180)


def _openrouter(settings: Settings) -> LLM | None:
    if not settings.openrouter_api_key:
        return None
    return OpenAICompatible("https://openrouter.ai/api/v1", settings.openrouter_model, settings.openrouter_api_key)


def build_llm(provider: str | None = None) -> LLM:
    settings = get_settings()
    chosen = provider or settings.llm_provider
    if chosen == "ollama":
        return _ollama(settings)
    if chosen == "openrouter":
        router = _openrouter(settings)
        if router is None:
            raise LLMError("OPENROUTER_API_KEY is not set")
        return router
    chain: list[tuple[str, LLM]] = [("gemini", GemmaOnGemini(settings))]
    if router := _openrouter(settings):
        chain.append(("openrouter", router))
    if settings.ollama_model:
        chain.append(("ollama", _ollama(settings)))
    return FallbackChain(chain) if len(chain) > 1 else chain[0][1]
