"""单文件项目 (.mprj) 容器格式编解码。

格式布局（大端）：
    偏移   大小   字段
    0       6     Magic: b"DMJPRJ"
    6       2     FormatVersion: uint16
    8       2     Flags: uint16            bit0 = manifest 已加密
    10      4     Reserved: uint32 = 0
    14      4     ManifestLen: uint32
    18      N     Manifest 密文（JSON, UTF-8）
    18+N    ...   ZIP 归档块（stdlib zipfile 整段字节）

保密与完整性（纯 stdlib，混淆级）：
    * 自定义魔数 DMJPRJ，通用解压/识别工具直接判非法。
    * Manifest 用 XOR 流式加密，密钥由固定盐 + 魔数 + 版本派生，
      明文 JSON 不可见。
    * ZIP 归档条目名混淆为内容哈希 ID（f_<sha256[:16]>.bin），真实
      虚拟路径只存在于加密 manifest 中。
    * 每条文件记录携带 SHA256，装载时逐条校验，防篡改/损坏。
    * 版本号守卫：不支持的更高版本拒绝打开；v1 项目仍可打开（按 v1 密钥解密，
      保存时升级为当前版本，读入时补齐 backend 字段）。
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile

from src.models.project import Project
from src.utils.constants import PROJECT_MAGIC, PROJECT_VERSION
from src.utils.logger import get_logger

logger = get_logger("project_format")

# 头部结构: magic(6) + version(H) + flags(H) + reserved(I) + manifest_len(I)
_HEADER = struct.Struct(">6s H H I I")
_HEADER_SIZE = _HEADER.size

# 加密派生盐（固定，配合魔数/版本）
_KEY_SALT = b"DeepMaven/.mprj/container"

FLAG_MANIFEST_ENCRYPTED = 0x0001

# 可打开的容器版本（v1 项目仍可读；保存时统一写为 PROJECT_VERSION）
SUPPORTED_VERSIONS = (1, PROJECT_VERSION)


# ---------------------------------------------------------------
# 加密 / 哈希
# ---------------------------------------------------------------
def _derive_key(version: int = PROJECT_VERSION) -> bytes:
    """由固定盐 + 魔数 + 版本派生加密密钥（确定性）。"""
    return hashlib.sha256(_KEY_SALT + PROJECT_MAGIC + struct.pack(">H", version)).digest()


def _xor_cipher(data: bytes, version: int = PROJECT_VERSION) -> bytes:
    """XOR 流式加密/解密（对称、确定性）。

    用密钥迭代 sha256 展开足够长的密钥流，与明文异或。
    """
    key = _derive_key(version)
    keystream = bytearray()
    counter = 0
    while len(keystream) < len(data):
        keystream.extend(hashlib.sha256(key + counter.to_bytes(4, "big")).digest())
        counter += 1
    ks = memoryview(keystream)[: len(data)]
    return bytes(a ^ b for a, b in zip(data, ks))


def encrypt_manifest(payload: bytes, version: int = PROJECT_VERSION) -> bytes:
    return _xor_cipher(payload, version)


def decrypt_manifest(payload: bytes, version: int = PROJECT_VERSION) -> bytes:
    return _xor_cipher(payload, version)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def to_entry_id(data: bytes) -> str:
    """归档条目混淆名：内容哈希前 16 位。"""
    return f"f_{sha256_bytes(data)[:16]}.bin"


# ---------------------------------------------------------------
# 容器
# ---------------------------------------------------------------
class ProjectContainer:
    """一个 .mprj 容器：持有原始字节与反序列化后的 Project。"""

    def __init__(self, data: bytes, project: Project):
        self.data = data
        self.project = project

    # -----------------------------------------------------------
    # 构建
    # -----------------------------------------------------------
    @classmethod
    def create(cls, project: Project, files: dict[str, bytes] | None = None) -> "ProjectContainer":
        """从 Project 构建新容器（files 为 entry_id -> 字节，可为空）。"""
        data = cls._pack(project, files or {})
        return cls(data, project)

    # -----------------------------------------------------------
    # 解析
    # -----------------------------------------------------------
    @classmethod
    def open(cls, data: bytes, verify: bool = True) -> "ProjectContainer":
        """解析容器字节并校验魔数 / 版本 / 文件哈希，返回容器。

        Args:
            verify: 是否逐条校验归档文件哈希。最近项目预览等只读场景可传
                False 以加快速度（容器级哈希仍需通过）。
        """
        if len(data) < _HEADER_SIZE:
            raise ProjectFormatError("文件过短，不是有效的 .mprj 项目")

        magic, version, flags, _reserved, manifest_len = _HEADER.unpack_from(data, 0)
        if magic != PROJECT_MAGIC:
            raise ProjectFormatError("文件头无效：非 DeepMaven 项目文件 (.mprj)")
        if version not in SUPPORTED_VERSIONS:
            supported = " / ".join(f"v{item}" for item in SUPPORTED_VERSIONS)
            raise ProjectFormatError(
                f"不支持的项目格式版本 v{version}（当前支持 {supported}）"
            )

        manifest_off = _HEADER_SIZE
        manifest_end = manifest_off + manifest_len
        if manifest_end > len(data):
            raise ProjectFormatError("项目清单损坏")

        manifest_cipher = data[manifest_off:manifest_end]
        if flags & FLAG_MANIFEST_ENCRYPTED:
            # 密钥由版本派生：旧版本文件必须按写入时的版本解密
            manifest_cipher = decrypt_manifest(manifest_cipher, version)
        try:
            raw_dict = json.loads(manifest_cipher.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectFormatError("项目清单解析失败，文件可能已损坏") from exc

        # 容器级完整性：校验整个 ZIP 载荷（含头部/校验字段）哈希
        zip_bytes = data[manifest_end:]
        embedded = raw_dict.pop("__container_sha256", "")
        if sha256_bytes(zip_bytes) != embedded:
            raise ProjectFormatError("项目文件完整性校验失败（已被篡改或损坏）")

        project = Project.from_dict(raw_dict)
        container = cls(data, project)

        # 校验归档内每条文件哈希
        if verify:
            with cls._open_zip(zip_bytes) as zf:
                for f in project.files:
                    try:
                        raw = zf.read(f.entry_id)
                    except KeyError as exc:
                        raise ProjectFormatError(
                            f"归档缺少文件: {f.virtual_path}"
                        ) from exc
                    if sha256_bytes(raw) != f.sha256:
                        raise ProjectFormatError(
                            f"文件校验失败（已被篡改或损坏）: {f.virtual_path}"
                        )

        logger.info("打开项目「%s」：%s 个文件", project.name, project.file_count)
        return container

    # -----------------------------------------------------------
    # 重打包
    # -----------------------------------------------------------
    def rebuild(self, project: Project) -> "ProjectContainer":
        """按当前 Project 重新打包（读取原归档字节，校验后重写）。"""
        # 收集仍在引用的条目字节
        files: dict[str, bytes] = {}
        with self._open_zip(self._zip_bytes()) as zf:
            entry_ids = {f.entry_id for f in project.files}
            for entry_id in entry_ids:
                files[entry_id] = zf.read(entry_id)
        data = self._pack(project, files)
        return ProjectContainer(data, project)

    # -----------------------------------------------------------
    # 文件读取
    # -----------------------------------------------------------
    def get_file_bytes(self, entry_id: str) -> bytes:
        with self._open_zip(self._zip_bytes()) as zf:
            try:
                raw = zf.read(entry_id)
            except KeyError as exc:
                raise KeyError(f"归档中不存在条目: {entry_id}") from exc
        return raw

    def get_files_bytes(self, entry_ids: list[str]) -> dict[str, bytes]:
        """批量读取条目（只打开一次归档，避免逐条重复解析）。"""
        result: dict[str, bytes] = {}
        if not entry_ids:
            return result
        with self._open_zip(self._zip_bytes()) as zf:
            for entry_id in entry_ids:
                try:
                    result[entry_id] = zf.read(entry_id)
                except KeyError as exc:
                    raise KeyError(f"归档中不存在条目: {entry_id}") from exc
        return result

    # -----------------------------------------------------------
    # 内部
    # -----------------------------------------------------------
    def _zip_bytes(self) -> bytes:
        manifest_len = _HEADER.unpack_from(self.data, 0)[4]
        return self.data[_HEADER_SIZE + manifest_len:]

    @staticmethod
    def _open_zip(zip_bytes: bytes) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(zip_bytes), "r")

    @staticmethod
    def _pack(project: Project, files: dict[str, bytes]) -> bytes:
        """将 Project 与文件字节打包为完整 .mprj 字节序列。"""
        # ZIP 载荷
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry_id, content in files.items():
                info = zipfile.ZipInfo(entry_id)
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, content)
        zip_bytes = buf.getvalue()

        # 容器级完整性：把 ZIP 载荷哈希写入清单，打开时校验
        manifest = project.to_dict()
        manifest["__container_sha256"] = sha256_bytes(zip_bytes)
        manifest_plain = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        manifest_cipher = encrypt_manifest(manifest_plain)

        flags = FLAG_MANIFEST_ENCRYPTED
        header = _HEADER.pack(
            PROJECT_MAGIC, PROJECT_VERSION, flags, 0, len(manifest_cipher)
        )
        return header + manifest_cipher + zip_bytes


class ProjectFormatError(Exception):
    """.mprj 格式相关错误（魔数不符、版本不符、损坏、篡改等）。"""