"""远程模式：通过系统 OpenSSH（ssh.exe）+ 密钥认证管理云服务器上的 im-bridge。

设计取舍
--------
不使用 paramiko，而是调用 Windows 自带的 ssh.exe。原因：
  1. PyInstaller 打包 exe 不需要新增任何第三方依赖；
  2. Windows 10/11 默认已装 OpenSSH 客户端；
  3. 密钥认证后与本地 subprocess 用法几乎一致，日志流可直接读 stdout。

所有密码都不落盘：认证靠 ~/.ssh 之外的项目内私钥 `.tools/deploy/id_im_bridge`
（该目录已被 .gitignore 忽略）。
"""
from __future__ import annotations

import base64
import collections
import json
import os
import queue
import shlex
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import config_store
from bot_manager import _detect_level
from config_store import ROOT

SERVICE = "im-bridge"
CONTAINER = "napcat"
MAX_LINES = 2000
MAX_TEXT = 4000
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ENV_FILE = ROOT / ".tools" / "deploy" / "deploy.env"
KEY_FILE = ROOT / ".tools" / "deploy" / "id_im_bridge"
CACHE_DIR = ROOT / ".tools" / "deploy" / "remote-cache"
HOSTS_FILE = ROOT / "data" / "hosts.json"
PRICING_FILE = ROOT / "data" / "pricing.json"

CONFIG_FILES = (".env", "config.json", "config.secret.json")

# 单价（元 / 百万 token）。默认取 DeepSeek V3.2 公开价，仅作估算，可在界面改。
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 2.0, "output": 3.0, "cached": 0.2},
    "deepseek-reasoner": {"input": 4.0, "output": 16.0, "cached": 1.0},
    "__default__": {"input": 2.0, "output": 3.0, "cached": 0.2},
}


class RemoteError(RuntimeError):
    """远程操作失败。"""


# ---------------------------------------------------------------- 连接参数
def parse_json_output(output: str) -> dict:
    """SSH stderr may follow stdout; find the CLI response independently of logs."""
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    raise RemoteError("服务器没有返回有效的操作结果")


def _read_env_file() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            cfg[key.strip()] = value.strip()
    return cfg


# ---------------------------------------------------------------- 主机档案
# v4 起「连接设置」不再是只读表单，而是可增删改的主机档案（hosts.json）。
# 优先级：当前选中主机 > launcher_settings > deploy.env（后者只作首次建档的种子）。
def _make_host(
    host: str = "",
    port: Any = 22,
    user: str = "root",
    key: str = "",
    directory: str = "/opt/im-bridge",
    name: str = "",
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:8],
        "name": (name or host or "未命名主机").strip(),
        "host": str(host or "").strip(),
        "port": int(port or 22),
        "user": str(user or "root").strip(),
        "key": str(key or "").strip(),
        "dir": str(directory or "/opt/im-bridge").strip(),
    }


