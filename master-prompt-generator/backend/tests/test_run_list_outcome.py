"""The run list has to carry each run's outcome, and has to carry every run.

Two separate regressions guard against each other here.

The list used to return domain, age and spend -- none of which answer "which
of these was any good", the only question someone scanning their history has.
Score and lift now ride along with the list.

Getting them there means joining the consensus table, and that join is the
trap: an INNER join reads correctly against a database where every run
finished, and silently drops every queued, running and failed run in
production. So the join direction is asserted, not assumed.
"""

from __future__ import annotations

import inspect
import re

from app.api.v1 import endpoints
from app.models.schemas import RunSummary


class TestTheSummaryCarriesTheOutcome:
    def test_the_three_fields_exist(self) -> None:
        for field in ("consensus_score", "improvement_over_best", "model_count"):
            assert field in RunSummary.model_fields, field

    def test_score_and_lift_are_optional(self) -> None:
        """A queued run has no consensus row; requiring them would 500 the list."""
        for field in ("consensus_score", "improvement_over_best"):
            assert RunSummary.model_fields[field].is_required() is False, field

    def test_a_run_with_no_models_yet_counts_zero_rather_than_null(self) -> None:
        summary = RunSummary(
            id="00000000-0000-0000-0000-000000000001",
            title="t",
            target_domain="d",
            status="queued",
            total_cost_usd=0.0,
            duration_ms=None,
            created_at="2026-01-01T00:00:00Z",
            completed_at=None,
        )
        assert summary.model_count == 0
        assert summary.consensus_score is None
        assert summary.improvement_over_best is None


class TestTheJoinKeepsEveryRun:
    def test_the_consensus_join_is_outer(self) -> None:
        """Inner would hide every run that has not synthesised yet."""
        source = inspect.getsource(endpoints.list_runs)
        assert "outerjoin(ConsensusPrompt" in source, (
            "the consensus join must be outer, or queued, running and failed "
            "runs disappear from the list"
        )
        assert not re.search(r"(?<!outer)\.join\(\s*ConsensusPrompt", source)

    def test_the_candidate_count_join_is_outer(self) -> None:
        source = inspect.getsource(endpoints.list_runs)
        assert "outerjoin(candidate_counts" in source

    def test_the_counts_are_aggregated_before_joining(self) -> None:
        """Joining candidates directly would multiply one run into N rows."""
        source = inspect.getsource(endpoints.list_runs)
        assert "group_by(PromptCandidate.run_id)" in source
        assert ".subquery()" in source

    def test_the_outcome_is_not_fetched_per_run(self) -> None:
        """The list is where an N+1 bites first -- it reads every run at once."""
        source = inspect.getsource(endpoints.list_runs)
        body = source.split("candidate_counts = ", 1)[1]
        assert "for " not in body.split("rows = ", 1)[0], (
            "the outcome must come from the list query, not a loop over runs"
        )
