# -*- coding: utf-8 -*-
"""配置持久化：JSON 单文件 + 原子写 + 损坏自动回退。

设计要点
--------
1. 只依赖标准库；不 import tkinter，可被无界面脚本直接单测。
2. 写入走「同目录临时文件 + os.replace」，断电或异常不会留下半截 JSON。
3. 文件损坏（手动改错、写到一半被杀）先隔离成 ``.bad``，再回落默认值，
   绝不让配置文件把程序挡在门外。
4. 目录不可写（只读介质、企业策略）时静默降级为内存态：本次运行照常读写，
   只是不落盘，调用方通过 ``save()`` 的返回值和 ``last_error`` 感知。

存储位置优先级
--------------
1. 环境变量 ``STM32HOST_CONFIG_DIR``（便携模式 / 测试用）
2. ``%APPDATA%\\STM32Host``
3. ``%LOCALAPPDATA%\\STM32Host``
4. ``~/.config/STM32Host``

不放在程序目录，是因为安装版位于 ``%LOCALAPPDATA%\\Programs``、绿色版可能放在
Program Files 或 U 盘只读区；放到用户配置目录才能保证卸载重装后配置还在。
"""

import json
import os
import threading
import time

APP_NAME = "STM32Host"
FILE_NAME = "settings.json"
SCHEMA_VERSION = 1
ENV_DIR = "STM32HOST_CONFIG_DIR"


def config_dir():
    """返回配置目录（不保证已存在）。"""
    override = os.environ.get(ENV_DIR)
    if override:
        return override
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME)


def config_path():
    return os.path.join(config_dir(), FILE_NAME)


class Config:
    """点号路径读写的小型配置容器，线程安全。"""

    def __init__(self, path=None):
        self.path = path or config_path()
        self._lock = threading.RLock()
        self._data = {}
        self.last_error = ""
        self.quarantined = ""
        self.loaded = False
        self.load()

    # ---------- 读写 ----------
    def load(self):
        """读取配置；文件不存在视为首次运行。"""
        with self._lock:
            self._data = {}
            self.loaded = False
        try:
            with open(self.path, "r", encoding="utf-8") as fp:
                raw = fp.read()
        except FileNotFoundError:
            self.loaded = True
            return True
        except OSError as exc:
            self.last_error = "读取失败: %s" % exc
            self.loaded = True
            return False

        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            self.last_error = "配置损坏: %s" % exc
            self.quarantined = self._quarantine()
            self.loaded = True
            return False

        if not isinstance(data, dict):
            self.last_error = "配置根节点不是对象，已忽略"
            self.quarantined = self._quarantine()
            self.loaded = True
            return False

        with self._lock:
            self._data = data
            self.loaded = True
            self.last_error = ""
        return True

    def get(self, key, default=None):
        """按 "ui.terminal_height" 这样的点号路径取值。"""
        node = self._data
        with self._lock:
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, key, value):
        """按点号路径写值，中间层级不存在时自动创建。"""
        parts = key.split(".")
        with self._lock:
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value

    def update(self, mapping):
        for key, value in mapping.items():
            self.set(key, value)

    def as_dict(self):
        with self._lock:
            return json.loads(json.dumps(self._data))  # 深拷贝，外部改不脏内部

    def save(self):
        """原子落盘。返回 False 时 last_error 给出原因，内存态仍然有效。"""
        with self._lock:
            payload = json.loads(json.dumps(self._data))
        payload.setdefault("schema", SCHEMA_VERSION)

        tmp = self.path + ".tmp"
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, self.path)      # 同分区替换，POSIX/NTFS 都是原子操作
            self.last_error = ""
            return True
        except OSError as exc:
            self.last_error = "写入失败: %s" % exc
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    # ---------- 内部 ----------
    def _quarantine(self):
        """把损坏文件改名保留，返回新路径；失败返回空串。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = "%s.%s.bad" % (self.path, stamp)
        try:
            os.replace(self.path, target)
            return target
        except OSError:
            return ""

    def status(self):
        """给日志用的一行状态描述。"""
        if self.last_error:
            return "配置: %s (%s)" % (self.last_error, self.path)
        return "配置: %s" % self.path


if __name__ == "__main__":       # pragma: no cover - 手工自测
    cfg = Config()
    print(cfg.status())
    cfg.set("ui.terminal_height", 260)
    cfg.set("demo.nested.value", 1)
    print("save:", cfg.save(), cfg.last_error)
    print("read back:", Config().as_dict())
