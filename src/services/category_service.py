"""缺陷类别管理服务：增删改、排序、颜色与导入导出。

类别定义存放在 `Project.classes`（随 `.mprj` 持久化），
**列表顺序即标签 id**，必须保持 0..n-1 连续，否则 YOLO 标签文件中的
类别索引会与之失配。

新建类别的颜色按「名称语义 + 类别类型」初始化（规则见下文常量与
《开发文档.md》§6.18）。**只作用于新建类别**，已有类别与用户手工
改过的颜色不会被覆盖。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.models.project_file import ClassDef
from src.utils.logger import get_logger

logger = get_logger("category")

# 中性色板：名称无语义可判时使用（灰 / 紫 / 品红 / 棕 / 石板蓝）。
# 刻意不含绿、红、橙、黄，避免无信息的类名被读成「良品」或「异常」。
NEUTRAL_COLORS = (
    "#5C5C5C", "#7A3E9D", "#B4009E", "#8764B8", "#8A5A00", "#4B4B6E",
)

# ---------------------------------------------------------------
# 语义配色
# ---------------------------------------------------------------
# 良品族：普通标识色（绿 → 蓝 → 青 → 橄榄 → 深青绿 → 靛蓝），首选绿色
GOOD_COLORS = (
    "#0F7B0F", "#005FB8", "#00838F", "#4D6A00", "#167A5C", "#3A5A8C",
)
# 异常族：警示色，按严重度分档，同档内另有深浅备用色（与中性色板不重叠）
SEVERE_COLORS = ("#C42B1C", "#8A1F14", "#D13438")      # 红
MODERATE_COLORS = ("#9D5D00", "#B86B00", "#C05600")    # 橙
MINOR_COLORS = ("#C29300", "#A88400", "#8A6D00")       # 黄
BAD_COLORS = SEVERE_COLORS + MODERATE_COLORS + MINOR_COLORS

SEVERE, MODERATE, MINOR = "severe", "moderate", "minor"

# 判定级不良：直接表示「这件是不良品」的表述（严重度取「一般」）
_JUDGE_BAD_CN = (
    "缺陷", "异常", "不良", "不合格", "次品", "废品", "坏品",
    "不良品", "异常品", "缺陷品",
)
_JUDGE_BAD_EN = (
    "NG", "NOK", "NOTOK", "NOTGOOD", "BAD", "DEFECT", "DEFECTIVE", "DEFECTS",
    "ABNORMAL", "ANOMALY", "ERROR", "ERR", "FAULT", "REJECT", "REJECTED",
    "UNQUALIFIED", "INVALID",
)
# 明确良品：良品 / 合格 / OK 等
_GOOD_CN = (
    "良品", "优品", "正品", "好品", "合格品", "合格", "良好", "正常品",
    "正常", "完好", "通过", "达标", "优质", "良", "好",
)
_GOOD_EN = (
    "OK", "OKAY", "GOOD", "GOODS", "NORMAL", "PASS", "PASSED", "ACCEPT",
    "ACCEPTED", "FINE", "QUALIFIED", "YES",
)
# 否定式良品中"无 X / 未 X"无法由不良词推出的补充表述
_GOOD_NEG_EXTRA_CN = ("无损", "无痕", "无伤", "无缺")

# 具体缺陷名：按严重度分档（重 → 红 / 中 → 橙 / 轻 → 黄）
_SEVERE_CN = (
    "断裂", "破裂", "烧毁", "烧焦", "短路", "断路", "开路", "缺件", "漏件",
    "错件", "反件", "倒装", "混料", "立碑", "报废", "失效", "致命", "严重",
)
_SEVERE_EN = (
    "BROKEN", "BREAK", "BREAKAGE", "FRACTURE", "RUPTURE", "BURN", "BURNT",
    "BURNED", "SHORT", "OPEN", "MISSING", "WRONG", "MIXED", "SCRAP", "FAIL",
    "FAILED", "FAILURE", "FATAL", "CRITICAL", "SEVERE",
)
_MODERATE_CN = (
    "破损", "损伤", "裂纹", "裂缝", "开裂", "龟裂", "划痕", "刮痕", "划伤",
    "刮伤", "擦伤", "碰伤", "压伤", "拉伤", "变形", "翘曲", "弯曲", "皱",
    "折痕", "异物", "夹杂", "杂质", "混入", "嵌入", "缺料", "缺口", "虚焊",
    "假焊", "漏焊", "焊偏", "偏位", "偏移", "错位", "露底", "露铜", "脱层",
    "剥离", "脱落", "掉漆", "孔洞", "破洞", "凹坑", "凹痕", "压痕", "磨痕",
    "拉丝", "套印", "漏印", "气泡", "起泡", "毛边", "多料", "锡珠", "锡渣",
    "溢料", "溢胶", "溢锡", "缺胶", "露白",
)
_MODERATE_EN = (
    "DEFORM", "DEFORMED", "DEFORMATION", "WARP", "WARPED", "WARPAGE",
    "WARPING", "DENT", "DENTS", "CRACK", "CRACKS", "CRACKED", "CHIP",
    "CHIPPED", "HOLE", "HOLES", "SCRATCH", "SCRATCHES", "SCRATCHED",
    "ABRASION", "BRUISE", "GOUGE", "FOREIGN", "IMPURITY", "INCLUSION",
    "CONTAMINATION", "CONTAMINANT", "PEEL", "PEELING", "DELAMINATION",
    "BLISTER", "BUBBLE", "BUBBLES", "VOID", "OFFSET", "MISALIGN",
    "MISALIGNED", "MISMATCH", "INCOMPLETE", "EXTRA", "LEAK", "LEAKAGE",
    "DAMAGE", "DAMAGED", "BRIDGE", "BRIDGING", "WRINKLE", "WRINKLES",
    "WRINKLED", "OVERFLOW",
)
_MINOR_CN = (
    "脏污", "污渍", "油污", "水渍", "污染", "色差", "变色", "发白", "发黑",
    "发黄", "氧化", "锈蚀", "锈斑", "腐蚀", "斑点", "色斑", "黑点", "白点",
    "亮点", "暗点", "麻点", "毛刺", "飞边", "批锋", "砂眼", "缩孔", "气孔",
    "起伏",
)
_MINOR_EN = (
    "STAIN", "STAINS", "DIRT", "DIRTY", "SMEAR", "SMUDGE", "MARK", "MARKS",
    "SPOT", "SPOTS", "DOT", "DOTS", "DISCOLOR", "DISCOLORATION", "OXIDATION",
    "OXIDE", "RUST", "CORROSION", "CORRODED", "PORE", "POROSITY", "PINHOLE",
    "SANDHOLE", "SHRINK", "SHRINKAGE", "BURR", "BURRS", "FLASH", "FLASHING",
    "IMPERFECTION",
)

_BAD_CN_ALL = _JUDGE_BAD_CN + _SEVERE_CN + _MODERATE_CN + _MINOR_CN
_BAD_EN_ALL = _JUDGE_BAD_EN + _SEVERE_EN + _MODERATE_EN + _MINOR_EN
_TIERS = (
    (SEVERE, _SEVERE_CN, _SEVERE_EN),
    (MODERATE, _MODERATE_CN, _MODERATE_EN),
    (MINOR, _MINOR_CN, _MINOR_EN),
)

_WORD_RE = re.compile(r"[A-Za-z]+|\d+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _split_name(name: str) -> tuple[str, list[str]]:
    """类别名 →（中文串, 英文词元表）。

    中文串只保留汉字，否则分隔符会把「不良」拆开；英文按非字母数字、
    驼峰与数字边界切词并统一大写，使「NORMAL」不会被「NO」误判、
    「defect1」也能命中「DEFECT」。
    """
    text = str(name or "")
    cn = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    words: list[str] = []
    for word in _WORD_RE.findall(text):
        words.extend(part for part in _CAMEL_RE.split(word) if part)
    return cn, [word.upper() for word in words]


def _singular(token: str) -> str:
    """英文词元去复数（仅用于词表匹配）。"""
    if len(token) > 4 and token.endswith("ES"):
        return token[:-2]
    if len(token) > 3 and token.endswith("S"):
        return token[:-1]
    return token


def _matched(cn: str, tokens: list[str], cn_terms, en_terms) -> bool:
    """中文按子串、英文按词元（含复数）匹配词表。"""
    if cn and any(term in cn for term in cn_terms):
        return True
    return any(
        token in en_terms or _singular(token) in en_terms for token in tokens
    )


def _is_bad_token(token: str) -> bool:
    """词元是否为不良词（含复数）。"""
    return bool(token) and (
        token in _BAD_EN_ALL or _singular(token) in _BAD_EN_ALL
    )


def _negated_good(cn: str, tokens: list[str]) -> bool:
    """否定式良品：「无缺陷」「未损伤」「NO DEFECT」「NONDEFECT」「DEFECT FREE」。"""
    if any(
        f"{prefix}{term}" in cn
        for prefix in ("无", "非", "未")
        for term in _BAD_CN_ALL
    ):
        return True
    if cn and any(term in cn for term in _GOOD_NEG_EXTRA_CN):
        return True
    for index, token in enumerate(tokens):
        # NO / NON 与不良词相邻：NO DEFECT、NO_DEFECT
        if token in ("NO", "NON"):
            following = tokens[index + 1] if index + 1 < len(tokens) else ""
            if _is_bad_token(following):
                return True
        # NO / NON 与不良词连写：NODEFECT、NONDEFECT（先试 NON）
        for prefix in ("NON", "NO"):
            if token.startswith(prefix) and _is_bad_token(token[len(prefix):]):
                return True
        # FREE 跟在不良词后：DEFECT FREE
        if token == "FREE" and index and _is_bad_token(tokens[index - 1]):
            return True
    return False


def _defect_tier(cn: str, tokens: list[str]) -> str:
    """具体缺陷名的严重度档，未命中返回 ""。"""
    for tier, cn_terms, en_terms in _TIERS:
        if _matched(cn, tokens, cn_terms, en_terms):
            return tier
    return ""


def _semantic_group(name: str, kind: str = "") -> tuple[str, str]:
    """判定配色族与严重度档：("good", "") / ("bad", 档) / ("", "")。

    判定顺序即优先级：类别类型 → 否定式良品 → 明确不良 → 明确良品 →
    具体缺陷名 → 兜底。顺序不可调换，否则「无缺陷」会被「缺陷」判成
    异常、「不良」会被「良」判成良品。
    """
    cn, tokens = _split_name(name)
    if str(kind) == "abnormal":
        return "bad", _defect_tier(cn, tokens) or MODERATE
    if str(kind) == "normal":
        return "good", ""
    if _negated_good(cn, tokens):
        return "good", ""
    if _matched(cn, tokens, _JUDGE_BAD_CN, _JUDGE_BAD_EN):
        return "bad", MODERATE
    if _matched(cn, tokens, _GOOD_CN, _GOOD_EN):
        return "good", ""
    tier = _defect_tier(cn, tokens)
    return ("bad", tier) if tier else ("", "")


def _first_free(candidates, used: set) -> str:
    """候选色中首个未被占用的颜色，全被占用时返回 ""。"""
    for color in candidates:
        if color.upper() not in used:
            return color
    return ""


def _pick(candidates, used: set, spare=()) -> str:
    """优先取候选色，其次备用色，最后退回候选首色。"""
    return (
        _first_free(candidates, used)
        or _first_free(spare, used)
        or candidates[0]
    )


def _id_sort_key(value) -> tuple:
    """类别 id 排序键：数字优先，其余按字符串。"""
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


class CategoryService:
    """缺陷类别列表的操作（静态方法，不持有状态）。"""

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @staticmethod
    def color_for(name: str, kind: str = "", used=()) -> str:
        """按名称语义与类别类型推荐初始颜色。

        Args:
            name: 类别名（可含 OK / NG / 良品 / 划痕 等语义词）。
            kind: 异常检测的类别类型，``"normal"`` / ``"abnormal"``，
                ``""`` 表示未指定；指定时优先于名称语义。
            used: 已被其它类别占用的颜色，用于同色错开。
        Returns:
            ``"#RRGGBB"`` 颜色值。
        """
        used_upper = {str(item).upper() for item in used if str(item)}
        group, tier = _semantic_group(name, kind)
        if group == "good":
            return _pick(GOOD_COLORS, used_upper)
        if group == "bad":
            colors = {
                SEVERE: SEVERE_COLORS,
                MODERATE: MODERATE_COLORS,
                MINOR: MINOR_COLORS,
            }[tier]
            return _pick(colors, used_upper, spare=BAD_COLORS)
        return _pick(NEUTRAL_COLORS, used_upper)

    # -----------------------------------------------------------
    # 增删改
    # -----------------------------------------------------------
    @staticmethod
    def add(
        classes: list[ClassDef],
        name: str,
        color: str = "",
        kind: str = "",
    ) -> ClassDef:
        """新增类别，id 取当前最大值 +1。

        ``color`` 留空时按 :meth:`color_for` 初始化颜色；``kind`` 为
        异常检测的类别类型，仅在新建时写入。
        """
        name = name.strip()
        if not name:
            raise ValueError("类别名不能为空")
        new_id = max((c.cls_id for c in classes), default=-1) + 1
        cls = ClassDef(
            cls_id=new_id,
            name=name,
            color=color or CategoryService.color_for(
                name, kind, used=[c.color for c in classes]
            ),
            kind=str(kind or ""),
        )
        classes.append(cls)
        return cls

    @staticmethod
    def rename(classes: list[ClassDef], cls_id: int, name: str) -> None:
        """重命名类别。"""
        name = name.strip()
        if not name:
            raise ValueError("类别名不能为空")
        for cls in classes:
            if cls.cls_id == cls_id:
                cls.name = name
                return
        raise KeyError(f"类别不存在: {cls_id}")

    @staticmethod
    def set_color(classes: list[ClassDef], cls_id: int, color: str) -> None:
        """设置类别颜色。"""
        for cls in classes:
            if cls.cls_id == cls_id:
                cls.color = color
                return
        raise KeyError(f"类别不存在: {cls_id}")

    @staticmethod
    def set_kind(classes: list[ClassDef], cls_id: int, kind: str) -> None:
        """设置类别类型（异常检测：normal=良好 / abnormal=异常，"" 为未指定）。"""
        for cls in classes:
            if cls.cls_id == cls_id:
                cls.kind = str(kind)
                return
        raise KeyError(f"类别不存在: {cls_id}")

    @staticmethod
    def remove(classes: list[ClassDef], cls_id: int) -> None:
        """删除类别并重排 id（保持 0..n-1 连续）。"""
        classes[:] = [c for c in classes if c.cls_id != cls_id]
        CategoryService.renumber(classes)

    @staticmethod
    def renumber(classes: list[ClassDef]) -> None:
        """按当前顺序把 cls_id 重排为 0..n-1。"""
        for index, cls in enumerate(classes):
            cls.cls_id = index

    @staticmethod
    def move(classes: list[ClassDef], cls_id: int, delta: int) -> int:
        """按 delta 上下移动类别，返回移动后的位置（越界则不动）。"""
        ids = [c.cls_id for c in classes]
        if cls_id not in ids:
            raise KeyError(f"类别不存在: {cls_id}")
        old = ids.index(cls_id)
        new = old + delta
        if new < 0 or new >= len(classes):
            return old
        classes.insert(new, classes.pop(old))
        CategoryService.renumber(classes)
        return new

    # -----------------------------------------------------------
    # 构建 / 导入 / 导出
    # -----------------------------------------------------------
    @staticmethod
    def from_names(names: list[str]) -> list[ClassDef]:
        """由名称列表构建类别定义（id = 顺序，颜色按名称语义初始化）。"""
        result: list[ClassDef] = []
        for index, name in enumerate(names):
            text = str(name)
            result.append(ClassDef(
                cls_id=index,
                name=text,
                color=CategoryService.color_for(
                    text, used=[cls.color for cls in result]
                ),
            ))
        return result

    @staticmethod
    def sync_from_dataset(dataset) -> list[ClassDef]:
        """按数据集统计中的类别 id 生成类别定义（名称暂用 id 本身）。"""
        result: list[ClassDef] = []
        for index, raw in enumerate(sorted(dataset.class_names, key=_id_sort_key)):
            try:
                cls_id = int(raw)
            except (TypeError, ValueError):
                cls_id = index
            result.append(ClassDef(
                cls_id=cls_id,
                name=str(raw),
                color=CategoryService.color_for(
                    str(raw), used=[cls.color for cls in result]
                ),
            ))
        return result

    @staticmethod
    def import_classes_txt(path: str | Path) -> list[ClassDef]:
        """从 classes.txt 导入类别（每行一个名称，行号即 id）。"""
        path = Path(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        names = [line.strip() for line in lines if line.strip()]
        logger.info("导入类别 %s 个：%s", len(names), path)
        return CategoryService.from_names(names)

    @staticmethod
    def import_data_yaml(path: str | Path) -> list[ClassDef]:
        """从 data.yaml / data.yml 的 names 字段导入类别。"""
        import yaml

        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names = data.get("names")
        if isinstance(names, dict):
            try:
                keys = sorted(names, key=lambda k: int(k))
            except (TypeError, ValueError):
                keys = sorted(names, key=lambda k: str(k))
            values = [names[k] for k in keys]
        elif isinstance(names, list):
            values = list(names)
        else:
            values = []
        logger.info("导入类别 %s 个：%s", len(values), path)
        return CategoryService.from_names([str(v) for v in values])

    @staticmethod
    def export_classes_txt(path: str | Path, classes: list[ClassDef]) -> Path:
        """导出为 classes.txt（每行一个名称，行号即 id）。"""
        path = Path(path)
        ordered = sorted(classes, key=lambda c: c.cls_id)
        path.write_text("\n".join(c.name for c in ordered) + "\n", encoding="utf-8")
        logger.info("导出类别 %s 个：%s", len(ordered), path)
        return path
