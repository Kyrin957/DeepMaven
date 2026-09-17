"""异常检测子进程入口：独立进程运行 Anomalib 训练，逐行输出 JSON 事件。

由 `TrainService` 通过 `python -m src.services.anomaly_worker` 启动，
事件协议与 `train_worker` 保持一致。
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
    parser = argparse.ArgumentParser(description="DeepMaven 异常检测子进程")
    parser.add_argument("--root", required=True, help="数据集根目录")
    parser.add_argument("--model", default="Padim")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--normal-dir", default="normal")
    parser.add_argument("--abnormal-dir", default="abnormal")
    parser.add_argument("--output", default="runs/anomaly")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--no-pretrained", action="store_true",
        help="不加载骨干预训练权重（离线环境使用）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    total = max(1, args.epochs)

    try:
        from anomalib.engine import Engine
        from lightning.pytorch.callbacks import Callback

        from src.services.anomalib_service import AnomalibService
    except ImportError as exc:
        emit({"type": "error", "text": f"未安装 Anomalib：{exc}"})
        return 2

    class _Progress(Callback):
        """把每轮与评估指标转成 JSON 事件。"""

        def on_train_epoch_end(self, trainer, pl_module):
            emit({
                "type": "epoch",
                "epoch": int(trainer.current_epoch) + 1,
                "total": total,
                "metrics": {},
            })

        def on_test_end(self, trainer, pl_module):
            metrics: dict[str, float] = {}
            for key, value in (trainer.callback_metrics or {}).items():
                try:
                    metrics[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            emit({"type": "metrics", "metrics": metrics})

    try:
        emit({"type": "phase", "text": "构建数据模块"})
        datamodule = AnomalibService.build_datamodule(
            args.root, args.normal_dir, args.abnormal_dir, args.batch, args.seed
        )
        model = None
        if not args.no_pretrained:
            try:
                model = AnomalibService.build_model(args.model, pretrained=True)
            except Exception as exc:  # noqa: BLE001 - 多为无法下载预训练骨干
                emit({
                    "type": "phase",
                    "text": f"预训练权重不可用，改为随机初始化：{exc}",
                })
        if model is None:
            model = AnomalibService.build_model(args.model, pretrained=False)

        engine = Engine(
            default_root_dir=args.output,
            max_epochs=total,
            callbacks=[_Progress()],
        )

        emit({"type": "phase", "text": "开始训练"})
        engine.fit(model=model, datamodule=datamodule)

        emit({"type": "phase", "text": "评估中"})
        engine.test(model=model, datamodule=datamodule)
    except Exception as exc:  # noqa: BLE001 - 子进程需回报任何失败
        traceback.print_exc()
        emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        return 1

    ckpt = ""
    trainer = getattr(engine, "trainer", None)
    callback = getattr(trainer, "checkpoint_callback", None) if trainer else None
    if callback is not None:
        ckpt = str(getattr(callback, "best_model_path", "") or "")
    if not ckpt:
        found = sorted(Path(args.output).rglob("*.ckpt"))
        ckpt = str(found[-1]) if found else ""

    emit({
        "type": "done",
        "save_dir": str(args.output),
        "best": ckpt,
        "last": ckpt,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
