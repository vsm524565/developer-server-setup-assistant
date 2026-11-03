
"""Conservative read-only application-state classification."""

APPLICATIONS = frozenset({
    "git",
    "nginx",
    "docker",
    "docker_compose",
})

STATES = frozenset({
    "INSTALLED_HEALTHY",
    "MISSING",
    "UNMANAGED",
    "UNHEALTHY",
    "UNKNOWN",
})


def classify_application_state(
    data,
    *,
    expected_application_id,
):
    """Classify accepted application observations without enabling execution."""

    if expected_application_id not in APPLICATIONS:
        raise ValueError("Unsupported application ID.")

    if not isinstance(data, dict):
        raise ValueError("Application observations must be an object.")

    if (
        data.get("schema_version") != "1.0"
        or data.get("application_id") != expected_application_id
        or data.get("read_only") is not True
        or data.get("execution_allowed") is not False
    ):
        raise ValueError("Invalid application observation identity or policy.")

    package = data.get("package")
    binary = data.get("binary")
    service = data.get("service")
    plugin = data.get("plugin")
    expected_packages = {
        "git": "git",
        "nginx": "nginx",
        "docker": "docker-ce",
        "docker_compose": "docker-compose-plugin",
    }
    if not all(isinstance(value, dict) for value in (
        package, binary, service, plugin
    )):
        raise ValueError("Incomplete application observation structure.")

    if package.get("name") != expected_packages[expected_application_id]:
        raise ValueError("Application package identity mismatch.")

    if data.get("package_manager") not in ("apt", "dnf"):
        raise ValueError("Invalid application package manager.")

    binary_present = binary.get("present")
    binary_path = binary.get("path")

    if (
        type(binary_present) is not bool
        or (
            binary_present
            and (
                not isinstance(binary_path, str)
                or not binary_path.startswith("/")
            )
        )
        or (not binary_present and binary_path is not None)
    ):
        raise ValueError("Inconsistent application binary observations.")
    if not all(isinstance(value, dict) for value in (
        package, binary, service, plugin
    )):
        raise ValueError("Incomplete application observation structure.")

    package_state = package.get("state")
    binary_present = binary.get("present")
    service_state = service.get("state")
    plugin_state = plugin.get("state")

    if (
        package_state not in ("installed", "absent", "unknown")
        or type(binary_present) is not bool
        or service_state not in (
            "active", "inactive", "failed",
            "activating", "deactivating",
            "unknown", "not_applicable",
        )
        or plugin_state not in (
            "available", "unknown", "not_applicable",
        )
    ):
        raise ValueError("Invalid application observation values.")

    application = expected_application_id
    state = "UNKNOWN"
    reasons = []

    if application in ("docker", "docker_compose"):
        # Current package discovery checks vendor package identities only.
        # No package ownership or daemon/plugin verification is available.
        reasons.append("ALTERNATIVE_INSTALLATION_METHODS_NOT_EXCLUDED")

        if package_state == "installed" and not binary_present:
            state = "UNHEALTHY"
            reasons.append("MANAGED_PACKAGE_BINARY_MISSING")
        elif package_state == "installed":
            reasons.append("RUNTIME_VERIFICATION_INCOMPLETE")
        elif binary_present or plugin_state == "available":
            reasons.append("INSTALLATION_OWNERSHIP_UNVERIFIED")
        else:
            reasons.append("INSTALLATION_ABSENCE_NOT_PROVEN")

    elif package_state == "unknown":
        reasons.append("PACKAGE_STATE_UNKNOWN")

    elif package_state == "absent":
        if binary_present:
            state = "UNMANAGED"
            reasons.append("BINARY_WITHOUT_EXPECTED_PACKAGE")
        else:
            state = "MISSING"
            reasons.append("PACKAGE_AND_BINARY_ABSENT")

    elif package_state == "installed":
        if not binary_present:
            state = "UNHEALTHY"
            reasons.append("MANAGED_PACKAGE_BINARY_MISSING")

        elif application == "git":
            state = "INSTALLED_HEALTHY"
            reasons.append("MANAGED_PACKAGE_AND_BINARY_PRESENT")

        elif application == "nginx":
            if service_state == "active":
                state = "INSTALLED_HEALTHY"
                reasons.append("MANAGED_PACKAGE_BINARY_AND_SERVICE_ACTIVE")
            elif service_state in ("inactive", "failed"):
                state = "UNHEALTHY"
                reasons.append("MANAGED_SERVICE_NOT_ACTIVE")
            else:
                reasons.append("SERVICE_HEALTH_UNCONFIRMED")

    return {
        "application_id": application,
        "state": state,
        "reasons": reasons,
        "observations": {
            "package_state": package_state,
            "binary_present": binary_present,
            "service_state": service_state,
            "plugin_state": plugin_state,
        },
        "planning_only": True,
        "execution_allowed": False,
    }


def classify_application_evidence(
    planning_result,
    *,
    expected_application_id,
    expected_target_id,
):
    """Require accepted, matching evidence before interpreting observations."""

    if not isinstance(planning_result, dict):
        raise ValueError("Invalid planning evidence.")

    gate = planning_result.get("gate")
    evidence = planning_result.get("evidence")

    if not isinstance(gate, dict) or gate.get("status") != "ACCEPTED":
        raise ValueError("Application evidence gate did not accept evidence.")

    if not isinstance(evidence, dict):
        raise ValueError("Application evidence is unavailable.")

    if (
        evidence.get("target_id") != expected_target_id
        or evidence.get("operation") != "discovery.application_state"
        or evidence.get("parameters") != {
            "application_id": expected_application_id,
        }
        or planning_result.get("execution_allowed") is not False
    ):
        raise ValueError("Application evidence does not match the request.")

    result = classify_application_state(
        evidence.get("data"),
        expected_application_id=expected_application_id,
    )

    result["target_id"] = expected_target_id
    result["evidence_request_id"] = evidence.get("request_id")

    return result
