"""
LLM token 用量记账 —— 离线脚本式测试（不依赖 pytest / 真实网络）。

运行：
    .build/venv/Scripts/python.exe tests/test_usage.py

全部通过时输出 ALL PASS；任一断言失败会打印 [FAIL] 并以非 0 退出。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "libs"))

import httpx  # noqa: E402  （libs/ 里的 vendored 版本）

from im_bridge.core.llm import LLMClient, LLMError  # noqa: E402
from im_bridge.core.usage import CST, RECENT_LIMIT, UsageTracker  # noqa: E402

TMP = ROOT / "tests" / ".tmp_usage"


# ---------------------------------------------------------------- 工具
def _fresh(name: str) -> Path:
    path = TMP / name
    shutil.rmtree(path, ignore_errors=True)
    return path


def _today_key(offset_days: int = 0) -> str:
    return (datetime.now(CST) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}：期望 {expected!r}，实际 {actual!r}")


# ---------------------------------------------------------------- 用例 1
def test_accumulate_and_recent(tmp: Path) -> None:
    """连续 record 后 total/models/days 累加正确，recent 倒序且不超 50。"""
    data_dir = _fresh("accumulate")
    tracker = UsageTracker(data_dir)

    tracker.record("deepseek-chat", 1000, 200, 300)
    tracker.record("deepseek-chat", 500, 100, 0)
    tracker.record("deepseek-reasoner", 700, 50, 100, source="summary")

    summary = tracker.summary()
    _equal(
        summary["total"],
        {"calls": 3, "prompt": 2200, "completion": 350, "cached": 400},
        "total 累加",
    )
    _equal(
        summary["models"]["deepseek-chat"],
        {"calls": 2, "prompt": 1500, "completion": 300, "cached": 300},
        "按模型累加",
    )
    _equal(
        summary["models"]["deepseek-reasoner"],
        {"calls": 1, "prompt": 700, "completion": 50, "cached": 100},
        "第二个模型累加",
    )

    today = _today_key()
    _expect(today in summary["days"], f"days 缺少今天 {today}")
    _equal(
        summary["days"][today]["prompt"],
        2200,
        "按天累加 prompt",
    )
    _equal(
        summary["days"][today]["models"]["deepseek-chat"]["calls"],
        2,
        "按天按模型累加",
    )

    _equal(len(summary["recent"]), 3, "recent 条数")
    _equal(summary["recent"][0]["model"], "deepseek-reasoner", "recent 新的在前")
    _equal(summary["recent"][0]["source"], "summary", "recent 记录 source")
    _equal(
        sorted(summary["recent"][0].keys()),
        ["cached", "completion", "model", "prompt", "source", "ts"],
        "recent 条目字段",
    )
    _expect(isinstance(summary["updated_at"], str) and "+08:00" in summary["updated_at"],
            f"updated_at 应为带时区的字符串，实际 {summary['updated_at']!r}")

    # 落盘校验
    on_disk = _read_json(data_dir / "summary.json")
    _equal(on_disk["total"], summary["total"], "summary.json 与内存一致")
    _equal(on_disk["models"], summary["models"], "summary.json models 一致")
    _equal(len(on_disk["recent"]), 3, "summary.json recent 条数")

    lines = (data_dir / "events.jsonl").read_text("utf-8").splitlines()
    _equal(len(lines), 3, "events.jsonl 行数")
    first = json.loads(lines[0])
    _equal(first["model"], "deepseek-chat", "events 第一条 model")
    _equal(first["prompt"], 1000, "events 第一条 prompt")
    _equal(first["calls"], 1, "events 第一条 calls")
    _expect("deepseek-reasoner" in (data_dir / "events.jsonl").read_text("utf-8"),
            "events 应写入非 ASCII 内容（ensure_ascii=False 可读）")

    # recent 上限
    for index in range(60):
        tracker.record("burst", 1, 1)
    summary = tracker.summary()
    _equal(len(summary["recent"]), RECENT_LIMIT, "recent 上限")
    _equal(summary["recent"][0]["model"], "burst", "recent 仍是最新在前")
    _equal(summary["total"]["calls"], 63, "total calls 累计")
    _equal(summary["models"]["burst"]["calls"], 60, "burst 模型累计")
    _equal(
        len(_read_json(data_dir / "summary.json")["recent"]),
        RECENT_LIMIT,
        "落盘 recent 上限",
    )


# ---------------------------------------------------------------- 用例 2
def test_keep_days_pruning(tmp: Path) -> None:
    """100 天前的 day key 在重新加载时被裁掉，近期的保留。"""
    data_dir = _fresh("prune")
    tracker = UsageTracker(data_dir, keep_days=90)
    tracker.record("m", 10, 5)

    summary = tracker.summary()
    old_key = _today_key(-100)
    edge_key = _today_key(-10)
    summary["days"][old_key] = {
        "calls": 9,
        "prompt": 900,
        "completion": 90,
        "cached": 0,
        "models": {"m": {"calls": 9, "prompt": 900, "completion": 90, "cached": 0}},
    }
    summary["days"][edge_key] = {
        "calls": 1,
        "prompt": 10,
        "completion": 5,
        "cached": 0,
        "models": {"m": {"calls": 1, "prompt": 10, "completion": 5, "cached": 0}},
    }
    (data_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), "utf-8"
    )

    reloaded = UsageTracker(data_dir, keep_days=90)
    days = reloaded.summary()["days"]
    _expect(old_key not in days, f"100 天前的 {old_key} 应被裁掉")
    _expect(edge_key in days, f"10 天前的 {edge_key} 应保留")
    _expect(_today_key() in days, "今天的桶应保留")
    _equal(days[edge_key]["prompt"], 10, "保留桶的数据未变形")

    # keep_days 很小时也裁得掉
    tiny = UsageTracker(data_dir, keep_days=1)
    _equal(sorted(tiny.summary()["days"].keys()), [_today_key()], "keep_days=1 只留今天")


# ---------------------------------------------------------------- 用例 3
def test_record_tolerates_io_failure(tmp: Path) -> None:
    """目录不可用 / 写文件失败时 record 不抛异常，也不污染汇总。"""
    # 3a：data_dir 指向一个已存在的文件 → mkdir 必失败
    file_path = TMP / "not_a_dir"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("x", "utf-8")
    blocked = UsageTracker(file_path)
    _expect(blocked.enabled is False, "目录不可用时应自动停用")
    blocked.record("m", 100, 20)  # 不应抛异常
    _equal(blocked.summary()["total"]["calls"], 0, "停用后不累计")

    # 3b：events.jsonl 是一个目录 → 追加写必失败
    data_dir = _fresh("io_fail")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "events.jsonl").mkdir()
    tracker = UsageTracker(data_dir)
    tracker.record("m", 100, 20)  # 不应抛异常
    _equal(tracker.summary()["total"]["calls"], 0, "写入失败时不累计")
    _expect(not (data_dir / "summary.json").exists(), "写入失败时不应生成 summary.json")

    # 3c：脏数值不炸，按 0 处理
    dirty_dir = _fresh("dirty")
    dirty = UsageTracker(dirty_dir)
    dirty.record("m", None, -5, None)  # type: ignore[arg-type]
    dirty.record("", "abc", 7)  # type: ignore[arg-type]
    summary = dirty.summary()
    _equal(summary["total"]["prompt"], 0, "非法 prompt 按 0")
    _equal(summary["total"]["completion"], 7, "非法 completion 按 0 相加")
    _expect("unknown" in summary["models"], "空 model 应归一为 unknown")


# ---------------------------------------------------------------- 用例 4
def test_disabled_is_noop(tmp: Path) -> None:
    """enabled=False 时 record 是空操作，不产生任何文件。"""
    data_dir = _fresh("disabled")
    tracker = UsageTracker(data_dir, enabled=False)
    tracker.record("m", 1000, 200)
    tracker.record("m", 1000, 200, source="summary")
    _equal(tracker.summary()["total"]["calls"], 0, "disabled 不累计")
    _equal(tracker.summary()["models"], {}, "disabled 不建模型桶")
    _expect(not data_dir.exists(), "disabled 不应创建数据目录")
    _expect(not (data_dir / "events.jsonl").exists(), "disabled 不应创建 events.jsonl")
    _expect(not (data_dir / "summary.json").exists(), "disabled 不应创建 summary.json")


# ---------------------------------------------------------------- 用例 5
def test_rebuild_from_events(tmp: Path) -> None:
    """summary.json 缺失/损坏时从 events.jsonl 重建。"""
    data_dir = _fresh("rebuild")
    data_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(CST).timestamp()
    rows = [
        {"ts": now - 100, "model": "a", "prompt": 10, "completion": 5,
         "cached": 0, "calls": 1, "source": "chat"},
        {"ts": now - 50, "model": "b", "prompt": 20, "completion": 7,
         "cached": 3, "calls": 1, "source": "summary"},
    ]
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    # 中间插一条坏行，重建应跳过
    lines = text.splitlines()
    lines.insert(1, "{ this is not json")
    (data_dir / "events.jsonl").write_text("\n".join(lines) + "\n", "utf-8")

    tracker = UsageTracker(data_dir)
    summary = tracker.summary()
    _equal(
        summary["total"],
        {"calls": 2, "prompt": 30, "completion": 12, "cached": 3},
        "重建 total",
    )
    _equal(
        sorted(summary["models"].keys()), ["a", "b"], "重建 models"
    )
    _equal(summary["models"]["b"]["cached"], 3, "重建 cached")
    _equal(summary["recent"][0]["model"], "b", "重建 recent 新的在前")
    _expect((data_dir / "summary.json").exists(), "重建后应写出 summary.json")

    # 已有 summary.json 时以文件为准，不会与 events 重复累加
    again = UsageTracker(data_dir)
    _equal(again.summary()["total"]["calls"], 2, "二次加载不重复累加")

    # 损坏的 summary.json 也能回退到重建
    (data_dir / "summary.json").write_text("{broken", "utf-8")
    recovered = UsageTracker(data_dir)
    _equal(recovered.summary()["total"]["calls"], 2, "损坏 summary 后重建")

    # reset 清空
    recovered.reset()
    _equal(recovered.summary()["total"]["calls"], 0, "reset 后 total 归零")
    _equal(recovered.summary()["recent"], [], "reset 后 recent 清空")
    _equal((data_dir / "events.jsonl").read_text("utf-8"), "", "reset 清空 events")


# ---------------------------------------------------------------- 用例 5b
def test_record_missing(tmp: Path) -> None:
    """record_missing 只累加调用次数，token 全 0。"""
    data_dir = _fresh("missing")
    tracker = UsageTracker(data_dir)
    tracker.record("deepseek-chat", 100, 20, 10)
    tracker.record_missing("deepseek-chat")
    tracker.record_missing("deepseek-chat", source="summary")

    summary = tracker.summary()
    _equal(
        summary["total"],
        {"calls": 3, "prompt": 100, "completion": 20, "cached": 10},
        "record_missing 计数但不加 token",
    )
    _equal(summary["recent"][0]["source"], "summary", "record_missing 透传 source")
    _equal(summary["recent"][0]["prompt"], 0, "record_missing prompt=0")
    _equal(
        len((data_dir / "events.jsonl").read_text("utf-8").splitlines()),
        3,
        "record_missing 也写 events",
    )


# ---------------------------------------------------------------- 用例 6
class _ScriptedLLM(LLMClient):
    """把 HTTP 客户端替换成脚本化的 httpx.MockTransport。"""

    def __init__(self, responses: list, **kwargs) -> None:
        kwargs.setdefault("api_key", "sk-test")
        kwargs.setdefault("base_url", "https://api.example.com/v1")
        kwargs.setdefault("model", "deepseek-chat")
        kwargs.setdefault("max_retries", 1)
        super().__init__(**kwargs)
        self._responses = list(responses)
        self.payloads: list[dict] = []

    def _client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.payloads.append(json.loads(request.content.decode("utf-8")))
            item = self._responses.pop(0)
            return item

        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(5),
        )


_COMPLETE_BODY = {
    "model": "deepseek-chat",
    "choices": [{"message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}],
    "usage": {
        "prompt_tokens": 700,
        "completion_tokens": 120,
        "total_tokens": 820,
        "prompt_cache_hit_tokens": 300,
        "prompt_cache_miss_tokens": 400,
    },
}

# DeepSeek 实际形态：usage 挂在「最后一个内容块」上，
# 该块 choices 长度为 1、finish_reason 非空（不是 OpenAI 那种空 choices 块）。
_DEEPSEEK_STREAM_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{"content":"好"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":1000,"completion_tokens":120,'
    '"prompt_cache_hit_tokens":800,"prompt_cache_miss_tokens":200}}\n\n'
    "data: [DONE]\n\n"
)

# 兼容形态：部分中转服务按 OpenAI 规范把 usage 放在 choices 为空的最末块
_OPENAI_STREAM_BODY = (
    'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
    'data: {"choices":[],"usage":{"prompt_tokens":700,"completion_tokens":120,'
    '"prompt_cache_hit_tokens":64}}\n\n'
    "data: [DONE]\n\n"
)

# 流正常结束但完全没有 usage（老服务端 / 未支持 include_usage）
_NO_USAGE_STREAM_BODY = (
    'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def _sse(body: str) -> httpx.Response:
    return httpx.Response(
        200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"}
    )


def test_llm_usage_wiring(tmp: Path) -> None:
    """LLMClient 的 complete / stream / summarize 都按约定记账。"""
    messages = [{"role": "user", "content": "hi"}]

    # 6a 非流式：prompt / completion / cached 都记上，source=chat
    tracker = UsageTracker(_fresh("llm_complete"))
    llm = _ScriptedLLM([httpx.Response(200, json=_COMPLETE_BODY)], usage_tracker=tracker)
    text = asyncio.run(llm.complete(messages))
    _equal(text, "你好", "complete 返回文本")
    summary = tracker.summary()
    _equal(
        summary["total"],
        {"calls": 1, "prompt": 700, "completion": 120, "cached": 300},
        "complete 记账",
    )
    _equal(summary["recent"][0]["source"], "chat", "complete source=chat")
    _equal(summary["models"].get("deepseek-chat", {}).get("calls"), 1,
            "complete 按响应 model 记账")

    # 6b summarize 复用 complete，但 source=summary
    summary_tracker = UsageTracker(_fresh("llm_summary"))
    llm2 = _ScriptedLLM([httpx.Response(200, json=_COMPLETE_BODY)],
                        usage_tracker=summary_tracker)
    asyncio.run(llm2.summarize("旧摘要", "新对话"))
    _equal(summary_tracker.summary()["recent"][0]["source"], "summary",
            "summarize source=summary")

    # 6c 流式（DeepSeek 实际形态）：usage 附在最后一个内容块上也要记到
    stream_tracker = UsageTracker(_fresh("llm_stream"))
    llm3 = _ScriptedLLM([_sse(_DEEPSEEK_STREAM_BODY)], usage_tracker=stream_tracker)

    async def collect(client: LLMClient) -> str:
        return "".join([chunk async for chunk in client.stream(messages)])

    _equal(asyncio.run(collect(llm3)), "你好", "stream 拼接文本")
    _equal(llm3.payloads[0].get("stream_options"), {"include_usage": True},
           "stream 请求带 stream_options")
    _equal(
        stream_tracker.summary()["total"],
        {"calls": 1, "prompt": 1000, "completion": 120, "cached": 800},
        "DeepSeek 形态流式记账（命中缓存单独记）",
    )
    _equal(
        stream_tracker.summary()["models"]["deepseek-chat"]["cached"],
        800,
        "cached 只记 prompt_cache_hit_tokens",
    )

    # 6d 兼容形态：usage 藏在 choices 为空的最末块（OpenAI / 部分中转）
    compat_tracker = UsageTracker(_fresh("llm_openai_stream"))
    llm_compat = _ScriptedLLM([_sse(_OPENAI_STREAM_BODY)], usage_tracker=compat_tracker)
    _equal(asyncio.run(collect(llm_compat)), "你好", "兼容形态仍能出文本")
    _equal(
        compat_tracker.summary()["total"],
        {"calls": 1, "prompt": 700, "completion": 120, "cached": 64},
        "兼容形态流式记账",
    )

    # 6e 服务端 400 拒绝 stream_options → 去掉字段重试，仍能拿到内容
    fallback_tracker = UsageTracker(_fresh("llm_fallback"))
    llm4 = _ScriptedLLM(
        [
            httpx.Response(400, json={"error": {"message": "unknown parameter: stream_options"}}),
            _sse(_DEEPSEEK_STREAM_BODY),
        ],
        usage_tracker=fallback_tracker,
    )
    _equal(asyncio.run(collect(llm4)), "你好", "回退后仍能收到内容")
    _equal(len(llm4.payloads), 2, "回退只额外请求一次")
    _expect("stream_options" in llm4.payloads[0], "第一次请求带 stream_options")
    _expect("stream_options" not in llm4.payloads[1], "第二次请求已去掉 stream_options")
    _equal(fallback_tracker.summary()["total"]["calls"], 1, "回退后正常记账")

    # 6f 流正常结束但完全没有 usage → 记一条 0，不抛异常
    no_usage_tracker = UsageTracker(_fresh("llm_no_usage"))
    llm_nu = _ScriptedLLM([_sse(_NO_USAGE_STREAM_BODY)], usage_tracker=no_usage_tracker)
    _equal(asyncio.run(collect(llm_nu)), "你好", "无 usage 的流仍正常返回")
    nu_summary = no_usage_tracker.summary()
    _equal(
        nu_summary["total"],
        {"calls": 1, "prompt": 0, "completion": 0, "cached": 0},
        "无 usage 时按 0 记账并计入调用次数",
    )
    _equal(nu_summary["recent"][0]["source"], "chat", "无 usage 时 source 仍为 chat")

    # 6g 非流式缺 usage 同样按 0 记，不抛异常
    nc_tracker = UsageTracker(_fresh("llm_complete_no_usage"))
    no_usage_complete = _ScriptedLLM(
        [
            httpx.Response(
                200,
                json={
                    "model": "deepseek-chat",
                    "choices": [{"message": {"content": "你好"}}],
                },
            )
        ],
        usage_tracker=nc_tracker,
    )
    _equal(
        asyncio.run(no_usage_complete.complete(messages)),
        "你好",
        "缺 usage 时仍正常返回",
    )
    _equal(
        nc_tracker.summary()["total"],
        {"calls": 1, "prompt": 0, "completion": 0, "cached": 0},
        "complete 缺 usage 时按 0 记账",
    )

    # 6h 只有 prompt_cache_miss_tokens 时，用 prompt - miss 反推命中量
    miss_tracker = UsageTracker(_fresh("llm_miss_only"))
    miss_llm = _ScriptedLLM(
        [
            httpx.Response(
                200,
                json={
                    "model": "deepseek-chat",
                    "choices": [{"message": {"content": "好"}}],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 50,
                        "prompt_cache_miss_tokens": 250,
                    },
                },
            )
        ],
        usage_tracker=miss_tracker,
    )
    asyncio.run(miss_llm.complete(messages))
    _equal(
        miss_tracker.summary()["total"],
        {"calls": 1, "prompt": 1000, "completion": 50, "cached": 750},
        "缺 hit 字段时用 prompt - miss 反推 cached",
    )

    # 6i 流中断（已产出内容后报错）→ 抛 LLMError 且不记账
    broken_tracker = UsageTracker(_fresh("llm_broken"))

    class _BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"\\u4f60"}}]}\n\n'
            raise httpx.ReadError("boom")

    llm5 = _ScriptedLLM(
        [httpx.Response(200, stream=_BrokenStream(),
                        headers={"content-type": "text/event-stream"})],
        usage_tracker=broken_tracker,
    )
    got: list[str] = []
    try:
        async def drain() -> None:
            async for chunk in llm5.stream(messages):
                got.append(chunk)

        asyncio.run(drain())
    except LLMError:
        pass
    else:
        raise AssertionError("流中断应抛 LLMError")
    _expect(got == ["你"], f"中断前已产出的片段应保留，实际 {got!r}")
    _equal(broken_tracker.summary()["total"]["calls"], 0, "中断的流不记账")

    # 6j 无 tracker / usage_enabled=False 都不影响正常返回
    plain = _ScriptedLLM([httpx.Response(200, json=_COMPLETE_BODY)])
    _equal(asyncio.run(plain.complete(messages)), "你好", "无 tracker 时正常返回")

    off_tracker = UsageTracker(_fresh("llm_off"))
    off = _ScriptedLLM(
        [httpx.Response(200, json=_COMPLETE_BODY)],
        usage_tracker=off_tracker,
        usage_enabled=False,
    )
    _equal(asyncio.run(off.complete(messages)), "你好", "usage_enabled=False 正常返回")
    _equal(off_tracker.summary()["total"]["calls"], 0, "usage_enabled=False 不记账")

    # 6k 旧的关键字参数调用方式仍然可用（向后兼容）
    legacy = LLMClient(
        api_key="sk-test",
        base_url="https://api.example.com/v1/",
        model="deepseek-chat",
        timeout=10,
        max_retries=2,
        temperature=0.7,
        max_tokens=100,
    )
    _equal(legacy.usage_tracker, None, "旧调用不传 tracker 时为 None")
    _equal(legacy.usage_enabled, True, "usage_enabled 默认 True")


# ---------------------------------------------------------------- 运行器
CASES = [
    test_accumulate_and_recent,
    test_keep_days_pruning,
    test_record_tolerates_io_failure,
    test_disabled_is_noop,
    test_rebuild_from_events,
    test_record_missing,
    test_llm_usage_wiring,
]


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    failed = 0
    for case in CASES:
        try:
            case(TMP)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {case.__name__}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            print(f"[PASS] {case.__name__}")

    if failed:
        print(f"FAILED {failed}/{len(CASES)}")
        return 1

    shutil.rmtree(TMP, ignore_errors=True)
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
