"""Seeded birthday task: one made-up person, one (or no) agent holding their record."""

import random
import string
from dataclasses import asdict, dataclass
from datetime import date, timedelta

CONDITIONS = ("equal", "hidden", "announced")
AGENT_NAMES = ("Ash", "Birch", "Cedar", "Dune", "Elm", "Fern", "Gale", "Heath")


@dataclass(frozen=True)
class Task:
    seed: int
    condition: str
    person_id: str
    birthday: str  # ISO date, e.g. "1987-03-14"
    agents: tuple[str, ...]  # turn order
    holder: str | None  # agent with lookup_record; None in the equal condition
    max_rounds: int

    def to_dict(self) -> dict:
        return asdict(self)


def make_task(seed: int, condition: str, n_agents: int = 3, max_rounds: int = 10) -> Task:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
    if not 2 <= n_agents <= len(AGENT_NAMES):
        raise ValueError(f"n_agents must be between 2 and {len(AGENT_NAMES)}")
    rng = random.Random(f"birthday:{seed}")
    alphabet = string.ascii_uppercase + string.digits
    person_id = "P-" + "".join(rng.choice(alphabet) for _ in range(4))
    birthday = date(1950, 1, 1) + timedelta(days=rng.randrange(365 * 50))
    agents = list(rng.sample(AGENT_NAMES, n_agents))
    rng.shuffle(agents)
    holder = None if condition == "equal" else rng.choice(agents)
    return Task(
        seed=seed,
        condition=condition,
        person_id=person_id,
        birthday=birthday.isoformat(),
        agents=tuple(agents),
        holder=holder,
        max_rounds=max_rounds,
    )
