
"""Normalize read-only application inventory observations."""

APPLICATION_PACKAGES = {
    "git": ("git",),
    "nginx": ("nginx",),
    "docker": (
        "docker-ce",
        "docker-ce-cli",
        "docker.io",
        "moby-engine",
        "moby-cli",
    ),
    "docker_compose": (
        "docker-compose-plugin",
        "docker-compose-v2",
        "docker-compose",
    ),
}

APPLICATION_BINARIES = {
    "git": "git",
    "nginx": "nginx",
    "docker": "docker",
    "docker_compose": "docker",
}

PACKAGE_METHODS = {
    "git": "distribution_package",
    "nginx": "distribution_package",
    "docker.io": "distribution_package",
    "docker-ce": "vendor_repository",
    "docker-ce-cli": "vendor_repository",
    "moby-engine": "alternative_package",
    "moby-cli": "alternative_package",
    "docker-compose-plugin": "vendor_repository",
    "docker-compose-v2": "distribution_package",
    "docker-compose": "distribution_package",
}


def _parse_owner(raw, manager, path):
    """Parse package ownership without trusting arbitrary output."""

    if not isinstance(raw, str) or not raw.strip():
        return None

    lines = raw.strip().splitlines()

    if len(lines) != 1:
        return None

    if manager == "apt":
        # Typical dpkg-query -S output: git: /usr/bin/git
        if ": " not in raw:
            return None

        package, reported_path = raw.rsplit(": ", 1)

        if reported_path.strip() != path:
            return None

        # Normalize optional Debian architecture suffix.
        package = package.split(":", 1)[0].strip()

    elif manager == "dnf":
        # RPM query returns the package name.
        package = raw.strip()

    else:
        return None

    if not package or any(char.isspace() for char in package):
        return None

    return package


