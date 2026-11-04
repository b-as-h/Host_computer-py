# -*- coding: utf-8 -*-
"""Windows 蓝牙 SPP 链路：扫描 → 配对 → 启用串口服务 → 定位 COM 口。

设计要点
--------
1. 只依赖标准库（ctypes），不引入 pybluez / bleak。后者在 Windows + 新
   Python 上要么装不上，要么只覆盖 BLE；而本项目主用 HC-05 / HC-06 这类
   经典蓝牙，走系统自带的 BluetoothAPIs 最省事，也和 appconfig / atcmd /
   sysinfo 的「零第三方依赖」风格一致。
2. 配对函数在 ``bthprops.cpl`` 里导出，不在 ``BluetoothAPIs.dll``：
   ``BluetoothAuthenticateDevice`` / ``...Ex`` / ``BluetoothDisplayDeviceProperties``
   都属于 bthprops，只加载 BluetoothAPIs 会全部 AttributeError。
3. 配对是一次**阻塞调用**（HC-05 常见 3~15 秒），所有对外函数都不加锁，
   由调用方放进后台线程。UI 层统一用 ``root.after`` 回主线程。
4. 任何一步失败都返回 ``(False, 中文原因)``，不抛异常。蓝牙这种事天生
   受驱动、固件、配对状态影响，抛异常只会让上层到处写 try。
5. 无蓝牙适配器、非 Windows、DLL 缺失时模块仍可 import：``AVAILABLE``
   为 False，所有函数安静返回空值，自测脚本照常能跑。

配对后 COM 口的定位
-------------------
Windows 给 SPP 分配的 COM 号事先不可知，所以用**端口差分**：
配对前记一份端口集合，配对并启用 SPP 后轮询，新冒出来的蓝牙口就是它。
同时用 hwid 里的 12 位十六进制段反查 MAC 做交叉验证 ——
``BTHENUM\\{00001101-...}_LOCALMFG&0002\\7&1A2B3C4D&0&001122334455_C00000000``
末尾那段 ``001122334455`` 就是设备地址。

用法
----
>>> import btlink
>>> btlink.AVAILABLE
True
>>> btlink.radio_info()["name"]        # 本机蓝牙名
'DESKTOP-INSB124'
>>> [d.name for d in btlink.scan(inquiry=False)]
['bash', 'vivo TWS 4']
>>> btlink.pair("00:11:22:33:44:55", "1234")
(True, '配对成功')
>>> btlink.enable_spp("00:11:22:33:44:55")
(True, '串口服务已启用')
"""

import ctypes
import re
import sys
import time
import uuid
from ctypes import wintypes

# ---------------------------------------------------------------- 常量

SPP_GUID = uuid.UUID("{00001101-0000-1000-8000-00805F9B34FB}")
BLUETOOTH_SERVICE_ENABLE = 0x00000001
BLUETOOTH_MAX_NAME_SIZE = 248

# 常见的串口透传模块名关键字，用来在列表里给用户一点提示
_SPP_HINTS = ("hc-0", "hc05", "hc06", "hc-05", "hc-06", "jdy", "at-09",
              "hm-10", "bt-", "esp32", "spp", "serial")

_MAJOR_KIND = {
    0x00: "未分类", 0x01: "电脑", 0x02: "手机", 0x03: "网络",
    0x04: "音频", 0x05: "外设", 0x06: "成像", 0x07: "穿戴",
    0x08: "玩具", 0x09: "医疗",
}


# ---------------------------------------------------------------- DLL

AVAILABLE = False
IMPORT_ERROR = ""
_API = None       # BluetoothAPIs.dll
_PROPS = None     # bthprops.cpl

try:
    if sys.platform == "win32":
        _API = ctypes.WinDLL("BluetoothAPIs.dll")
        _PROPS = ctypes.WinDLL("bthprops.cpl")
        AVAILABLE = True
    else:
        IMPORT_ERROR = "仅支持 Windows"
