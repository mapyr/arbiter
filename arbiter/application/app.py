"""Application facade — composition of command/query handlers for callers/tests."""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import Any

from arbiter.application.handlers.commands import CommandHandlers
from arbiter.application.handlers.queries import QueryHandlers
from arbiter.application.ports import (
    Clock,
    EvidenceStore,
    EventStore,
    IdGenerator,
    ResponseStore,
    RulesSource,
    VoterGateway,
    VotersConfigSource,
)
from arbiter.application.services.model_quorum import ModelQuorumService
from arbiter.application.voters_config import VotersConfig
from arbiter.domain.errors import DomainError
from arbiter.domain.model import Decision


class Application:
    """CQRS application root used by inbound adapters and tests."""

    def __init__(
        self,
        *,
        events: EventStore,
        evidence: EvidenceStore,
        responses: ResponseStore,
        rules: RulesSource,
        voters_config: VotersConfigSource,
        clock: Clock,
        ids: IdGenerator,
        voter_gateway: VoterGateway | None = None,
        # Convenience for tests that inspect on-disk layout:
        root: Path | None = None,
    ) -> None:
        self._events = events
        self._evidence = evidence
        self._responses = responses
        self._rules = rules
        self._voters_config = voters_config
        self._voter_gateway = voter_gateway
        self.root = Path(root) if root is not None else None
        self.commands = CommandHandlers(
            events=events,
            evidence=evidence,
            rules=rules,
            voters_config=voters_config,
            clock=clock,
            ids=ids,
        )
        self.queries = QueryHandlers(events=events, clock=clock)
        self._clock = clock

    @property
    def ledger_path(self) -> Path:
        if self.root is None:
            raise DomainError("application root not configured")
        return self.root / "ledger.jsonl"

    @property
    def bundles_dir(self) -> Path:
        if self.root is None:
            raise DomainError("application root not configured")
        return self.root / "bundles"

    def open_decision(self, **kwargs: Any) -> dict[str, Any]:
        return self.commands.open_decision(**kwargs)

    def cast_vote(self, **kwargs: Any) -> dict[str, Any]:
        return self.commands.cast_vote(**kwargs)

    def resolve_decision(self, decision_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.commands.resolve_decision(decision_id, **kwargs)

    def get_decision(self, decision_id: str, **kwargs: Any) -> dict[str, Any]:
        return self.queries.get_decision(decision_id, **kwargs)

    def expand_decision_scope(self, decision_id: str, patterns: list[str]) -> None:
        self.commands.expand_decision_scope(decision_id, patterns)

    def record_vote_failed(self, **kwargs: Any) -> dict[str, Any]:
        return self.commands.record_vote_failed(**kwargs)

    def record_round2_labels(self, **kwargs: Any) -> None:
        self.commands.record_round2_labels(**kwargs)

    def load_voters_config(self) -> VotersConfig | None:
        return self._voters_config.load()

    def load_rules(self) -> dict[str, Any] | None:
        return self._rules.load()

    def get_gate_policy(self) -> dict[str, Any]:
        from arbiter.application.services.plan_gate import PlanGateService

        return PlanGateService(self).get_gate_policy()

    async def ensure_plan(self, plan: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        from arbiter.application.services.plan_gate import PlanGateService

        return await PlanGateService(self).ensure_plan(plan, **kwargs)

    def now(self) -> datetime:
        return self._clock.now()

    def read_all_wire(self) -> list[dict[str, Any]]:
        return self._events.read_all_wire()

    def replay(self, decision_id: str) -> Decision | None:
        return self._events.load_decision(decision_id)

    def check_coverage(self, **kwargs: Any) -> dict[str, Any]:
        from arbiter.application.services.coverage import CoverageService

        return CoverageService(self, rules=self._rules.load()).check(**kwargs)

    def eval_report(
        self, *, repo: Path | None = None, horizon_days: int = 14
    ) -> dict[str, Any]:
        from arbiter.application.services.eval_report import EvalReportBuilder

        return EvalReportBuilder(
            self, repo=repo or self.root, horizon_days=horizon_days
        ).build()

    def verify_commit_paths(
        self,
        *,
        paths: list[str],
        decision_id: str | None,
        commit_at: datetime | None = None,
        allow_break_glass: bool = False,
    ) -> dict[str, Any]:
        from arbiter.application.services.commit_guard import verify_commit

        return verify_commit(
            self,
            paths=paths,
            decision_id=decision_id,
            commit_at=commit_at,
            allow_break_glass=allow_break_glass,
            rules=self._rules.load(),
        )

    def load_bundle(self, digest: str) -> dict[str, Any]:
        return self._evidence.load(digest)

    def store_response(self, **kwargs: Any) -> str:
        return self._responses.store(**kwargs)

    async def run_model_quorum(
        self,
        decision_id: str,
        *,
        config: VotersConfig | None = None,
        rng: random.Random | None = None,
        probes: Any | None = None,
        changed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        cfg = config if config is not None else self._voters_config.load()
        if cfg is None:
            raise DomainError(
                "arbiter.voters.yaml required for run_model_quorum "
                "(set ARBITER_VOTERS_PATH or place the file in cwd)"
            )
        if self._voter_gateway is None:
            raise DomainError("voter gateway not configured")
        service = ModelQuorumService(
            commands=self.commands,
            events=self._events,
            evidence=self._evidence,
            responses=self._responses,
            voters=self._voter_gateway,
            clock=self._clock,
            config=cfg,
            rng=rng or random.Random(),
            probes=probes,
            changed_paths=list(changed_paths or []),
        )
        return await service.run(decision_id)
