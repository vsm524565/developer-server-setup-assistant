
"""Ensure Docker planning always uses the dedicated safety planner."""

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLANNER = ROOT / "backend/linux/planning/components.sh"


class DockerPlannerDispatchTests(unittest.TestCase):

    def test_docker_uses_dedicated_planner(self):
        """Docker must never fall through to generic Git/Nginx planning."""

        script = f"""
set -e
source "{PLANNER}"

package_is_installed() {{
    return 1
}}

plan_docker_component() {{
    [[ "$1" == "apt" ]] || return 1

    jq -cn '{{
        component: "docker",
        package: "docker-ce",
        package_manager: "apt",
        action: "REVIEW",
        reasons: ["DOCKER_DEDICATED_PLANNER_CALLED"]
    }}'
}}

plan_component \
    apt \
    '{{"panels":[]}}' \
    '{{"listening_tcp_ports":[]}}' \
    docker
"""

        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

        response = json.loads(result.stdout)

        self.assertEqual(response["component"], "docker")
        self.assertEqual(response["action"], "REVIEW")
        self.assertEqual(
            response["reasons"],
            ["DOCKER_DEDICATED_PLANNER_CALLED"],
        )


if __name__ == "__main__":
    unittest.main()
