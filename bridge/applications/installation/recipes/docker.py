"""Draft Docker Engine installation recipe."""

from bridge.applications.installation.contracts import (
    ExpectedOperation,
    InstallationRecipe,
    PackageSource,
    PlatformRequirement,
    VerificationStep,
)


def build_docker_recipe():
    """Describe vendor-repository Docker Engine installation."""

    return InstallationRecipe(
        recipe_id="docker-ubuntu24-amd64-vendor",
        application_id="docker",
        platform=PlatformRequirement(
            os_ids=("ubuntu",),
            os_versions=("24.04",),
            architectures=("x86_64",),
            package_manager="apt",
        ),
        package_source=PackageSource(
            source_type="vendor_repository",
            repository_id="docker-official-ubuntu",
            packages=(
                "docker-ce",
                "docker-ce-cli",
                "containerd.io",
                "docker-buildx-plugin",
            ),
        ),
        expected_operations=(
            ExpectedOperation(
                action="repository_configuration",
                target="docker-official-ubuntu",
                description="Configure the approved Docker repository",
            ),
            ExpectedOperation(
                action="package_install",
                target="docker-ce",
                description="Install Docker Engine and dependencies",
            ),
            ExpectedOperation(
                action="service_start",
                target="docker",
                description="Start the Docker daemon",
            ),
        ),
        verification_steps=(
            VerificationStep(
                check_type="package",
                target="docker-ce",
                description="Verify Docker Engine package installation",
            ),
            VerificationStep(
                check_type="service",
                target="docker",
                description="Verify Docker daemon readiness",
            ),
            VerificationStep(
                check_type="version",
                target="docker",
                description="Verify Docker CLI version",
            ),
        ),
    )
