"""Slice 9: README / lambda_handler / PROJECT.md / lab2-discussion.md describe what was built.

TC-15.1, 15.4–15.7, 16.1–16.5, 3.1(f).
"""

import ast
import re


def _read(repo_root, path):
    return (repo_root / path).read_text(encoding="utf-8")


def test_readme_layout_points_to_project_md(repo_root):
    """TC-16.1."""
    readme = _read(repo_root, "README.md")
    layout = readme.split("## Layout", 1)[1].split("\n## ", 1)[0]
    assert "PROJECT.md" in layout
    assert len([line for line in layout.splitlines() if line.strip()]) <= 5


def test_readme_has_no_lecturer_domain_or_ci_keys(repo_root):
    """TC-16.2 / TC-16.3."""
    readme = _read(repo_root, "README.md")
    assert "dobosevych" not in readme
    ci = readme.split("## CI/CD", 1)[1].split("\n## ", 1)[0]
    assert "AWS_ACCESS_KEY_ID" not in ci and "OIDC" in ci


def test_readme_diagram_shows_both_entry_points(repo_root):
    """TC-16.4."""
    mermaid = _read(repo_root, "README.md").split("```mermaid", 1)[1].split("```", 1)[0]
    assert "API Gateway" in mermaid and "function URL" in mermaid


def test_lambda_handler_docstring_names_both_entry_points(repo_root):
    """TC-16.5."""
    doc = ast.get_docstring(ast.parse(_read(repo_root, "back/app/lambda_handler.py")))
    assert "API Gateway" in doc and "function URL" in doc


def test_project_md_mentions_every_template_and_workflow(repo_root):
    """TC-3.1(f): the as-built completeness check."""
    text = _read(repo_root, "PROJECT.md")
    files = [*repo_root.glob("infra/*.yaml"), *repo_root.glob(".github/workflows/*.yml")]
    missing = [f.name for f in files if f.name not in text]
    assert not missing


TOPICS = {
    "compose keys": ["image", "ports", "expose", "volumes", "command"],
    "readiness": ["service_healthy", "pool_pre_ping"],
    "dockerfile vs compose": ["Dockerfile", "Cloud Run"],
    "base images": ["slim", "Alpine"],
    "backend structure": ["routers/", "services/"],
    "sqlalchemy": ["SQLAlchemy"],
    "alembic": ["create_all", "alembic upgrade head"],
    "cloudfront": ["CloudFront", "invalidation"],
    "ecr vs ecs": ["ECR", "ECS"],
    "health check": ["health check", "503"],
    "cname": ["CNAME", "validation"],
    "oidc": ["OIDC", "StringEquals"],
    "scale": ["1 000"],
}


def test_discussion_covers_every_topic_with_real_files(repo_root):
    """TC-15.1: every topic is answered and every cited path exists."""
    text = _read(repo_root, "docs/lab2-discussion.md")
    for topic, words in TOPICS.items():
        assert all(w in text for w in words), topic
    paths = set(re.findall(r"`((?:back|front|infra|\.github)/[\w./-]+\.\w+)`", text))
    paths |= {"compose.yaml"}
    assert len(paths) >= 10
    assert [p for p in paths if not (repo_root / p).exists()] == []


def test_discussion_is_honest_about_lambda_and_names_real_resources(repo_root):
    """TC-15.4–15.7."""
    text = _read(repo_root, "docs/lab2-discussion.md")
    assert "Lambda + Aurora Serverless v2, not ECS/ALB" in text
    assert "repo:MasterDay3/OneTwoThree:ref:refs/heads/main" in text
    assert "infra/github-oidc.yaml" in text and "infra/backend-domain.yaml" in text
    assert "aws-destroy" in text and "DESTROY_OIDC=1" in text
    assert "last 5" in text and "migrations are never rolled back" in text.replace("\n", " ")
    assert "screenshot" in text
