"""Package-manager-specific draft Nginx recipes."""

from bridge.applications.installation.contracts import (
    ExpectedOperation,
    InstallationRecipe,
    PackageSource,
    PlatformRequirement,
    VerificationStep,
)


def _build_nginx_recipe(recipe_id, os_ids, manager):
    """Create a draft OS-repository Nginx installation recipe."""

    return InstallationRecipe(
        recipe_id=recipe_id,
        application_id="nginx",
        platform=PlatformRequirement(
            os_ids=os_ids,
            os_versions=("*",),
            architectures=("x86_64",),
            package_manager=manager,
        ),
        package_source=PackageSource(
            source_type="os_repository",
            packages=("nginx",),
        ),
        expected_operations=(
            ExpectedOperation(
                action="package_install",
                target="nginx",
                description=f"Install Nginx using {manager}",
            ),
            ExpectedOperation(
                action="service_start",
                target="nginx",
                description="Start Nginx after installation",
            ),
        ),
        verification_steps=(
            VerificationStep(
                check_type="package",
                target="nginx",
                description="Verify Nginx package ownership",
            ),
            VerificationStep(
                check_type="configuration",
                target="nginx",
                description="Validate Nginx configuration",
            ),
            VerificationStep(
                check_type="service",
                target="nginx",
                description="Verify Nginx service readiness",
            ),
        ),
    )


def build_nginx_apt_recipe():
    """Declare Nginx installation on Ubuntu and Debian."""

    return _build_nginx_recipe(
        "nginx-apt-os-repository",
        ("ubuntu", "debian"),
        "apt",
    )


def build_nginx_dnf_recipe():
    """Declare Nginx installation on AlmaLinux and Rocky."""

    return _build_nginx_recipe(
        "nginx-dnf-os-repository",
        ("almalinux", "rocky"),
        "dnf",
    )
