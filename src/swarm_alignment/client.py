"""OpenAI-compatible chat client for the vLLM server (or any OpenAI-style endpoint)."""

from typing import Any

from openai import AsyncOpenAI


class OpenAIChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "llm",
        temperature: float = 0.7,
        max_tokens: int = 512,
        disable_thinking: bool = True,
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=5, timeout=600)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.disable_thinking = disable_thinking

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], seed: int
    ) -> dict[str, Any]:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        tool_kwargs: dict[str, Any] = {"tools": tools, "tool_choice": "auto"} if tools else {}
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            **tool_kwargs,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            seed=seed,
            extra_body=extra_body if self.disable_thinking else None,
        )
        msg = resp.choices[0].message
        return {
            "content": msg.content,
            "tool_calls": [
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                for c in msg.tool_calls or []
            ],
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        }