except OSError as _exc:      # pragma: no cover - 缺少蓝牙组件的老系统
    IMPORT_ERROR = str(_exc)


# ---------------------------------------------------------------- 结构体

class SYSTEMTIME(ctypes.Structure):
    _fields_ = [("wYear", wintypes.WORD), ("wMonth", wintypes.WORD),
                ("wDayOfWeek", wintypes.WORD), ("wDay", wintypes.WORD),
                ("wHour", wintypes.WORD), ("wMinute", wintypes.WORD),
                ("wSecond", wintypes.WORD), ("wMilliseconds", wintypes.WORD)]


class BLUETOOTH_ADDRESS(ctypes.Union):
    """8 字节地址。rgBytes[0] 是最低字节，即 MAC 的最后一段。"""
    _fields_ = [("ullLong", ctypes.c_ulonglong),
                ("rgBytes", ctypes.c_ubyte * 6)]


class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("Address", BLUETOOTH_ADDRESS),
                ("ulClassofDevice", wintypes.ULONG),
                ("fConnected", wintypes.BOOL),
                ("fRemembered", wintypes.BOOL),
                ("fAuthenticated", wintypes.BOOL),
                ("stLastSeen", SYSTEMTIME),
                ("stLastUsed", SYSTEMTIME),
                ("szName", wintypes.WCHAR * BLUETOOTH_MAX_NAME_SIZE)]


class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("fReturnAuthenticated", wintypes.BOOL),
                ("fReturnRemembered", wintypes.BOOL),
                ("fReturnUnknown", wintypes.BOOL),
                ("fReturnConnected", wintypes.BOOL),
                ("fIssueInquiry", wintypes.BOOL),
                ("cTimeoutMultiplier", ctypes.c_ubyte),
                ("hRadio", wintypes.HANDLE)]


class BLUETOOTH_FIND_RADIO_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD)]


class BLUETOOTH_RADIO_INFO(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("address", BLUETOOTH_ADDRESS),
                ("szName", wintypes.WCHAR * BLUETOOTH_MAX_NAME_SIZE),
                ("ulClassofDevice", wintypes.ULONG),
                ("lmpSubversion", wintypes.USHORT),
                ("manufacturer", wintypes.USHORT)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


if AVAILABLE:
    PDEV = ctypes.POINTER(BLUETOOTH_DEVICE_INFO)
    PRADIO_INFO = ctypes.POINTER(BLUETOOTH_RADIO_INFO)
    PDWORD = ctypes.POINTER(wintypes.DWORD)

    _API.BluetoothFindFirstRadio.argtypes = [
        ctypes.POINTER(BLUETOOTH_FIND_RADIO_PARAMS), ctypes.POINTER(wintypes.HANDLE)]
    _API.BluetoothFindFirstRadio.restype = wintypes.HANDLE
    _API.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
    _API.BluetoothFindRadioClose.restype = wintypes.BOOL
    _API.BluetoothGetRadioInfo.argtypes = [wintypes.HANDLE, PRADIO_INFO]
    _API.BluetoothGetRadioInfo.restype = wintypes.DWORD

    _API.BluetoothFindFirstDevice.argtypes = [
        ctypes.POINTER(BLUETOOTH_DEVICE_SEARCH_PARAMS), PDEV]
    _API.BluetoothFindFirstDevice.restype = wintypes.HANDLE
    _API.BluetoothFindNextDevice.argtypes = [wintypes.HANDLE, PDEV]
    _API.BluetoothFindNextDevice.restype = wintypes.BOOL
    _API.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]
    _API.BluetoothFindDeviceClose.restype = wintypes.BOOL

    _API.BluetoothGetDeviceInfo.argtypes = [wintypes.HANDLE, PDEV]
    _API.BluetoothGetDeviceInfo.restype = wintypes.DWORD
    _API.BluetoothSetServiceState.argtypes = [
        wintypes.HANDLE, PDEV, ctypes.POINTER(GUID), wintypes.DWORD]
    _API.BluetoothSetServiceState.restype = wintypes.DWORD
    _API.BluetoothEnumerateInstalledServices.argtypes = [
        wintypes.HANDLE, PDEV, PDWORD, ctypes.POINTER(GUID)]
    _API.BluetoothEnumerateInstalledServices.restype = wintypes.DWORD
    _API.BluetoothRemoveDevice.argtypes = [ctypes.POINTER(BLUETOOTH_ADDRESS)]
    _API.BluetoothRemoveDevice.restype = wintypes.DWORD

    _PROPS.BluetoothAuthenticateDevice.argtypes = [
        wintypes.HWND, wintypes.HANDLE, PDEV, wintypes.LPWSTR, wintypes.ULONG]
    _PROPS.BluetoothAuthenticateDevice.restype = wintypes.DWORD
    _PROPS.BluetoothAuthenticateDeviceEx.argtypes = [
        wintypes.HWND, wintypes.HANDLE, PDEV, ctypes.c_void_p, ctypes.c_int]
    _PROPS.BluetoothAuthenticateDeviceEx.restype = wintypes.DWORD


