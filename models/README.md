# 预训练模型（随程序发布）

本目录存放程序运行所需的**全部预训练权重**，随程序一起发布，
开发、测试与最终用户都不需要联网下载。解析逻辑见 `src/utils/pretrained.py`。

> 目录约定：`models/<供应商>/<模型>`。

## 一、清单

### 1. `ultralytics/` —— YOLO 官方权重与 SAM（训练起点 / 预标注）

| 文件 | 用途 |
|------|------|
| `yolo11{n,s,m,l,x}.pt` | 对象检测训练起点（YOLO11，n~x 五档） |
| `yolo11{n,s,m,l,x}-seg.pt` | 实例分割 |
| `yolo11{n,s,m,l,x}-cls.pt` | 整图分类 |
| `yolo11{n,s,m,l,x}-obb.pt` | 对象检测·旋转框 |
| `yolo26{n,s,m,l,x}.pt` 及同名 `-seg / -cls / -obb` | 新一代 YOLO26，同样四类任务 |
| `mobile_sam.pt` | 标注页「SAM 细化选中」的轻量分割模型 |

合计 **41 个文件 / 约 1.7 GB**。

### 2. `anomalib/` —— 异常检测骨干（timm 预训练）

| 路径 | 说明 |
|------|------|
| `anomalib/huggingface/` | HuggingFace 缓存根（`HF_HOME`）。Padim / Fastflow 用 `resnet18.a1_in1k`；Patchcore / ReverseDistillation 用 `wide_resnet50_2.racm_in1k` |
| `anomalib/torch/` | torch.hub / torchvision 权重缓存根（`TORCH_HOME`），备用 |

说明：**EfficientAd** 的 teacher 为 anomalib 自研网络（无外部预训练权重），
无需额外下载；其可选的 imagenette 微调数据不属于模型，训练时按需提供。

程序启动时（`src/app.py`）与训练子进程（`train_worker` / `anomaly_worker`）
都会调用 `apply_model_env()`，把上述两个缓存目录指向本目录（强制生效）。

## 二、程序如何使用

| 场景 | 解析方式 |
|------|---------|
| YOLO 训练（传给子进程的 `--weights`） | `resolve_pretrained(model_key + ".pt")` |
| 预标注（YOLO 候选框 / SAM 细化） | `resolve_pretrained("yolo11n.pt" / "mobile_sam.pt")` |
| 模型管理（加载官方权重探测信息） | 同上 |
| 异常检测骨干 | `HF_HOME` / `TORCH_HOME` → `models/anomalib/...` |

`resolve_pretrained()` 只处理「纯文件名」输入：项目内命中 → 返回本地绝对路径；
用户自定义权重或带目录的路径 → 原样返回；项目内缺失 → 原样返回并交给
Ultralytics 自行下载（开发期兜底）。**发布版应保证本目录清单完整**。

## 三、重新下载 / 更新（跨机开发）

**推荐：一键脚本**（新电脑 clone 后运行一次即可拉齐）：

```powershell
python scripts/download_models.py            # 默认优先国内加速镜像（gh-proxy.com）
python scripts/download_models.py --direct   # 官方源直连（境外网络）
```

脚本特性：

* **可重复运行**：已存在且大小合理的文件自动跳过，缺失的补齐（断点重跑即可）；
* 下载 `ultralytics/` 全部 41 个权重，并经 HF 镜像（`hf-mirror.com`）缓存
  `anomalib/` 骨干；清单直接读取代码里的模型注册表，不会遗漏；
* 优先用 `curl --ssl-no-revoke` 下载（规避 Windows 证书吊销检查导致的失败），
  镜像失败自动换直连。

**手工方式**（个别文件）。官方源为 GitHub，权重版本对应 **assets v8.4.0**
（与 `requirements.txt` 的 ultralytics 8.4.x 匹配）：

```powershell
# 单文件（--ssl-no-revoke 规避 Windows schannel 吊销检查导致的失败）
curl.exe --ssl-no-revoke -L -o models/ultralytics/yolo11n.pt `
  https://gh-proxy.com/https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n.pt
```

异常检测骨干（走 HF 国内镜像）：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:HF_HOME = "<项目路径>/models/anomalib/huggingface"
python -c "import timm; timm.create_model('resnet18', pretrained=True); timm.create_model('wide_resnet50_2', pretrained=True)"
```

## 四、与版本控制的关系

`.gitignore` 默认忽略 `*.pt`（避免约 2 GB 模型进入 git 历史，拖慢 clone / push）。
发布打包时请把**整个 `models/` 目录**复制进发行包即可离线运行。
若确需将模型纳入 git 仓库，在 `.gitignore` 末尾追加白名单：

```
!models/
!models/**/*.pt
```

## 五、其他任务的模型

OCR（PaddleOCR）训练入口尚未接入（见《开发文档.md》§8.12），故不含其模型；
接入时应在本目录新增 `models/paddleocr/`。
