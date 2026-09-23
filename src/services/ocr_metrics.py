"""OCR 识别质量指标：字符错误率（CER）、词错误率（WER）与精确匹配率。

与检测的 mAP、异常的 AUROC 同属「按任务范式分组」的指标（见《开发文档.md》§8.3）：
* CER = 编辑距离（字符级） / 真值字符数
* WER = 编辑距离（空白分词后） / 真值词数
* 精确匹配率 = 整串完全一致的样本占比

按语料汇总（总编辑距离 / 总真值长度），比逐样本平均更贴近实际使用。
"""

from __future__ import annotations


def edit_distance(source, target) -> int:
    """编辑距离（插入 / 删除 / 替换，均计 1）。"""
    left = list(source)
    right = list(target)
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, item in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            cost = 0 if item == other else 1
            current.append(min(
                previous[j] + 1,          # 删除
                current[j - 1] + 1,       # 插入
                previous[j - 1] + cost,   # 替换
            ))
        previous = current
    return previous[-1]


def cer(predicted: str, truth: str) -> float:
    """单条样本的字符错误率（真值为空时：预测也为空记 0，否则记 1）。"""
    truth = str(truth or "")
    predicted = str(predicted or "")
    if not truth:
        return 0.0 if not predicted else 1.0
    return edit_distance(predicted, truth) / len(truth)


def wer(predicted: str, truth: str) -> float:
    """单条样本的词错误率（按空白分词）。"""
    truth_words = str(truth or "").split()
    predicted_words = str(predicted or "").split()
    if not truth_words:
        return 0.0 if not predicted_words else 1.0
    return edit_distance(predicted_words, truth_words) / len(truth_words)


def evaluate(pairs: list[tuple[str, str]]) -> dict:
    """按语料汇总评价识别结果。

    Args:
        pairs: [(预测文本, 真值文本), ...]

    Returns:
        {"total", "exact_match", "exact_rate", "cer", "wer"}
    """
    pairs = [(str(pred or ""), str(truth or "")) for pred, truth in (pairs or [])]
    if not pairs:
        return {"total": 0, "exact_match": 0, "exact_rate": 0.0, "cer": 0.0, "wer": 0.0}

    char_edits = 0
    char_total = 0
    word_edits = 0
    word_total = 0
    exact = 0
    for predicted, truth in pairs:
        char_edits += edit_distance(predicted, truth)
        char_total += max(1, len(truth))
        words = truth.split()
        word_edits += edit_distance(predicted.split(), words)
        word_total += max(1, len(words))
        if predicted == truth:
            exact += 1

    return {
        "total": len(pairs),
        "exact_match": exact,
        "exact_rate": round(exact / len(pairs), 4),
        "cer": round(char_edits / char_total, 4),
        "wer": round(word_edits / word_total, 4),
    }
