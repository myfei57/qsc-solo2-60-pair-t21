"""Ordered treatment line and the actions performed at each stage."""

from __future__ import annotations

from dataclasses import dataclass

from .stages import Stage

STAGE_ACTIONS: dict[Stage, str] = {
    Stage.INTAKE: "collect flow",
    Stage.COAG: "dose coagulant",
    Stage.CHLOR: "dose chlorine",
    Stage.FILTER: "filter water",
    Stage.CLEARWELL: "store clear water",
    Stage.QUOTA: "meter chemicals",
    Stage.AUDIT: "record audit",
}


@dataclass(frozen=True)
class Step:
    """A stage paired with the action the operator performs there."""

    stage: Stage
    action: str

    def as_dict(self) -> dict[str, str]:
        return {"stage": self.stage.value, "action": self.action}


@dataclass(frozen=True)
class Pipeline:
    """An ordered collection of stages."""

    name: str
    stages: tuple[Stage, ...]

    def index_of(self, stage: Stage) -> int:
        for index, candidate in enumerate(self.stages):
            if candidate is stage or candidate == stage:
                return index
        return -1

    def before(self, first: Stage, second: Stage) -> bool:
        first_index = self.index_of(first)
        second_index = self.index_of(second)
        return first_index >= 0 and second_index >= 0 and first_index < second_index

    def steps(self) -> list[Step]:
        return [Step(stage=stage, action=STAGE_ACTIONS.get(stage, "")) for stage in self.stages]

    def describe(self) -> str:
        return f"pipeline {self.name} has {len(self.stages)} stages"

    def last(self) -> Stage | None:
        if not self.stages:
            return None
        return self.stages[-1]

    def count(self) -> int:
        return len(self.stages)

    def contains(self, stage: Stage) -> bool:
        return self.index_of(stage) >= 0


def treatment_line() -> Pipeline:
    """Return the canonical ordering of the treatment stages."""

    return Pipeline(
        name="treatment",
        stages=(
            Stage.INTAKE,
            Stage.COAG,
            Stage.CHLOR,
            Stage.FILTER,
            Stage.CLEARWELL,
            Stage.QUOTA,
            Stage.AUDIT,
        ),
    )

