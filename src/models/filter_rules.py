"""自定义筛选规则：把「条件 + 关系」组合成图库筛选表达式。

与 Halcon DLT 的「自定义筛选规则」对齐：条件字段覆盖
名称 / 路径 / 标注状态 / 备注 / 图像标记 / 标注类别 / 数据集拆分 /
标注数量 / 宽 / 高 / 通道数；关系覆盖包含、不包含、开头、结尾、正则、
`==`、`!=`、`<`、`<=`、`>`、`>=`；条件之间用 AND / OR 组合（模型支持嵌套子组）。

规则随项目保存在 `project.params["filter_rules"]`，结构为 JSON 可序列化的树：

```python
{
    "logic": "and",                    # and / or
    "conditions": [
        {"field": "width", "op": "lt", "value": "640"},
        {"field": "tags", "op": "contains", "value": "待复核"},
        {"logic": "or", "conditions": [...]},      # 子组（可选）
    ],
}
```
"""

from __future__ import annotations

import re

# 字段：键 / 显示名 / 值类型（text / number / choice / list）
FILTER_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("name", "文件名", "text"),
    ("path", "路径", "text"),
    ("state", "标注状态", "choice"),
    ("comment", "备注", "text"),
    ("tags", "图像标记", "list"),
    ("classes", "标注类别", "list"),
    ("split", "数据集拆分", "choice"),
    ("label_count", "标注数量", "number"),
    ("width", "宽度", "number"),
    ("height", "高度", "number"),
    ("channels", "通道数", "number"),
)
FIELD_LABELS: dict[str, str] = {key: label for key, label, _kind in FILTER_FIELDS}
FIELD_KINDS: dict[str, str] = {key: kind for key, _label, kind in FILTER_FIELDS}

# 各种值类型可用的关系
_OPERATORS: dict[str, tuple[tuple[str, str], ...]] = {
    "text": (
        ("contains", "包含"), ("not_contains", "不包含"), ("starts", "开头是"),
        ("ends", "结尾是"), ("regex", "正则"), ("eq", "等于"), ("ne", "不等于"),
    ),
    "list": (("contains", "包含"), ("not_contains", "不包含"), ("eq", "等于")),
    "choice": (("eq", "是"), ("ne", "不是")),
    "number": (
        ("eq", "="), ("ne", "≠"), ("lt", "<"), ("le", "≤"),
        ("gt", ">"), ("ge", "≥"),
    ),
}
OP_LABELS: dict[str, str] = {
    op: label for ops in _OPERATORS.values() for op, label in ops
}

# 选择型字段的候选值（键, 显示名）
STATE_OPTIONS = (("annotated", "已标注"), ("unannotated", "未标注"))
SPLIT_OPTIONS = (
    ("train", "训练"), ("val", "验证"), ("test", "测试"), ("none", "未划分"),
)
_CHOICE_OPTIONS = {"state": STATE_OPTIONS, "split": SPLIT_OPTIONS}

# 需要读取图像尺寸 / 通道数的字段（按需解析，避免无谓的解码）
_META_FIELDS = frozenset({"width", "height", "channels"})

# 数值型字段（含非元信息的标注数量）
_NUMBER_FIELDS = frozenset(
    key for key, _label, kind in FILTER_FIELDS if kind == "number"
)

# 「无标签」在界面上的显示名
UNLABELED_NAME = "（无标签）"


def operators_for(field: str) -> list[tuple[str, str]]:
    """字段可用的关系列表 [(op, 显示名)]。"""
    kind = FIELD_KINDS.get(str(field), "text")
    return list(_OPERATORS.get(kind, _OPERATORS["text"]))


def choices_for(field: str) -> list[tuple[str, str]]:
    """选择型字段的候选值；非选择型返回空。"""
    return list(_CHOICE_OPTIONS.get(str(field), ()))


def default_op(field: str) -> str:
    options = operators_for(field)
    return options[0][0] if options else "contains"


def new_condition(field: str = "name", op: str = "", value: str = "") -> dict:
    """新建一条条件（关系缺省取该字段的默认关系）。"""
    return {
        "field": str(field),
        "op": str(op) or default_op(field),
        "value": str(value),
    }


def field_label(field: str) -> str:
    return FIELD_LABELS.get(str(field), str(field))


def op_label(op: str) -> str:
    return OP_LABELS.get(str(op), str(op))


