"""Reusable local workflow runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection

from atlas_os.core.artifacts import Artifact, create_artifact
from atlas_os.core.audit_log import create_audit_log
from atlas_os.core.workflow_runs import WorkflowRun, complete_workflow_run, create_workflow_run
from atlas_os.core.workflow_steps import (
    StepStatus,
    WorkflowStepRecord,
    create_workflow_step,
    update_workflow_step,
)


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    handler: Callable[["WorkflowContext"], None]


@dataclass
class WorkflowContext:
    connection: Connection
    run: WorkflowRun
    output_dir: Path
    output_paths: dict[str, Path] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    approval_id: int | None = None
    blocked_for_approval: bool = False

    def record_artifact(self, artifact_type: str, path: Path, output_key: str | None = None) -> Artifact:
        artifact = create_artifact(self.connection, self.run.run_id, artifact_type, path)
        self.artifacts.append(artifact)
        if output_key:
            self.output_paths[output_key] = path
        create_audit_log(
            self.connection,
            actor="workflow_runner",
            action="artifact_created",
            detail=f"{artifact_type}: {path}",
            run_id=self.run.run_id,
            artifact_id=artifact.id,
        )
        return artifact


@dataclass(frozen=True)
class WorkflowExecution:
    run: WorkflowRun
    steps: tuple[WorkflowStepRecord, ...]
    artifacts: tuple[Artifact, ...]
    approval_id: int | None


class WorkflowRunner:
    def __init__(
        self,
        connection: Connection,
        division: str,
        workflow_name: str,
        steps: tuple[WorkflowStep, ...],
        output_dir: Path,
        mock_data_used: bool = True,
    ) -> None:
        self.connection = connection
        self.division = division
        self.workflow_name = workflow_name
        self.steps = steps
        self.output_dir = output_dir
        self.mock_data_used = mock_data_used

    def run(self) -> WorkflowExecution:
        workflow_run = create_workflow_run(
            self.connection,
            division=self.division,
            workflow_name=self.workflow_name,
            mock_data_used=self.mock_data_used,
        )
        create_audit_log(
            self.connection,
            actor="workflow_runner",
            action="workflow_run_created",
            detail=self.workflow_name,
            run_id=workflow_run.run_id,
        )
        context = WorkflowContext(
            connection=self.connection,
            run=workflow_run,
            output_dir=self.output_dir,
        )
        step_records = [create_workflow_step(self.connection, workflow_run.run_id, step.name) for step in self.steps]

        for step, step_record in zip(self.steps, step_records, strict=True):
            update_workflow_step(self.connection, step_record.id, StepStatus.RUNNING)
            create_audit_log(
                self.connection,
                actor="workflow_runner",
                action="step_started",
                detail=step.name,
                run_id=workflow_run.run_id,
            )
            try:
                step.handler(context)
            except Exception as exc:
                failed_step = update_workflow_step(
                    self.connection,
                    step_record.id,
                    StepStatus.FAILED,
                    error=str(exc),
                )
                create_audit_log(
                    self.connection,
                    actor="workflow_runner",
                    action="step_failed",
                    detail=f"{step.name}: {exc}",
                    run_id=workflow_run.run_id,
                )
                completed_run = complete_workflow_run(
                    self.connection,
                    workflow_run.run_id,
                    output_paths=context.output_paths,
                    status="failed",
                )
                step_records[step_records.index(step_record)] = failed_step
                return WorkflowExecution(
                    run=completed_run,
                    steps=tuple(step_records),
                    artifacts=tuple(context.artifacts),
                    approval_id=context.approval_id,
                )

            status = StepStatus.BLOCKED_FOR_APPROVAL if context.blocked_for_approval else StepStatus.COMPLETED
            completed_step = update_workflow_step(self.connection, step_record.id, status)
            step_records[step_records.index(step_record)] = completed_step
            create_audit_log(
                self.connection,
                actor="workflow_runner",
                action="step_completed",
                detail=f"{step.name}: {status.value}",
                run_id=workflow_run.run_id,
            )

        final_status = "awaiting_approval" if context.blocked_for_approval else "completed"
        completed_run = complete_workflow_run(
            self.connection,
            workflow_run.run_id,
            output_paths=context.output_paths,
            status=final_status,
        )
        return WorkflowExecution(
            run=completed_run,
            steps=tuple(step_records),
            artifacts=tuple(context.artifacts),
            approval_id=context.approval_id,
        )

