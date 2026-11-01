"""表情包：标签 → 文件映射、流式标签剥离、频控与 base64 发送。

设计参考 GitHub 上成熟的表情包插件（astrbot-Aemeath-meme-pack 的目录约定）
与 OneBot v11 的 image 消息段：

- 模型只在回复里输出标记 ``[[表情:标签]]``，本模块负责**从文字流里剥离**
  （流式分片时也不能把标记发给用户），再随机挑一张图片发送；
- 素材放在 ``data/stickers/``：``index.json`` + 标签目录或扁平文件都支持；
- 任何失败都只记日志，**绝不影响文字回复**；
- 默认关闭，配置页的四项开关全部生效后才工作。
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from collections import defaultdict
from pathlib import Path

TAG_PATTERN = re.compile(r"\[\[表情[:：]\s*([^\[\]]{1,16})\]\]")
SINGLE_TAG = re.compile(r"^表情[:：]\s*(.+)$")
IMAGE_SUFFIX_RE = re.compile(r"\.(png|jpe?g|gif|webp|bmp)$", re.IGNORECASE)
TAG_PREFIX = "[[表情"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 超过 5MB 的表情图直接放弃（与语音同策略）

logger = logging.getLogger("im_bridge.stickers")


def parse_tags(text: str) -> list[str]:
    """提取回复里的表情标签。"""
    return [match.group(1).strip() for match in TAG_PATTERN.finditer(text or "")]


def strip_tags(text: str) -> str:
    """把标签从文本里剥掉（用于非流式回复）。"""
    return TAG_PATTERN.sub("", text or "").strip()


class TagFilter:
    """流式过滤：把 ``[[表情:xxx]]`` 与 ``[xxx.jpg]`` 从输出流里剥离。

    resolver 由 StickerService 提供：返回（是否标记, 标签）。
    """

    def __init__(self, resolver=None) -> None:
        self.buffer = ""
        self.tags: list[str] = []
        self._resolve = resolver

    def feed(self, delta: str) -> str:
        self.buffer += delta or ""
        output: list[str] = []
        while True:
            start = self.buffer.find("[")
            if start < 0:
                output.append(self.buffer)
                self.buffer = ""
                break
            if start:
                output.append(self.buffer[:start])
                self.buffer = self.buffer[start:]

            # 1) 标准双层标记 [[表情:标签]]
            if self.buffer.startswith("[["):
                match = TAG_PATTERN.match(self.buffer)
                if match:
                    self.tags.append(match.group(1).strip())
                    self.buffer = self.buffer[match.end() :]
                    continue
                if len(self.buffer) < 16:
                    break  # 可能还没收全，等下一段
                output.append(self.buffer[0])
                self.buffer = self.buffer[1:]
                continue

            # 2) 单层方括号：[标签] / [文件名.jpg]
            close = self.buffer.find("]")
            if close < 0:
                if len(self.buffer) <= 48:
                    break  # 等闭合，避免把半个标记发出去
                output.append(self.buffer[0])
                self.buffer = self.buffer[1:]
                continue
            if self._resolve is not None:
                is_tag, tag = self._resolve(self.buffer[1:close])
                if is_tag:
                    if tag:
                        self.tags.append(tag)
                    self.buffer = self.buffer[close + 1 :]
                    continue
            output.append(self.buffer[0])
            self.buffer = self.buffer[1:]
        return "".join(output)

    def flush(self) -> str:
        """流结束：把缓冲里剩下的文本原样吐出（标记已经在 feed 里处理掉）。"""
        tail, self.buffer = self.buffer, ""
        return tail


class RateLimiter:
    """每个用户独立的表情频控：冷却时间 + 每日上限。"""

    def __init__(self, cooldown: float = 180.0, max_per_day: int = 30) -> None:
        self.cooldown = max(0.0, float(cooldown))
        self.max_per_day = max(0, int(max_per_day))
        self._last_at: dict[str, float] = defaultdict(float)
        self._daily: dict[str, tuple[str, int]] = {}

    @staticmethod
    def _day_of(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(now))

    def allow(self, user_id: str, now: float | None = None) -> bool:
        if self.max_per_day <= 0:
            return False
        now = time.time() if now is None else now
        day = self._day_of(now)
        stored_day, count = self._daily.get(user_id, (day, 0))
        if stored_day != day:
            count = 0
        if count >= self.max_per_day:
            return False
        if now - self._last_at.get(user_id, 0.0) < self.cooldown:
            return False
        return True

    def mark(self, user_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        day = self._day_of(now)
        stored_day, count = self._daily.get(user_id, (day, 0))
        if stored_day != day:
            count = 0
        self._last_at[user_id] = now
        self._daily[user_id] = (day, count + 1)


class StickerStore:
    """读取 ``data/stickers``：优先 index.json，没有索引时自动扫描标签目录。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.index: dict[str, list[Path]] = self._load()

    def _load(self) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = {}
        index_file = self.root / "index.json"
        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            for tag, raw_files in data.items():
                tag = str(tag).strip()
                if not tag:
                    continue
                files = raw_files if isinstance(raw_files, list) else [raw_files]
                resolved: list[Path] = []
                for item in files:
                    name = str(item).strip()
                    if not name:
                        continue
                    for candidate in (self.root / name, self.root / tag / name):
                        if candidate.is_file():
                            resolved.append(candidate)
                            break
                if resolved:
                    index[tag] = resolved
        if not index and self.root.is_dir():
            # 兼容纯目录结构：data/stickers/<标签>/*.png
            for directory in sorted(self.root.iterdir()):
                if not directory.is_dir():
                    continue
                files = [
                    path
                    for path in sorted(directory.iterdir())
                    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
                ]
                if files:
                    index[directory.name] = files
        return index

    def tag_set(self) -> set[str]:
        return set(self.index.keys())

    def stem_lookup(self) -> dict[str, str]:
        """文件名（不含后缀）→ 标签，兼容模型直接把文件名当标签写出来。"""
        mapping: dict[str, str] = {}
        for tag, files in self.index.items():
            for name in files:
                stem = Path(str(name)).stem
                mapping.setdefault(stem, tag)
                mapping.setdefault(Path(str(name)).name, tag)
        return mapping

    def tags(self) -> list[str]:
        return sorted(self.index)

    def count(self) -> int:
        return sum(len(files) for files in self.index.values())

    def pick(self, tag: str, rng: random.Random | None = None) -> Path | None:
        files = [path for path in self.index.get(tag, []) if path.is_file()]
        if not files:
            return None
        usable = []
        for path in files:
            try:
                if path.stat().st_size <= MAX_IMAGE_BYTES:
                    usable.append(path)
            except OSError:
                continue
        if not usable:
            return None
        return (rng or random).choice(usable)

    def rules(self) -> str:
        """注入 system prompt 的表情规则（没有素材时返回空串）。"""
        tags = self.tags()
        if not tags:
            return ""
        return (
            "\n\n【表情包】当你想表达强烈情绪时，可以在回复末尾加一个标记，格式固定为："
            "[[表情:标签]]。可用标签：" + "、".join(tags) + "。"
            "例如：[[表情:开心]]。只能写上面列出的标签，不要写文件名、不要加 .jpg/.gif 后缀、"
            "不要用单层方括号，也不要在正文里解释或提到这个标记。"
            "一条回复最多一个；没有合适的就不加，不要每句都加。"
        )


