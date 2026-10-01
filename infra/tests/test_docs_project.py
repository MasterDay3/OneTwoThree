"""PROJECT.md must match the repository it describes (TC-3.1 to TC-3.5, AC-1, NFR-7).

Every checker is a pure function: it takes the PROJECT.md text plus facts read from the repo and
returns a list of problems (empty = the document agrees with the source). The negative tests feed
the same checkers an in-memory mutated copy of PROJECT.md to prove they would catch a real defect.

Self-contained on purpose: only the standard library and PyYAML, no conftest fixtures.
"""

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PROJECT_MD = REPO / "PROJECT.md"

# FR-1: top-level folders plus the subfolders README.md's Layout section calls out.
REQUIRED_FOLDERS = (
    "back",
    "front",
    "infra",
    ".github",
    "docs",
    "back/app",
    "back/app/routers",
    "back/app/services",
    "back/alembic",
    "back/tests",
    "front/src/pages",
    "front/src/components",
    "front/src/components/ui",
    "front/src/hooks",
    "front/src/lib",
)
# FR-4: key libraries whose constraints must be copied verbatim.
REQUIRED_BACK_LIBS = ("fastapi", "sqlalchemy", "alembic", "mangum", "pyjwt", "psycopg")
REQUIRED_FRONT_LIBS = ("react", "vite", "typescript", "tailwindcss", "@tanstack/react-query")
API_SCHEMAS = ("MeetingCreate", "MeetingRead", "ParticipantRead")
MEETINGS_GET = "GET /api/meetings"
MEETINGS_POST = "POST /api/meetings"

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


# --------------------------------------------------------------------------------------------
# Markdown helpers
# --------------------------------------------------------------------------------------------


def section(text: str, title: str) -> str:
    """Body of the first heading containing `title`, up to the next heading of the same or higher level."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and title in match.group(2):
            level = len(match.group(1))
            body = []
            for following in lines[i + 1 :]:
                nxt = _HEADING.match(following)
                if nxt and len(nxt.group(1)) <= level:
                    break
                body.append(following)
            return "\n".join(body)
    return ""


def table_rows(text: str) -> list[list[str]]:
    """Cells of every Markdown table row; `\\|` inside a cell is an escaped pipe."""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|") or _TABLE_SEPARATOR.match(line):
            continue
        cells = _UNESCAPED_PIPE.split(line[1:-1] if line.endswith("|") else line[1:])
        rows.append([cell.strip().replace("\\|", "|") for cell in cells])
    return rows


def code(cell: str) -> str | None:
    """Content of the first `code span` in a table cell."""
    match = re.search(r"`([^`]+)`", cell)
    return match.group(1) if match else None


def replace_row(text: str, first_cell: str, old: str, new: str) -> str:
    """Mutate one table row (identified by its first code span) in memory; fails loudly if absent."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        cells = table_rows(line)
        if cells and code(cells[0][0]) == first_cell and old in line:
            lines[i] = line.replace(old, new, 1)
            return "\n".join(lines)
    raise AssertionError(f"no table row for {first_cell!r} containing {old!r}")


# --------------------------------------------------------------------------------------------
# Facts read from the repository
# --------------------------------------------------------------------------------------------


def tracked_dirs() -> set[str]:
    """Every directory holding a git-tracked (or staged) file; never node_modules, .venv or dist."""
    out = subprocess.run(
        ["git", "ls-files", "--cached"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout
    dirs: set[str] = set()
    for path in out.splitlines():
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:depth]))
    return dirs


def load_compose() -> dict:
    return yaml.safe_load((REPO / "compose.yaml").read_text())