# ---------------------------------------------------------------- 错误信息

_WINERR = {
    0: "成功",
    5: "拒绝访问，请用管理员身份试一次，或在系统设置里手动配对",
    87: "参数无效（设备地址可能已失效，重新扫描再试）",
    1168: "找不到该设备（可能已关机或超出范围）",
    1223: "用户取消了配对",
    1236: "设备已被其他主机占用",
}


def _err(code):
    """把 Win32 错误码翻成能看懂的中文。"""
    if code in _WINERR:
        return "%s（%d）" % (_WINERR[code], code)
    try:
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.FormatMessageW(
            0x00001000, None, code, 0, buf, 512, None)
        if n:
            txt = buf.value.strip().rstrip("\r\n").rstrip("。.")
            return "%s（%d）" % (txt, code)
    except Exception:      # noqa: BLE001 - 取错误描述失败不该盖住原始错误
        pass
    return "错误码 %d" % code


# ---------------------------------------------------------------- 地址工具

def mac_to_ulonglong(mac):
    """'68:62:8A:A0:5B:A0' -> ullLong；非法输入返回 None。"""
    parts = re.split(r"[:\-]", mac.strip())
    if len(parts) != 6:
        return None
    try:
        b = [int(p, 16) for p in parts]
    except ValueError:
        return None
    if any(x < 0 or x > 0xFF for x in b):
        return None
    val = 0
    for i, byte in enumerate(b):        # 第一个字节是最高位
        val |= byte << (8 * (5 - i))
    return val


def ulonglong_to_mac(value):
    b = [(value >> (8 * i)) & 0xFF for i in range(6)]   # b[0] = 最低字节
    return ":".join("%02X" % x for x in reversed(b))


def _addr_of(mac):
    addr = BLUETOOTH_ADDRESS()
    val = mac_to_ulonglong(mac)
    if val is None:
        return None
    addr.ullLong = val
    return addr


_MAC12 = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
_BRACED = re.compile(r"\{[^}]*\}")
_BTH_ADDR = re.compile(r"&0&([0-9A-Fa-f]{12})(?:_|$)")


def _fmt_mac12(raw):
    return ":".join(raw[i:i + 2].upper() for i in range(0, 12, 2))


def mac_from_hwid(hwid):
    """从 BTHENUM 的实例 ID 里抠出 MAC；抠不到返回空串。

    Windows 的蓝牙端口 hwid 形如::

        BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\\
            7&1A2B3C4D&0&001122334455_C00000000

    末尾 ``001122334455`` 是设备地址。

    坑：直接扫 12 位 hex 会把 GUID 尾巴抓出来 —— ``{00001101-0000-1000-
    8000-00805F9B34FB}`` 末尾的 ``00805F9B34FB`` 也长得像 MAC，而且它
    不会命中「全 0 过滤」。所以先把 ``{...}`` 整段剔除，再按 BTHENUM 的
    ``&0&<12hex>_`` 位置精确匹配，最后才用宽松扫描兜底。
    """
    if not hwid:
        return ""
    text = _BRACED.sub("", hwid.upper())     # 去掉 {GUID}，避开假 MAC

    hit = _BTH_ADDR.search(text)
    if hit and set(hit.group(1)) != {"0"}:
        return _fmt_mac12(hit.group(1))

    for raw in reversed(_MAC12.findall(text)):
        if set(raw) == {"0"}:                # 全 0 是「未分配」，不是有效地址
            continue
        return _fmt_mac12(raw)
    return ""


