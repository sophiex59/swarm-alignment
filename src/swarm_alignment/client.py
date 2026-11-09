"""OpenAI-compatible chat client for the vLLM server (or any OpenAI-style endpoint)."""

from typing import Any

from openai import AsyncOpenAI


def split_thinking(text: str | None) -> tuple[str, str]:
    """Split Qwen3 output into (reasoning, visible text).

    With thinking on, Qwen3 writes ``<think>...</think>`` before its answer. If the reply was cut
    off inside the thinking block there is no closing tag, and everything is reasoning.
    """
    text = text or ""
    if "</think>" in text:
        reasoning, visible = text.split("</think>", 1)
        return reasoning.replace("<think>", "", 1).strip(), visible.strip()
    if text.lstrip().startswith("<think>"):
        return text.replace("<think>", "", 1).strip(), ""
    return "", text


class OpenAIChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "llm",
        temperature: float = 0.7,
        max_tokens: int = 512,
        enable_thinking: bool = False,
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=5, timeout=600)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking

    async def check_authentication(self) -> None:
        """Make a small authenticated request before starting a run."""
        await self._client.models.list()

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], seed: int
    ) -> dict[str, Any]:
        """Return {"content", "reasoning", "tool_calls", "usage", "finish_reason"}.

        "reasoning" is the model's thinking, kept apart from "content" so callers can keep it
        private. It is never sent back to the model in later turns.
        """
        extra_body = {"chat_template_kwargs": {"enable_thinking": self.enable_thinking}}
        tool_kwargs: dict[str, Any] = {"tools": tools, "tool_choice": "auto"} if tools else {}
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            **tool_kwargs,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            seed=seed,
            extra_body=extra_body,
        )
        choice = resp.choices[0]
        msg = choice.message
        extra = getattr(msg, "model_extra", None) or {}
        parsed = getattr(msg, "reasoning_content", None) or extra.get("reasoning_content")
        reasoning, content = split_thinking(msg.content)
        if parsed:  # the server split it for us
            reasoning = parsed
        return {
            "content": content or None,
            "reasoning": reasoning or None,
            "tool_calls": [
                {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                for c in msg.tool_calls or []
            ],
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
            "finish_reason": choice.finish_reason,
        }
