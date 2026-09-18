"""训练子进程入口：独立进程运行 Ultralytics 训练，逐行输出 JSON 事件。

由 `TrainService` 通过 `python -m src.services.train_worker` 启动。

输出协议（每行一个 JSON 对象）：
    {"type": "phase", "text": "..."}                          阶段提示
    {"type": "epoch", "epoch": 1, "total": 100, "metrics": {}} 每轮指标
    {"type": "done", "save_dir": "...", "best": "...", "last": "..."}
    {"type": "error", "text": "..."}
非 JSON 行会被父进程当作普通日志。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def emit(event: dict) -> None:
    """向父进程输出一行 JSON 事件。"""
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepMaven 训练子进程")
    parser.add_argument("--data", required=True, help="data.yaml 路径")
    parser.add_argument("--weights", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--lr", type=float, default=0.01)
    parser.add_argument("--optimizer", default="auto")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--project", default="runs")
    parser.add_argument("--name", default="exp")
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        emit({"type": "error", "text": f"未安装 Ultralytics：{exc}"})
        return 2

    total = max(1, args.epochs)
    emit({"type": "phase", "text": "加载模型"})

    try:
        model = YOLO(args.weights)

        def on_train_start(_trainer):
            emit({"type": "phase", "text": "训练开始"})

        def on_train_epoch_end(trainer):
            metrics: dict[str, float] = {}
            for key, value in (getattr(trainer, "metrics", None) or {}).items():
                try:
                    metrics[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            if "train/loss" not in metrics:
                # 部分任务的 loss 不在 metrics 里，用 loss_items 兜底
                # （分类返回 dict，检测返回张量，这里统一只累加数值项）
                loss_items = getattr(trainer, "loss_items", None)
                if isinstance(loss_items, dict):
                    candidates = list(loss_items.values())
                elif loss_items is None:
                    candidates = []
                elif hasattr(loss_items, "tolist"):
                    candidates = loss_items.tolist()
                else:
                    candidates = list(loss_items)
                numbers: list[float] = []
                for item in candidates:
                    try:
                        numbers.append(float(item))
                    except (TypeError, ValueError):
                        continue
                if numbers:
                    metrics["train/loss"] = float(sum(numbers))
            emit({
                "type": "epoch",
                "epoch": int(getattr(trainer, "epoch", 0)) + 1,
                "total": total,
                "metrics": metrics,
            })

        model.add_callback("on_train_start", on_train_start)
        model.add_callback("on_train_epoch_end", on_train_epoch_end)

        model.train(
            data=args.data,
            epochs=total,
            batch=args.batch,
            imgsz=args.imgsz,
            lr0=args.lr,
            optimizer=args.optimizer,
            device=args.device,
            workers=args.workers,
            seed=args.seed,
            project=args.project,
            name=args.name,
            resume=args.resume,
            exist_ok=True,
        )
    except Exception as exc:  # noqa: BLE001 - 子进程需回报任何失败
        traceback.print_exc()
        emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        return 1

    save_dir = ""
    trainer = getattr(model, "trainer", None)
    if trainer is not None:
        save_dir = str(getattr(trainer, "save_dir", "") or "")

    weights_dir = Path(save_dir) / "weights" if save_dir else None
    emit({
        "type": "done",
        "save_dir": save_dir,
        "best": str(weights_dir / "best.pt") if weights_dir else "",
        "last": str(weights_dir / "last.pt") if weights_dir else "",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
