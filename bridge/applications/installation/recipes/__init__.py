"""Explicit registry of draft application installation recipes."""

from .git import build_git_apt_recipe, build_git_dnf_recipe
from .nginx import build_nginx_apt_recipe, build_nginx_dnf_recipe
from .docker import build_docker_recipe
from .docker_compose import build_docker_compose_recipe


RECIPE_BUILDERS = (
    build_git_apt_recipe,
    build_git_dnf_recipe,
    build_nginx_apt_recipe,
    build_nginx_dnf_recipe,
    build_docker_recipe,
    build_docker_compose_recipe,
)
