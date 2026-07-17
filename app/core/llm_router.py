import os
import time
import httpx
from typing import Dict, List, Optional
from pydantic import BaseModel
from .exceptions import RateLimitError, AllProvidersFailedError
from .logger import get_logger
from .db import get_session
from .models import LLMCall

logger = get_logger(__name__)

class ProviderResponse(BaseModel):
    text: str
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None

class BaseProvider:
    name: str
    async def health_check(self) -> bool:
        raise NotImplementedError
    async def call(self, prompt: str, **kwargs) -> ProviderResponse:
        raise NotImplementedError

class GroqProvider(BaseProvider):
    name = "groq"
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        self.model = model
        
    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {self.api_key}"})
                return res.status_code == 200
        except Exception:
            return False

    async def call(self, prompt: str, **kwargs) -> ProviderResponse:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                res = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        **kwargs
                    }
                )
                if res.status_code == 429:
                    raise RateLimitError(f"Groq Rate Limit: {res.text}")
                res.raise_for_status()
                data = res.json()
                usage = data.get("usage", {})
                return ProviderResponse(
                    text=data["choices"][0]["message"]["content"],
                    tokens_in=usage.get("prompt_tokens"),
                    tokens_out=usage.get("completion_tokens")
                )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Groq Timeout: {e}")
        except httpx.RequestError as e:
            raise ConnectionError(f"Groq Connection Error: {e}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RateLimitError(f"Groq Rate Limit: {e.response.text}")
            raise ConnectionError(f"Groq HTTP Error {e.response.status_code}: {e.response.text}")

class OllamaProvider(BaseProvider):
    name = "ollama"
    def __init__(self, base_url: str, model: str = "qwen2.5:14b"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        
    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    async def call(self, prompt: str, **kwargs) -> ProviderResponse:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                res = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        **kwargs
                    }
                )
                res.raise_for_status()
                data = res.json()
                return ProviderResponse(
                    text=data.get("message", {}).get("content", ""),
                    tokens_in=data.get("prompt_eval_count"),
                    tokens_out=data.get("eval_count")
                )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Ollama Timeout: {e}")
        except httpx.RequestError as e:
            raise ConnectionError(f"Ollama Connection Error: {e}")
        except httpx.HTTPStatusError as e:
            raise ConnectionError(f"Ollama HTTP Error {e.response.status_code}: {e.response.text}")