# ---------------------------------------------------------------- 设备对象

class Device(object):
    """一次扫描结果。字段都取自 BLUETOOTH_DEVICE_INFO，不做额外推断。"""

    __slots__ = ("name", "mac", "paired", "remembered", "connected",
                 "cod", "kind")

    def __init__(self, name, mac, paired, remembered, connected, cod):
        self.name = name or "(无名称)"
        self.mac = mac
        self.paired = paired
        self.remembered = remembered
        self.connected = connected
        self.cod = cod
        self.kind = _MAJOR_KIND.get((cod >> 8) & 0x1F, "未分类")

    @property
    def looks_like_spp(self):
        low = self.name.lower()
        return any(k in low for k in _SPP_HINTS)

    def __repr__(self):
        return "<Device %s %s paired=%s>" % (self.mac, self.name, self.paired)


# ---------------------------------------------------------------- radio

def _radio_handle():
    """打开本机第一个蓝牙适配器，返回 (hRadio, close_fn)；无适配器返回 (None, None)。"""
    if not AVAILABLE:
        return None, None
    params = BLUETOOTH_FIND_RADIO_PARAMS()
    params.dwSize = ctypes.sizeof(BLUETOOTH_FIND_RADIO_PARAMS)
    h_radio = wintypes.HANDLE()
    h_find = _API.BluetoothFindFirstRadio(ctypes.byref(params),
                                          ctypes.byref(h_radio))
    if not h_find:
        return None, None
    _API.BluetoothFindRadioClose(h_find)      # find 上下文用完即关，radio 句柄保留

    def closer():
        try:
            ctypes.windll.kernel32.CloseHandle(h_radio)
        except Exception:      # noqa: BLE001
            pass

    return h_radio, closer


def radio_info():
    """本机蓝牙适配器信息；无适配器返回 None。"""
    h, close = _radio_handle()
    if h is None:
        return None
    try:
        info = BLUETOOTH_RADIO_INFO()
        info.dwSize = ctypes.sizeof(BLUETOOTH_RADIO_INFO)
        if _API.BluetoothGetRadioInfo(h, ctypes.byref(info)) != 0:
            return None
        return {"name": info.szName,
                "mac": ulonglong_to_mac(info.address.ullLong),
                "manufacturer": info.manufacturer}
    finally:
        close()


def has_radio():
    """本机是否有可用蓝牙适配器（不抛异常）。"""
    return radio_info() is not None


# ---------------------------------------------------------------- 扫描

def _device_info(mac, h_radio=None):
    """按 MAC 取 BLUETOOTH_DEVICE_INFO；取不到返回 None。"""
    addr = _addr_of(mac)
    if addr is None:
        return None
    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
    info.Address = addr
    own = h_radio is None
    if own:
        h_radio, close = _radio_handle()
        if h_radio is None:
            return None
    try:
        if _API.BluetoothGetDeviceInfo(h_radio, ctypes.byref(info)) != 0:
            return None
        return info
    finally:
        if own:
            close()


