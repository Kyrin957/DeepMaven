"""训练服务：以独立子进程运行 Ultralytics 训练，并把进度事件转回界面。

独立进程的好处：训练崩溃 / 显存溢出不会带崩 GUI，且可以随时强杀终止。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from src.models.training import TrainingConfig
from src.utils.constants import PROJECT_ROOT
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
        elif not config.data_yaml or not Path(config.data_yaml).is_file():
            self.failed.emit(f"数据集配置不存在：{config.data_yaml or '（未设置）'}")
            return False

        cwd = Path(project_root or PROJECT_ROOT)
        process = QProcess(self)
        process.setWorkingDirectory(str(cwd))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)

        args = self.build_args(config)
        logger.info("启动训练子进程：%s", " ".join(args))
        self._buffer = ""
        self._summary = {}
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
        self._process.kill()

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
        ]
        if not config.anomaly_pretrained:
            args.append("--no-pretrained")
        return args

    @staticmethod
    def _yolo_args(config: TrainingConfig) -> list[str]:
        """YOLO 训练参数。"""
        args = [
            "-m", "src.services.train_worker",
            "--data", config.data_yaml,
            "--weights", config.weights_path or f"{config.model_key}.pt",
            "--epochs", str(config.epochs),
            "--batch", str(config.batch),
            "--imgsz", str(config.imgsz),
            "--lr", str(config.lr),
            "--optimizer", config.optimizer,
            "--device", config.device,
            "--workers", str(config.workers),
            "--seed", str(config.seed),
            "--project", config.project_dir or str(PROJECT_ROOT / "runs"),
            "--name", config.model_key,
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
