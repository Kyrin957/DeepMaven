"""训练服务：以独立子进程运行 Ultralytics 训练，并把进度事件转回界面。

独立进程的好处：训练崩溃 / 显存溢出不会带崩 GUI，且可以随时强杀终止。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from src.models.training import TrainingConfig
from src.services.backends import resolve as resolve_backend
from src.services.backends.base import pause_file as _pause_file
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
        # 启动前校验由任务对应的后端提供（检测 / 分类看 data.yaml，异常看数据目录）
        problem = resolve_backend(config).validate(config)
        if problem is not None:
            self.failed.emit(problem.message)
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
        return _pause_file(config)

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
        """构造子进程命令行参数（由任务对应的后端适配器提供）。"""
        return resolve_backend(config).build_args(config)

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
