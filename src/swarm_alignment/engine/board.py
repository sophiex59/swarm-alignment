"""Shared message-board representation and mention parsing."""

import re
from dataclasses import dataclass


@dataclass
class Post:
    round: int
    phase: int
    author: str
    text: str
    mentions: list[str]

    def render(self) -> str:
        return f"[r{self.round}.p{self.phase}] {self.author}: {self.text}"


def parse_mentions(text: str, agents: tuple[str, ...] | list[str]) -> list[str]:
    names = {name.casefold(): name for name in agents}
    pattern = re.compile(
        r"(?<![\w])@(" + "|".join(re.escape(name) for name in agents) + r")(?![\w])",
        re.IGNORECASE,
    )
    return [names[match.group(1).casefold()] for match in pattern.finditer(text)]


def mentioned_names(text: str, agents: tuple[str, ...] | list[str]) -> list[str]:
    pattern = re.compile(
        r"(?<![\w])(" + "|".join(re.escape(name) for name in agents) + r")(?![\w])",
        re.IGNORECASE,
    )
    canonical = {name.casefold(): name for name in agents}
    return [canonical[match.group(1).casefold()] for match in pattern.finditer(text)]