def load_hosts() -> dict[str, Any]:
    """读取主机档案；文件不存在时用既有配置自动建一条，避免升级后连接丢失。"""
    if HOSTS_FILE.is_file():
        try:
            data = json.loads(HOSTS_FILE.read_text("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("hosts"), list):
                data.setdefault("active", "")
                if data["hosts"]:
                    return data
        except (OSError, ValueError):
            pass


    env = _read_env_file()
    settings = config_store.load_settings()
    seed = _make_host(
        host=str(env.get("SRV_HOST") or settings.get("remote_host") or ""),
        port=env.get("SRV_PORT") or settings.get("remote_port") or 22,
        user=str(env.get("SRV_USER") or settings.get("remote_user") or "root"),
        key=str(env.get("SRV_KEY") or settings.get("remote_key") or ""),
        directory=str(env.get("REMOTE_DIR") or settings.get("remote_path") or "/opt/im-bridge"),
        name="默认主机",
    )
    data = {"active": seed["id"], "hosts": [seed]}
    save_hosts(data)
    return data


def save_hosts(data: dict[str, Any]) -> dict[str, Any]:
    HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOSTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def list_hosts() -> list[dict[str, Any]]:
    data = load_hosts()
    active = data.get("active")
    return [{**host, "active": host.get("id") == active} for host in data.get("hosts", [])]


def active_host() -> dict[str, Any]:
    data = load_hosts()
    hosts = data.get("hosts") or []
    if not hosts:
        return {}
    for host in hosts:
        if host.get("id") == data.get("active"):
            return host
    return hosts[0]


def upsert_host(payload: dict[str, Any]) -> dict[str, Any]:
    """新建或更新一台主机；返回落盘后的档案。"""
    data = load_hosts()
    hosts = data.setdefault("hosts", [])
    host_id = str(payload.get("id") or "").strip()
    fields = {
        "name": str(payload.get("name") or "").strip(),
        "host": str(payload.get("host") or "").strip(),
        "port": int(payload.get("port") or 22),
        "user": str(payload.get("user") or "root").strip(),
        "key": str(payload.get("key") or "").strip(),
        "dir": str(payload.get("dir") or "/opt/im-bridge").strip(),
    }
    if not fields["name"]:
        fields["name"] = fields["host"] or "未命名主机"

    if host_id:
        for host in hosts:
            if host.get("id") == host_id:
                host.update(fields)
                save_hosts(data)
                return host
    created = {"id": host_id or uuid.uuid4().hex[:8], **fields}
    hosts.append(created)
    if len(hosts) == 1 or not data.get("active"):
        data["active"] = created["id"]
    save_hosts(data)
    return created


def delete_host(host_id: str) -> dict[str, Any]:
    data = load_hosts()
    hosts = [host for host in data.get("hosts", []) if host.get("id") != host_id]
    if not hosts:
        raise RemoteError("至少要保留一台主机")
    data["hosts"] = hosts
    if data.get("active") == host_id:
        data["active"] = hosts[0]["id"]
    return save_hosts(data)


def set_active_host(host_id: str) -> dict[str, Any]:
    data = load_hosts()
    if not any(host.get("id") == host_id for host in data.get("hosts", [])):
        raise RemoteError(f"主机不存在：{host_id}")
    data["active"] = host_id
    return save_hosts(data)


def read_deploy_env() -> dict[str, str]:
    """返回当前生效的连接参数（界面可编辑，deploy.env 仅作兜底）。"""
    host = active_host()
    env = _read_env_file()
    return {
        "host": str(host.get("host") or env.get("SRV_HOST") or ""),
        "port": str(host.get("port") or env.get("SRV_PORT") or 22),
        "user": str(host.get("user") or env.get("SRV_USER") or "root"),
        "dir": str(host.get("dir") or env.get("REMOTE_DIR") or "/opt/im-bridge"),
        "key": str(host.get("key") or env.get("SRV_KEY") or ""),
    }


def ssh_exe() -> str:
    found = shutil.which("ssh")
    if found:
        return found
    for candidate in (
        r"C:\Windows\System32\OpenSSH\ssh.exe",
        r"C:\Program Files\OpenSSH\ssh.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    raise RemoteError("未找到 ssh.exe，请确认 Windows 已安装 OpenSSH 客户端")


def _base_cmd(cfg: dict[str, str] | None = None) -> list[str]:
    cfg = dict(cfg or read_deploy_env())
    key_path = Path(cfg["key"]).expanduser() if cfg.get("key") else KEY_FILE
    if not cfg["host"]:
        raise RemoteError("未配置服务器地址：请在「连接」页填写地址与私钥路径")
    if not key_path.is_file():
        raise RemoteError(f"未找到 SSH 私钥：{key_path}")
    return [
        ssh_exe(),
        "-i", str(key_path),
        "-p", str(cfg["port"]),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        f"{cfg['user']}@{cfg['host']}",
    ]


def run(
    cmd: str,
    timeout: float = 30,
    stdin_text: str | None = None,
    cfg: dict[str, str] | None = None,
) -> tuple[int, str]:
    """在服务器上执行命令，返回 (退出码, 输出)。"""
    try:
        proc = subprocess.run(
            _base_cmd(cfg) + [cmd],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteError(f"远程命令超时（{timeout}s）：{cmd}") from exc
    except OSError as exc:
        raise RemoteError(f"无法执行 ssh：{exc}") from exc
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def run_checked(cmd: str, timeout: float = 30) -> str:
    code, out = run(cmd, timeout=timeout)
    if code != 0 and "Warning: Permanently added" not in out:
        raise RemoteError(out or f"远程命令失败（退出码 {code}）")
    return out


def _friendly_ssh_error(text: str) -> str:
    """把 ssh 的英文报错翻译成能照着做的中文。"""
    raw = (text or "").strip()
    low = raw.lower()
    if not raw:
        return "SSH 无输出（可能是网络中断）"
    if "permission denied" in low:
        return "认证被拒绝：私钥与服务端 authorized_keys 不匹配（确认用的是同一把钥匙）"
    if "no such file" in low and "identity" in low:
        return "私钥文件不存在或路径写错"
    if "connection refused" in low:
        return "端口拒绝连接：确认 SSH 端口（默认 22）与服务器防火墙"
    if "timed out" in low or "timeout" in low:
        return "连接超时：服务器不可达 / 安全组未放行 / IP 被 fail2ban 封禁"
    if "connection reset" in low:
        return "连接被重置：可能触发 fail2ban 或中间设备拦截"
    if "host key verification failed" in low:
        return "主机指纹校验失败：known_hosts 里记录的指纹与服务器不一致"
    if "could not resolve hostname" in low:
        return "域名解析失败：检查服务器地址是否写错"
    lines = raw.splitlines()
    return lines[-1][:180] if lines else "未知错误"


def test_connection(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """连通性自检。``override`` 用于「还没保存就点测试」的场景。"""
    info = dict(read_deploy_env())
    if override:
        info.update({k: str(v) for k, v in override.items() if k in ("host", "port", "user", "dir", "key")})
    try:
        code, out = run("echo ok; hostname; whoami", timeout=15, cfg=info)
        ok = code == 0 and "ok" in out
        lines = [line for line in out.splitlines() if line.strip()]
        return {
            "ok": ok,
            "host": info["host"],
            "dir": info["dir"],
            "detail": (" · ".join(lines[-2:]) if ok and lines else ""),
            "error": "" if ok else _friendly_ssh_error(out),
        }
    except RemoteError as exc:
        return {
            "ok": False,
            "host": info["host"],
            "dir": info["dir"],
            "detail": "",
            "error": _friendly_ssh_error(str(exc)),
        }


def diagnose(override: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """逐项体检，任一项失败不影响后续项。

    返回 ``[{key, label, ok, level, detail}]``，level 为 ``error`` / ``warn``。
    """
    cfg = dict(read_deploy_env())
    if override:
        cfg.update({k: str(v) for k, v in override.items() if k in ("host", "port", "user", "dir", "key")})
    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, ok: bool, detail: str, level: str = "error") -> None:
        checks.append({"key": key, "label": label, "ok": ok, "level": level, "detail": detail})

    # 1) 本机 ssh.exe
    try:
        add("ssh", "OpenSSH 客户端", True, ssh_exe())
    except RemoteError as exc:
        add("ssh", "OpenSSH 客户端", False, str(exc))

    # 2) 私钥
    key_path = Path(cfg["key"]).expanduser() if cfg.get("key") else KEY_FILE
    if key_path.is_file():
        size = key_path.stat().st_size
        add("key", "SSH 私钥", size > 0, f"{key_path.name}（{size} 字节）", "warn" if size == 0 else "error")
    else:
        add("key", "SSH 私钥", False, f"文件不存在：{key_path}")

    # 3) 地址
    add("host", "服务器地址", bool(cfg["host"]), cfg["host"] or "未填写")

    # 4) TCP 可达（不依赖 ssh，能区分「网络不通」和「认证失败」）
    if cfg["host"]:
        try:
            with socket.create_connection((cfg["host"], int(cfg["port"])), timeout=5):
                add("tcp", f"TCP {cfg['port']} 可达", True, f"{cfg['host']}:{cfg['port']}")
        except OSError as exc:
            add("tcp", f"TCP {cfg['port']} 可达", False, f"{type(exc).__name__}：{exc}")

    # 5) SSH 认证 + 远端环境（一条命令拿齐，少开连接）
    directory = shlex.quote(cfg["dir"])
    probe = (
        f'echo "DIR=$(test -d {directory} && echo ok || echo no)"; '
        f'echo "PY=$(test -x {directory}/.venv/bin/python && echo ok || echo no)"; '
        f'echo "UNIT=$(systemctl list-unit-files | grep -c im-bridge)"; '
        f'echo "ACT=$(systemctl is-active {SERVICE} 2>/dev/null)"; '
        f"echo \"DOCKER=$(command -v docker >/dev/null && docker inspect -f '{{{{.State.Status}}}}' {CONTAINER} 2>/dev/null || echo none)\""
    )
    try:
        code, out = run(probe, timeout=25, cfg=cfg)
        if code != 0 and "DIR=" not in out:
            add("auth", "SSH 认证", False, _friendly_ssh_error(out))
        else:
            add("auth", "SSH 认证", True, "密钥登录成功")
            values: dict[str, str] = {}
            for line in out.splitlines():
                if "=" in line:
                    name, _, value = line.partition("=")
                    values[name.strip()] = value.strip()
            add("dir", "远端项目目录", values.get("DIR") == "ok", cfg["dir"])
            add("python", "远端虚拟环境", values.get("PY") == "ok", f"{cfg['dir']}/.venv/bin/python", "warn")
            unit_count = values.get("UNIT", "0")
            add("unit", "systemd 单元", unit_count not in ("", "0"), f"匹配到 {unit_count} 个 im-bridge 单元")
            act = values.get("ACT", "") or "unknown"
            add("service", "bot 服务状态", act == "active", f"systemctl is-active = {act}", "warn")
            docker = values.get("DOCKER", "")
            add("napcat", "NapCat 容器", docker == "running", f"docker 状态 = {docker or 'unknown'}", "warn")
    except RemoteError as exc:
        add("auth", "SSH 认证", False, _friendly_ssh_error(str(exc)))

    return checks


# ---------------------------------------------------------------- 用量
def load_pricing() -> dict[str, dict[str, float]]:
    pricing = {key: dict(value) for key, value in DEFAULT_PRICING.items()}
    if PRICING_FILE.is_file():
        try:
            data = json.loads(PRICING_FILE.read_text("utf-8"))
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        pricing[str(key)] = {
                            "input": float(value.get("input", 0) or 0),
                            "output": float(value.get("output", 0) or 0),
                            "cached": float(value.get("cached", 0) or 0),
                        }
        except (OSError, ValueError, TypeError):
            pass
    return pricing


def save_pricing(pricing: dict[str, dict[str, float]]) -> None:
    PRICING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRICING_FILE.write_text(json.dumps(pricing, ensure_ascii=False, indent=2), "utf-8")


def price_of(pricing: dict[str, dict[str, float]], model: str) -> dict[str, float]:
    return pricing.get(model) or pricing.get("__default__") or DEFAULT_PRICING["__default__"]


def cost_of(
    pricing: dict[str, dict[str, float]],
    model: str,
    prompt: float,
    completion: float,
    cached: float = 0,
) -> float:
    """按「元 / 百万 token」估算花费；cached 走缓存命中价，其余走输入价。"""
    price = price_of(pricing, model)
    hit = min(float(cached or 0), float(prompt or 0))
    miss = max(0.0, float(prompt or 0) - hit)
    return (
        hit * price["cached"] + miss * price["input"] + float(completion or 0) * price["output"]
    ) / 1_000_000


def read_usage() -> dict[str, Any]:
    """从服务器读回 usage/summary.json；读不到就返回 available=False（不是错误）。"""
    info = read_deploy_env()
    path = f"{info['dir']}/data/usage/summary.json"
    try:
        code, out = run(f"cat {shlex.quote(path)} 2>/dev/null", timeout=20)
    except RemoteError as exc:
        return {"available": False, "reason": str(exc)}
    text = (out or "").strip()
    if not text.startswith("{"):
        return {
            "available": False,
            "reason": "服务器上还没有用量数据（需要先部署带 usage 记账的版本）",
            "path": path,
        }
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {"available": False, "reason": f"summary.json 解析失败：{exc}", "path": path}
    if not isinstance(data, dict):
        return {"available": False, "reason": "summary.json 结构异常", "path": path}
    data["available"] = True
    data["path"] = path
    return data


# ---------------------------------------------------------------- 主机指标（只读）
_METRIC_CACHE: tuple[float, dict[str, Any]] | None = None
_METRIC_TTL = 5.0


def _parse_metrics(out: str) -> dict[str, Any]:
    """解析 MEMINFO / DISKINFO / S1 / S2 等原始行（不做 shell 端 awk，避免转义地狱）。"""
    parts: dict[str, str] = {}
    for line in (out or "").splitlines():
        key, _, value = line.partition(" ")
        if key in ("S1", "S2", "MEMINFO", "DISKINFO", "CORES", "UP", "MEM", "DISK"):
            parts[key] = value.strip()

    def _cpu(line: str | None) -> tuple[int, int] | None:
        if not line:
            return None
        try:
            values = [int(v) for v in line.split()[1:9]]
        except (ValueError, IndexError):
            return None
        if len(values) < 5:
            return None
        return sum(values), values[3] + values[4]

    first, second = _cpu(parts.get("S1")), _cpu(parts.get("S2"))
    cpu_pct: float | None = None
    if first and second:
        total = second[0] - first[0]
        idle = second[1] - first[1]
        if total > 0:
            cpu_pct = max(0.0, min(100.0, (total - idle) / total * 100.0))

    def _free_pair() -> tuple[float, float] | None:
        raw = parts.get("MEMINFO")
        if not raw:
            return None
        pieces = raw.split()
        # Mem: total used free shared buff/cache available
        if len(pieces) < 3:
            return None
        try:
            return float(pieces[2]), float(pieces[1])
        except ValueError:
            return None

    def _disk_pair() -> tuple[float, float] | None:
        raw = parts.get("DISKINFO")
        if not raw:
            return None
        pieces = raw.split()
        # Filesystem 1M-blocks Used Available Use% Mounted
        if len(pieces) < 3:
            return None
        try:
            return float(pieces[2]), float(pieces[1])
        except ValueError:
            return None

    mem, disk = _free_pair(), _disk_pair()
    return {
        "ok": cpu_pct is not None or mem is not None,
        "cpu_pct": cpu_pct,
        "mem_used_mb": mem[0] if mem else None,
        "mem_total_mb": mem[1] if mem else None,
        "mem_pct": (mem[0] / mem[1] * 100.0) if mem and mem[1] else None,
        "disk_used_mb": disk[0] if disk else None,
        "disk_total_mb": disk[1] if disk else None,
        "disk_pct": (disk[0] / disk[1] * 100.0) if disk and disk[1] else None,
        "cores": int(parts["CORES"]) if parts.get("CORES", "").isdigit() else None,
        "host_uptime": float(parts["UP"]) if parts.get("UP", "").isdigit() else None,
    }


def _metric_cmd() -> str:
    return (
        "echo \"S1 $(grep '^cpu ' /proc/stat)\"; sleep 0.25; "
        "echo \"S2 $(grep '^cpu ' /proc/stat)\"; "
        "echo \"MEMINFO $(free -m | sed -n '2p')\"; "
        "echo \"DISKINFO $(df -m / | sed -n '2p')\"; "
        "echo \"CORES $(nproc)\"; echo \"UP $(cut -d. -f1 /proc/uptime)\""
    )


def system_metrics(force: bool = False) -> dict[str, Any]:
    """读取服务器 CPU / 内存 / 磁盘占用（纯只读，5 秒缓存）。"""
    global _METRIC_CACHE
    now = time.time()
    if not force and _METRIC_CACHE and now - _METRIC_CACHE[0] < _METRIC_TTL:
        return dict(_METRIC_CACHE[1])

    try:
        _code, out = run(_metric_cmd(), timeout=20)
    except RemoteError as exc:
        return {"ok": False, "error": str(exc)}

    result = _parse_metrics(out)
    _METRIC_CACHE = (now, dict(result))
    return result


# ---------------------------------------------------------------- 配置同步
def pull_config() -> list[str]:
    """把服务器上的 .env / config.json 拉到本地缓存，返回成功拉取的文件名。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    remote_dir = read_deploy_env()["dir"]
    pulled: list[str] = []
    for name in CONFIG_FILES:
        code, out = run(f"base64 -w0 {remote_dir}/{name} 2>/dev/null", timeout=20)
        target = CACHE_DIR / name
        if code == 0 and out:
            try:
                target.write_bytes(base64.b64decode(out))
                pulled.append(name)
                continue
            except (ValueError, OSError):
                pass
        if target.exists():
            target.unlink()
    return pulled


def push_config() -> list[str]:
    """把本地缓存里的配置文件推回服务器，返回推送的文件名。"""
    remote_dir = read_deploy_env()["dir"]
    pushed: list[str] = []
    for name in CONFIG_FILES:
        local = CACHE_DIR / name
        if not local.is_file():
            continue
        data = base64.b64encode(local.read_bytes()).decode("ascii")
        code, out = run(f"base64 -d > {remote_dir}/{name} && chmod 600 {remote_dir}/{name}", timeout=25, stdin_text=data)
        if code != 0:
            raise RemoteError(f"推送 {name} 失败：{out}")
        pushed.append(name)
    return pushed


def activate_config_cache() -> list[str]:
    """把 config_store 的文件路径切到远程缓存，并拉取一次。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config_store.ENV_PATH = CACHE_DIR / ".env"
    config_store.CONFIG_PATH = CACHE_DIR / "config.json"
    config_store.SECRET_PATH = CACHE_DIR / "config.secret.json"
    return pull_config()


# ---------------------------------------------------------------- Bot 管理
class RemoteBotManager:
    """与本地 BotManager 暴露完全相同的接口，但操作的是服务器的 systemd 服务。"""

    def __init__(self, instance_id: str = "default") -> None:
        self.instance_id = instance_id or "default"
        # default 沿用 im-bridge.service；其余实例走模板单元 im-bridge@<id>.service
        self.service = (
            SERVICE if self.instance_id == "default" else f"{SERVICE}@{self.instance_id}"
        )
        self._lock = threading.RLock()
        self._lines: collections.deque[dict[str, Any]] = collections.deque(maxlen=MAX_LINES)
        self._subscribers: set[queue.Queue] = set()
        self._sub_lock = threading.Lock()
        self._tail_proc: subprocess.Popen | None = None
        self._tail_stop = threading.Event()
        self._auto_restart = bool(config_store.load_settings().get("auto_restart", False))
        self._status_cache: tuple[float, dict[str, Any]] | None = None
        threading.Thread(target=self._tail_loop, daemon=True, name="remote-log-tail").start()

    # ---- 日志 ----
    def _append(self, text: str, source: str = "bot", level: str | None = None) -> None:
        text = (text or "").rstrip()
        if not text or text.startswith("Warning: Permanently added"):
            return
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + " …(截断)"
        line = {
            "ts": time.time(),
            "level": level or _detect_level(text),
            "text": text,
            "source": source,
        }
        with self._lock:
            self._lines.append(line)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(line)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(line)
                except (queue.Empty, queue.Full):
                    pass

    def log(self, text: str, level: str = "INFO") -> None:
        self._append(text, source="manager", level=level)

    def snapshot(self, tail: int = 400) -> list[dict[str, Any]]:
        with self._lock:
            lines = list(self._lines)
        return lines[-max(1, tail):]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
        self.log("日志已清空（仅清空管理台缓冲区，服务器 journal 不受影响）", "INFO")

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=1000)
        with self._sub_lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._sub_lock:
            self._subscribers.discard(subscriber)

    def _tail_loop(self) -> None:
        """长驻 ssh + journalctl -f，把服务器日志实时灌进缓冲区。"""
        first = True
        while not self._tail_stop.is_set():
            try:
                proc = subprocess.Popen(
                    _base_cmd() + [f"journalctl -u {self.service} -f -n 0 --no-pager -o short-iso"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, RemoteError) as exc:
                self.log(f"日志流启动失败：{exc}", "ERROR")
                time.sleep(15)
                continue

            self._tail_proc = proc
            if first:
                self.log("已连接服务器日志流（journalctl -f）", "INFO")
                self._load_recent(200)
                first = False
            try:
                for raw in proc.stdout or []:
                    if self._tail_stop.is_set():
                        break
                    self._append(raw.rstrip("\r\n"), source="bot")
            except (OSError, ValueError):
                pass
            finally:
                self._tail_proc = None
                try:
                    proc.terminate()
                except OSError:
                    pass
            if not self._tail_stop.is_set():
                self.log("日志流断开，5 秒后重连", "WARN")
                time.sleep(5)

    def _load_recent(self, lines: int) -> None:
        try:
            code, out = run(f"journalctl -u {self.service} -n {lines} --no-pager -o short-iso", timeout=25)
        except RemoteError:
            return
        if code != 0:
            return
        for row in out.splitlines():
            self._append(row, source="bot")

    def stop_tail(self) -> None:
        self._tail_stop.set()
        proc = self._tail_proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    # ---- 状态 ----
    def is_running(self) -> bool:
        return bool(self.status().get("running"))

    def host_snapshot(self, force: bool = False) -> dict[str, Any]:
        """一次 SSH 往返同时取服务状态与主机指标（首页用）。

        注意：不要叫 snapshot —— 类里已有同名的日志快照方法。
        """
        now = time.time()
        cached = getattr(self, "_host_cache", None)
        if not force and cached and now - cached[0] < 3.0:
            return dict(cached[1])

        cmd = (
            f"state=$(systemctl is-active {self.service} 2>/dev/null); "
            f"enabled=$(systemctl is-enabled {self.service} 2>/dev/null); "
            f"pid=$(systemctl show -p MainPID --value {self.service} 2>/dev/null); "
            f"up=$(ps -o etimes= -p $pid 2>/dev/null | tr -d ' '); "
            'echo "$state|$enabled|$pid|$up"; '
            + _metric_cmd()
        )
        try:
            _code, out = run(cmd, timeout=25)
        except RemoteError as exc:
            return {
                "status": {
                    "running": False, "pid": None, "uptime": 0.0,
                    "service": "unknown", "auto_restart": self._auto_restart,
                    "error": str(exc),
                },
                "metrics": {"ok": False, "error": str(exc)},
            }

        lines = (out or "").splitlines()
        head = lines[0] if lines else "|"
        parts = head.split("|")
        while len(parts) < 4:
            parts.append("")
        state, enabled, pid, uptime = parts[0], parts[1], parts[2], parts[3]
        running = state.strip() == "active"
        status = {
            "running": running,
            "pid": int(pid) if pid.strip().isdigit() else None,
            "started_at": (time.time() - int(uptime)) if (running and uptime.strip().isdigit()) else None,
            "uptime": float(uptime) if uptime.strip().isdigit() else 0.0,
            "exit_code": None,
            "auto_restart": self._auto_restart,
            "python": "/opt/im-bridge/.venv/bin/python",
            "mode": "remote",
            "service": state.strip() or "unknown",
            "enabled": enabled.strip() == "enabled",
        }
        metrics = _parse_metrics("\n".join(lines[1:]))
        result = {"status": status, "metrics": metrics}
        self._host_cache = (now, dict(result))
        return result

    def status(self, force: bool = False) -> dict[str, Any]:
        """Return the flat BotManager status contract; metrics belong to host_snapshot."""
        return dict(self.host_snapshot(force=force)["status"])

    def set_auto_restart(self, enabled: bool) -> None:
        with self._lock:
            self._auto_restart = bool(enabled)
        settings = config_store.load_settings()
        settings["auto_restart"] = bool(enabled)
        config_store.save_settings(settings)
        self.log(
            f"自动重启已{'开启' if enabled else '关闭'}"
            "（远程模式由 systemd Restart=always 兜底，此项仅作记录）",
            "INFO",
        )

    # ---- 生命周期 ----
    def _systemctl(self, action: str, timeout: float = 60) -> None:
        code, out = run(f"systemctl {action} {self.service} 2>&1", timeout=timeout)
        if code != 0:
            _, tail = run(f"journalctl -u {self.service} -n 20 --no-pager -o short-iso", timeout=20)
            raise RemoteError(f"{action} 失败：{out}\n{tail[-800:]}")

    def start(self) -> dict[str, Any]:
        if self.is_running():
            return {"already": True, **self.status(force=True)}
        self.log("正在启动服务器上的 bot（systemctl start）…", "INFO")
        self._systemctl("start")
        time.sleep(1.2)
        st = self.status(force=True)
        if not st["running"]:
            raise RemoteError("启动后服务未进入 active 状态，请查看日志")
        self.log("bot 已启动", "INFO")
        return {"already": False, **st}

    def stop(self) -> dict[str, Any]:
        if not self.is_running():
            return {"stopped": False, "reason": "not_running"}
        self.log("正在停止服务器上的 bot（systemctl stop）…", "WARN")
        self._systemctl("stop")
        time.sleep(0.6)
        self.log("bot 已停止", "INFO")
        return {"stopped": True}

    def restart(self) -> dict[str, Any]:
        self.log("正在重启服务器上的 bot（systemctl restart）…", "INFO")
        self._systemctl("restart")
        time.sleep(1.2)
        st = self.status(force=True)
        if not st["running"]:
            raise RemoteError("重启后服务未进入 active 状态，请查看日志")
        self.log("bot 已重启", "INFO")
        return {"already": False, **st}


# ---------------------------------------------------------------- NapCat
def napcat_status() -> dict[str, Any]:
    try:
        code, out = run(
            f"docker inspect -f '{{{{.State.Status}}}}|{{{{.State.StartedAt}}}}' {CONTAINER} 2>/dev/null",
            timeout=15,
        )
    except RemoteError as exc:
        return {"ok": False, "running": False, "error": str(exc)}
    if code != 0 or "|" not in out:
        return {"ok": True, "running": False, "detail": "容器不存在或未运行"}
    state, started = out.splitlines()[-1].split("|", 1)
    return {"ok": True, "running": state.strip() == "running", "state": state.strip(), "started_at": started.strip()}


def napcat_action(action: str) -> dict[str, Any]:
    if action not in ("start", "stop", "restart"):
        raise RemoteError(f"不支持的操作：{action}")
    code, out = run(f"docker {action} {CONTAINER} 2>&1", timeout=90)
    if code != 0:
        raise RemoteError(f"NapCat {action} 失败：{out}")
    time.sleep(1.0)
    return {"ok": True, **napcat_status()}


def napcat_update() -> dict[str, Any]:
    """拉取 NapCat 当前镜像的最新版本并重启容器。

    只做「pull + restart」，不动挂载目录，账号与配置保留。失败抛 ``RemoteError``。
    """
    code, out = run(
        f"docker inspect -f '{{{{.Config.Image}}}}' {CONTAINER} 2>/dev/null", timeout=15
    )
    image = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    if not image:
        raise RemoteError("读不到 NapCat 镜像名，容器可能不存在")

    code, out = run(f"docker pull {image} 2>&1", timeout=600)
    if code != 0:
        raise RemoteError(f"拉取 {image} 失败：{out[-200:]}")
    updated = "Image is up to date" not in out and "已是最新" not in out

    code, out = run(f"docker restart {CONTAINER} 2>&1", timeout=120)
    if code != 0:
        raise RemoteError(f"重启容器失败：{out[-200:]}")
    time.sleep(1.0)
    return {"ok": True, "image": image, "updated": updated, **napcat_status()}


def read_secrets() -> dict[str, Any]:
    """读取服务器 .env 里的密钥掩码（api_key / asr / tts）。"""
    data = _cli("secrets")
    if not data.get("ok"):
        raise RemoteError(data.get("error") or "读取密钥失败")
    return data.get("secrets", {})


def tts_probe(instance_id: str = "default") -> dict[str, Any]:
    """在服务器上真的合成一个「好」字，用来判断 TTS 链路通不通。

    本地语音走隧道时，这一步能同时验证「隧道是否活着」和「本机模型是否在跑」。
    """
    return _cli("tts-probe", str(instance_id), timeout=75)


def write_secret(key: str, value: str) -> dict[str, Any]:
    """把密钥写进服务器 .env；密钥走 stdin，不留在命令行历史里。"""
    code, out = run(
        f"{REMOTE_PYTHON} {CLI_PATH} secret-stdin {shlex.quote(str(key))}",
        timeout=45,
        stdin_text=str(value),
    )
    line = (out.strip().splitlines() or [""])[-1]
    try:
        data = json.loads(line)
    except ValueError as exc:
        raise RemoteError(f"写入密钥失败：{out.strip()[:200] or '无输出'}") from exc
    if not data.get("ok"):
        raise RemoteError(data.get("error") or "写入密钥失败")
    return data.get("secrets", {})


# ---------------------------------------------------------------- 探活与自检
def onebot_status() -> dict[str, Any]:
    values = config_store.resolved_values()
    url = str(values.get("napcat_ws_url") or "ws://127.0.0.1:3001")
    port = 3001
    try:
        from urllib.parse import urlparse
        port = urlparse(url).port or 3001
    except ValueError:
        pass
    try:
        code, out = run(f"ss -lnt 2>/dev/null | grep -c ':{port} '", timeout=12)
        reachable = code == 0 and out.strip().isdigit() and int(out.strip()) > 0
    except RemoteError:
        reachable = False
    return {"reachable": reachable, "host": read_deploy_env()["host"], "port": port, "url": url}


def env_checks(refresh: bool = False) -> list[dict[str, Any]]:
    remote_dir = read_deploy_env()["dir"]

    def probe(cmd: str, timeout: float = 15) -> tuple[bool, str]:
        try:
            code, out = run(cmd, timeout=timeout)
            return code == 0, out
        except RemoteError as exc:
            return False, str(exc)

    conn = test_connection()
    checks: list[dict[str, Any]] = [
        {
            "id": "ssh",
            "title": "SSH 连接",
            "status": "ok" if conn["ok"] else "fail",
            "detail": f"{conn['host']}" + (f"（{conn['detail']}）" if conn.get("detail") else ""),
            "hint": "" if conn["ok"] else conn.get("error", ""),
        }
    ]
    if not conn["ok"]:
        checks.append({
            "id": "project", "title": "项目文件", "status": "skip",
            "detail": "SSH 不通，跳过后续检查", "hint": "",
        })
        return checks

    ok, out = probe(f"test -f {remote_dir}/run.py && echo yes")
    checks.append({
        "id": "project", "title": "项目文件",
        "status": "ok" if "yes" in out else "fail",
        "detail": remote_dir,
        "hint": "" if "yes" in out else "服务器上缺少 run.py，请先部署",
    })

    ok, out = probe(f"{remote_dir}/.venv/bin/python --version 2>&1")
    checks.append({
        "id": "python", "title": "Python 运行时",
        "status": "ok" if ok and "Python" in out else "fail",
        "detail": out.splitlines()[0] if out else "未找到 .venv",
        "hint": "" if ok else "请在服务器重建 venv",
    })

    ok, out = probe(f"{remote_dir}/.venv/bin/pip list 2>/dev/null | grep -ciE 'httpx|websockets'")
    cnt = int(out) if out.strip().isdigit() else 0
    checks.append({
        "id": "deps", "title": "Python 依赖",
        "status": "ok" if cnt >= 2 else "fail",
        "detail": f"找到 {cnt} / 2 个依赖（httpx、websockets）",
        "hint": "" if cnt >= 2 else "运行 deploy.py 重新安装依赖",
    })

    try:
        code, out = run(f"systemctl is-active {SERVICE}; systemctl is-enabled {SERVICE}", timeout=12)
        lines = out.splitlines()
        active = lines[0].strip() if lines else ""
        enabled = lines[1].strip() if len(lines) > 1 else ""
        checks.append({
            "id": "service", "title": f"服务 {SERVICE}",
            "status": "ok" if active == "active" else "fail",
            "detail": f"{active} / 开机自启 {enabled}",
            "hint": "" if active == "active" else "在总览页点「启动」",
        })
    except RemoteError as exc:
        checks.append({"id": "service", "title": f"服务 {SERVICE}", "status": "fail", "detail": str(exc), "hint": ""})

    ob = onebot_status()
    checks.append({
        "id": "onebot_port", "title": "OneBot 连接",
        "status": "ok" if ob["reachable"] else "fail",
        "detail": f"{ob['url']} {'可连通' if ob['reachable'] else '不可达'}",
        "hint": "" if ob["reachable"] else "确认 NapCat 容器在运行且已配置 3001 端口",
    })

    nc = napcat_status()
    checks.append({
        "id": "napcat", "title": "NapCat",
        "status": "ok" if nc.get("running") else "fail",
        "detail": f"容器状态：{nc.get('state', '未知')}",
        "hint": "" if nc.get("running") else "在 NapCat 页点「启动容器」",
    })

    checks.append({
        "id": "edge", "title": "部署工具",
        "status": "ok" if (ROOT / ".tools" / "deploy" / "deploy.py").is_file() else "warn",
        "detail": ".tools/deploy/deploy.py 可用" if (ROOT / ".tools" / "deploy" / "deploy.py").is_file() else "未找到部署脚本",
        "hint": "",
    })
    return checks


# --------------------------------------------------------------------------
# 实例管理：通过 SSH 调用服务端的 instance_cli.py
# --------------------------------------------------------------------------
CLI_PATH = "/opt/im-bridge/launcher/instance_cli.py"
REMOTE_PYTHON = "/opt/im-bridge/.venv/bin/python"


def _cli(*args: str, timeout: float = 30) -> dict[str, Any]:
    """执行远端实例 CLI，解析最后一行 JSON。"""
    quoted = " ".join(shlex.quote(str(item)) for item in args)
    code, out = run(f"{REMOTE_PYTHON} {CLI_PATH} {quoted}", timeout=timeout)
    line = (out.strip().splitlines() or [""])[-1]
    try:
        return json.loads(line)
    except ValueError as exc:
        raise RemoteError(f"实例命令返回异常：{out.strip()[:200] or '无输出'}") from exc


class RemoteInstanceStore:
    """实例注册表的远端视图（校验与写盘都在服务端完成）。"""

    def list(self) -> list[dict[str, Any]]:
        data = _cli("list")
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "读取实例列表失败")
        return data.get("instances", [])

    def get(self, instance_id: str) -> dict[str, Any]:
        data = _cli("get", instance_id)
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "实例不存在")
        return data["instance"]

    def create(
        self,
        instance_id: str,
        name: str = "",
        bot_qq: str = "",
        persona: str = "",
        onebot_ws_url: str = "",
    ) -> dict[str, Any]:
        payload = {
            "id": instance_id,
            "name": name,
            "bot_qq": bot_qq,
            "persona": persona,
            "onebot_ws_url": onebot_ws_url,
        }
        data = _cli("create", json.dumps(payload, ensure_ascii=False), timeout=45)
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "创建实例失败")
        return data["instance"]

    def update(self, instance_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = _cli(
            "update", instance_id, json.dumps(payload, ensure_ascii=False), timeout=45
        )
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "更新实例失败")
        return data["instance"]

    def delete(self, instance_id: str, keep_backup: bool = True) -> dict[str, Any]:
        args = ["delete", instance_id]
        if keep_backup:
            args.append("--keep-backup")
        data = _cli(*args, timeout=60)
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "删除实例失败")
        return data

    def read_config(self, instance_id: str) -> dict[str, Any]:
        data = _cli("config", instance_id)
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "读取实例配置失败")
        return data.get("config", {})

    def write_config(self, instance_id: str, payload: dict[str, Any]) -> None:
        data = _cli(
            "save", instance_id, json.dumps(payload, ensure_ascii=False), timeout=45
        )
        if not data.get("ok"):
            raise RemoteError(data.get("error") or "保存实例配置失败")


class RemoteInstanceManager:
    """把 RemoteBotManager 按实例聚合，接口对齐本地 InstanceManager。"""

    def __init__(self) -> None:
        self.store = RemoteInstanceStore()
        self._managers: dict[str, RemoteBotManager] = {}
        self._lock = threading.RLock()

    def manager(self, instance_id: str) -> RemoteBotManager:
        with self._lock:
            manager = self._managers.get(instance_id)
            if manager is None:
                manager = RemoteBotManager(instance_id)
                self._managers[instance_id] = manager
            return manager

    def list_instances(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for instance in self.store.list():
            try:
                status = self.manager(instance["id"]).status()
            except Exception as exc:  # noqa: BLE001
                status = {"running": False, "state": "unknown", "error": str(exc)}
            running = bool(status.get("running"))
            items.append(
                {
                    **instance,
                    "status": {
                        **status,
                        "state": "running" if running else "stopped",
                    },
                }
            )
        return items

    def start(self, instance_id: str) -> dict[str, Any]:
        return self.manager(instance_id).start()

    def stop(self, instance_id: str) -> dict[str, Any]:
        return self.manager(instance_id).stop()

    def restart(self, instance_id: str) -> dict[str, Any]:
        return self.manager(instance_id).restart()
