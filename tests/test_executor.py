from pathlib import Path

from fastapi.testclient import TestClient

from bot_runner.server import create_app

from .conftest import SECRET, TOKEN, make_job


async def test_reply_reaches_bot_api(executor, fake):
    code = """
async def handle(update, ctx):
    await ctx.reply("you said " + update.message.text)
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert fake.calls == [("sendMessage", {"chat_id": 42, "text": "you said hello"})]
    assert result["api_calls"][0]["method"] == "sendMessage"
    assert result["api_calls"][0]["ok"] is True


async def test_print_and_log_are_collected(executor):
    code = """
def handle(update, ctx):
    print("from print", update.message.from_.first_name)
    ctx.log("from ctx.log", 1, 2)
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"] == ["from print Test", "from ctx.log 1 2"]


async def test_exception_is_reported_with_masked_token(executor):
    code = f"""
async def handle(update, ctx):
    ctx.log("token is {TOKEN}")
    raise ValueError("boom {TOKEN}")
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "error"
    assert "ValueError: boom <token>" in result["error"]
    assert TOKEN not in result["error"]
    assert result["logs"] == ["token is <token>"]


async def test_telegram_error_is_raised(executor):
    code = """
async def handle(update, ctx):
    await ctx.api.bad_method()
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "error"
    assert "Bad Request: nope" in result["error"]
    assert result["api_calls"][0] == {
        "method": "badMethod",
        "params": {},
        "ok": False,
        "error": "Bad Request: nope",
    }


async def test_hanging_handler_is_killed(executor):
    code = """
def handle(update, ctx):
    print("started")
    while True:
        pass
"""
    result = await executor.run(make_job(code, timeout=1))
    assert result["status"] == "timeout"
    assert result["logs"] == ["started"]
    assert result["duration_ms"] < 5000


async def test_missing_handle_function(executor):
    result = await executor.run(make_job("x = 1"))
    assert result["status"] == "error"
    assert "must define a function `handle(update, ctx)`" in result["error"]


async def test_dry_run_does_not_send(executor, fake):
    code = """
async def handle(update, ctx):
    me = await ctx.api.get_me()
    await ctx.reply("hi")
"""
    result = await executor.run(make_job(code, dry_run=True))
    assert result["status"] == "ok", result
    assert [m for m, _ in fake.calls] == ["getMe"]
    assert result["api_calls"][1] == {
        "method": "sendMessage",
        "params": {"chat_id": 42, "text": "hi"},
        "ok": True,
        "dry_run": True,
    }


async def test_files_move_into_bot_folder(executor, settings):
    code = """
async def handle(update, ctx):
    doc = update.message.document
    path = await ctx.files.local_path(doc.file_id)
    saved = ctx.files.move(path, "inbox/" + ctx.files.safe_name(doc.file_name))
    ctx.log(saved.relative_to(ctx.files.root))
    again = ctx.files.write_text("inbox/report.pdf", "second")
    ctx.log(again.name)
    try:
        ctx.files.path("../escape.txt")
    except ValueError:
        ctx.log("escape blocked")
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    assert result["logs"] == ["inbox/report.pdf", "report.pdf", "escape blocked"]
    saved = Path(settings.files_root) / "123456" / "inbox" / "report.pdf"
    assert saved.read_text() == "second"


async def test_unique_names_do_not_overwrite(executor, settings):
    code = """
def handle(update, ctx):
    ctx.files.write_text("a.txt", "1")
    ctx.files.copy("a.txt", "a.txt")
    ctx.log(sorted(p.name for p in ctx.files.list()))
"""
    result = await executor.run(make_job(code))
    assert result["logs"] == ["['a (1).txt', 'a.txt']"], result


async def test_kv_and_secrets(executor, fake):
    code = """
async def handle(update, ctx):
    assert await ctx.kv.get("count", 0) == 0
    await ctx.kv.set("count", {"n": 1})
    ctx.log((await ctx.kv.get("count"))["n"], ctx.secrets["API_KEY"])
    await ctx.kv.delete("count")
    ctx.log(await ctx.kv.get("count"))
"""
    result = await executor.run(make_job(code, secrets={"API_KEY": "k1"}))
    assert result["status"] == "ok", result
    assert result["logs"] == ["1 k1", "None"]


async def test_path_values_become_file_uris(executor, fake, settings):
    code = """
async def handle(update, ctx):
    path = ctx.files.write_text("out.txt", "data")
    await ctx.api.send_document(chat_id=ctx.chat_id, document=path)
"""
    result = await executor.run(make_job(code))
    assert result["status"] == "ok", result
    method, body = fake.calls[-1]
    assert method == "sendDocument"
    assert body["document"] == (Path(settings.files_root) / "123456" / "out.txt").as_uri()


def test_run_endpoint_requires_secret(settings):
    client = TestClient(create_app(settings))
    job = make_job("def handle(update, ctx): pass")
    assert client.post("/run", json=job).status_code == 401
    assert client.post("/run", json=job, headers={"X-Runner-Secret": "nope"}).status_code == 401
    response = client.post("/run", json=job, headers={"X-Runner-Secret": SECRET})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert client.get("/health").json()["status"] == "ok"


async def test_memory_limit_stops_runaway_handler(settings):
    from bot_runner.executor import Executor

    limited = Executor(settings.model_copy(update={"handler_memory_mb": 300}))
    code = """
def handle(update, ctx):
    blocks = [bytearray(50 * 1024 * 1024) for _ in range(20)]
"""
    result = await limited.run(make_job(code))
    assert result["status"] == "error"
    assert "MemoryError" in result["error"]


def test_mask_tokens_inside_urls():
    from bot_runner.masking import mask

    assert mask(f"https://api/bot{TOKEN}/getMe") == "https://api/bot<token>/getMe"
    assert mask("12:short") == "12:short"
