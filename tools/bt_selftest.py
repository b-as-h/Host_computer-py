#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蓝牙链路自检：不需要开发板，先把环境和软件层排干净。

分五段，每段都能独立下结论：

    1. 环境      解释器、tkinter、pyserial、btlink 是否就位
    2. 适配器    本机蓝牙是否可用，拿得到名字和地址
    3. 纯函数    MAC 转换、hwid 解析等不依赖硬件的逻辑
    4. 设备枚举  缓存扫描（可选 --inquiry 真扫，约 5 秒）
    5. 串口盘点  当前 COM 口、哪些是蓝牙 SPP 口

刻意**不做**配对和删配对：那是真实操作，应该在图形界面里、由人确认后
执行。脚本只读不写，随时可以重复跑。

用法：
    python tools/bt_selftest.py
    python tools/bt_selftest.py --inquiry      # 真扫附近设备
    python tools/bt_selftest.py --json out.json
"""

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import btlink  # noqa: E402


class Checker(object):
    """和 selftest.py 同样的输出风格，方便对照看。"""

    def __init__(self):
        self.ok = 0
        self.fail = 0
        self.warn = 0
        self.failures = []
        self.rows = []

    def check(self, desc, cond, detail=""):
        if cond:
            self.ok += 1
            print("  OK   %s" % desc)
        else:
            self.fail += 1
            self.failures.append(desc)
            print("  FAIL %s  %s" % (desc, detail))
        self.rows.append({"desc": desc, "ok": bool(cond), "detail": str(detail)})
        return bool(cond)

    def notice(self, desc, detail=""):
        self.warn += 1
        print("  WARN %s  %s" % (desc, detail))
        self.rows.append({"desc": desc, "ok": None, "detail": str(detail)})

    def info(self, text):
        print("       %s" % text)

    def summary(self):
        print("\n结果: %d 通过 / %d 失败 / %d 提示"
              % (self.ok, self.fail, self.warn))
        for f in self.failures:
            print("  失败项: %s" % f)
        return 1 if self.fail else 0


def stage_env(c):
    print("[1/5] 环境")
    c.info("Python %s (%d 位) · %s"
           % (platform.python_version(), 64 if sys.maxsize > 2 ** 32 else 32,
              platform.platform()))
    c.check("pyserial 可用", importlib.util.find_spec("serial") is not None,
            "装一下：pip install pyserial")
    c.check("tkinter 可用（主程序需要）",
            importlib.util.find_spec("tkinter") is not None,
            "解释器没带 tcl/tk，换一个 Python 或重装时勾上 tcl/tk")
    c.check("btlink 已加载", btlink.AVAILABLE,
            btlink.IMPORT_ERROR or "Windows 蓝牙组件缺失")
    c.info("运行目录: %s" % ROOT)


def stage_radio(c):
    print("\n[2/5] 蓝牙适配器")
    if not btlink.AVAILABLE:
        c.notice("跳过：btlink 不可用")
        return None
    info = btlink.radio_info()
    if info is None:
        c.check("存在可用蓝牙适配器", False,
                "本机没有蓝牙，或蓝牙开关没开 / 适配器被禁用")
        return None
    c.check("存在可用蓝牙适配器", True)
    c.info("名称: %s" % info["name"])
    c.info("地址: %s" % info["mac"])
    if "VIRTUAL" in info["name"].upper() or "虚拟" in info["name"]:
        c.notice("这像是虚拟蓝牙适配器，实际连模块可能失败")
    return info


def stage_pure(c):
    print("\n[3/5] 纯函数（不碰硬件）")
    val = btlink.mac_to_ulonglong("68:62:8A:A0:5B:A0")
    c.check("MAC 转整数", val == 0x68628AA05BA0, val and hex(val))
    c.check("整数转回 MAC", btlink.ulonglong_to_mac(val) == "68:62:8A:A0:5B:A0",
            btlink.ulonglong_to_mac(val) if val else val)
    c.check("非法 MAC 被拒", btlink.mac_to_ulonglong("ZZ:11:22:33:44:55") is None)
    c.check("GUID 尾巴不被当成 MAC",
            btlink.mac_from_hwid(
                r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}"
                r"_LOCALMFG&0000\7&2A0A8B1&0&000000000000_00000000") == "")
    c.check("从 hwid 提取 MAC",
            btlink.mac_from_hwid(
                r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}"
                r"_LOCALMFG&0002\7&1A2B3C4D&0&001122334455_C00000000")
            == "00:11:22:33:44:55")


def stage_devices(c, inquiry):
    print("\n[4/5] 设备枚举%s" % ("（主动扫描，约 5 秒）" if inquiry else "（读系统缓存）"))
    if not btlink.AVAILABLE:
        c.notice("跳过：btlink 不可用")
        return []
    devs = btlink.scan(inquiry=inquiry, seconds=6)
    c.check("枚举调用未抛异常", True)
    c.info("共 %d 个设备" % len(devs))
    if not devs:
        c.notice("一个设备都没有",
                 "确认蓝牙已开；配对过的设备应该至少能看到几条")
        return devs

    print("       %-22s %-18s %-8s %s" % ("名称", "地址", "状态", "类型"))
    for d in devs:
        state = "已连接" if d.connected else ("已配对" if d.paired else "未配对")
        mark = " *" if d.looks_like_spp else ""
        print("       %-22s %-18s %-8s %s%s"
              % (d.name[:22], d.mac, state, d.kind, mark))
    if any(d.looks_like_spp for d in devs):
        c.info("* 标记的是名字像串口模块的设备")
    else:
        c.notice("没看到像串口模块的设备",
                 "HC-05 未上电是正常的；上电后点「扫描附近」再试")
    return devs


def stage_ports(c, devs):
    print("\n[5/5] 串口盘点")
    try:
        from serial.tools import list_ports
    except ImportError:
        c.notice("跳过：没装 pyserial")
        return

    ports = list(list_ports.comports())
    bt_ports = btlink.bluetooth_ports()
    c.info("当前系统报告 %d 个可用串口" % len(ports))
    if not ports:
        c.notice("没有串口", "USB-TTL 或开发板没插")
    for p in ports:
        mac = btlink.mac_from_hwid(p.hwid or "")
        kind = "蓝牙" if ("BTHENUM" in (p.hwid or "").upper() or mac) else "USB/主板"
        print("       %-6s %-40s %s%s"
              % (p.device, (p.description or "")[:40], kind,
                 (" " + mac) if mac else ""))

    if bt_ports:
        c.check("存在蓝牙 SPP 口", True)
        for dev, desc, mac in bt_ports:
            c.info("%s ← %s %s" % (dev, desc, mac or "(MAC 未知)"))
        c.info("下一步：在程序里选这个口，点「连接设备」，日志应出现 +PONG")
    else:
        c.notice("当前没有蓝牙 SPP 口",
                 "正常现象：还没配对，或配的是耳机/手机这类非串口设备")

    # 已配对设备的 SPP 能力矩阵，直接告诉用户「哪个能当串口用」
    if btlink.AVAILABLE and devs:
        print("\n       已配对设备的串口能力：")
        any_spp = False
        for d in devs:
            if not d.paired:
                continue
            has = btlink.installed_spp(d.mac)
            any_spp = any_spp or has
            print("       %-22s %s" % (d.name[:22],
                                       "支持 SPP" if has else "不支持 SPP"))
        if not any_spp:
            c.info("没有任何设备支持 SPP —— 蓝牙耳机/手机属于正常情况")


def main():
    parser = argparse.ArgumentParser(description="STM32 上位机蓝牙链路自检")
    parser.add_argument("--inquiry", action="store_true",
                        help="真发一次查询扫描（约 5 秒），能发现未配对设备")
    parser.add_argument("--json", metavar="PATH", help="把结果写成 JSON")
    args = parser.parse_args()

    print("STM32 Host · 蓝牙链路自检")
    print("=" * 52)
    c = Checker()

    stage_env(c)
    stage_radio(c)
    stage_pure(c)
    devs = stage_devices(c, args.inquiry)
    stage_ports(c, devs)

    code = c.summary()

    print("\n提示：本脚本只读不写，不会配对或删除任何设备。")
    print("      配对流程请在上位机界面点「蓝牙」→「配对并连接」完成。")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"results": c.rows, "ok": c.ok, "fail": c.fail},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("      结果已写入: %s" % args.json)

    return code


if __name__ == "__main__":
    sys.exit(main())
