"""
文件安全校验：魔数（真实类型）、大小、命名、可选病毒扫描
"""
import re
import logging
from typing import Optional, Tuple

from app.core.config import settings


# 扩展名 -> 文件头魔数（前若干字节）。用于校验真实类型与扩展名一致，防止伪造扩展名
_MAGIC_BY_TYPE: dict[str, list[bytes]] = {
    "pdf": [b"%PDF"],
    "zip": [b"PK\x03\x04", b"PK\x05\x06"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpeg": [b"\xff\xd8\xff"],
    "jpg": [b"\xff\xd8\xff"],
    "gif": [b"GIF87a", b"GIF89a"],
    "txt": [],  # 无统一魔数，仅做扩展名白名单
    "md": [],
    "html": [b"<!DOCTYPE", b"<html", b"<HTML"],
    "docx": [b"PK\x03\x04"],  # Office 为 zip 格式
    "xlsx": [b"PK\x03\x04"],
    "pptx": [b"PK\x03\x04"],
    "ppt": [b"\xd0\xcf\x11\xe0"],  # OLE
}


def _get_magic_for_extension(ext: str) -> Optional[list[bytes]]:
    ext = (ext or "").strip().lower()
    return _MAGIC_BY_TYPE.get(ext)


def validate_file_content(content: bytes, extension_from_filename: str) -> None:
    """
    根据文件头魔数校验真实类型与扩展名一致。若该扩展名未配置魔数则只做扩展名白名单校验（由调用方保证）。
    不一致时抛出 ValueError。
    """
    if not content:
        raise ValueError("文件内容为空")
    ext = (extension_from_filename or "").strip().lower()
    allowed = getattr(settings, "allowed_file_types_list", None) or []
    if ext not in allowed:
        raise ValueError(f"不允许上传该类型: {ext}，允许: {', '.join(allowed)}")
    magics = _get_magic_for_extension(ext)
    if magics is None or len(magics) == 0:
        # 无魔数配置的类型（如 txt, md）仅依赖扩展名白名单
        return
    for magic in magics:
        if content[: len(magic)] == magic:
            return
    raise ValueError(
        f"文件真实类型与扩展名不符（扩展名为 .{ext}），可能为伪造类型，已拒绝上传"
    )


def validate_filename(filename: str) -> None:
    """校验文件名：长度、禁止路径穿越、禁止危险扩展名。"""
    if not filename or not filename.strip():
        raise ValueError("文件名为空")
    name = filename.strip()
    max_len = getattr(settings, "FILE_NAME_MAX_LENGTH", 200)
    if len(name) > max_len:
        raise ValueError(f"文件名长度不能超过 {max_len} 个字符")
    if ".." in name or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("文件名不得包含路径或非法字符")
    # 禁止扩展名（可执行/脚本）
    forbidden = getattr(settings, "forbidden_file_extensions_list", None) or []
    ext = name.split(".")[-1].lower() if "." in name else ""
    if ext in forbidden:
        raise ValueError(f"禁止上传该类型文件: .{ext}")


def virus_scan_content(content: bytes) -> Tuple[bool, str]:
    """
    可选病毒扫描。返回 (是否通过, 消息)。
    未启用或未配置 ClamAV 时直接返回 (True, "")。
    """
    if not getattr(settings, "FILE_VIRUS_SCAN_ENABLED", False):
        return True, ""
    socket_path = getattr(settings, "CLAMAV_SOCKET", "").strip()
    if not socket_path:
        return True, ""
    try:
        import clamd
        cd = clamd.ClamdUnixSocket(socket_path)
        result = cd.scan_stream(content)
        if result is None:
            return True, ""
        # result 形如 {None: ('OK', '')} 或 {None: ('FOUND', 'VirusName')}
        for key, (status, msg) in (result or {}).items():
            if status != "OK":
                logging.warning("病毒扫描发现: %s %s", status, msg)
                return False, msg or "检测到恶意内容"
        return True, ""
    except ImportError:
        logging.debug("clamd 未安装，跳过病毒扫描")
        return True, ""
    except Exception as e:
        logging.warning("病毒扫描失败（按通过处理）: %s", e)
        return True, ""
