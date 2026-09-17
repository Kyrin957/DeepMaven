"""模型评估 ViewModel：图片 / 视频 / 相机检测与报告导出。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.models.training import EvaluationConfig
from src.services.anomalib_service import AnomalibService
from src.services.inference_service import InferenceService
from src.services.report_service import ReportService
from src.utils.logger import get_logger
from src.utils.workers import FunctionWorker
from src.viewmodels.project_vm import ProjectViewModel

logger = get_logger("evaluate_vm")


class EvaluateViewModel(QObject):
    """模型评估页的业务逻辑。"""

    configChanged = Signal(object)       # EvaluationConfig
    resultReady = Signal(dict)           # {annotated, records, count, elapsed}
    cameraFrame = Signal(object, list)   # 相机实时：标注图, 检测记录
    taskStarted = Signal(str)
    taskProgress = Signal(int, str)
    taskFinished = Signal(str)
    taskFailed = Signal(str)
    message = Signal(str, str)           # level, text

    def __init__(
        self,
        project_vm: ProjectViewModel | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._config = EvaluationConfig()
        self._project_vm = project_vm
        self._worker: FunctionWorker | None = None
        self._records: list = []

    # -----------------------------------------------------------
    # 查询
    # -----------------------------------------------------------
    @property
    def config(self) -> EvaluationConfig:
        return self._config

    @property
    def records(self) -> list:
        return self._records

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # -----------------------------------------------------------
    # 配置
    # -----------------------------------------------------------
    def set_source(self, source_type: str) -> None:
        self._config.source_type = source_type
        self.configChanged.emit(self._config)

    def set_weights(self, path: str) -> None:
        self._config.weights_path = path
        self.configChanged.emit(self._config)

    def set_source_path(self, path: str) -> None:
        self._config.source_path = path
        self.configChanged.emit(self._config)

    def set_thresholds(self, confidence: float, iou: float) -> None:
        self._config.confidence = confidence
        self._config.iou = iou
        self.configChanged.emit(self._config)

    def set_device(self, device: str) -> None:
        self._config.device = device
        self.configChanged.emit(self._config)

    def set_anomaly_model(self, name: str) -> None:
        if name:
            self._config.anomaly_model = name
            self.configChanged.emit(self._config)

    # -----------------------------------------------------------
    # 异常检测（Anomalib）
    # -----------------------------------------------------------
    def _run_anomaly(self, ckpt: str) -> None:
        """用 Anomalib 检查点对图片 / 目录做异常检测。"""
        if not AnomalibService.is_available():
            self.message.emit("error", "未安装 Anomalib，无法执行异常检测")
            return
        source = self._config.source_path
        if not source or not Path(source).exists():
            self.message.emit("warning", "请选择要检测的图片或目录")
            return

        model_name = self._config.anomaly_model or "Padim"

        def job(progress, _is_cancelled):
            progress(20, "异常检测推理中")
            return AnomalibService.predict(ckpt, model_name, source)

        self._start_worker(job, "异常检测", on_done=self._apply_anomaly)

    def _apply_anomaly(self, results: list) -> None:
        records: list[dict] = []
        abnormal = 0
        for item in results or []:
            is_abnormal = int(item.get("label", 0)) == 1
            abnormal += 1 if is_abnormal else 0
            records.append({
                "class_name": "异常" if is_abnormal else "正常",
                "confidence": round(float(item.get("score", 0.0)), 4),
                "path": str(item.get("path", "")),
            })
        self._records = records
        self.resultReady.emit({
            "annotated": None,
            "records": records,
            "count": len(records),
            "abnormal": abnormal,
            "elapsed": 0.0,
        })
        self.message.emit(
            "success", f"异常检测完成：{abnormal} / {len(records)} 张判定为异常"
        )

    # -----------------------------------------------------------
    # 检测
    # -----------------------------------------------------------
    def run_detection(self) -> None:
        """按当前输入源执行一次检测（相机为连续检测）。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行，请先停止")
            return
        weights = self._config.weights_path
        if not weights:
            self.message.emit("warning", "请先选择模型权重")
            return

        # .ckpt 走 Anomalib 异常检测分支
        if Path(weights).suffix.lower() == ".ckpt":
            self._run_anomaly(weights)
            return

        if not InferenceService.is_available():
            self.message.emit("error", "未安装 Ultralytics / OpenCV，无法执行检测")
            return

        source_type = self._config.source_type
        if source_type == "camera":
            self.start_camera()
            return

        source = self._config.source_path
        if not source or not Path(source).exists():
            self.message.emit("warning", "请选择要检测的图片或视频")
            return

        conf = self._config.confidence
        iou = self._config.iou
        device = self._config.device

        def job(progress, is_cancelled):
            if source_type == "video":
                return InferenceService.detect_video(
                    weights, source, conf=conf, iou=iou, device=device,
                    progress=progress, is_cancelled=is_cancelled,
                )
            return InferenceService.detect_image(
                weights, source, conf=conf, iou=iou, device=device
            )

        def done(result):
            self._apply_result(result)

        self._start_worker(job, "缺陷检测", on_done=done)

    def start_camera(self) -> None:
        """打开相机并持续检测，直到调用 stop()。"""
        if self.is_busy():
            self.message.emit("warning", "已有任务在运行")
            return
        weights = self._config.weights_path
        conf = self._config.confidence
        iou = self._config.iou
        device = self._config.device
        index = int(self._config.source_path or 0)

        def job(progress, is_cancelled):
            import cv2

            model = InferenceService.load_model(weights)
            capture = cv2.VideoCapture(index)
            if not capture.isOpened():
                raise RuntimeError(f"无法打开相机（序号 {index}）")
            frames = 0
            try:
                while not is_cancelled():
                    ok, frame = capture.read()
                    if not ok:
                        break
                    results = model.predict(
                        source=frame, conf=conf, iou=iou, device=device, verbose=False
                    )
                    if results:
                        result = results[0]
                        records = InferenceService.extract_records(result)
                        self._records = records
                        self.cameraFrame.emit(
                            InferenceService.plot(result), records
                        )
                    frames += 1
                    progress(min(frames, 100), f"已检测 {frames} 帧")
            finally:
                capture.release()
            return {"frames": frames}

        self._start_worker(job, "相机检测")

    def stop(self) -> None:
        """停止当前检测任务（视频 / 相机）。"""
        if not self.is_busy():
            self.message.emit("warning", "当前没有正在进行的检测")
            return
        self._worker.cancel()
        self.message.emit("info", "已请求停止检测")

    # -----------------------------------------------------------
    # 报告导出
    # -----------------------------------------------------------
    def export_report(self, path: str) -> None:
        """按扩展名导出 CSV 或 Excel 报告。"""
        if not self._records:
            self.message.emit("warning", "没有可导出的检测结果")
            return

        try:
            if path.lower().endswith((".xlsx", ".xls")):
                ReportService.write_excel(path, self._records)
            else:
                ReportService.write_csv(path, self._records)
        except Exception as exc:  # noqa: BLE001 - 文件写入异常类型较多
            logger.warning("导出报告失败: %s", exc)
            self.message.emit("error", f"导出报告失败：{exc}")
            return
        self.message.emit("success", f"报告已导出：{path}")

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _apply_result(self, result: dict) -> None:
        records = list(result.get("records") or [])
        self._records = records
        self.resultReady.emit({
            "annotated": result.get("annotated"),
            "records": records,
            "count": len(records),
            "elapsed": float(result.get("elapsed") or 0.0),
            "frames": result.get("frames"),
        })
        self.message.emit("success", f"检测完成：{len(records)} 个目标")

    def _start_worker(self, job, name: str, on_done=None) -> None:
        worker = FunctionWorker(job, self)
        worker.progress.connect(self.taskProgress.emit)
        worker.finishedOk.connect(
            lambda payload: self._on_task_done(name, payload, on_done)
        )
        worker.failed.connect(lambda error: self._on_task_failed(name, error))
        self._worker = worker
        self.taskStarted.emit(name)
        worker.start()

    def _on_task_done(self, name: str, payload, on_done) -> None:
        if on_done is not None:
            try:
                on_done(payload)
            except Exception as exc:  # noqa: BLE001 - 结果应用异常需回传界面
                logger.warning("%s结果应用失败: %s", name, exc)
                self.taskFailed.emit(f"{name}失败：{exc}")
                return
            self.taskFinished.emit(f"{name}完成")
        else:
            self.taskFinished.emit(str(payload) if payload else f"{name}完成")

    def _on_task_failed(self, name: str, error: str) -> None:
        logger.warning("%s失败: %s", name, error)
        self.taskFailed.emit(f"{name}失败：{error}")
