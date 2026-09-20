"""归一化多边形几何工具。

标注点统一为归一化坐标 (x, y)（0~1），与项目其它部分保持一致。
"""

from __future__ import annotations

import math


def bounds(points: list) -> tuple[float, float, float, float]:
    """包围盒 (x1, y1, x2, y2)。"""
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def signed_area(points: list) -> float:
    """有符号面积（正负表示绕向；逆时针为正）。"""
    if len(points) < 3:
        return 0.0
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def area(points: list) -> float:
    """多边形面积（鞋带公式，取绝对值）。"""
    return abs(signed_area(points))


def translate(points: list, dx: float, dy: float, clamp: bool = True) -> list:
    """平移多边形；`clamp` 时把结果限制在 0~1。"""
    moved = [(x + dx, y + dy) for x, y in points]
    if not clamp:
        return moved
    return [(min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)) for x, y in moved]


def rotate(points: list, degrees: float, center: tuple[float, float] | None = None) -> list:
    """绕中心（默认几何中心）旋转。"""
    if not points:
        return []
    if center is None:
        x1, y1, x2, y2 = bounds(points)
        center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    cx, cy = center
    radians = math.radians(float(degrees))
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    return [
        (
            min(max(cx + (x - cx) * cos_a - (y - cy) * sin_a, 0.0), 1.0),
            min(max(cy + (x - cx) * sin_a + (y - cy) * cos_a, 0.0), 1.0),
        )
        for x, y in points
    ]


def scale_points(points: list, sx: float, sy: float,
                anchor: tuple[float, float] | None = None) -> list:
    """按比例缩放（默认以包围盒左上角为锚点）。"""
    if not points:
        return []
    x1, y1, _x2, _y2 = bounds(points)
    ax, ay = anchor if anchor is not None else (x1, y1)
    return [
        (
            min(max(ax + (x - ax) * sx, 0.0), 1.0),
            min(max(ay + (y - ay) * sy, 0.0), 1.0),
        )
        for x, y in points
    ]


def resample(points: list, step: float) -> list:
    """按归一化步长在边上补点（供区域填充使用）。"""
    if len(points) < 2:
        return list(points)
    output: list[tuple[float, float]] = []
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        distance = math.hypot(x2 - x1, y2 - y1)
        times = max(1, int(distance / step) + 1) if step > 0 else 1
        for step_index in range(times):
            ratio = step_index / times
            output.append((x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio))
    return output


def merge_hole(outer: list, hole: list) -> list:
    """把孔洞并入外轮廓，返回单个多边形（用零宽「桥」连接）。

    这是把「带孔区域」表示为**单个简单多边形**的经典做法：训练端
    （YOLO 多边形标注）只接受单条闭合轮廓，因此用一条极窄的通道把孔洞
    接到外轮廓上，面积上等效于「挖掉」孔洞。
    """
    if len(outer) < 3 or len(hole) < 3:
        return list(outer)
    # 孔洞必须与外轮廓反向绕行，否则面积会「加上」而不是「挖掉」
    hole_points = list(hole)
    if signed_area(outer) * signed_area(hole_points) > 0:
        hole_points.reverse()
    best = (0, 0, float("inf"))
    for i, (ox, oy) in enumerate(outer):
        for j, (hx, hy) in enumerate(hole_points):
            distance = (ox - hx) ** 2 + (oy - hy) ** 2
            if distance < best[2]:
                best = (i, j, distance)
    i, j, _ = best
    return (
        list(outer[: i + 1])
        + list(hole_points[j:]) + list(hole_points[: j + 1]) + [outer[i]]
        + list(outer[i + 1:])
    )


def merge_holes(outer: list, holes: list) -> list:
    """依次并入多个孔洞。"""
    result = list(outer)
    for hole in holes or []:
        if len(hole) >= 3:
            result = merge_hole(result, hole)
    return result
