
"""
Bind a proposed installation plan to a specific server context.

Read-only. No SSH commands or installation operations.
"""

import hashlib
import json
from copy import deepcopy
import hmac

class ApprovalBindingError(ValueError):
    """Approval context is incomplete or invalid."""


class ApprovalBinding:
    """Create and validate deterministic approval fingerprints."""

    @staticmethod
    def create(target, host_key_sha256, snapshot, plan):
        if not isinstance(target, dict):
            raise ApprovalBindingError("Invalid SSH target.")

        required_target_fields = ("hostname", "port", "username")

        for field in required_target_fields:
            value = target.get(field)

            if field == "port":
                if type(value) is not int or not 1 <= value <= 65535:
                    raise ApprovalBindingError("Invalid SSH port.")
            elif not isinstance(value, str) or not value.strip():
                raise ApprovalBindingError(
                    f"Missing SSH target field: {field}"
                )

        if (
            not isinstance(host_key_sha256, str)
            or not host_key_sha256.startswith("SHA256:")
            or len(host_key_sha256) <= len("SHA256:")
        ):
            raise ApprovalBindingError(
                "Missing or invalid SSH host-key fingerprint."
            )

        if not isinstance(snapshot, dict) or not snapshot:
            raise ApprovalBindingError("Invalid server snapshot.")

        if not isinstance(plan, list) or not plan:
            raise ApprovalBindingError("Invalid installation plan.")

        required_step_fields = (
            "component",
            "action",
            "status",
            "dependencies",
            "reasons",
        )

        for index, step in enumerate(plan, start=1):
            if not isinstance(step, dict):
                raise ApprovalBindingError(
                    f"Invalid installation step: {index}"
                )

            for field in required_step_fields:
                if field not in step:
                    raise ApprovalBindingError(
                        f"Missing {field} in step {index}"
                    )

        context = {
            "schema_version": 1,
            "target": {
                "hostname": target["hostname"].strip(),
                "port": target["port"],
                "username": target["username"].strip(),
            },
            "host_key_sha256": host_key_sha256,
            "snapshot": deepcopy(snapshot),
            "plan": deepcopy(plan),
        }

        try:
            serialized = json.dumps(
                context,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise ApprovalBindingError(
                "Approval context cannot be serialized."
            ) from error

        return hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def matches(expected_fingerprint, target,
                host_key_sha256, snapshot, plan):
        current_fingerprint = ApprovalBinding.create(
            target, host_key_sha256, snapshot, plan
        )

        return (
            isinstance(expected_fingerprint, str)
            and len(expected_fingerprint) == 64
            and hmac.compare_digest(
                expected_fingerprint,
                current_fingerprint,
            )
        )
