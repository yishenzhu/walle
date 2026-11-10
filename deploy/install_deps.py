"""安装 pyproject.toml 的运行期依赖（不装本包，避免 PROJECT_ROOT 指向 site-packages）。"""

from __future__ import annotations

import subprocess
import sys
import tomllib


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "pyproject.toml"
    with open(path, "rb") as f:
        project = tomllib.load(f)["project"]
    deps = list(project.get("dependencies", []))
    if not deps:
        print(f"no dependencies declared in {path}", file=sys.stderr)
        raise SystemExit(1)
    print(f"installing {len(deps)} runtime dependencies from {path}")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", *deps]
    )


if __name__ == "__main__":
    main()
