
"""Validate and render read-only Docker Compose readiness reports."""

import json

from bridge.applications.readiness.compose_explanation import (
    COMPOSE_RUNTIME_FINDINGS,
)
from bridge.applications.readiness.runtime_reconciliation import (
    READINESS_STATUSES,
    RUNTIME_STATUSES,
)


REPORT_FIELDS = frozenset({
    "schema_version",
    "check",
    "target_id",
    "application_id",
    "status",
    "runtime_status",
    "summary",
    "findings",
    "source_reasons",
    "evidence_request_ids",
    "daemon_responsive",
    "planning_only",
    "execution_allowed",
})

FINDING_FIELDS = frozenset({
    "category",
    "code",
    "title",
    "description",
    "investigation",
    "execution_allowed",
})


def validate_compose_explanation(report):
    """Reject malformed, contradictory or execution-enabled reports."""

    if (
        not isinstance(report, dict)
        or set(report) != REPORT_FIELDS
        or report.get("schema_version") != "1.0"
        or report.get("check") != "docker_compose_readiness_explanation"
        or report.get("application_id") != "docker_compose"
        or not isinstance(report.get("target_id"), str)
        or not report["target_id"].strip()
        or report.get("status") not in READINESS_STATUSES
        or report.get("runtime_status")
            not in RUNTIME_STATUSES["docker_compose"]
        or report.get("planning_only") is not True
        or report.get("execution_allowed") is not False
        or report.get("daemon_responsive") is not None
    ):
        raise ValueError("Invalid Compose explanation contract.")

    expected_summary = (
        f"Docker Compose readiness is {report['status']}. "
        "Docker Engine and Compose execution remain unverified."
    )

    if report.get("summary") != expected_summary:
        raise ValueError("Compose readiness summary is inconsistent.")

    identities = report["evidence_request_ids"]
    reasons = report["source_reasons"]
    findings = report["findings"]

    if (
        not isinstance(identities, dict)
        or len(identities) != 8
        or "discovery.application_runtime" not in identities
        or "discovery.docker_daemon_preflight" in identities
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in identities.items()
        )
        or len(set(identities.values())) != 8
        or not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason
               for reason in reasons)
        or not isinstance(findings, list)
    ):
        raise ValueError("Invalid Compose evidence or findings.")

    expected_categories = ["runtime", "health"]

    if report["status"] == "BLOCKED":
        expected_categories.append("readiness")

    if len(findings) != len(expected_categories):
        raise ValueError("Invalid Compose finding count.")

    for index, finding in enumerate(findings):
        if (
            not isinstance(finding, dict)
            or set(finding) != FINDING_FIELDS
            or any(
                not isinstance(finding[field], str)
                or not finding[field].strip()
                for field in (
                    "category",
                    "code",
                    "title",
                    "description",
                    "investigation",
                )
            )
            or finding["execution_allowed"] is not False
            or finding["category"] != expected_categories[index]
        ):
            raise ValueError("Invalid Compose finding.")

    runtime = report["runtime_status"]
    expected = COMPOSE_RUNTIME_FINDINGS[runtime]

    if (
        findings[0]["code"] != expected[0]
        or findings[0]["title"] != expected[1]
        or findings[0]["description"] != expected[2]
        or findings[0]["investigation"] != expected[3]
        or expected[4] not in reasons
    ):
        raise ValueError("Compose runtime finding contradicts evidence.")

    if (
        findings[1]["code"] != "COMPOSE_EXECUTION_UNVERIFIED"
        or findings[1]["title"] != "Compose execution unverified"
        or findings[1]["description"] != (
            "Neither Docker Engine responsiveness nor Compose "
            "project execution has been verified."
        )
        or findings[1]["investigation"] != (
            "Use a separately approved verification workflow "
            "before deploying workloads."
        )
    ):
        raise ValueError("Invalid Compose execution finding.")

    if report["status"] == "BLOCKED":
        if findings[2]["code"] != "EXISTING_READINESS_BLOCKER":
            raise ValueError("Invalid Compose blocker finding.")

    return None


def render_compose_report(report, output_format="text"):
    """Render a completely validated report without changing its data."""

    if output_format not in ("text", "json"):
        raise ValueError("Unsupported Compose report format.")

    validate_compose_explanation(report)

    if output_format == "json":
        return json.dumps(report, indent=2, sort_keys=True) + "\n"

    lines = [
        "=== DSSA DOCKER COMPOSE READINESS REPORT ===",
        f"Target       : {report['target_id']}",
        f"Application  : {report['application_id']}",
        f"Readiness    : {report['status']}",
        f"Runtime      : {report['runtime_status']}",
        "Engine health: NOT VERIFIED",
        "Compose run  : NOT VERIFIED",
        "",
        "=== FINDINGS ===",
    ]

    for index, finding in enumerate(report["findings"], start=1):
        lines.extend([
            f"[{index}] {finding['title']}",
            f"    Code: {finding['code']}",
            f"    {finding['description']}",
            f"    Investigation: {finding['investigation']}",
            "",
        ])

    lines.extend([
        "=== SOURCE REASONS ===",
        *(f"- {reason}" for reason in report["source_reasons"]),
        "",
        "=== EVIDENCE ===",
        f"Discovery records: {len(report['evidence_request_ids'])}",
        "Distinct identities: 8",
        "",
        "=== SAFETY ===",
        "Planning only: YES",
        "Execution: DISABLED",
        "Docker Engine responsiveness: UNKNOWN",
        "Compose project execution: UNKNOWN",
    ])

    return "\n".join(lines) + "\n"
