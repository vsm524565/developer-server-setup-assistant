"""Package-manager-specific draft Git recipes."""

from bridge.applications.installation.contracts import (
    ExpectedOperation,
    InstallationRecipe,
    PackageSource,
    PlatformRequirement,
    VerificationStep,
)


def _build_git_recipe(recipe_id, os_ids, manager):
    """Create a draft Git recipe using the OS package repository."""

    return InstallationRecipe(
        recipe_id=recipe_id,
        application_id="git",
        platform=PlatformRequirement(
            os_ids=os_ids,
            os_versions=("*",),
            architectures=("x86_64",),
            package_manager=manager,
        ),
        package_source=PackageSource(
            source_type="os_repository",
            packages=("git",),
        ),
        expected_operations=(
            ExpectedOperation(
                action="package_install",
                target="git",
                description=f"Install Git using {manager}",
            ),
        ),
        verification_steps=(
            VerificationStep(
                check_type="package",
                target="git",
                description="Verify Git package ownership",
            ),
            VerificationStep(
                check_type="binary",
                target="git",
                description="Verify Git executable",
            ),
        ),
    )


def build_git_apt_recipe():
    """Declare Git installation for Ubuntu and Debian with APT."""

    return _build_git_recipe(
        "git-apt-os-repository",
        ("ubuntu", "debian"),
        "apt",
    )


def build_git_dnf_recipe():
    """Declare Git installation for AlmaLinux and Rocky with DNF."""

    return _build_git_recipe(
        "git-dnf-os-repository",
        ("almalinux", "rocky"),
        "dnf",
    )