def schema_fields() -> dict[str, dict[str, str]]:
    """{class: {field: annotation as written}} for the API models in back/app/schemas.py."""
    tree = ast.parse((REPO / "back/app/schemas.py").read_text())
    fields: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in API_SCHEMAS:
            fields[node.name] = {
                stmt.target.id: ast.unparse(stmt.annotation)
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    return fields


def source_status_codes() -> dict[str, set[int]]:
    """Status codes GET/POST /api/meetings can return, derived from the backend source."""
    router = (REPO / "back/app/routers/meetings.py").read_text()
    auth = (REPO / "back/app/auth.py").read_text()
    main = (REPO / "back/app/main.py").read_text()
    service = (REPO / "back/app/services/meetings.py").read_text()

    def success(method: str) -> int:
        decorator = re.search(rf'@router\.{method}\(""([^)]*)\)', router)
        assert decorator, f'no @router.{method}("") in meetings.py'
        explicit = re.search(r"status_code=status\.HTTP_(\d{3})", decorator.group(1))
        return int(explicit.group(1)) if explicit else 200  # FastAPI's default

    codes = {MEETINGS_GET: {success("get")}, MEETINGS_POST: {success("post")}}
    if "HTTP_401_UNAUTHORIZED" in auth and router.count("Depends(get_current_user)") >= 2:
        codes[MEETINGS_GET].add(401)
        codes[MEETINGS_POST].add(401)
    # A body model on POST: FastAPI answers a failed validation with 422.
    if re.search(r"def create_meeting\(\s*data: MeetingCreate", router):
        codes[MEETINGS_POST].add(422)
    # Unknown participant_ids raise NotFoundError, which main.py maps to 404.
    handler = r"NotFoundError\) -> JSONResponse:\s*return JSONResponse\(status_code=(\d{3})"
    not_found = re.search(handler, main)
    if not_found and "raise NotFoundError" in service.split("def _load_participants")[1].split("def ")[0]:
        codes[MEETINGS_POST].add(int(not_found.group(1)))
    return codes


def pyproject_constraints() -> dict[str, str]:
    """{package name without extras: constraint string as written} + requires-python."""
    data = tomllib.loads((REPO / "back/pyproject.toml").read_text())
    specs = {"requires-python": data["project"]["requires-python"]}
    deps = list(data["project"]["dependencies"])
    for group in data.get("dependency-groups", {}).values():
        deps.extend(group)
    for dep in deps:
        match = re.match(r"([A-Za-z0-9_.-]+)(\[[^\]]*\])?(.*)$", dep)
        assert match, dep
        specs[match.group(1)] = match.group(3)
    return specs


def package_json_constraints() -> dict[str, str]:
    data = json.loads((REPO / "front/package.json").read_text())
    return {**data.get("dependencies", {}), **data.get("devDependencies", {})}


# --------------------------------------------------------------------------------------------
# Checkers (pure: text + repo facts in, problems out)
# --------------------------------------------------------------------------------------------


def check_folders(text: str, dirs: set[str]) -> list[str]:
    sec = section(text, "Folders")
    if not sec:
        return ["no '## Folders' section"]
    documented = {}
    for cells in table_rows(sec):
        path = code(cells[0])
        if path and path.endswith("/"):
            documented[path.rstrip("/")] = cells[1] if len(cells) > 1 else ""
    problems = [f"required folder {f}/ is not documented" for f in REQUIRED_FOLDERS if f not in documented]
    problems += [f"{f}/ has no stated purpose" for f, purpose in documented.items() if not purpose.strip()]
    problems += [f"{f}/ is documented but not a tracked folder" for f in documented if f not in dirs]
    # "Every folder has a stated purpose": no tracked folder may go undocumented.
    problems += [f"tracked folder {d}/ is not documented" for d in sorted(dirs - set(documented))]
    return problems


def check_compose(text: str, compose: dict) -> list[str]:
    sec = section(text, "Compose services")
    if not sec:
        return ["no '## Compose services' section"]
    problems = []
    for name, svc in compose["services"].items():
        sub = section(sec, f"`{name}`")
        if not sub:
            problems.append(f"service {name} has no subsection")
            continue
        expected = []
        if "image" in svc:
            expected.append(svc["image"])
        if "build" in svc:
            build = svc["build"]
            expected.append(build if isinstance(build, str) else build["context"])
        expected += svc.get("ports", [])
        for dep, cfg in (svc.get("depends_on") or {}).items():
            expected += [dep, cfg["condition"]]
        if "command" in svc:
            expected.append(svc["command"])
        healthcheck = svc.get("healthcheck")
        if healthcheck:
            probe = " ".join(healthcheck["test"])
            tokens = re.findall(r"pg_isready|urllib\.request|/api/[\w/]+", probe)
            if not tokens:
                problems.append(f"{name}: healthcheck {probe!r} not understood by this checker")
            expected += tokens
        elif "no healthcheck" not in sub.lower():
            problems.append(f"{name}: has no healthcheck but the doc does not say so")
        problems += [f"{name}: {value!r} not stated" for value in expected if value not in sub]
    order = section(sec, "Startup order")
    if not order:
        problems.append("no 'Startup order' subsection")
    elif "service_healthy" not in order:
        problems.append("startup order does not name the service_healthy condition")
    return problems


def check_api_fields(text: str, fields: dict[str, dict[str, str]]) -> list[str]:
    problems = []
    for cls, expected in fields.items():
        sub = section(text, f"`{cls}`")
        if not sub:
            problems.append(f"no subsection for {cls}")
            continue
        documented = {code(c[0]): code(c[1]) for c in table_rows(sub) if len(c) > 1 and code(c[0])}
        for field, annotation in expected.items():
            if field not in documented:
                problems.append(f"{cls}.{field} is not documented")
            elif documented[field] != annotation:
                problems.append(f"{cls}.{field}: doc says {documented[field]!r}, schemas.py {annotation!r}")
        extra = [f for f in documented if f not in expected]
        problems += [f"{cls}.{f} is documented but not in schemas.py" for f in extra]
    return problems


def check_status_codes(text: str, expected: dict[str, set[int]]) -> list[str]:
    sec = section(text, "Status codes")
    if not sec:
        return ["no 'Status codes' section"]
    documented: dict[str, set[int]] = {}
    for cells in table_rows(sec):
        endpoint, status = code(cells[0]), code(cells[1]) if len(cells) > 1 else None
        if endpoint and status and status.isdigit():
            documented.setdefault(endpoint, set()).add(int(status))
    problems = []
    for endpoint, codes in expected.items():
        doc = documented.get(endpoint, set())
        problems += [f"{endpoint}: {c} returned by the code but not documented" for c in sorted(codes - doc)]
        problems += [f"{endpoint}: {c} documented but never returned" for c in sorted(doc - codes)]
    return problems


def check_versions(text: str, py: dict[str, str], js: dict[str, str]) -> list[str]:
    sec = section(text, "Pinned versions")
    if not sec:
        return ["no 'Pinned versions' section"]
    problems = []
    listed = {"back/pyproject.toml": set(), "front/package.json": set()}
    for cells in table_rows(sec):
        if len(cells) < 3 or not (code(cells[0]) and code(cells[1]) and code(cells[2])):
            continue
        item, value, source = code(cells[0]), code(cells[1]), code(cells[2])
        if source == "back/pyproject.toml":
            name = re.sub(r"\[[^\]]*\]$", "", item)
            listed[source].add(name)
            if py.get(name) != value:
                problems.append(f"{item}: doc says {value!r}, pyproject.toml says {py.get(name)!r}")
        elif source == "front/package.json":
            listed[source].add(item)
            if js.get(item) != value:
                problems.append(f"{item}: doc says {value!r}, package.json says {js.get(item)!r}")
        elif not (REPO / source).is_file():
            problems.append(f"{item}: source {source} does not exist")
        elif not re.search(rf"(?<![\w.]){re.escape(value)}(?![\w.])", (REPO / source).read_text()):
            problems.append(f"{item}: {value!r} does not appear in {source}")
    required = {"back/pyproject.toml": (*REQUIRED_BACK_LIBS, "requires-python")}
    required["front/package.json"] = REQUIRED_FRONT_LIBS
    for source, names in required.items():
        problems += [f"{n} ({source}) is not listed" for n in names if n not in listed[source]]
    return problems


def check_tradeoff(text: str) -> list[str]:
    sec = section(text, "Monorepo decision")
    if not sec:
        return ["no 'Monorepo decision' section"]
    lower = sec.lower()
    problems = [
        f"monorepo section does not mention {phrase!r}"
        for phrase in ("atomic", "context window", "trade-off", "independen", "split")
        if phrase not in lower
    ]
    if len(sec.split()) < 150:
        problems.append("monorepo section is too short to be a written argument")
    return problems


# --------------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------------


def project_md() -> str:
    assert PROJECT_MD.is_file(), "PROJECT.md is missing at the repository root"
    return PROJECT_MD.read_text()


def test_tc_3_1a_folders_match_tracked_tree():
    assert check_folders(project_md(), tracked_dirs()) == []


def test_tc_3_1b_compose_services_match_compose_yaml():
    assert check_compose(project_md(), load_compose()) == []


def test_tc_3_1c_api_fields_match_schemas():
    assert check_api_fields(project_md(), schema_fields()) == []


def test_tc_3_1c_status_codes_match_source():
    expected = source_status_codes()
    assert expected[MEETINGS_POST] >= {201, 401, 422}
    assert check_status_codes(project_md(), expected) == []


def test_tc_3_1d_versions_match_source_files():
    assert check_versions(project_md(), pyproject_constraints(), package_json_constraints()) == []


def test_tc_3_1e_monorepo_tradeoff_is_written_down():
    assert check_tradeoff(project_md()) == []


def test_tc_3_2_starts_at_type_and_format_match_schemas():
    text = project_md()
    assert schema_fields()["MeetingCreate"]["starts_at"] == "AwareDatetime"
    rows = {code(c[0]): c for c in table_rows(section(text, "`MeetingCreate`")) if code(c[0])}
    row = " ".join(rows["starts_at"])
    assert code(rows["starts_at"][1]) == "AwareDatetime"
    assert "ISO 8601" in row and "timezone" in row


def test_tc_3_3_wrong_post_status_code_is_caught():
    mutated = replace_row(project_md(), MEETINGS_POST, "`201`", "`200`")
    problems = check_status_codes(mutated, source_status_codes())
    assert any("201" in p for p in problems), problems
    assert any("200" in p for p in problems), problems


def test_tc_3_4_invented_fastapi_version_is_caught():
    text = project_md()
    mutated = replace_row(text, "fastapi", "`>=0.115`", "`==0.999.0`")
    assert mutated != text
    problems = check_versions(mutated, pyproject_constraints(), package_json_constraints())
    assert any("fastapi" in p and "0.999.0" in p for p in problems), problems


def test_tc_3_5_constraints_are_copied_byte_for_byte():
    sec = section(project_md(), "Pinned versions")
    py, js = pyproject_constraints(), package_json_constraints()
    copied = {}
    for cells in table_rows(sec):
        if len(cells) >= 3 and code(cells[2]) in ("back/pyproject.toml", "front/package.json"):
            source = py if code(cells[2]) == "back/pyproject.toml" else js
            name = re.sub(r"\[[^\]]*\]$", "", code(cells[0]))
            copied[name] = (code(cells[1]), source[name])
    for name in (*REQUIRED_BACK_LIBS, *REQUIRED_FRONT_LIBS):
        doc, src = copied[name]
        assert doc.encode() == src.encode(), f"{name}: {doc!r} != {src!r}"
    # Ranges stay ranges: none was collapsed to a single invented pin.
    ranges = [doc for doc, _ in copied.values() if doc[:1] in (">", "^", "~")]
    assert ranges and all(not re.fullmatch(r"\d+(\.\d+)*", doc) for doc, _ in copied.values())


def test_checkers_reject_other_mutations():
    text = project_md()
    assert check_folders(text.replace("| `back/app/routers/`", "| `back/app/routerz/`"), tracked_dirs())
    compose = load_compose()
    compose["services"]["db"]["image"] = "postgres:17-alpine"
    assert check_compose(text, compose)
    assert check_api_fields(replace_row(text, "starts_at", "`AwareDatetime`", "`datetime`"), schema_fields())
    assert check_tradeoff(text.replace("## Monorepo decision", "## Something else"))
