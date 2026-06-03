"""Domain pack scaffolding from Jinja2 templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "domain_packs" / "template"
_IGNORED_TEMPLATE = {"__pycache__"}


def scaffold_domain_pack(
    *,
    pack_id: str,
    pack_name: str,
    pack_description: str = "",
    target_dir: Path | None = None,
    include_tools: bool = True,
    include_runtime: bool = True,
    include_skills: bool = True,
    include_workflows: bool = True,
    include_evals: bool = False,
    include_tests: bool = True,
) -> Path:
    """Scaffold a new domain pack from the built-in Jinja2 template directory.

    Returns the path to the created pack directory.
    """
    if target_dir is None:
        target_dir = Path("domain_packs") / pack_id
    else:
        target_dir = target_dir.resolve()

    class_name = _derive_class_name(pack_name)
    tool_name = pack_name.replace(" ", "_").replace("-", "_").lower()
    tool_id_prefix = f"{pack_id}_{tool_name}"

    context: dict[str, Any] = {
        "pack_id": pack_id,
        "pack_name": pack_name,
        "pack_description": pack_description,
        "tool_id_prefix": tool_id_prefix,
        "class_name": class_name,
        "include_tools": include_tools,
        "include_runtime": include_runtime,
        "include_skills": include_skills,
        "include_workflows": include_workflows,
        "include_evals": include_evals,
        "include_tests": include_tests,
    }

    env = Environment(autoescape=select_autoescape(default=False))
    _render_template_dir(_TEMPLATE_DIR, target_dir, env, context, flags=context)

    return target_dir


def _derive_class_name(pack_name: str) -> str:
    name = pack_name.replace("-", " ").replace("_", " ")
    parts = name.split()
    title = "".join(part.capitalize() for part in parts)
    return title + "Tool" if not title.endswith("Tool") else title


def _render_template_dir(
    template_root: Path,
    output_root: Path,
    env: Environment,
    context: dict[str, Any],
    flags: dict[str, bool],
) -> None:
    """Recursively copy and render templates from template_root to output_root."""
    for item in sorted(template_root.iterdir()):
        if item.name in _IGNORED_TEMPLATE:
            continue

        if item.name == "tools" and not flags.get("include_tools", True):
            continue
        if item.name == "runtime" and not flags.get("include_runtime", True):
            continue
        if item.name == "skills" and not flags.get("include_skills", True):
            continue
        if item.name == "workflows" and not flags.get("include_workflows", True):
            continue
        if item.name == "evals" and not flags.get("include_evals", False):
            continue
        if item.name == "tests" and not flags.get("include_tests", True):
            continue

        if item.is_dir():
            output_dir = output_root / item.name
            output_dir.mkdir(parents=True, exist_ok=True)
            _render_template_dir(item, output_dir, env, context, flags)
        else:
            template_source = item.read_text(encoding="utf-8")
            try:
                template = env.from_string(template_source)
                rendered = template.render(**context)
            except Exception:
                rendered = template_source

            output_name = item.name
            if output_name.endswith(".j2"):
                output_name = output_name[:-3]
            output_path = output_root / output_name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
