"""Background tasks, cancellation, downloads and sending files."""

import asyncio
from pathlib import Path

from .conftest import make_job


async def test_background_creates_task(executor, fake):
    code = """
from pathlib import Path

async def fetch(ctx, url, folder):
    pass

async def handle(update, ctx):
    task_id = await ctx.background(fetch, "https://x.test/v", Path("inbox"), title="Video")
    ctx.log("task", task_id)
"""
    result = await executor.run(make_job(code, origin={"update_row": 9}))
    assert result["status"] == "ok", result
    assert result["logs"] == ["task 1"]
    task = fake.tasks[1]
    assert task["bot_id"] == 123456
    assert task["entry"] == "fetch"
    assert task["args"] == ["https://x.test/v", "inbox"]
    assert task["title"] == "Video"
    assert task["handler"] == {"id": 7, "name": "main"}
    assert "async def fetch" in task["code"]
    assert task["update"]["message"]["chat"]["id"] == 42
    assert task["origin"] == {"update_row": 9}
    assert result["api_calls"][0]["method"] == "background"
    assert result["api_calls"][0]["task_id"] == 1


async def test_background_rejects_bad_input(executor):
    code = """
async def ok(ctx, value):
    pass

async def handle(update, ctx):
    async def inner(ctx):
        pass
    for func in (inner, print, handle):
        try:
            await ctx.background(func)
        except ValueError:
            ctx.log("refused")
    try:
        await ctx.background(ok, object())
    except TypeError:
        ctx.log("not json")
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"] == ["refused", "refused", "refused", "not json"]


async def test_background_dry_run(executor, fake):
    code = """
async def job(ctx):
    pass

async def handle(update, ctx):
    ctx.log(await ctx.background(job))
"""
    result = await executor.run(make_job(code, dry_run=True))
    assert result["logs"] == ["None"]
    assert fake.tasks == {}
    assert result["api_calls"][0]["dry_run"] is True


async def test_task_kind_runs_entry_with_args(executor, fake):
    code = """
async def handle(update, ctx):
    raise AssertionError("not called")

async def fetch(ctx, url, *, quality="best"):
    await ctx.progress("half way", force=True)
    ctx.log(ctx.task.id, ctx.task.title, url, quality)
    await ctx.reply("done")
"""
    job = make_job(
        code,
        kind="task",
        entry="fetch",
        args=["u1"],
        kwargs={"quality": "low"},
        task={"id": 5, "title": "Fetch"},
    )
    result = await executor.run(job)
    assert result["status"] == "ok", result
    assert result["logs"] == ["5 Fetch u1 low"]
    assert fake.progress == [(5, "half way")]
    assert fake.calls[-1] == ("sendMessage", {"chat_id": 42, "text": "done"})


async def test_task_missing_entry(executor):
    job = make_job("def handle(u, c): pass", kind="task", entry="nope", task={"id": 1})
    result = await executor.run(job)
    assert result["status"] == "error"
    assert "`nope` is not defined" in result["error"]


async def test_task_timeout_is_separate(executor, settings):
    code = """
import time

def handle(update, ctx):
    time.sleep(2)

def slow(ctx):
    time.sleep(2)
"""
    short = executor.__class__(settings.model_copy(update={"handler_timeout": 1}))
    handler = await short.run(make_job(code, timeout=None))
    task = await short.run(make_job(code, kind="task", entry="slow", task={"id": 1}))
    assert handler["status"] == "timeout"
    assert task["status"] == "ok", task


async def test_cancel_running_task(executor):
    code = """
import time

def slow(ctx):
    print("started")
    time.sleep(30)
"""
    job = make_job(code, kind="task", entry="slow", task={"id": 1}, run_id="r1")
    running = asyncio.create_task(executor.run(job))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if executor.cancel("r1"):
            break
    result = await running
    assert result["status"] == "cancelled"
    assert executor.cancel("r1") is False


async def test_tasks_get_and_cancel_from_handler(executor, fake):
    fake.tasks[1] = {"bot_id": 123456, "title": "Old", "status": "queued"}
    code = """
async def handle(update, ctx):
    task = await ctx.tasks.get(1)
    ctx.log(task.title, task.status)
    ctx.log(await ctx.tasks.cancel(1), await ctx.tasks.cancel(1))
    ctx.log(await ctx.tasks.get(99))
"""
    result = await executor.run(make_job(code))
    assert result["logs"] == ["Old queued", "True False", "None"], result


async def test_stderr_flood_does_not_block(executor):
    code = """
import sys

def handle(update, ctx):
    for _ in range(2000):
        sys.stderr.write("x" * 1000 + "\\n")
    ctx.log("finished")
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"] == ["finished"]


async def test_download_direct_file(executor, fake, settings):
    code = f"""
async def handle(update, ctx):
    result = await ctx.download("{fake.url}/media/sample.mp4")
    ctx.log(result.path.relative_to(ctx.files.root), result.size, result.message_id, sep="|")
    await ctx.reply_file(result.path, caption=result.title)
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    rel, size, message_id = result["logs"][-1].split("|")
    assert rel.startswith("downloads/") and rel.endswith(".mp4")
    assert int(size) == len(fake.media)
    assert not any("warning" in line for line in result["logs"]), result["logs"]
    assert message_id == "1"
    saved = Path(settings.files_root) / "123456" / rel
    assert saved.read_bytes() == fake.media
    methods = [m for m, _ in fake.calls]
    assert methods[0] == "sendMessage"
    assert "editMessageText" in methods
    assert fake.calls[-2][1]["text"].startswith("✅ Downloaded: sample")
    method, body = fake.calls[-1]
    assert method == "sendVideo"
    assert body["video"] == saved.as_uri()
    assert body["supports_streaming"] is True


async def test_download_failure_is_readable(executor, fake):
    code = f"""
async def handle(update, ctx):
    from bot_runner.media import DownloadFailed
    try:
        await ctx.download("{fake.url}/missing.mp4", messages={{"failed": "Ошибка: {{error}}"}})
    except DownloadFailed as exc:
        ctx.log("failed:", "404" in str(exc))
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"][-1] == "failed: True"
    assert fake.calls[-1][1]["text"].startswith("Ошибка: ")


async def test_download_size_limit(executor, fake):
    code = f"""
async def handle(update, ctx):
    from bot_runner.media import DownloadFailed
    try:
        await ctx.download("{fake.url}/media/sample.mp4", max_size_mb=0, status=False)
    except DownloadFailed as exc:
        ctx.log(str(exc))
"""
    result = await executor.run(make_job(code))
    assert "nothing was downloaded" in result["logs"][-1], result


async def test_reply_file_kinds_and_shared_dir(executor, fake, settings):
    shared = Path(settings.shared_read_dir)
    shared.mkdir()
    (shared / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    code = f"""
async def handle(update, ctx):
    for name in ("a.mp3", "b.png", "c.zip", "d.gif"):
        await ctx.reply_file(ctx.files.write_bytes(name, b"x"))
    await ctx.reply_file("c.zip", kind="photo")
    ctx.log(ctx.files.read_text("{shared}/cookies.txt").strip())
    try:
        ctx.files.write_text("{shared}/x.txt", "no")
    except ValueError:
        ctx.log("shared is read-only for handlers")
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert [m for m, _ in fake.calls] == [
        "sendAudio",
        "sendPhoto",
        "sendDocument",
        "sendAnimation",
        "sendPhoto",
    ]
    assert result["logs"] == ["# Netscape HTTP Cookie File", "shared is read-only for handlers"]
