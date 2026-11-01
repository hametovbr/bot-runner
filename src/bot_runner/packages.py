"""Extra Python packages for handlers, installed into a persistent folder with uv.

Layout under PACKAGES_DIR:

    envs/<hash>/          one folder per installed requirements list (uv pip install --target)
    envs/<hash>/.complete marker written after a successful install
    current -> envs/<hash> the environment workers use
    cache/                uv's download cache

A new list is installed into a fresh folder and ``current`` is switched only after the
install succeeds, so a failed install never affects running handlers. Installs are
constrained to the versions of the runner's own dependencies, so a package cannot
replace httpx or yt-dlp under the SDK.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

MAX_LOG_LINES = 400


def normalize(requirements: str) -> str:
    """Strip blank lines and comments; the result is what gets hashed and installed."""
    lines = []
    for raw in requirements.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return "\n".join(lines)


def requirements_hash(requirements: str) -> str:
    """The same function runs in the orchestrator, so both sides agree on the hash."""
    return hashlib.sha256(normalize(requirements).encode()).hexdigest()[:16]


def validate(requirements: str) -> str | None:
    """Return an error message, or None if the list is acceptable."""
    for line in normalize(requirements).splitlines():
        if line.startswith("-"):
            return f"options are not supported, only package specifications: {line}"
    return None


def runner_constraints() -> str:
    """name==version for every distribution the runner itself uses."""
    pins = set()
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name and dist.version:
            pins.add(f"{name}=={dist.version}")
    return "\n".join(sorted(pins, key=str.lower))


def installed_packages(env: Path) -> list[dict[str, str]]:
    if not env.is_dir():
        return []
    found = {}
    for dist in importlib.metadata.distributions(path=[str(env)]):
        name = dist.metadata.get("Name")
        if name:
            found[name.lower()] = {"name": name, "version": dist.version}
    return sorted(found.values(), key=lambda d: d["name"].lower())


class PackageManager:
    def __init__(self, root: str | Path, uv: str | None = None):
        self.root = Path(root)
        self.uv = uv or shutil.which("uv") or "uv"
        self.installing = False
        self.log: list[str] = []
        self.error: str | None = None
        self.target_hash: str | None = None
        self.finished_at: float | None = None
        self._task: asyncio.Task | None = None

    @property
    def current(self) -> Path:
        return self.root / "current"

    def current_env(self) -> Path | None:
        """The folder workers should add to sys.path, if any."""
        env = self.current
        if env.is_dir() and (env / ".complete").exists():
            return env.resolve()
        return None

    def current_hash(self) -> str:
        env = self.current_env()
        if env is None:
            return requirements_hash("")
        return env.name

    def current_requirements(self) -> str:
        env = self.current_env()
        if env is None:
            return ""
        path = env / ".requirements.txt"
        return path.read_text() if path.exists() else ""

    def status(self) -> dict[str, Any]:
        return {
            "hash": self.current_hash(),
            "requirements": self.current_requirements(),
            "installed": installed_packages(self.current_env() or Path("/nonexistent")),
            "installing": self.installing,
            "target_hash": self.target_hash,
            "error": self.error,
            "log": self.log[-MAX_LOG_LINES:],
            "finished_at": self.finished_at,
        }

    def start(self, requirements: str) -> None:
        """Begin installing in the background. Raises RuntimeError if one is running."""
        if self.installing:
            raise RuntimeError("an install is already running")
        problem = validate(requirements)
        if problem:
            raise ValueError(problem)
        self.installing = True
        self.error = None
        self.log = []
        self.target_hash = requirements_hash(requirements)
        self._task = asyncio.create_task(self._install(normalize(requirements)))

    async def wait(self) -> None:
        if self._task:
            await self._task

    def _say(self, line: str) -> None:
        self.log.append(line.rstrip())
        if len(self.log) > MAX_LOG_LINES * 2:
            self.log = self.log[-MAX_LOG_LINES:]

    def _switch(self, env: Path | None) -> None:
        """Point `current` at env (or remove it) atomically."""
        self.root.mkdir(parents=True, exist_ok=True)
        link = self.current
        if env is None:
            if link.is_symlink() or link.exists():
                link.unlink()
            return
        temp = self.root / f".current-{os.getpid()}"
        if temp.is_symlink() or temp.exists():
            temp.unlink()
        temp.symlink_to(env.relative_to(self.root))
        os.replace(temp, link)

    def _prune(self, keep: Path | None) -> None:
        envs = self.root / "envs"
        if not envs.is_dir():
            return
        for item in envs.iterdir():
            if keep is None or item.resolve() != keep.resolve():
                shutil.rmtree(item, ignore_errors=True)

    async def _install(self, requirements: str) -> None:
        digest = requirements_hash(requirements)
        envs = self.root / "envs"
        target = envs / digest
        try:
            if not requirements:
                self._say("No packages: handlers use only the runner's own libraries.")
                self._switch(None)
                self._prune(None)
                return
            if (target / ".complete").exists():
                self._say("This list is already installed; switching to it.")
                self._switch(target)
                self._prune(target)
                return
            work = envs / f".{digest}.tmp"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            requirements_file = work.parent / f".{digest}.requirements.txt"
            constraints_file = work.parent / f".{digest}.constraints.txt"
            requirements_file.write_text(requirements + "\n")
            constraints_file.write_text(runner_constraints() + "\n")
            command = [
                self.uv,
                "pip",
                "install",
                "--target",
                str(work),
                "--python",
                sys.executable,
                "--requirement",
                str(requirements_file),
                "--constraint",
                str(constraints_file),
                "--no-progress",
            ]
            self._say("$ uv pip install " + " ".join(requirements.splitlines()))
            env = {
                **os.environ,
                "UV_CACHE_DIR": str(self.root / "cache"),
                "UV_NO_CONFIG": "1",
            }
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            assert process.stdout
            async for raw in process.stdout:
                self._say(raw.decode(errors="replace"))
            code = await process.wait()
            requirements_file.unlink(missing_ok=True)
            constraints_file.unlink(missing_ok=True)
            if code != 0:
                shutil.rmtree(work, ignore_errors=True)
                raise RuntimeError(f"uv exited with code {code}; see the log")
            (work / ".requirements.txt").write_text(requirements + "\n")
            (work / ".complete").write_text(str(time.time()))
            shutil.rmtree(target, ignore_errors=True)
            work.rename(target)
            self._switch(target)
            self._prune(target)
            self._say("Installed. New handler runs use these packages.")
        except Exception as exc:  # noqa: BLE001 - reported through status()
            self.error = str(exc)
            self._say(f"Failed: {exc}")
        finally:
            self.installing = False
            self.finished_at = time.time()