class StickerService:
    """表情包总入口：配置、素材、概率、频控与编码。"""

    def __init__(self, config, logger_: logging.Logger | None = None) -> None:
        data_dir = getattr(config, "data_dir", None) or Path("data")
        self.enabled = bool(getattr(config, "sticker_enabled", False))
        try:
            probability = float(getattr(config, "sticker_probability", 0.25))
        except (TypeError, ValueError):
            probability = 0.25
        self.probability = max(0.0, min(1.0, probability))
        try:
            cooldown = float(getattr(config, "sticker_cooldown", 180))
        except (TypeError, ValueError):
            cooldown = 180.0
        try:
            max_per_day = int(getattr(config, "sticker_max_per_day", 30))
        except (TypeError, ValueError):
            max_per_day = 30
        self.limiter = RateLimiter(cooldown, max_per_day)
        self.store = StickerStore(Path(data_dir) / "stickers")
        self.logger = logger_ or logger
        self._rng = random.Random()
        self.last_reason = ""

    @property
    def rules_prompt(self) -> str:
        if not self.enabled:
            return ""
        return self.store.rules()

    def new_filter(self) -> TagFilter:
        return TagFilter(self.classify)

    def classify(self, inner: str) -> tuple[bool, str | None]:
        """判断方括号里的内容是不是表情标记。

        返回（是否标记, 标签）。标签能对上就返回标签；对不上但长得像
        （写了文件名/后缀）也算标记——只是不发图，避免把假标记漏给用户。
        """
        text = (inner or "").strip()
        if not text:
            return False, None
        stripped = SINGLE_TAG.sub(r"\1", text).strip()
        if stripped in self.store.tag_set():
            return True, stripped
        if IMAGE_SUFFIX_RE.search(stripped):
            stem = Path(stripped).stem
            tag = self.store.stem_lookup().get(stem) or self.store.stem_lookup().get(stripped)
            return True, tag
        tag = self.store.stem_lookup().get(stripped)
        if tag:
            return True, tag
        return False, None

    def parse(self, text: str) -> tuple[str, list[str]]:
        """非流式回复：返回（剥离后文本, 标签列表），同时容忍文件名式标记。"""
        raw = text or ""
        tags = [match.group(1).strip() for match in TAG_PATTERN.finditer(raw)]
        cleaned = TAG_PATTERN.sub("", raw)

        output: list[str] = []
        index = 0
        while index < len(cleaned):
            if cleaned[index] != "[":
                output.append(cleaned[index])
                index += 1
                continue
            end = cleaned.find("]", index + 1)
            if end < 0 or end - index > 48:
                output.append(cleaned[index])
                index += 1
                continue
            is_tag, tag = self.classify(cleaned[index + 1 : end])
            if is_tag:
                if tag:
                    tags.append(tag)
                index = end + 1
                continue
            output.append(cleaned[index])
            index += 1
        return "".join(output).strip(), tags

    def choose(self, user_id: str, tags: list[str], force: bool = False) -> str | None:
        """选一张表情并编码为 base64。

        force=True：用户明确索要表情包，跳过概率闸门；即使模型没给标签，
        也随机挑一个标签发送，保证「要了就一定有」。
        """
        self.last_reason = ""
        if not self.enabled:
            self.last_reason = "disabled"
            return None
        if not tags and force:
            pool = sorted(self.store.tag_set())
            if pool:
                tags = [self._rng.choice(pool)]
        if not tags:
            self.last_reason = "no_tags"
            return None
        if not force and self._rng.random() > self.probability:
            self.last_reason = "probability"
            return None
        if not self.limiter.allow(str(user_id)):
            self.last_reason = "cooldown_or_daily_cap"
            return None
        candidates = [tag for tag in tags if self.store.index.get(tag)]
        if not candidates:
            self.last_reason = "no_matching_tag"
            return None
        tag = self._rng.choice(candidates)
        path = self.store.pick(tag, self._rng)
        if path is None:
            self.last_reason = "pick_failed"
            return None
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.logger.warning("读取表情失败：%s", exc)
            self.last_reason = "read_failed"
            return None
        if not data or len(data) > MAX_IMAGE_BYTES:
            self.last_reason = "too_large"
            return None
        self.limiter.mark(str(user_id))
        self.last_reason = "ok"
        self.logger.info("[表情] 命中标签「%s」→ %s（%.1f KB）", tag, path.name, len(data) / 1024)
        return "base64://" + base64.b64encode(data).decode("ascii")
