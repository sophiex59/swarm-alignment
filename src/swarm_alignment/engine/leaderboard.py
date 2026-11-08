"""Leaderboard messages shared by the game environments."""


def _ordinal(rank: int) -> str:
    if 10 <= rank % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def leaderboard_text(
    condition: str, round_: int, agents: tuple[str, ...], scores: dict[str, int]
) -> str | None:
    if condition == "off":
        return None
    if condition == "team":
        return f"Team score after round {round_}: {sum(scores.values())}"
    if condition != "on":
        raise ValueError(f"unknown leaderboard condition {condition!r}")

    ordered = sorted(agents, key=lambda name: (-scores[name], agents.index(name)))
    parts: list[str] = []
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and scores[ordered[end]] == scores[ordered[index]]:
            end += 1
        rank = index + 1
        rank_text = f"={_ordinal(rank)}" if end - index > 1 else _ordinal(rank)
        parts.extend(f"{name} {scores[name]} ({rank_text})" for name in ordered[index:end])
        index = end
    return f"Scores after round {round_}: " + ", ".join(parts)