class GeminiProvider(BaseProvider):
    name = "gemini"
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model
        
    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}")
                return res.status_code == 200
        except Exception:
            return False

    async def call(self, prompt: str, **kwargs) -> ProviderResponse:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                res = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}",
                    json={
                        "contents": [{"parts":[{"text": prompt}]}]
                    }
                )
                if res.status_code == 429:
                    raise RateLimitError(f"Gemini Rate Limit: {res.text}")
                res.raise_for_status()
                data = res.json()
                
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    text = ""
                    
                usage = data.get("usageMetadata", {})
                return ProviderResponse(
                    text=text,
                    tokens_in=usage.get("promptTokenCount"),
                    tokens_out=usage.get("candidatesTokenCount")
                )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Gemini Timeout: {e}")
        except httpx.RequestError as e:
            raise ConnectionError(f"Gemini Connection Error: {e}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RateLimitError(f"Gemini Rate Limit: {e.response.text}")
            raise ConnectionError(f"Gemini HTTP Error {e.response.status_code}: {e.response.text}")

class NvidiaProvider(BaseProvider):
    name = "nvidia"
    def __init__(self, api_key: str, model: str = "nvidia/llama-3.1-nemotron-70b-instruct"):
        self.api_key = api_key
        self.model = model
        
    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get("https://integrate.api.nvidia.com/v1/models", headers={"Authorization": f"Bearer {self.api_key}"})
                return res.status_code == 200
        except Exception:
            return False

    async def call(self, prompt: str, **kwargs) -> ProviderResponse:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                res = await client.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        **kwargs
                    }
                )
                if res.status_code == 429:
                    raise RateLimitError(f"Nvidia Rate Limit: {res.text}")
                res.raise_for_status()
                data = res.json()
                usage = data.get("usage", {})
                return ProviderResponse(
                    text=data["choices"][0]["message"]["content"],
                    tokens_in=usage.get("prompt_tokens"),
                    tokens_out=usage.get("completion_tokens")
                )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Nvidia Timeout: {e}")
        except httpx.RequestError as e:
            raise ConnectionError(f"Nvidia Connection Error: {e}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RateLimitError(f"Nvidia Rate Limit: {e.response.text}")
            raise ConnectionError(f"Nvidia HTTP Error {e.response.status_code}: {e.response.text}")

class RouterConfig(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    nvidia_api_key: str = os.getenv("NVIDIA_API_KEY", "")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
    providers_override: Optional[Dict[str, BaseProvider]] = None

class LLMRouter:
    def __init__(self, config: RouterConfig):
        self.config = config
        self.task_routing = {
            "research_synthesis": ["groq", "gemini"],
            "email_craft": ["groq", "gemini"],
            "classification": ["ollama", "groq"],
            "orchestration_decision": ["groq", "ollama"],
        }
        self.default_order = ["groq", "ollama", "gemini", "nvidia"]
        
        if config.providers_override is not None:
            self.providers = config.providers_override
        else:
            self.providers = {
                "groq": GroqProvider(config.groq_api_key),
                "ollama": OllamaProvider(config.ollama_base_url, config.ollama_model),
                "gemini": GeminiProvider(config.gemini_api_key),
                "nvidia": NvidiaProvider(config.nvidia_api_key),
            }

    async def call(self, prompt: str, task_type: str = "general", **kwargs) -> str:
        order = self.task_routing.get(task_type, self.default_order)
        
        last_error = None
        
        for provider_name in order:
            provider = self.providers.get(provider_name)
            if not provider:
                continue
                
            success = 0
            tokens_in = None
            tokens_out = None
            error_msg = None
            start_time = time.monotonic()
            
            try:
                # 1. Health check
                is_healthy = await provider.health_check()
                if not is_healthy:
                    error_msg = f"{provider_name} health check failed or not configured."
                    latency_ms = int((time.monotonic() - start_time) * 1000)
                    await self._log_call(provider_name, task_type, 0, latency_ms, None, None, error_msg)
                    last_error = error_msg
                    continue
                
                # 2. Call
                call_start = time.monotonic()
                response = await provider.call(prompt, **kwargs)
                latency_ms = int((time.monotonic() - call_start) * 1000)
                
                # 3. Success logging
                await self._log_call(
                    provider_name, 
                    task_type, 
                    1, 
                    latency_ms, 
                    response.tokens_in, 
                    response.tokens_out, 
                    None
                )
                return response.text
                
            except (RateLimitError, TimeoutError, ConnectionError) as e:
                error_msg = str(e)
                last_error = error_msg
                latency_ms = int((time.monotonic() - start_time) * 1000)
                await self._log_call(provider_name, task_type, 0, latency_ms, None, None, error_msg)
                logger.warning(f"Provider {provider_name} failed for task {task_type}: {error_msg}")
                continue
            except Exception as e:
                error_msg = f"Unexpected Error: {str(e)}"
                last_error = error_msg
                latency_ms = int((time.monotonic() - start_time) * 1000)
                await self._log_call(provider_name, task_type, 0, latency_ms, None, None, error_msg)
                logger.error(f"Provider {provider_name} unexpectedly failed: {error_msg}")
                continue
                
        raise AllProvidersFailedError(f"All providers failed. Last error: {last_error}")
        
    async def _log_call(self, provider: str, task_type: str, success: int, latency_ms: int, 
                        tokens_in: Optional[int], tokens_out: Optional[int], error: Optional[str]):
        try:
            async with get_session() as session:
                llm_call = LLMCall(
                    provider=provider,
                    task_type=task_type,
                    success=success,
                    latency_ms=latency_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    error=error
                )
                session.add(llm_call)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to log LLM call to database: {e}")