def _as_number(raw) -> float | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _number_match(op: str, actual: float, wanted: float) -> bool:
    if op == "eq":
        return actual == wanted
    if op == "ne":
        return actual != wanted
    if op == "lt":
        return actual < wanted
    if op == "le":
        return actual <= wanted
    if op == "gt":
        return actual > wanted
    if op == "ge":
        return actual >= wanted
    return True


def _text_match(op: str, haystack: str, needle: str) -> bool:
    hay = str(haystack or "")
    want = str(needle or "")
    if op == "eq":
        return hay == want
    if op == "ne":
        return hay != want
    if op == "starts":
        return hay.lower().startswith(want.lower())
    if op == "ends":
        return hay.lower().endswith(want.lower())
    if op == "regex":
        try:
            return re.search(want, hay) is not None
        except re.error:
            return True
    if op == "not_contains":
        return want.lower() not in hay.lower()
    return want.lower() in hay.lower()


def condition_active(condition: dict) -> bool:
    """条件是否生效：显式禁用或值为空的条件不参与筛选。"""
    if not isinstance(condition, dict):
        return False
    if condition.get("enabled") is False:
        return False
    return bool(str(condition.get("value") or "").strip())


def match_condition(condition: dict, context: dict) -> bool:
    """单条条件是否命中（值为空的条件视为「不约束」）。"""
    if not condition_active(condition):
        return True
    field = str(condition.get("field") or "")
    op = str(condition.get("op") or default_op(field))
    value = str(condition.get("value") or "").strip()
    kind = FIELD_KINDS.get(field, "text")

    if field in _NUMBER_FIELDS:
        wanted = _as_number(value)
        actual = _as_number(context.get(field))
        if wanted is None or actual is None:
            return True
        return _number_match(op, actual, wanted)

    if kind == "list":
        items = [str(item) for item in (context.get(field) or [])]
        if op == "eq":
            return any(item == value for item in items)
        if op == "not_contains":
            return not any(value.lower() in item.lower() for item in items)
        return any(value.lower() in item.lower() for item in items)

    return _text_match(op, str(context.get(field) or ""), value)


def match_tree(node: dict, context: dict) -> bool:
    """按规则树判断上下文是否命中。"""
    if not isinstance(node, dict):
        return True
    conditions = node.get("conditions")
    if isinstance(conditions, list) and conditions:
        hits = [
            match_tree(item, context)
            for item in conditions
            if isinstance(item, dict)
        ]
        if not hits:
            return True
        return all(hits) if str(node.get("logic") or "and") == "and" else any(hits)
    return match_condition(node, context)


def _walk(node: dict):
    if not isinstance(node, dict):
        return
    conditions = node.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        yield node
        return
    for item in conditions:
        yield from _walk(item)


def count_active(tree: dict) -> int:
    """生效条件条数。"""
    return sum(1 for node in _walk(tree or {}) if condition_active(node))


def active_fields(tree: dict) -> set[str]:
    """生效条件用到的字段集合。"""
    return {
        str(node.get("field"))
        for node in _walk(tree or {})
        if condition_active(node)
    }


def describe_tree(node: dict, depth: int = 0) -> str:
    """把规则树压成一行文字（按钮上显示用）。"""
    parts = []
    conditions = node.get("conditions") if isinstance(node, dict) else None
    logic_text = " 且 " if str((node or {}).get("logic") or "and") == "and" else " 或 "
    if isinstance(conditions, list) and conditions:
        for item in conditions:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("conditions"), list) and item.get("conditions"):
                inner = describe_tree(item, depth + 1)
                parts.append(f"({inner})" if inner else "")
                continue
            if not condition_active(item):
                continue
            parts.append(
                f"{field_label(item.get('field'))}"
                f"{op_label(item.get('op'))}"
                f"{str(item.get('value') or '').strip()}"
            )
        parts = [part for part in parts if part]
        return logic_text.join(parts)
    return ""


class FilterRules:
    """一组自定义筛选规则（薄封装，便于界面传递与项目保存）。"""

    def __init__(self, tree: dict | None = None):
        self._tree = dict(tree or {})

    @property
    def tree(self) -> dict:
        return dict(self._tree)

    def is_empty(self) -> bool:
        return self.active_count() == 0

    def active_count(self) -> int:
        return count_active(self._tree)

    def fields(self) -> set[str]:
        return active_fields(self._tree)

    def needs_meta(self) -> bool:
        """是否需要图像尺寸 / 通道数（决定要不要读图）。"""
        return bool(self.fields() & _META_FIELDS)

    def describe(self) -> str:
        return describe_tree(self._tree)

    def match(self, context: dict) -> bool:
        if self.is_empty():
            return True
        return match_tree(self._tree, context)
