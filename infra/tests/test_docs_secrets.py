"""No committed credentials and no implicit static-key defaults (FR-19a, AC-8, NFR-1)."""

import re
import subprocess

# TC-16.6: an `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` assignment with a key-like value.
KEY_VALUE_RE = re.compile(r"AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)\s*=\s*[A-Za-z0-9/+=]{16,}")


def _env_example_lines(repo_root):
    return (repo_root / ".env.example").read_text(encoding="utf-8").splitlines()


def test_env_example_static_keys_are_commented(repo_root):
    """TC-8.12: a copied `.env` does not define the static keys, not even as empty strings."""
    lines = _env_example_lines(repo_root)

    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert f"# {key}=" in lines, f"expected a commented `# {key}=` line"
        assert not any(re.match(rf"^\s*(export\s+)?{key}\s*=", line) for line in lines), (
            f"{key} must not be assigned in .env.example"
        )


def test_env_example_prefers_sso_and_documents_domain(repo_root):
    """FR-19a / FR-8: SSO/profile is the preferred credential source; `DOMAIN` is documented."""
    text = "\n".join(_env_example_lines(repo_root))

    assert "aws sso login" in text or "AWS_PROFILE" in text
    assert re.search(r"^#?\s*DOMAIN=\s*$", text, re.M), "expected an empty `DOMAIN=` entry"


def test_no_committed_long_lived_keys(repo_root):
    """TC-16.6: no tracked file assigns a key-like value to the static AWS key variables."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo_root, capture_output=True, check=True
    ).stdout.split(b"\0")

    hits = []
    for name in filter(None, tracked):
        path = repo_root / name.decode()
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if KEY_VALUE_RE.search(line):
                hits.append(f"{name.decode()}:{number}: {line.strip()}")

    assert hits == []


def test_no_lecturer_domain_in_makefile_or_env_example(repo_root):
    """AC-8 / FR-8: the lecturer's domain is not a default anywhere users copy from."""
    for name in ("Makefile", ".env.example"):
        assert "onetwothree.dobosevych.com" not in (repo_root / name).read_text(encoding="utf-8"), name
