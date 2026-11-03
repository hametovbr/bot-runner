import os
import sys

from bot_runner import handler_env
from bot_runner.executor import Executor

from .conftest import make_job


def test_build_strips_prefix_and_skips_unsafe_names():
    env = handler_env.build(
        {
            "PATH": "/usr/bin",
            "HANDLER_TIMEOUT": "60",
            "BOTENV_API_URL": "https://example.com",
            "BOTENV_lower_ok": "1",
            "BOTENV_": "empty",
            "BOTENV_PATH": "/evil",
            "BOTENV_PYTHONPATH": "/evil",
            "BOTENV_LD_PRELOAD": "/evil.so",
            "BOTENV_RUNNER_SECRET": "x",
            "BOTENV_BAD-NAME": "x",
            "BOTENV_BOTENV_X": "x",
        }
    )
    assert env.variables == {"API_URL": "https://example.com", "lower_ok": "1"}
    assert set(env.skipped) == {
        "BOTENV_",
        "BOTENV_PATH",
        "BOTENV_PYTHONPATH",
        "BOTENV_LD_PRELOAD",
        "BOTENV_RUNNER_SECRET",
        "BOTENV_BAD-NAME",
        "BOTENV_BOTENV_X",
    }
    assert env.environ == {
        "PATH": "/usr/bin",
        "HANDLER_TIMEOUT": "60",
        "API_URL": "https://example.com",
        "lower_ok": "1",
    }


def test_build_overrides_existing_variable():
    env = handler_env.build({"TZ": "UTC", "BOTENV_TZ": "Europe/Moscow"})
    assert env.environ == {"TZ": "Europe/Moscow"}


async def test_handler_and_shell_see_variables(settings):
    environ = {**os.environ, "BOTENV_GREETING": "hi there"}
    executor = Executor(settings, environ=environ)
    code = f"""
import os

async def handle(update, ctx):
    ctx.log(os.environ.get("GREETING"), "BOTENV_GREETING" in os.environ)
    result = await ctx.shell({sys.executable!r}, "-c", "import os; print(os.environ['GREETING'])")
    ctx.log(result.stdout.strip())
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"] == ["hi there False", "hi there"]


async def test_task_sees_variables(settings, fake):
    executor = Executor(settings, environ={**os.environ, "BOTENV_MODE": "task"})
    code = """
import os

async def work(ctx):
    ctx.log(os.environ["MODE"])
"""
    result = await executor.run(make_job(code, kind="task", entry="work", task={"id": 1}))
    assert result["status"] == "ok", result
    assert result["logs"] == ["task"]
