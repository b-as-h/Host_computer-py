# -*- coding: utf-8 -*-
"""AT 快捷指令的数据模型：默认表、校验、增删改、行尾策略。

纯 Python，不 import tkinter，可无界面单测。

行尾策略（每条指令独立设置，默认跟随全局 AT 开关）
    auto  跟随「AT 自动回车」开关
    crlf  固定追加 \\r\\n
    cr    固定追加 \\r（个别模块只认单个回车）
    none  原样发送

这样既能满足「AT 发送自动追加回车的独立选项」，又保留了单条指令的例外能力，
同时不会和全局「追加 CRLF」互相叠加导致发出 \\r\\n\\r\\n。
"""

import copy

MAX_ITEMS = 12          # 超过两行按钮就没有意义了，管理弹窗里提示
NAME_MAX = 6            # 按钮文字上限，中英文都按字符数算
CMD_MAX = 64

EOL_AUTO = "auto"
EOL_CRLF = "crlf"
EOL_CR = "cr"
EOL_NONE = "none"
EOL_ORDER = (EOL_AUTO, EOL_CRLF, EOL_CR, EOL_NONE)
EOL_LABELS = {
    EOL_AUTO: "跟随开关",
    EOL_CRLF: "\\r\\n",
    EOL_CR: "\\r",
    EOL_NONE: "不加",
}

# HC-05 / HC-06 在 AT 模式下的常用查询指令，正好 6 条铺满一行。
DEFAULTS = [
    {"name": "AT", "cmd": "AT", "eol": EOL_AUTO},
    {"name": "版本", "cmd": "AT+VERSION?", "eol": EOL_AUTO},
    {"name": "名称", "cmd": "AT+NAME?", "eol": EOL_AUTO},
    {"name": "地址", "cmd": "AT+ADDR?", "eol": EOL_AUTO},
    {"name": "波特率", "cmd": "AT+BAUD?", "eol": EOL_AUTO},
    {"name": "复位", "cmd": "AT+RESET", "eol": EOL_AUTO},
]


def default_items():
    return copy.deepcopy(DEFAULTS)


def _derive_name(cmd):
    """名称留空时从指令里猜一个短标签：AT+VERSION? -> VERSION。"""
    text = cmd.strip()
    if text.upper().startswith("AT"):
        text = text[2:]
    text = text.lstrip("+")
    for sep in ("?", "=", ",", " "):
        idx = text.find(sep)
        if idx > 0:
            text = text[:idx]
            break
    return (text or cmd)[:NAME_MAX]


def sanitize_item(raw):
    """把一条外部数据（可能来自手改的 JSON）修正成合法结构，非法返回 None。"""
    if not isinstance(raw, dict):
        return None
    cmd = str(raw.get("cmd", "")).strip()
    if not cmd:
        return None
    name = str(raw.get("name", "")).strip()
    name = name.replace("\r", " ").replace("\n", " ").strip()
    if not name:
        name = _derive_name(cmd)
    eol = raw.get("eol", EOL_AUTO)
    if eol not in EOL_LABELS:
        eol = EOL_AUTO
    return {"name": name[:NAME_MAX], "cmd": cmd[:CMD_MAX], "eol": eol}


def normalize(items):
    """把读回来的列表清洗成可用列表。

    ``items`` 为 None（没存过）时给默认表；为 ``[]``（用户主动删空）时保持空，
    否则用户永远删不干净。
    """
    if items is None:
        return default_items()
    if not isinstance(items, (list, tuple)):
        return default_items()

    out = []
    seen = set()
    for raw in items:
        item = sanitize_item(raw)
        if item is None:
            continue
        key = item["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return out


def validate(name, cmd, items=None, skip_index=None):
    """校验一条待保存的指令，返回 (ok, 提示文字)。"""
    name = (name or "").strip()
    cmd = (cmd or "").strip()

    if not cmd:
        return False, "指令内容不能为空"
    if len(cmd) > CMD_MAX:
        return False, "指令最长 %d 个字符" % CMD_MAX

    for ch in cmd:
        if not (32 <= ord(ch) < 127):
            return False, "指令只能包含半角 ASCII 字符（发现 %r）" % ch

    if not name:
        return False, "名称不能为空（会显示在按钮上）"
    if len(name) > NAME_MAX:
        return False, "名称最长 %d 个字符" % NAME_MAX

    if items:
        low = name.lower()
        for i, item in enumerate(items):
            if i == skip_index:
                continue
            if item.get("name", "").lower() == low:
                return False, "名称「%s」已经存在" % name
    return True, ""


def build_payload(cmd, eol=EOL_AUTO, at_crlf=True):
    """按行尾策略拼出真正要写进串口的字符串。

    ``at_crlf`` 是「AT 自动回车」开关的当前状态，只影响 ``auto`` 策略。
    先 strip 再拼，避免用户自己写了 \\r\\n 又叠加一份。
    """
    text = (cmd or "").strip()
    eol = eol if eol in EOL_LABELS else EOL_AUTO
    if eol == EOL_AUTO:
        eol = EOL_CRLF if at_crlf else EOL_NONE
    tail = {EOL_CRLF: "\r\n", EOL_CR: "\r", EOL_NONE: ""}.get(eol, "\r\n")
    return text + tail


def add_item(items, name, cmd, eol=EOL_AUTO):
    """追加一条；成功返回 (True, "")，失败返回 (False, 原因)，items 不变。"""
    if len(items) >= MAX_ITEMS:
        return False, "最多只能有 %d 条快捷指令" % MAX_ITEMS
    ok, msg = validate(name, cmd, items)
    if not ok:
        return False, msg
    items.append({"name": name.strip(), "cmd": cmd.strip(),
                  "eol": eol if eol in EOL_LABELS else EOL_AUTO})
    return True, ""


def update_item(items, index, name, cmd, eol=EOL_AUTO):
    if not (0 <= index < len(items)):
        return False, "选中项已失效"
    ok, msg = validate(name, cmd, items, skip_index=index)
    if not ok:
        return False, msg
    items[index] = {"name": name.strip(), "cmd": cmd.strip(),
                    "eol": eol if eol in EOL_LABELS else EOL_AUTO}
    return True, ""


def remove_item(items, index):
    if not (0 <= index < len(items)):
        return False, "选中项已失效"
    items.pop(index)
    return True, ""


def move_item(items, index, delta):
    target = index + delta
    if not (0 <= index < len(items)) or not (0 <= target < len(items)):
        return False
    items[index], items[target] = items[target], items[index]
    return True


def restore_defaults(items):
    """就地恢复默认表，返回新列表。"""
    items[:] = default_items()
    return items


if __name__ == "__main__":       # pragma: no cover - 手工自测
    demo = default_items()
    print("默认条目:", [i["name"] for i in demo])
    print("auto + 开关开:", repr(build_payload("AT", EOL_AUTO, True)))
    print("auto + 开关关:", repr(build_payload("AT", EOL_AUTO, False)))
    print("固定 cr:", repr(build_payload("AT+NAME=X", EOL_CR, True)))
    print("校验非 ASCII:", validate("测试", "AT+名字", demo))