def _enum(h_radio, inquiry, mult):
    params = BLUETOOTH_DEVICE_SEARCH_PARAMS()
    params.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_SEARCH_PARAMS)
    params.fReturnAuthenticated = True
    params.fReturnRemembered = True
    params.fReturnUnknown = True
    params.fReturnConnected = True
    params.fIssueInquiry = bool(inquiry)
    params.cTimeoutMultiplier = max(1, min(48, int(mult)))
    params.hRadio = h_radio

    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
    handle = _API.BluetoothFindFirstDevice(ctypes.byref(params),
                                           ctypes.byref(info))
    if not handle:
        return []

    found = []
    try:
        while True:
            found.append(Device(info.szName,
                                ulonglong_to_mac(info.Address.ullLong),
                                bool(info.fAuthenticated),
                                bool(info.fRemembered),
                                bool(info.fConnected),
                                int(info.ulClassofDevice)))
            info = BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
            if not _API.BluetoothFindNextDevice(handle, ctypes.byref(info)):
                break
    finally:
        _API.BluetoothFindDeviceClose(handle)
    return found


def scan(inquiry=True, seconds=6):
    """枚举蓝牙设备。

    inquiry=False 只读系统缓存（毫秒级，只能看到配对过/附近出现过的）；
    inquiry=True 会真的发一次查询，能发现没配对过的 HC-05，代价是阻塞
    ``seconds`` 秒左右（系统按 1.28 秒一个单位收，实际还会更长一点）。

    返回按「已配对 > 像串口模块 > 名称」排好序的 Device 列表。
    """
    if not AVAILABLE:
        return []
    h, close = _radio_handle()
    if h is None:
        return []
    try:
        merged = {}
        for dev in _enum(h, False, 1):
            merged[dev.mac] = dev
        if inquiry:
            mult = max(1, int(round(float(seconds) / 1.28)))
            for dev in _enum(h, True, mult):
                old = merged.get(dev.mac)
                # 新结果信息更全时覆盖（inquiry 后才拿得到 fConnected 等）
                if old is None or (dev.paired, dev.connected) > (old.paired, old.connected):
                    merged[dev.mac] = dev
        devs = list(merged.values())
    finally:
        close()

    devs.sort(key=lambda d: (not d.paired, not d.looks_like_spp, d.name.lower()))
    return devs


# ---------------------------------------------------------------- 配对

def pair(mac, pin="1234", parent_hwnd=None):
    """配对指定设备，返回 (ok, 中文说明)。

    先用 ``BluetoothAuthenticateDevice`` 带 PIN 静默配对（HC-05 出厂
    1234 / 0000 直接过）；系统不接受这种带 PIN 的方式时，退到 Ex 版本
    让 Windows 自己弹配对窗 —— 比让用户去设置页翻菜单还是快得多。
    """
    if not AVAILABLE:
        return False, IMPORT_ERROR or "蓝牙模块不可用"
    if mac_to_ulonglong(mac) is None:
        return False, "设备地址格式不对：%s" % mac

    h, close = _radio_handle()
    if h is None:
        return False, "本机没有可用的蓝牙适配器"
    try:
        info = _device_info(mac, h)
        if info is None:
            return False, "系统里找不到这个设备，请先扫描一次"
        if info.fAuthenticated:
            return True, "已经配对过，无需重复配对"

        pin = (pin or "1234").strip() or "1234"
        rc = _PROPS.BluetoothAuthenticateDevice(
            parent_hwnd, h, ctypes.byref(info), pin, len(pin))
        if rc == 0:
            return True, "配对成功"

        # 降级：Ex + NULL 参数，交给系统弹窗输入 PIN
        rc2 = _PROPS.BluetoothAuthenticateDeviceEx(
            parent_hwnd, h, ctypes.byref(info), None, 0)
        if rc2 == 0:
            return True, "配对成功（系统配对窗口）"
        return False, "配对失败：%s" % _err(rc if rc != rc2 else rc2)
    finally:
        close()


def unpair(mac):
    """删除配对记录，返回 (ok, 说明)。"""
    if not AVAILABLE:
        return False, IMPORT_ERROR or "蓝牙模块不可用"
    addr = _addr_of(mac)
    if addr is None:
        return False, "设备地址格式不对：%s" % mac
    try:
        rc = _API.BluetoothRemoveDevice(ctypes.byref(addr))
    except Exception as exc:      # noqa: BLE001
        return False, "删除配对失败：%s" % exc
    if rc == 0:
        return True, "已删除配对记录"
    return False, "删除配对失败：%s" % _err(rc)