def normalize_application_inventory(data, *, expected_application_id):
    """Interpret a validated inventory without authorizing execution."""

    if expected_application_id not in APPLICATION_PACKAGES:
        raise ValueError("Unsupported application ID.")

    if not isinstance(data, dict):
        raise ValueError("Inventory must be an object.")

    if (
        data.get("schema_version") != "1.0"
        or data.get("application_id") != expected_application_id
        or data.get("read_only") is not True
        or data.get("execution_allowed") is not False
    ):
        raise ValueError("Invalid inventory identity or execution policy.")

    manager = data.get("package_manager")

    if manager not in ("apt", "dnf"):
        raise ValueError("Unsupported inventory package manager.")

    expected_packages = APPLICATION_PACKAGES[expected_application_id]
    packages = data.get("packages")
    binary = data.get("binary")

    if not isinstance(packages, list) or not isinstance(binary, dict):
        raise ValueError("Invalid inventory structure.")

    if tuple(
        item.get("name") if isinstance(item, dict) else None
        for item in packages
    ) != expected_packages:
        raise ValueError("Inventory package candidates do not match.")

    installed = []
    unknown_packages = []

    for item in packages:
        state = item.get("state")
        version = item.get("version")

        if state not in ("installed", "absent", "unknown"):
            raise ValueError("Invalid package state.")

        if version is not None and (
            not isinstance(version, str) or not version
        ):
            raise ValueError("Invalid package version.")

        if state != "installed" and version is not None:
            raise ValueError("Non-installed package has a version.")

        if state == "installed":
            installed.append({
                "name": item["name"],
                "version": version,
            })
        elif state == "unknown":
            unknown_packages.append(item["name"])

    present = binary.get("present")
    path = binary.get("path")
    ownership = binary.get("ownership")

    if (
        binary.get("name") != APPLICATION_BINARIES[expected_application_id]
        or type(present) is not bool
        or not isinstance(ownership, dict)
    ):
        raise ValueError("Invalid executable inventory.")

    if present:
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("Invalid executable path.")
    elif path is not None:
        raise ValueError("Absent executable has a path.")

    owner_status = ownership.get("status")
    owner_raw = ownership.get("raw")

    if owner_status not in ("reported", "unknown"):
        raise ValueError("Invalid ownership status.")

    if owner_status == "reported":
        if not present or not isinstance(owner_raw, str) or not owner_raw:
            raise ValueError("Invalid reported ownership.")
        owner = _parse_owner(owner_raw, manager, path)
    else:
        if owner_raw is not None:
            raise ValueError("Unknown ownership must not carry raw data.")
        owner = None

    installed_names = {item["name"] for item in installed}

    status = "UNKNOWN"
    method = None
    conflicts = []
    reasons = []

    if unknown_packages:
        reasons.append("PACKAGE_INVENTORY_INCOMPLETE")

    if any(item["version"] is None for item in installed):
        reasons.append("INSTALLED_PACKAGE_VERSION_UNKNOWN")

    # Distinguish incompatible Docker package families.
    if expected_application_id == "docker":
        families = set()

        if installed_names & {"docker-ce", "docker-ce-cli"}:
            families.add("docker_ce")

        if "docker.io" in installed_names:
            families.add("docker_io")

        if installed_names & {"moby-engine", "moby-cli"}:
            families.add("moby")

        if len(families) > 1:
            conflicts.append("MULTIPLE_DOCKER_PACKAGE_FAMILIES")

    if conflicts:
        status = "REVIEW_REQUIRED"
        reasons.append("CONFLICTING_INSTALLATION_METHODS")

    elif expected_application_id == "docker_compose":
        # The docker executable belongs to the Docker CLI,
        # not necessarily the Compose plugin.
        if installed:
            status = "REVIEW_REQUIRED"
            reasons.append("COMPOSE_PLUGIN_VERIFICATION_REQUIRED")
        else:
            status = "UNKNOWN"
            reasons.append("COMPOSE_AVAILABILITY_NOT_VERIFIED")

    elif installed:
        if not present:
            status = "REVIEW_REQUIRED"
            reasons.append("INSTALLED_PACKAGE_WITHOUT_BINARY")

        elif owner is None:
            status = "REVIEW_REQUIRED"
            reasons.append("BINARY_OWNERSHIP_UNVERIFIED")

        elif owner not in installed_names:
            status = "REVIEW_REQUIRED"
            reasons.append("BINARY_OWNER_MISMATCH")

        elif unknown_packages or any(
            item["version"] is None for item in installed
        ):
            status = "REVIEW_REQUIRED"

        else:
            status = "IDENTIFIED"
            method = PACKAGE_METHODS.get(owner)
            reasons.append("PACKAGE_AND_BINARY_OWNER_MATCH")

    elif present:
        status = "REVIEW_REQUIRED"
        reasons.append("BINARY_WITHOUT_RECOGNIZED_INSTALLED_PACKAGE")

    elif unknown_packages:
        status = "UNKNOWN"

    else:
        status = "ABSENT"
        reasons.append("RECOGNIZED_PACKAGES_AND_BINARY_ABSENT")

    return {
        "application_id": expected_application_id,
        "inventory_status": status,
        "installation_method": method,
        "installed_packages": installed,
        "unknown_packages": unknown_packages,
        "binary": {
            "path": path,
            "present": present,
            "owner_package": owner,
            "ownership_verified": (
                owner is not None and owner in installed_names
            ),
        },
        "conflicts": conflicts,
        "reasons": reasons,
        "planning_only": True,
        "execution_allowed": False,
    }


def normalize_application_inventory_evidence(
    planning_result,
    *,
    expected_application_id,
    expected_target_id,
):
    """Normalize matching evidence accepted by the existing gate."""

    if not isinstance(planning_result, dict):
        raise ValueError("Invalid planning evidence.")

    gate = planning_result.get("gate")
    evidence = planning_result.get("evidence")

    if not isinstance(gate, dict) or gate.get("status") != "ACCEPTED":
        raise ValueError("Inventory evidence was not accepted.")

    if not isinstance(evidence, dict):
        raise ValueError("Inventory evidence is missing.")

    if (
        evidence.get("target_id") != expected_target_id
        or evidence.get("operation") != "discovery.application_inventory"
        or evidence.get("parameters") != {
            "application_id": expected_application_id,
        }
        or planning_result.get("execution_allowed") is not False
    ):
        raise ValueError("Inventory evidence does not match the request.")

    result = normalize_application_inventory(
        evidence.get("data"),
        expected_application_id=expected_application_id,
    )

    result["target_id"] = expected_target_id
    result["evidence_request_id"] = evidence.get("request_id")

    return result
