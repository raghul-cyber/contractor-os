import pytest
import pytest_asyncio
import asyncio
from typing import Optional
from sqlalchemy import text

from app.core.llm_router import LLMRouter, RouterConfig, BaseProvider, ProviderResponse
from app.core.exceptions import RateLimitError, AllProvidersFailedError
from app.core.db import engine

class MockProvider(BaseProvider):
    def __init__(self, name: str, behavior: str, error_type: Optional[str] = None):
        self.name = name
        self.behavior = behavior
        self.error_type = error_type
        self.call_count = 0
        self.health_check_count = 0
        
    async def health_check(self) -> bool:
        self.health_check_count += 1
        return self.behavior != "unhealthy"
        
    async def call(self, prompt: str, **kwargs) -> ProviderResponse:
        self.call_count += 1
        if self.behavior == "fail":
            if self.error_type == "rate":
                raise RateLimitError(f"{self.name} rate limit")
            elif self.error_type == "time":
                raise TimeoutError(f"{self.name} timeout")
            elif self.error_type == "conn":
                raise ConnectionError(f"{self.name} connection error")
            else:
                raise Exception("Unknown error")
        return ProviderResponse(text=f"{self.name} response", tokens_in=10, tokens_out=20)

@pytest_asyncio.fixture(autouse=True)
async def clear_db():
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM llm_calls"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM llm_calls"))

async def get_db_calls():
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT provider, success, task_type FROM llm_calls ORDER BY id ASC"))
        return [dict(row._mapping) for row in res.fetchall()]

@pytest.mark.asyncio
async def test_case_a_full_fallback():
    # 3 fail, 1 succeeds
    cfg = RouterConfig(
        providers_override={
            "groq": MockProvider("groq", "fail", "rate"),
            "ollama": MockProvider("ollama", "fail", "time"),
            "gemini": MockProvider("gemini", "fail", "conn"),
            "nvidia": MockProvider("nvidia", "success")
        }
    )
    router = LLMRouter(cfg)
    res = await router.call("test prompt", task_type="general")
    
    assert res == "nvidia response"
    calls = await get_db_calls()
    
    assert len(calls) == 4
    assert calls[0]["provider"] == "groq"
    assert calls[0]["success"] == 0
    assert calls[1]["provider"] == "ollama"
    assert calls[1]["success"] == 0
    assert calls[2]["provider"] == "gemini"
    assert calls[2]["success"] == 0
    assert calls[3]["provider"] == "nvidia"
    assert calls[3]["success"] == 1

@pytest.mark.asyncio
async def test_case_b_all_fail():
    cfg = RouterConfig(
        providers_override={
            "groq": MockProvider("groq", "fail", "rate"),
            "ollama": MockProvider("ollama", "fail", "time"),
            "gemini": MockProvider("gemini", "fail", "conn"),
            "nvidia": MockProvider("nvidia", "fail", "time")
        }
    )
    router = LLMRouter(cfg)
    
    with pytest.raises(AllProvidersFailedError):
        await router.call("test prompt", task_type="general")
        
    calls = await get_db_calls()
    assert len(calls) == 4
    assert all(c["success"] == 0 for c in calls)

@pytest.mark.asyncio
async def test_case_c_task_routing():
    m_ollama = MockProvider("ollama", "success")
    m_groq = MockProvider("groq", "success")
    cfg = RouterConfig(
        providers_override={
            "groq": m_groq,
            "ollama": m_ollama,
            "gemini": MockProvider("gemini", "success"),
            "nvidia": MockProvider("nvidia", "success")
        }
    )
    router = LLMRouter(cfg)
    
    res = await router.call("test", task_type="classification")
    assert res == "ollama response"
    
    assert m_ollama.call_count == 1
    assert m_groq.call_count == 0
    
    calls = await get_db_calls()
    assert len(calls) == 1
    assert calls[0]["provider"] == "ollama"

@pytest.mark.asyncio
async def test_case_d_unhealthy_skipped():
    m_groq = MockProvider("groq", "unhealthy")
    m_ollama = MockProvider("ollama", "success")
    cfg = RouterConfig(
        providers_override={
            "groq": m_groq,
            "ollama": m_ollama,
            "gemini": MockProvider("gemini", "success"),
            "nvidia": MockProvider("nvidia", "success")
        }
    )
    router = LLMRouter(cfg)
    
    res = await router.call("test", task_type="general")
    assert res == "ollama response"
    
    assert m_groq.health_check_count == 1
    assert m_groq.call_count == 0
    assert m_ollama.call_count == 1
    
    calls = await get_db_calls()
    assert len(calls) == 2
    assert calls[0]["provider"] == "groq"
    assert calls[0]["success"] == 0
    assert calls[1]["provider"] == "ollama"
    assert calls[1]["success"] == 1