# ---------------------------------------------------------------- SPP

def enable_spp(mac):
    """启用设备的串口服务（GUID 00001101），启用后系统才分配 COM 口。"""
    if not AVAILABLE:
        return False, IMPORT_ERROR or "蓝牙模块不可用"
    h, close = _radio_handle()
    if h is None:
        return False, "本机没有可用的蓝牙适配器"
    try:
        info = _device_info(mac, h)
        if info is None:
            return False, "系统里找不到这个设备"
        guid = GUID()
        ctypes.memmove(ctypes.byref(guid), SPP_GUID.bytes_le, 16)
        rc = _API.BluetoothSetServiceState(
            h, ctypes.byref(info), ctypes.byref(guid), BLUETOOTH_SERVICE_ENABLE)
        if rc == 0:
            return True, "串口服务已启用"
        return False, "启用串口服务失败：%s" % _err(rc)
    finally:
        close()


def installed_spp(mac):
    """设备是否已装 SPP 服务。用来判断「这个设备到底能不能当串口用」。"""
    if not AVAILABLE:
        return False
    h, close = _radio_handle()
    if h is None:
        return False
    try:
        info = _device_info(mac, h)
        if info is None:
            return False
        count = wintypes.DWORD(16)
        buf = (GUID * 16)()
        rc = _API.BluetoothEnumerateInstalledServices(
            h, ctypes.byref(info), ctypes.byref(count), buf)
        if rc != 0:
            return False
        want = SPP_GUID.bytes_le
        for i in range(min(count.value, 16)):
            raw = ctypes.string_at(ctypes.byref(buf[i]), 16)
            if raw == want:
                return True
        return False
    finally:
        close()


# ---------------------------------------------------------------- 端口定位

def bluetooth_ports():
    """当前所有蓝牙端口，返回 [(device, description, mac)]，mac 可能为空串。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        hwid = p.hwid or ""
        desc = p.description or ""
        if "BTHENUM" in hwid.upper() or "蓝牙" in desc or "Bluetooth" in desc:
            out.append((p.device, desc, mac_from_hwid(hwid)))
    return out


def port_snapshot():
    """所有端口名集合，配对前拍一张，配对后做差分。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return set()
    return {p.device for p in list_ports.comports()}


def wait_spp_port(mac, before=None, timeout=12.0, interval=0.4):
    """等配对后的 COM 口出现，返回端口名；超时返回空串。

    ``before`` 是调用方在配对前拍的 ``port_snapshot()``。新出现的蓝牙口
    优先；找不到新的就退而按 MAC 匹配已有蓝牙口（重复配对的场景）。
    """
    before = before or set()
    deadline = time.time() + max(1.0, float(timeout))
    want = (mac or "").upper().replace("-", ":")

    while time.time() < deadline:
        ports = bluetooth_ports()
        fresh = [p for p in ports if p[0] not in before]
        if fresh:
            # 差分有多个时按 MAC 收窄
            for dev, _desc, pmac in fresh:
                if pmac and want and pmac == want:
                    return dev
            return fresh[0][0]
        if want:
            for dev, _desc, pmac in ports:
                if pmac and pmac == want:
                    return dev
        time.sleep(interval)
    return ""


def describe(dev):
    """一行人类可读描述，给日志用。"""
    state = []
    if dev.paired:
        state.append("已配对")
    if dev.connected:
        state.append("已连接")
    if not state:
        state.append("未配对")
    return "%s [%s] %s" % (dev.name, dev.mac, " / ".join(state))


if __name__ == "__main__":       # pragma: no cover - 手工自测
    print("AVAILABLE:", AVAILABLE, IMPORT_ERROR)
    print("radio:", radio_info())
    for d in scan(inquiry=False):
        print(" ", describe(d), "| kind=%s cod=0x%06X" % (d.kind, d.cod))
    print("蓝牙端口:", bluetooth_ports())
