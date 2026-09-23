"""U-Net 语义分割子进程入口：独立进程训练，逐行输出 JSON 事件。

由 `TrainService` 通过 `python -m src.services.unet_worker` 启动；
事件协议与 `train_worker` 一致（见 `worker_utils`）。

数据约定（拆分产物目录）：
    <root>/images/{train,val}/<图>
    <root>/masks/{train,val}/<同名>.png     像素值 = 类别 id + 1，0 = 背景
产物：
    <output>/weights/best.pt、last.pt，<output>/results.csv（每轮指标）
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from src.services.worker_utils import emit, use_utf8_stdio, wait_if_paused

# 批次级进度事件的限流间隔（秒）
_PROGRESS_INTERVAL = 0.5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DeepMaven U-Net 语义分割子进程")
    parser.add_argument("--root", required=True, help="拆分产物目录（含 images / masks）")
    parser.add_argument("--variant", default="unet-s", help="unet-s / unet-m")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--imgsz", type=int, default=256)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--device", default="cpu", help="cpu / cuda:0")
    parser.add_argument("--output", default="runs/unet")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--pause-file", default="")
    return parser


# ---------------------------------------------------------------
# 数据
# ---------------------------------------------------------------
def split_pairs(root: Path, split: str) -> list[tuple[Path, Path]]:
    """取某个子集的（原图, 掩码）对，缺掩码的图片跳过（实现见分段服务）。"""
    from src.services.segmentation_service import SegmentationService

    return SegmentationService.pairs_for(root, split)


def read_sample(image_path: Path, mask_path: Path, imgsz: int):
    """读一张（图, 掩码）并统一尺寸：图为 RGB，掩码用最近邻避免类别 id 被插值。"""
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as handle:
        image = handle.convert("RGB").resize((imgsz, imgsz))
    with Image.open(mask_path) as handle:
        mask = handle.convert("L").resize((imgsz, imgsz), Image.NEAREST)
    return (
        np.asarray(image, dtype="float32") / 255.0,
        np.asarray(mask, dtype="int64"),
    )


def detect_classes(pairs: list[tuple[Path, Path]]) -> int:
    """按掩码里出现的最大类别 id 推出类别数（含背景）。"""
    import numpy as np
    from PIL import Image

    biggest = 0
    for _image, mask_path in pairs:
        with Image.open(mask_path) as handle:
            values = np.asarray(handle.convert("L"))
        if values.size:
            biggest = max(biggest, int(values.max()))
    return max(2, biggest + 1)


def batches(items: list, size: int):
    for start in range(0, len(items), max(1, int(size))):
        yield items[start:start + max(1, int(size))]


# ---------------------------------------------------------------
# 训练
# ---------------------------------------------------------------
def train(args) -> dict:
    import numpy as np
    import torch
    import torch.nn as nn

    from src.services.segmentation_metrics import evaluate as mask_scores
    from src.services.unet_model import DEFAULT_VARIANT, VARIANTS, build_model, save_checkpoint

    root = Path(args.root)
    output = Path(args.output)
    weights_dir = output / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)

    train_pairs = split_pairs(root, "train")
    val_pairs = split_pairs(root, "val") or split_pairs(root, "test") or train_pairs
    if not train_pairs:
        raise RuntimeError("训练集没有「图 + 掩码」对：请先在数据拆分页生成掩码数据集")

    torch.manual_seed(int(args.seed))
    num_classes = detect_classes(train_pairs)
    variant = args.variant if args.variant in VARIANTS else DEFAULT_VARIANT
    device = torch.device(
        args.device if str(args.device).startswith("cuda") and torch.cuda.is_available()
        else "cpu"
    )
    emit({
        "type": "phase",
        "text": f"训练 {len(train_pairs)} 张 / 验证 {len(val_pairs)} 张 · "
                f"{num_classes} 类（含背景）· {device.type}",
    })

    model = build_model(variant, num_classes).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(args.lr))
    criterion = nn.CrossEntropyLoss()
    meta = {"num_classes": num_classes, "imgsz": int(args.imgsz)}

    emit({"type": "phase", "text": "读取数据"})
    train_data = [read_sample(image, mask, int(args.imgsz)) for image, mask in train_pairs]
    val_data = [read_sample(image, mask, int(args.imgsz)) for image, mask in val_pairs]

    csv_path = output / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "epoch", "time", "train/loss", "val/loss", "metrics/mIoU", "metrics/Dice",
        ])

    best_value = -1.0
    best_path = weights_dir / "best.pt"
    last_path = weights_dir / "last.pt"
    total = max(1, int(args.epochs))
    started = time.perf_counter()
    last_tick = 0.0

    for epoch in range(1, total + 1):
        model.train()
        running = 0.0
        steps = 0
        for batch in batches(train_data, args.batch):
            images = torch.from_numpy(
                np.stack([item[0] for item in batch]).transpose(0, 3, 1, 2)
            ).to(device)
            targets = torch.from_numpy(
                np.stack([item[1] for item in batch])
            ).to(device)
            optimizer.zero_grad()
            logits = model(images)
            loss = criterion(logits, targets)
            loss.backward()
            optimizer.step()
            running += float(loss.item())
            steps += 1
            now = time.perf_counter()
            if now - last_tick >= _PROGRESS_INTERVAL:
                last_tick = now
                emit({
                    "type": "iteration",
                    "epoch": epoch, "total": total,
                    "loss": round(running / max(1, steps), 4),
                    "elapsed": round(now - started, 2),
                })

        train_loss = running / max(1, steps)

        model.eval()
        val_loss = 0.0
        targets_all: list = []
        preds_all: list = []
        with torch.no_grad():
            for batch in batches(val_data, args.batch):
                images = torch.from_numpy(
                    np.stack([item[0] for item in batch]).transpose(0, 3, 1, 2)
                ).to(device)
                targets = torch.from_numpy(np.stack([item[1] for item in batch])).to(device)
                logits = model(images)
                val_loss += float(criterion(logits, targets).item())
                predicted = logits.argmax(dim=1).cpu().numpy()
                for index in range(predicted.shape[0]):
                    preds_all.append(predicted[index])
                    targets_all.append(batch[index][1])
        scores = mask_scores(targets_all, preds_all, num_classes)
        val_loss = val_loss / max(1, len(list(batches(val_data, args.batch))))

        elapsed = time.perf_counter() - started
        emit({
            "type": "epoch",
            "epoch": epoch,
            "total": total,
            "metrics": {
                "train/loss": round(train_loss, 6),
                "val/loss": round(val_loss, 6),
                "metrics/mIoU": scores["miou"],
                "metrics/Dice": scores["dice"],
            },
            "lr": float(optimizer.param_groups[0]["lr"]),
            "elapsed": round(elapsed, 2),
            "eta": round(elapsed / epoch * (total - epoch), 2),
        })

        with csv_path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow([
                epoch, round(elapsed, 2), round(train_loss, 6), round(val_loss, 6),
                scores["miou"], scores["dice"],
            ])

        save_checkpoint(last_path, model, meta, variant)
        if scores["miou"] >= best_value:
            best_value = scores["miou"]
            save_checkpoint(best_path, model, meta, variant)

        if wait_if_paused(args.pause_file):
            continue

    emit({"type": "phase", "text": f"完成：mIoU {best_value:.4f}"})
    return {
        "save_dir": str(output),
        "best": str(best_path),
        "last": str(last_path),
        "results": str(csv_path),
    }


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        summary = train(args)
    except Exception as exc:  # noqa: BLE001 - 子进程需回报任何失败
        traceback_text = __import__("traceback").format_exc()
        print(traceback_text, flush=True)
        emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        return 1
    emit({"type": "done", **summary})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
