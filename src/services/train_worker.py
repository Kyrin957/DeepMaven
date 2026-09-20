"""训练子进程入口：独立进程运行 Ultralytics 训练，逐行输出 JSON 事件。

由 `TrainService` 通过 `python -m src.services.train_worker` 启动。

输出协议（每行一个 JSON 对象）：
    {"type": "phase", "text": "..."}                          阶段提示
    {"type": "iteration", ...}                                批次级进度（限流 ~2 次/秒）
    {"type": "epoch", "epoch": 1, "total": 100, "metrics": {}, "lr": ..,
     "iteration": .., "iterations": .., "elapsed": .., "eta": ..}  每轮指标
    {"type": "done", "save_dir": "...", "best": "...", "last": "...", "results": "..."}
    {"type": "error", "text": "..."}
非 JSON 行会被父进程当作普通日志。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

# 批次级进度事件的限流间隔（秒）
_ITERATION_INTERVAL = 0.5


def emit(event: dict) -> None:
    """向父进程输出一行 JSON 事件。"""
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def use_utf8_stdio() -> None:
    """把标准输出 / 错误切到 UTF-8（Windows 中文控制台默认 GBK 会中断输出）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def wait_if_paused(pause_file: str, poll: float = 0.5) -> bool:
    """若「暂停标记文件」存在则阻塞等待（在轮边界调用）。

    父进程通过创建 / 删除该文件来请求暂停与继续；子进程被终止时等待也随之结束。

    Returns:
        是否真的暂停过（供上报状态用）。
    """
    if not pause_file:
        return False
    from pathlib import Path

    marker = Path(pause_file)
    if not marker.exists():
        return False
    emit({"type": "status", "status": "paused", "text": "已暂停（等待继续）"})
    while marker.exists():
        time.sleep(max(0.05, float(poll)))
    emit({"type": "status", "status": "running", "text": "已继续训练"})
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepMaven 训练子进程")
    parser.add_argument("--data", required=True, help="data.yaml 路径")
    parser.add_argument("--weights", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--lr", type=float, default=0.01)
    parser.add_argument("--optimizer", default="auto")
    parser.add_argument("--weight-decay", type=float, default=0.0005)
    parser.add_argument("--momentum", type=float, default=0.937)
    parser.add_argument("--warmup-epochs", type=float, default=3.0)
    parser.add_argument("--patience", type=int, default=50)
    parser.add_argument("--cos-lr", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--deterministic", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--close-mosaic", type=int, default=10)
    parser.add_argument("--val", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--cache", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--single-cls", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument("--rect", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--project", default="runs")
    parser.add_argument("--name", default="exp")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=False)
    # 暂停控制：父进程创建该文件请求暂停，删除则继续（在轮边界检查）
    parser.add_argument("--pause-file", default="")
    # 在线数据增强
    parser.add_argument("--augment", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--hflip", type=float, default=0.5)
    parser.add_argument("--vflip", type=float, default=0.0)
    parser.add_argument("--degrees", type=float, default=0.0)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--translate", type=float, default=0.1)
    parser.add_argument("--hsv-h", type=float, default=0.015)
    parser.add_argument("--hsv-s", type=float, default=0.7)
    parser.add_argument("--hsv-v", type=float, default=0.4)
    parser.add_argument("--mosaic", type=float, default=1.0)
    parser.add_argument("--mixup", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdio()
    args = build_parser().parse_args(argv)

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        emit({"type": "error", "text": f"未安装 Ultralytics：{exc}"})
        return 2

    total = max(1, args.epochs)
    started = time.time()
    # reported: 已上报的轮次（Ultralytics 收尾阶段会再触发一次 on_fit_epoch_end）
    state = {"per_epoch": 0, "batch": 0, "last_emit": 0.0,
             "epochs_done": 0, "reported": 0}
    emit({"type": "phase", "text": f"设备 {args.device or 'auto'}"})
    emit({"type": "phase", "text": "加载模型"})

    def current_lr(trainer) -> float:
        """当前学习率（优先 trainer.lr，退回优化器参数组）。"""
        lr = getattr(trainer, "lr", None)
        if isinstance(lr, dict) and lr:
            try:
                return float(next(iter(lr.values())))
            except (TypeError, ValueError, StopIteration):
                return 0.0
        groups = getattr(
            getattr(trainer, "optimizer", None), "param_groups", None
        ) or []
        for group in groups:
            try:
                return float(group.get("lr", 0.0))
            except (TypeError, ValueError):
                continue
        return 0.0

    def train_losses(trainer) -> dict:
        """本轮训练损失（Ultralytics 的 label_loss_items 给出各项损失）。"""
        collect = getattr(trainer, "label_loss_items", None)
        if not callable(collect):
            return {}
        try:
            items = collect(getattr(trainer, "tloss", None), prefix="train")
        except Exception:  # noqa: BLE001 - 指标收集失败不应中断训练
            return {}
        if not isinstance(items, dict):
            return {}
        result: dict[str, float] = {}
        for key, value in items.items():
            try:
                result[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return result

    def iteration_totals() -> tuple[int, int]:
        per_epoch = max(0, int(state["per_epoch"]))
        return per_epoch, total * per_epoch

    try:
        model = YOLO(args.weights)

        def on_train_start(trainer):
            try:
                state["per_epoch"] = len(getattr(trainer, "train_loader", []) or [])
            except TypeError:
                state["per_epoch"] = 0
            emit({"type": "phase", "text": "训练开始"})

        def on_train_epoch_start(_trainer):
            state["batch"] = 0

        def on_train_epoch_end(_trainer):
            # 每完成一轮训练 +1（on_fit_epoch_end 在收尾阶段还会额外触发一次）
            state["epochs_done"] += 1
            # 轮边界响应「暂停」请求：文件在则挂起，删除后自动继续
            wait_if_paused(args.pause_file)

        def on_train_batch_end(trainer):
            state["batch"] += 1
            now = time.time()
            if now - state["last_emit"] < _ITERATION_INTERVAL:
                return
            state["last_emit"] = now
            per_epoch, all_iterations = iteration_totals()
            epoch = int(getattr(trainer, "epoch", 0)) + 1
            emit({
                "type": "iteration",
                "epoch": epoch,
                "total": total,
                "iteration": (epoch - 1) * per_epoch + state["batch"],
                "iterations": all_iterations,
                "lr": round(current_lr(trainer), 6),
                "elapsed": round(now - started, 1),
            })

        def on_fit_epoch_end(trainer):
            # 以「训练轮次结束」计数为准：收尾阶段那次 epoch+1 的重复回调直接跳过
            epoch = min(total, int(state["epochs_done"]))
            if epoch <= 0 or epoch == state["reported"]:
                return
            state["reported"] = epoch
            metrics = train_losses(trainer)
            for key, value in (getattr(trainer, "metrics", None) or {}).items():
                try:
                    metrics[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            elapsed = time.time() - started
            per_epoch, all_iterations = iteration_totals()
            emit({
                "type": "epoch",
                "epoch": epoch,
                "total": total,
                "metrics": metrics,
                "lr": round(current_lr(trainer), 6),
                "iteration": epoch * per_epoch,
                "iterations": all_iterations,
                "elapsed": round(elapsed, 1),
                "eta": round(elapsed / max(epoch, 1) * max(total - epoch, 0), 1),
            })

        model.add_callback("on_train_start", on_train_start)
        model.add_callback("on_train_epoch_start", on_train_epoch_start)
        model.add_callback("on_train_epoch_end", on_train_epoch_end)
        model.add_callback("on_train_batch_end", on_train_batch_end)
        model.add_callback("on_fit_epoch_end", on_fit_epoch_end)

        model.train(
            data=args.data,
            epochs=total,
            batch=args.batch,
            imgsz=args.imgsz,
            lr0=args.lr,
            optimizer=args.optimizer,
            weight_decay=args.weight_decay,
            momentum=args.momentum,
            warmup_epochs=args.warmup_epochs,
            patience=args.patience,
            cos_lr=args.cos_lr,
            deterministic=args.deterministic,
            close_mosaic=args.close_mosaic,
            val=args.val,
            cache=args.cache,
            single_cls=args.single_cls,
            rect=args.rect,
            dropout=args.dropout,
            device=args.device,
            workers=args.workers,
            seed=args.seed,
            project=args.project,
            name=args.name,
            resume=args.resume,
            exist_ok=True,
            # 在线数据增强
            augment=args.augment,
            fliplr=args.hflip,
            flipud=args.vflip,
            degrees=args.degrees,
            scale=args.scale,
            translate=args.translate,
            hsv_h=args.hsv_h,
            hsv_s=args.hsv_s,
            hsv_v=args.hsv_v,
            mosaic=args.mosaic,
            mixup=args.mixup,
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
        "results": str(Path(save_dir) / "results.csv") if save_dir else "",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
