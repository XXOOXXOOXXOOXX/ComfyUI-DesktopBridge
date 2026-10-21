import base64
import io
from pathlib import Path, PurePosixPath
import zipfile

LIMIT = 128 * 1024 * 1024


def pack(paths, root):
    root = Path(root).resolve()
    output, total = io.BytesIO(), 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(paths):
            path = Path(path)
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("素材引用了目录外部的文件，请先打包到同一目录。")
            total += path.stat().st_size
            if total > LIMIT or index >= 512:
                raise ValueError("单个素材包上限 128 MB / 512 个文件，请减少文件大小。")
            archive.write(path, path.relative_to(root).as_posix())
    return base64.b64encode(output.getvalue()).decode("ascii")


def unpack(encoded, root):
    root = Path(root).resolve()
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > LIMIT + 1024 * 1024:
        raise ValueError("素材包过大。")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members, total = archive.infolist(), 0
        if len(members) > 512:
            raise ValueError("素材包文件过多。")
        for info in members:
            name = PurePosixPath(info.filename)
            total += info.file_size
            target = root.joinpath(*name.parts).resolve()
            if total > LIMIT or name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename or not target.is_relative_to(root) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("素材包包含不安全路径或过大的文件。")
        root.mkdir(parents=True, exist_ok=True)
        for info in members:
            if not info.is_dir():
                target = root.joinpath(*PurePosixPath(info.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
