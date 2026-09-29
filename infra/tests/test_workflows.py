"""Slice 8: GitHub Actions workflows (deploy.yml + reusable code-style.yml).

TC-9.1a–f, 9.2, 9.3, 9.9a, 9.10a, 9.11, 10.2, 10.6a, 10.7, 16.7.
"""

import re

import pytest
import yaml


def _load(repo_root, name):
    data = yaml.safe_load((repo_root / ".github" / "workflows" / name).read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 parses the key `on` as True
    return data


@pytest.fixture
def deploy(repo_root):
    return _load(repo_root, "deploy.yml")


@pytest.fixture
def style(repo_root):
    return _load(repo_root, "code-style.yml")


def _steps(wf):
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


def test_deploy_triggers_only_on_push_to_main(deploy):
    """TC-9.1a, 9.11, 10.2, 10.7: no PRs, no manual/scheduled runs, no path filters."""
    assert deploy["on"] == {"push": {"branches": ["main"]}}


def test_code_style_is_reusable_and_no_longer_runs_on_push(style):
    """TC-9.1b / FR-21."""
    assert set(style["on"]) == {"pull_request", "workflow_call"}


def test_deploy_needs_lint_and_tests(deploy):
    """TC-9.1c."""
    jobs = deploy["jobs"]
    assert jobs["lint"]["uses"] == "./.github/workflows/code-style.yml"
    assert set(jobs["deploy"]["needs"]) == {"lint", "back-test", "front-test"}
    back = jobs["back-test"]
    assert back["services"]["postgres"]["image"] == "postgres:16-alpine"
    assert any("TEST_DATABASE_URL" in str(s) and "pytest" in str(s) for s in back["steps"])
    assert any("npm test" in str(s.get("run", "")) for s in jobs["front-test"]["steps"])


def test_permissions_least_privilege(deploy):
    """TC-9.1d: id-token only on the deploy job."""
    assert deploy["permissions"] == {"contents": "read"}
    for name, job in deploy["jobs"].items():
        perms = job.get("permissions", {})
        if name == "deploy":
            assert perms == {"contents": "read", "id-token": "write"}
        else:
            assert "id-token" not in perms


def test_no_environment_and_no_static_keys(repo_root, deploy):
    """TC-10.6a (environment changes the OIDC sub) and TC-16.7."""
    assert all("environment" not in job for job in deploy["jobs"].values())
    for name in ("deploy.yml", "code-style.yml"):
        text = (repo_root / ".github" / "workflows" / name).read_text()
        assert not re.search(r"secrets\.AWS_", text)
        assert "aws-access-key-id" not in text


def test_actions_are_pinned_to_commit_shas(deploy, style):
    """TC-9.2: every third-party action is pinned by a full SHA; checkout keeps no token."""
    for wf in (deploy, style):
        for step in _steps(wf):
            uses = step.get("uses")
            if uses and not uses.startswith("./"):
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uses), uses
            if uses and uses.startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_deploy_job_runs_on_arm_with_the_oidc_role(deploy):
    """TC-9.3 + FR-20."""
    job = deploy["jobs"]["deploy"]
    assert job["runs-on"] == "ubuntu-24.04-arm"
    creds = next(s for s in job["steps"] if "configure-aws-credentials" in s.get("uses", ""))
    assert creds["with"]["role-to-assume"] == "${{ vars.AWS_DEPLOY_ROLE_ARN }}"
    assert creds["with"]["aws-region"] == "us-east-1"
    assert "ARCH=amd64" not in str(job)


def test_deploy_step_order(deploy):
    """TC-9.9a / FR-24: credentials → .env-absent guard → deploy-backend (SHA tag) → deploy-frontend."""
    runs = [str(s.get("uses", "")) + str(s.get("run", "")) for s in deploy["jobs"]["deploy"]["steps"]]

    def at(text):
        return next(i for i, r in enumerate(runs) if text in r)

    assert at("configure-aws-credentials") < at("test ! -f .env") < at("make deploy-backend")
    assert at("make deploy-backend") < at("make deploy-frontend")
    backend = next(s for s in deploy["jobs"]["deploy"]["steps"] if "make deploy-backend" in str(s.get("run")))
    assert backend["env"]["TAG"] == "${{ github.sha }}"


def test_concurrency(deploy, style):
    """TC-9.10a: workflow-level, never cancels a running deploy; code-style cancels only PR runs."""
    assert deploy["concurrency"] == {"group": "deploy-${{ github.ref }}", "cancel-in-progress": False}
    assert all("concurrency" not in job for job in deploy["jobs"].values())
    assert "github.event_name" in style["concurrency"]["group"]
    assert style["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_infra_tests_run_in_ci(style):
    job = style["jobs"]["infra-tests"]
    assert any("pytest infra/tests" in str(s.get("run", "")) for s in job["steps"])
