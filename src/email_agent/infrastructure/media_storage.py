"""Local storage for email media used by multimodal analysis."""
import hashlib
import re
from pathlib import Path

from email_agent.domain.models import EmailMedia, StoredMedia


class MediaStorage:
    IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"}

    def __init__(self, media_root: Path, config: dict | None = None):
        self.media_root = Path(media_root)
        self.config = config or {}
        self.max_media_per_email = int(self.config.get("max_media_per_email", 8))
        self.max_image_bytes = int(self.config.get("max_image_bytes", 10 * 1024 * 1024))
        self.max_video_bytes = int(self.config.get("max_video_bytes", 50 * 1024 * 1024))
        self.video_frame_count = int(self.config.get("video_frame_count", 3))

    def save(self, message_id: str, media: list[EmailMedia]) -> tuple[list[StoredMedia], list[str]]:
        self.media_root.mkdir(parents=True, exist_ok=True)
        message_dir = self._message_dir(message_id)
        message_dir.mkdir(parents=True, exist_ok=True)
        stored, errors = [], []
        for index, item in enumerate(media, 1):
            if index > self.max_media_per_email:
                errors.append(f"{item.filename} 超过单封邮件媒体数量限制")
                continue
            if not self._is_supported(item):
                errors.append(f"{item.filename} 媒体类型不支持：{item.content_type}")
                continue
            limit = self.max_image_bytes if item.content_type in self.IMAGE_TYPES else self.max_video_bytes
            size = item.size_bytes or len(item.data)
            if size > limit:
                kind = "图片" if item.content_type in self.IMAGE_TYPES else "视频"
                errors.append(f"{item.filename} 超过{kind}大小限制")
                continue
            target = self._unique_path(message_dir, item.filename, item.media_id)
            target.write_bytes(item.data)
            original = StoredMedia(
                media_id=item.media_id,
                filename=target.name,
                content_type=item.content_type,
                source=item.source,
                path=str(target),
                size_bytes=target.stat().st_size,
                content_id=item.content_id,
                metadata={"kind": "original", **item.metadata},
            )
            stored.append(original)
            if item.content_type in self.VIDEO_TYPES:
                frames, frame_errors = self._extract_video_frames(target, item.media_id)
                stored.extend(frames)
                errors.extend(frame_errors)
        return stored, errors

    @staticmethod
    def manifest(stored: list[StoredMedia]) -> list[dict]:
        return [
            {
                "media_id": item.media_id,
                "filename": item.filename,
                "content_type": item.content_type,
                "source": item.source,
                "path": item.path,
                "size_bytes": item.size_bytes,
                "content_id": item.content_id,
                "derived_from": item.derived_from,
                "metadata": item.metadata,
            }
            for item in stored
        ]

    def _message_dir(self, message_id: str) -> Path:
        digest = hashlib.sha256(str(message_id or "unknown").encode("utf-8")).hexdigest()[:32]
        return self.media_root / digest

    def _unique_path(self, directory: Path, filename: str, media_id: str) -> Path:
        clean = self._safe_filename(filename)
        stem = Path(clean).stem[:80] or "media"
        suffix = Path(clean).suffix.lower()[:10] or ".bin"
        target = directory / f"{stem}-{media_id[:8]}{suffix}"
        resolved = target.resolve()
        if directory.resolve() not in resolved.parents:
            raise ValueError("媒体文件路径越界")
        return resolved

    @classmethod
    def _safe_filename(cls, filename: str) -> str:
        name = Path(str(filename or "")).name.strip()
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" ._")
        return name or "media.bin"

    @classmethod
    def _is_supported(cls, item: EmailMedia) -> bool:
        return item.content_type in cls.IMAGE_TYPES | cls.VIDEO_TYPES

    def _extract_video_frames(self, video_path: Path, media_id: str) -> tuple[list[StoredMedia], list[str]]:
        try:
            import cv2
        except Exception as exc:
            return [], [f"视频关键帧提取失败：OpenCV 不可用：{exc}"]
        try:
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                return [], ["视频关键帧提取失败：无法解码视频"]
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count <= 0:
                return [], ["视频关键帧提取失败：视频没有可读取帧"]
            count = max(1, self.video_frame_count)
            positions = sorted({min(frame_count - 1, int(frame_count * (i + 1) / (count + 1)))
                                for i in range(count)})
            frames = []
            for number, position in enumerate(positions, 1):
                capture.set(cv2.CAP_PROP_POS_FRAMES, position)
                ok, frame = capture.read()
                if not ok:
                    continue
                frame_path = video_path.with_name(f"{video_path.stem}-frame-{number}.jpg")
                if not cv2.imwrite(str(frame_path), frame):
                    continue
                frame_id = hashlib.sha256(f"{media_id}:{position}".encode("utf-8")).hexdigest()[:32]
                frames.append(
                    StoredMedia(
                        media_id=frame_id,
                        filename=frame_path.name,
                        content_type="image/jpeg",
                        source="video_frame",
                        path=str(frame_path),
                        size_bytes=frame_path.stat().st_size,
                        derived_from=media_id,
                        metadata={"kind": "video_frame", "frame_index": position},
                    )
                )
            if not frames:
                return [], ["视频关键帧提取失败：未能写入关键帧"]
            return frames, []
        except Exception as exc:
            return [], [f"视频关键帧提取失败：{exc}"]
        finally:
            try:
                capture.release()
            except Exception:
                pass
