"""Hatch build hook that bundles the WebUI into OriginAgent/web/dist."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "webui-build"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        webui_dir = root / "webui"
        package_json = webui_dir / "package.json"
        dist_dir = root / "OriginAgent" / "web" / "dist"
        index_html = dist_dir / "index.html"

        if self.target_name == "wheel" and version == "editable":
            self.app.display_info(
                "[webui-build] skipped for editable install; run `cd webui && bun run build` manually"
            )
            return
        if os.environ.get("ORIGINAGENT_SKIP_WEBUI_BUILD") == "1":
            self.app.display_info("[webui-build] skipped via ORIGINAGENT_SKIP_WEBUI_BUILD=1")
            return
        if not package_json.exists():
            self.app.display_info("[webui-build] no webui source tree, assuming prebuilt OriginAgent/web/dist")
            return
        if index_html.exists() and os.environ.get("ORIGINAGENT_FORCE_WEBUI_BUILD") != "1":
            self.app.display_info(f"[webui-build] reusing existing build at {dist_dir}")
            return

        runner = "bun" if shutil.which("bun") else "npm" if shutil.which("npm") else None
        if runner is None:
            raise RuntimeError(
                "[webui-build] neither `bun` nor `npm` is available; set ORIGINAGENT_SKIP_WEBUI_BUILD=1 to skip"
            )

        self.app.display_info(f"[webui-build] using {runner} to build webui")
        self._run([runner, "install"], cwd=webui_dir)
        self._run([runner, "run", "build"], cwd=webui_dir)
        if not index_html.exists():
            raise RuntimeError(f"[webui-build] build finished but {index_html} is missing")
        self.app.display_info(f"[webui-build] webui ready at {dist_dir}")

    def _run(self, cmd: list[str], *, cwd: Path) -> None:
        self.app.display_info(f"[webui-build] $ {' '.join(cmd)} (cwd={cwd})")
        try:
            subprocess.run(cmd, cwd=str(cwd), check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"[webui-build] command failed ({exc.returncode}): {' '.join(cmd)}"
            ) from exc
