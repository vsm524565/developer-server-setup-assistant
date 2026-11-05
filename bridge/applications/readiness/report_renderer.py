
"""Render validated Docker readiness explanations as text or JSON."""

import json
from bridge.applications.readiness.daemon_reconciliation import (
    PREFLIGHT_REASONS,
)

from bridge.applications.readiness.readiness_explanation import (
    DAEMON_FINDINGS,
    RUNTIME_FINDINGS,
)

REQUIRED_EXPLANATION_FIELDS = frozenset({
    "schema_version",
    "check",
    "target_id",
    "application_id",
    "status",
    "runtime_status",
    "daemon_preflight_status",
    "summary",
    "findings",
    "source_reasons",
    "evidence_request_ids",
    "daemon_responsive",
    "planning_only",
    "execution_allowed",
})

REQUIRED_FINDING_FIELDS = frozenset({
    "category",
    "code",
    "title",
    "description",
    "investigation",
    "execution_allowed",
})


def validate_explanation(explanation):
    """Enforce the structured explanation's reporting contract."""

    if (
        not isinstance(explanation, dict)
        or set(explanation) != REQUIRED_EXPLANATION_FIELDS
        or explanation.get("schema_version") != "1.0"
        or explanation.get("check") != "docker_readiness_explanation"
        or explanation.get("application_id") != "docker"
        or not isinstance(explanation.get("target_id"), str)
        or not explanation["target_id"].strip()
        or explanation.get("status") not in {
            "READY_INSTALLED",
            "INSTALL_CANDIDATE_DRAFT",
            "REVIEW_REQUIRED",
            "BLOCKED",
        }
        or explanation.get("runtime_status") not in {
            "CLI_AVAILABLE",
            "CLI_ABSENT",
            "UNVERIFIED",
        }
        or explanation.get("daemon_preflight_status") not in {
            "DAEMON_UNIT_NOT_FOUND",
            "DAEMON_UNIT_MASKED",
            "DAEMON_INACTIVE",
            "DAEMON_FAILED",
            "DAEMON_UNVERIFIED",
            "PASSIVE_CANDIDATE",
        }
        or not isinstance(explanation.get("summary"), str)
        or not explanation["summary"].strip()
        or explanation.get("daemon_responsive") is not None
        or explanation.get("planning_only") is not True
        or explanation.get("execution_allowed") is not False
    ):
        raise ValueError("Invalid Docker explanation report contract.")

    findings = explanation.get("findings")
    reasons = explanation.get("source_reasons")
    identities = explanation.get("evidence_request_ids")

    if (
        not isinstance(findings, list)
        or len(findings) not in (3, 4)
        or not isinstance(reasons, list)
        or any(
            not isinstance(reason, str) or not reason
            for reason in reasons
        )
        or not isinstance(identities, dict)
        or len(identities) != 9
        or "discovery.application_runtime" not in identities
        or "discovery.docker_daemon_preflight" not in identities
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in identities.items()
        )
        or len(set(identities.values())) != 9
    ):
        raise ValueError("Invalid Docker report evidence or findings.")

    categories = [finding.get("category") for finding in findings
                  if isinstance(finding, dict)]

    expected_categories = ["runtime", "daemon", "health"]

    if explanation["status"] == "BLOCKED":
        expected_categories.append("readiness")

    if categories != expected_categories:
        raise ValueError("Unexpected Docker report finding categories.")

    for finding in findings:
        if (
            not isinstance(finding, dict)
            or set(finding) != REQUIRED_FINDING_FIELDS
            or any(
                not isinstance(finding[field], str)
                or not finding[field].strip()
                for field in (
                    "category", "code", "title",
                    "description", "investigation",
                )
            )
            or finding["execution_allowed"] is not False
        ):
            raise ValueError("Invalid Docker report finding.")

    # Verify that report findings match the classified evidence.
    runtime = explanation["runtime_status"]
    daemon = explanation["daemon_preflight_status"]

    if findings[0]["code"] != RUNTIME_FINDINGS[runtime][0]:
        raise ValueError(
            "Runtime finding contradicts its classification."
        )

    if findings[1]["code"] != DAEMON_FINDINGS[daemon][0]:
        raise ValueError(
            "Daemon finding contradicts its classification."
        )

    if findings[2]["code"] != "DOCKER_API_HEALTH_UNVERIFIED":
        raise ValueError(
            "Docker API health finding is invalid."
        )

    if PREFLIGHT_REASONS[daemon] not in reasons:
        raise ValueError(
            "Daemon source reason is missing."
        )

    if explanation["status"] == "BLOCKED":
        if findings[3]["code"] != "EXISTING_READINESS_BLOCKER":
            raise ValueError(
                "Readiness blocker finding is invalid."
            )


def render_docker_report(explanation, output_format="text"):
    """Render a safe, deterministic report without modifying its input."""

    if output_format not in {"text", "json"}:
        raise ValueError("Unsupported Docker report format.")

    validate_explanation(explanation)

    if output_format == "json":
        return json.dumps(
            explanation,
            indent=2,
            sort_keys=True,
        ) + "\n"

    lines = [
        "=== DSSA DOCKER READINESS REPORT ===",
        f"Target       : {explanation['target_id']}",
        f"Application  : {explanation['application_id']}",
        f"Readiness    : {explanation['status']}",
        f"Runtime      : {explanation['runtime_status']}",
        f"Daemon       : {explanation['daemon_preflight_status']}",
        "API health   : NOT VERIFIED",
        "",
        "=== FINDINGS ===",
    ]

    for index, finding in enumerate(explanation["findings"], start=1):
        lines.extend([
            f"[{index}] {finding['title']}",
            f"    Code: {finding['code']}",
            f"    {finding['description']}",
            f"    Investigation: {finding['investigation']}",
            "",
        ])

    lines.extend([
        "=== SOURCE REASONS ===",
        *(
            f"- {reason}"
            for reason in explanation["source_reasons"]
        ),
        "",
        "=== EVIDENCE ===",
        f"Discovery records: {len(explanation['evidence_request_ids'])}",
        "Distinct identities: 9",
        "",
        "=== SAFETY ===",
        "Planning only: YES",
        "Execution: DISABLED",
        "Docker API responsiveness: UNKNOWN",
    ])

    return "\n".join(lines) + "\n"
