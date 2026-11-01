"""
LLM token 用量记账 —— 轻量、线程安全、只做汇总。

落盘两份文件（都在 ``data_dir`` 下）：

- ``events.jsonl``：追加写的明细，一行一条 ``UsageRecord``，用于事后重算；
- ``summary.json``：汇总快照（总量 / 按模型 / 按天 / 最近调用），
  供桌面端（launcher）远程读取，字段名是对外契约，不要随意改。

设计原则：记账是旁路能力。所有 IO 异常都在内部吞掉并 ``logger.warning``，
绝不允许因为统计失败而影响聊天主流程。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("im_bridge.usage")

# 北京时间（与 state.py 保持一致）
CST = timezone(timedelta(hours=8))

RECENT_LIMIT = 50
DEFAULT_KEEP_DAYS = 90


@dataclass
class UsageRecord:
    """一次 LLM 调用的用量。"""

    ts: float
    model: str
    prompt: int
    completion: int
    cached: int = 0
    calls: int = 1
    source: str = "chat"  # chat / summary / 其他

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UsageRecord":
        return cls(
            ts=float(data.get("ts") or 0.0),
            model=str(data.get("model") or "unknown"),
            prompt=_to_count(data.get("prompt")),
            completion=_to_count(data.get("completion")),
            cached=_to_count(data.get("cached")),
            calls=max(1, _to_count(data.get("calls")) or 1),
            source=str(data.get("source") or "chat"),
        )


def _to_count(value) -> int:
    """把任意值转成非负整数，脏数据一律按 0 处理。"""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _empty_bucket() -> dict:
    return {"calls": 0, "prompt": 0, "completion": 0, "cached": 0}


def _day_key(ts: float) -> str:
    """时间戳 -> 本地日期键 (YYYY-MM-DD)。"""
    return datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d")


class UsageTracker:
    """token 用量累加器。

    参数
    ----
    data_dir : 数据目录（不存在会自动创建）
    enabled  : False 时 ``record`` 是空操作，不产生任何文件
    keep_days: ``days`` 只保留最近这么多天
    """

    def __init__(
        self,
        data_dir: Path | str,
        enabled: bool = True,
        keep_days: int = DEFAULT_KEEP_DAYS,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.enabled = bool(enabled)
        self.keep_days = max(1, int(keep_days))
        self._lock = threading.Lock()
        self._summary = self._empty_summary()

        if not self.enabled:
            return

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("用量统计目录不可用，统计已停用：%s", exc)
            self.enabled = False
            return

        self._summary = self._load()

    # ---------- 路径 ----------
    @property
    def events_path(self) -> Path:
        return self.data_dir / "events.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.data_dir / "summary.json"

    # ---------- 对外接口 ----------
    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        source: str = "chat",
    ) -> None:
        """追加一条用量。任何异常都不外抛。"""
        if not self.enabled:
            return
        try:
            record = UsageRecord(
                ts=time.time(),
                model=str(model or "unknown"),
                prompt=_to_count(prompt_tokens),
                completion=_to_count(completion_tokens),
                cached=_to_count(cached_tokens),
                source=str(source or "chat"),
            )
            with self._lock:
                self._append_event(record)
                self._apply(self._summary, record)
                self._prune(self._summary)
                self._write_summary(self._summary)
        except Exception as exc:  # noqa: BLE001 - 记账失败绝不能影响主流程
            logger.warning("token 用量记账失败（已忽略）：%s", exc)

    def record_missing(self, model: str, source: str = "chat") -> None:
        """记录一次「拿不到 usage」的调用：token 全 0，只累加调用次数。"""
        self.record(model, 0, 0, 0, source=source)

    def summary(self) -> dict:
        """返回当前汇总的副本（内存缓存为准）。"""
        with self._lock:
            return json.loads(json.dumps(self._summary, ensure_ascii=False))

    def reset(self) -> None:
        """清空明细与汇总。"""
        try:
            with self._lock:
                self._summary = self._empty_summary()
                if self.events_path.exists():
                    self.events_path.write_text("", "utf-8")
                self._write_summary(self._summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("token 用量重置失败（已忽略）：%s", exc)

    # ---------- 内部：累加 ----------
    @staticmethod
    def _empty_summary() -> dict:
        return {
            "updated_at": datetime.now(CST).isoformat(timespec="seconds"),
            "total": _empty_bucket(),
            "models": {},
            "days": {},
            "recent": [],
        }

    @staticmethod
    def _add_into(bucket: dict, record: UsageRecord) -> None:
        bucket["calls"] += record.calls
        bucket["prompt"] += record.prompt
        bucket["completion"] += record.completion
        bucket["cached"] += record.cached

    def _apply(self, summary: dict, record: UsageRecord) -> None:
        """把一条记录累加进汇总（内存结构）。"""
        self._add_into(summary["total"], record)

        model_bucket = summary["models"].setdefault(record.model, _empty_bucket())
        self._add_into(model_bucket, record)

        day = _day_key(record.ts)
        day_bucket = summary["days"].setdefault(day, {**_empty_bucket(), "models": {}})
        self._add_into(day_bucket, record)
        day_models = day_bucket.setdefault("models", {})
        self._add_into(day_models.setdefault(record.model, _empty_bucket()), record)

        recent = summary.setdefault("recent", [])
        recent.insert(
            0,
            {
                "ts": record.ts,
                "model": record.model,
                "prompt": record.prompt,
                "completion": record.completion,
                "cached": record.cached,
                "source": record.source,
            },
        )
        del recent[RECENT_LIMIT:]

    def _prune(self, summary: dict) -> None:
        """裁掉超过 keep_days 的日期桶，并限制 recent 长度。"""
        cutoff = (datetime.now(CST).date() - timedelta(days=self.keep_days - 1)).isoformat()
        days = summary.get("days") or {}
        for key in list(days):
            try:
                key_date = date.fromisoformat(str(key))
            except ValueError:
                continue  # 无法解析的键保留，宁可多留
            if key_date.isoformat() < cutoff:
                days.pop(key, None)

        recent = summary.get("recent") or []
        if len(recent) > RECENT_LIMIT:
            del recent[RECENT_LIMIT:]
        summary["recent"] = recent

    # ---------- 内部：IO ----------
    def _append_event(self, record: UsageRecord) -> None:
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with open(self.events_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    def _write_summary(self, summary: dict) -> None:
        summary["updated_at"] = datetime.now(CST).isoformat(timespec="seconds")
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        temp_path = self.data_dir / "summary.json.tmp"
        temp_path.write_text(text, "utf-8")
        os.replace(temp_path, self.summary_path)

    # ---------- 内部：加载 / 重建 ----------
    def _load(self) -> dict:
        loaded = self._read_summary_file()
        if loaded is not None:
            return loaded
        rebuilt = self._rebuild_from_events()
        if rebuilt is not None:
            return rebuilt
        return self._empty_summary()

    def _read_summary_file(self) -> dict | None:
        try:
            if not self.summary_path.exists():
                return None
            data = json.loads(self.summary_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("summary.json 读取失败，将尝试用 events 重建：%s", exc)
            return None
        if not isinstance(data, dict):
            return None
        summary = self._normalise(data)
        self._prune(summary)
        return summary

    def _rebuild_from_events(self) -> dict | None:
        """从 events.jsonl 重算汇总；文件不存在或没有有效记录时返回 None。"""
        try:
            if not self.events_path.exists():
                return None
            lines = self.events_path.read_text("utf-8").splitlines()
        except OSError as exc:
            logger.warning("events.jsonl 读取失败，用量汇总保持为空：%s", exc)
            return None

        records: list[UsageRecord] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                records.append(UsageRecord.from_dict(item))

        if not records:
            return None

        records.sort(key=lambda item: item.ts)
        summary = self._empty_summary()
        for record in records:
            self._apply(summary, record)
        self._prune(summary)
        try:
            self._write_summary(summary)
        except OSError as exc:
            logger.warning("重建后的 summary.json 写入失败：%s", exc)
        return summary

    def _normalise(self, data: dict) -> dict:
        """把外部读来的 JSON 收敛成稳定结构，脏字段全部重置。"""
        summary = self._empty_summary()

        updated_at = data.get("updated_at")
        if isinstance(updated_at, str) and updated_at:
            summary["updated_at"] = updated_at

        total = data.get("total")
        if isinstance(total, dict):
            summary["total"] = self._bucket_from(total)

        models = data.get("models")
        if isinstance(models, dict):
            for name, bucket in models.items():
                if isinstance(bucket, dict):
                    summary["models"][str(name)] = self._bucket_from(bucket)

        days = data.get("days")
        if isinstance(days, dict):
            for key, bucket in days.items():
                if not isinstance(bucket, dict):
                    continue
                day = {**self._bucket_from(bucket), "models": {}}
                day_models = bucket.get("models")
                if isinstance(day_models, dict):
                    for name, model_bucket in day_models.items():
                        if isinstance(model_bucket, dict):
                            day["models"][str(name)] = self._bucket_from(model_bucket)
                summary["days"][str(key)] = day

        recent = data.get("recent")
        if isinstance(recent, list):
            summary["recent"] = [
                {
                    "ts": float(item.get("ts") or 0.0),
                    "model": str(item.get("model") or "unknown"),
                    "prompt": _to_count(item.get("prompt")),
                    "completion": _to_count(item.get("completion")),
                    "cached": _to_count(item.get("cached")),
                    "source": str(item.get("source") or "chat"),
                }
                for item in recent
                if isinstance(item, dict)
            ][:RECENT_LIMIT]

        return summary

    @staticmethod
    def _bucket_from(data: dict) -> dict:
        return {
            "calls": _to_count(data.get("calls")),
            "prompt": _to_count(data.get("prompt")),
            "completion": _to_count(data.get("completion")),
            "cached": _to_count(data.get("cached")),
        }
