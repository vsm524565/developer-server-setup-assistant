"""Draft Docker Compose plugin installation recipe."""

from bridge.applications.installation.contracts import (
    DependencyRequirement,
    ExpectedOperation,
    InstallationRecipe,
    PackageSource,
    PlatformRequirement,
    VerificationStep,
)


def build_docker_compose_recipe():
    """Describe Docker Compose plugin installation."""

    return InstallationRecipe(
        recipe_id="docker-compose-ubuntu24-amd64-vendor",
        application_id="docker_compose",
        platform=PlatformRequirement(
            os_ids=("ubuntu",),
            os_versions=("24.04",),
            architectures=("x86_64",),
            package_manager="apt",
        ),
        package_source=PackageSource(
            source_type="vendor_repository",
            repository_id="docker-official-ubuntu",
            packages=("docker-compose-plugin",),
        ),
        dependencies=(
            DependencyRequirement(
                application_id="docker",
                required=True,
            ),
        ),
        expected_operations=(
            ExpectedOperation(
                action="repository_configuration",
                target="docker-official-ubuntu",
                description="Ensure the approved Docker repository is configured",
            ),
            ExpectedOperation(
                action="package_install",
                target="docker-compose-plugin",
                description="Install the Docker Compose CLI plugin",
            ),
        ),
        verification_steps=(
            VerificationStep(
                check_type="package",
                target="docker-compose-plugin",
                description="Verify Docker Compose plugin installation",
            ),
            VerificationStep(
                check_type="version",
                target="docker compose",
                description="Verify Docker Compose CLI version",
            ),
        ),
    )
