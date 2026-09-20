"""训练服务：以独立子进程运行 Ultralytics 训练，并把进度事件转回界面。

独立进程的好处：训练崩溃 / 显存溢出不会带崩 GUI，且可以随时强杀终止。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from src.models.training import TrainingConfig
from src.utils.constants import PROJECT_ROOT
from src.utils.device import normalize_device
from src.utils.logger import get_logger

logger = get_logger("train")


class TrainService(QObject):
    """训练子进程管理。"""

    event = Signal(dict)            # 子进程事件（phase / epoch / done / error）
    logLine = Signal(str)           # 非 JSON 的标准输出行
    finished = Signal(int, dict)    # 退出码, 汇总信息（含产物路径）
    failed = Signal(str)            # 启动或进程级错误

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._buffer = ""
        self._summary: dict = {}
        self._config: TrainingConfig | None = None

    # -----------------------------------------------------------
    # 状态
    # -----------------------------------------------------------
    def is_running(self) -> bool:
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    # -----------------------------------------------------------
    # 控制
    # -----------------------------------------------------------
    def start(
        self, config: TrainingConfig, project_root: Path | None = None
    ) -> bool:
        """启动训练子进程。"""
        if self.is_running():
            self.failed.emit("训练已在运行中")
            return False
        if config.task_type == "anomaly":
            if not config.anomaly_root or not Path(config.anomaly_root).is_dir():
                self.failed.emit(
                    f"异常检测数据目录不存在：{config.anomaly_root or '（未设置）'}"
                )
                return False
        elif not config.data_yaml or not Path(config.data_yaml).exists():
            # 检测/分割为 data.yaml 文件；分类任务直接使用数据集目录
            self.failed.emit(f"数据集配置不存在：{config.data_yaml or '（未设置）'}")
            return False

        cwd = Path(project_root or PROJECT_ROOT)
        process = QProcess(self)
        process.setWorkingDirectory(str(cwd))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        # 子进程统一按 UTF-8 输出：中文 Windows 控制台默认 GBK，Anomalib/Lightning
        # 经 rich 输出 • 等字符时会抛 UnicodeEncodeError 中断训练
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUTF8", "1")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)

        args = self.build_args(config)
        logger.info("启动训练子进程：%s", " ".join(args))
        self._buffer = ""
        self._summary = {}
        self._config = config      # 暂停 / 继续需要知道控制文件位置
        process.start(sys.executable, args)
        if not process.waitForStarted(8000):
            self.failed.emit("训练进程启动失败")
            return False
        self._process = process
        return True

    def stop(self) -> None:
        """强制终止训练子进程。"""
        if self._process is None:
            return
        logger.info("终止训练子进程")
        self.resume()          # 清掉暂停标记，避免下次训练一启动就被挂起
        self._process.kill()

    # -----------------------------------------------------------
    # 暂停 / 继续（轮边界生效）
    # -----------------------------------------------------------
    @staticmethod
    def pause_file(config: TrainingConfig) -> Path:
        """暂停标记文件：父进程与训练子进程约定的控制文件。"""
        root = Path(config.project_dir or (PROJECT_ROOT / "runs"))
        return root / "train.pause"

    def pause(self) -> bool:
        """请求暂停训练（在当前轮结束后生效）。"""
        if self._config is None or not self.is_running():
            return False
        try:
            path = self.pause_file(self._config)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pause", encoding="utf-8")
        except OSError as exc:
            logger.warning("创建暂停标记失败: %s", exc)
            return False
        logger.info("已请求暂停（轮边界生效）：%s", path)
        return True

    def resume(self) -> None:
        """移除暂停标记（训练在下一轮边界自动继续）。"""
        if self._config is None:
            return
        path = self.pause_file(self._config)
        try:
            if path.exists():
                path.unlink()
                logger.info("已继续训练：%s", path)
        except OSError as exc:
            logger.warning("移除暂停标记失败: %s", exc)

    def is_paused(self) -> bool:
        """当前是否处于暂停请求状态。"""
        return bool(
            self._config is not None and self.pause_file(self._config).exists()
        )

    @staticmethod
    def build_args(config: TrainingConfig) -> list[str]:
        """构造子进程命令行参数（异常检测走独立的 Anomalib 子进程）。"""
        if config.task_type == "anomaly":
            return TrainService._anomaly_args(config)
        return TrainService._yolo_args(config)

    @staticmethod
    def _anomaly_args(config: TrainingConfig) -> list[str]:
        """异常检测（Anomalib）参数。"""
        args = [
            "-m", "src.services.anomaly_worker",
            "--root", config.anomaly_root,
            "--model", config.model_key or "Padim",
            "--epochs", str(config.epochs),
            "--batch", str(config.batch),
            "--normal-dir", config.anomaly_normal_dir,
            "--abnormal-dir", config.anomaly_abnormal_dir,
            "--output", config.project_dir or str(PROJECT_ROOT / "runs" / "anomaly"),
            "--seed", str(config.seed),
            "--device", normalize_device(config.device),
        ]
        if not config.anomaly_pretrained:
            args.append("--no-pretrained")
        return args

    @staticmethod
    def _yolo_args(config: TrainingConfig) -> list[str]:
        """YOLO 训练参数（覆盖训练页「设置」里的全部参数）。"""
        args = [
            "-m", "src.services.train_worker",
            "--data", config.data_yaml,
            "--weights", config.weights_path or f"{config.model_key}.pt",
            "--epochs", str(config.epochs),
            "--batch", str(config.batch),
            "--imgsz", str(config.imgsz),
            "--lr", str(config.lr),
            "--optimizer", config.optimizer,
            "--weight-decay", str(config.weight_decay),
            "--momentum", str(config.momentum),
            "--warmup-epochs", str(config.warmup_epochs),
            "--patience", str(config.patience),
            "--cos-lr" if config.cos_lr else "--no-cos-lr",
            "--deterministic" if config.deterministic else "--no-deterministic",
            "--close-mosaic", str(config.close_mosaic),
            "--val" if config.val else "--no-val",
            "--cache" if config.cache else "--no-cache",
            "--single-cls" if config.single_cls else "--no-single-cls",
            "--rect" if config.rect else "--no-rect",
            "--dropout", str(config.dropout),
            "--device", normalize_device(config.device),
            "--workers", str(config.workers),
            "--seed", str(config.seed),
            "--project", config.project_dir or str(PROJECT_ROOT / "runs"),
            "--name", config.model_key,
            "--augment" if config.augment else "--no-augment",
            "--hflip", str(config.hflip),
            "--vflip", str(config.vflip),
            "--degrees", str(config.degrees),
            "--scale", str(config.scale),
            "--translate", str(config.translate),
            "--hsv-h", str(config.hsv_h),
            "--hsv-s", str(config.hsv_s),
            "--hsv-v", str(config.hsv_v),
            "--mosaic", str(config.mosaic),
            "--mixup", str(config.mixup),
            "--pause-file", str(TrainService.pause_file(config)),
        ]
        if config.resume:
            args.append("--resume")
        return args

    # -----------------------------------------------------------
    # 输出解析
    # -----------------------------------------------------------
    def _read_output(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle_line(line.rstrip("\r"))

    def _handle_line(self, line: str) -> None:
        if not line.strip():
            return
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                pass
            else:
                if payload.get("type") == "done":
                    self._summary = payload
                self.event.emit(payload)
                return
        self.logLine.emit(line)

    def _flush(self) -> None:
        if self._buffer.strip():
            self._handle_line(self._buffer)
        self._buffer = ""

    # -----------------------------------------------------------
    # 进程事件
    # -----------------------------------------------------------
    def _on_finished(self, exit_code: int, _status) -> None:
        self._flush()
        self._process = None
        self.finished.emit(int(exit_code), dict(self._summary))

    def _on_error(self, error) -> None:
        logger.warning("训练进程错误: %s", error)
        self.failed.emit(f"训练进程错误：{error}")
