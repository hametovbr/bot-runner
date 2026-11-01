"""Extra packages (installed offline from a hand-made wheel) and shared modules."""

import base64
import hashlib
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot_runner.executor import Executor
from bot_runner.packages import PackageManager, normalize, requirements_hash, validate
from bot_runner.server import create_app

from .conftest import SECRET, make_job


def make_wheel(folder: Path, name: str = "tinypkg", version: str = "1.0") -> Path:
    """A minimal pure-Python wheel, so tests need no network."""
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / f"{name}-{version}-py3-none-any.whl"
    dist_info = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": (
            f'VERSION = "{version}"\n\ndef shout(text):\n    return text.upper() + "!"\n'
        ),
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = []
    for path, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content.encode()).digest()).rstrip(b"=")
        record.append(f"{path},sha256={digest.decode()},{len(content.encode())}")
    record.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = "\n".join(record) + "\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return wheel


def test_normalize_and_hash():
    text = "# tools\nrequests==2.32.3  # http\n\n  pillow\n"
    assert normalize(text) == "requests==2.32.3\npillow"
    assert requirements_hash(text) == requirements_hash("requests==2.32.3\npillow")
    assert requirements_hash("") == requirements_hash("# nothing\n")
    # The orchestrator computes the same hash (see its test_requirements_hash_matches_runner).
    assert requirements_hash("pillow\nopenpyxl") == "217427db06ee0d6c"
    assert validate("-e git+https://x") is not None
    assert validate("--index-url https://evil") is not None
    assert validate("pillow>=10") is None


async def test_install_switch_and_use_in_handler(tmp_path, settings):
    packages = PackageManager(settings.packages_dir)
    wheel = make_wheel(tmp_path / "wheels")
    packages.start(str(wheel))
    await packages.wait()
    status = packages.status()
    assert status["error"] is None, status["log"]
    assert status["hash"] == requirements_hash(str(wheel))
    assert status["installed"] == [{"name": "tinypkg", "version": "1.0"}]
    assert status["requirements"].strip() == str(wheel)

    executor = Executor(settings, packages)
    code = """
import tinypkg

def handle(update, ctx):
    ctx.log(tinypkg.shout("hi"), tinypkg.VERSION)
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"] == ["HI! 1.0"]

    # The same list again only switches.
    packages.start(str(wheel))
    await packages.wait()
    assert "already installed" in " ".join(packages.status()["log"])


async def test_failed_install_keeps_previous_environment(tmp_path, settings):
    packages = PackageManager(settings.packages_dir)
    wheel = make_wheel(tmp_path / "wheels")
    packages.start(str(wheel))
    await packages.wait()
    good = packages.current_hash()

    packages.start(str(tmp_path / "missing-1.0-py3-none-any.whl"))
    await packages.wait()
    status = packages.status()
    assert status["error"], "the broken list must report an error"
    assert status["hash"] == good, "running handlers keep the previous packages"
    assert [p["name"] for p in status["installed"]] == ["tinypkg"]
    envs = list((Path(settings.packages_dir) / "envs").iterdir())
    assert [e.name for e in envs] == [good], "the failed attempt is cleaned up"


async def test_empty_list_removes_packages(tmp_path, settings):
    packages = PackageManager(settings.packages_dir)
    packages.start(str(make_wheel(tmp_path / "wheels")))
    await packages.wait()
    packages.start("# nothing\n")
    await packages.wait()
    assert packages.current_env() is None
    assert packages.status()["installed"] == []
    assert packages.current_hash() == requirements_hash("")


async def test_runner_dependencies_cannot_be_replaced(tmp_path, settings):
    packages = PackageManager(settings.packages_dir)
    # httpx is pinned to the runner's version; asking for another one must fail.
    packages.start("httpx==0.0.1")
    await packages.wait()
    assert packages.status()["error"]
    assert packages.current_env() is None


def test_packages_endpoints(settings):
    client = TestClient(create_app(settings))
    headers = {"X-Runner-Secret": SECRET}
    assert client.get("/packages").status_code == 401
    assert client.get("/packages", headers=headers).json()["hash"] == requirements_hash("")
    bad = client.post("/packages", json={"requirements": "-e ."}, headers=headers)
    assert bad.status_code == 400


async def test_shared_modules(executor):
    modules = {
        "texts": {"version": 2, "code": 'GREETING = "Привет"\n'},
        "greet": {
            "version": 5,
            "code": (
                "from shared import texts\n\n"
                "def hello(name):\n    return f'{texts.GREETING}, {name}'\n"
            ),
        },
    }
    code = """
from shared import greet
import shared.texts

def handle(update, ctx):
    ctx.log(greet.hello("Ann"), shared.texts.GREETING)
"""
    result = await executor.run(make_job(code, modules=modules))
    assert result["status"] == "ok", result
    assert result["logs"] == ["Привет, Ann Привет"]


async def test_tracebacks_show_module_and_handler_lines(executor):
    modules = {
        "broken": {"version": 3, "code": "def explode():\n    raise ValueError('boom in module')\n"}
    }
    code = """
from shared import broken

def handle(update, ctx):
    broken.explode()
"""
    result = await executor.run(make_job(code, modules=modules))
    assert result["status"] == "error"
    error = result["error"]
    assert '<shared.broken v3>", line 2' in error
    assert "raise ValueError('boom in module')" in error, error
    assert "broken.explode()" in error, "the handler's own line is shown too"


@pytest.mark.parametrize("statement", ["from shared import missing", "import shared.missing"])
async def test_unknown_shared_module(executor, statement):
    result = await executor.run(make_job(f"{statement}\ndef handle(u, c): pass\n"))
    assert result["status"] == "error"
    assert "ModuleNotFoundError" in result["error"] or "ImportError" in result["error"]
