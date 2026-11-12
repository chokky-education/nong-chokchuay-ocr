import io
import os
import tempfile
from typing import Any, Callable, Dict, Optional

from PIL import Image


def make_png_bytes(width: int = 400, height: int = 200, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """Create valid in-memory PNG bytes for API and Watch Folder tests."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeLineOCR:
    """Deterministic test double for LineOCR matching its public read() contract."""

    def __init__(
        self,
        text: str = "ใบเสร็จรับเงิน\nร้านกาแฟทดสอบ\nยอดสุทธิ 107.00 บาท",
        responder: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> None:
        self.default_text = text
        self.responder = responder
        self.calls: int = 0

    def read(self, image: Any) -> Dict[str, Any]:
        self.calls += 1
        if self.responder is not None:
            return self.responder(image)
        lines = []
        for idx, line_text in enumerate(self.default_text.splitlines()):
            y0 = 20 + idx * 40
            lines.append(
                {
                    "text": line_text,
                    "confidence": 0.96,
                    "row": idx,
                    "box": [[20, y0], [320, y0], [320, y0 + 28], [20, y0 + 28]],
                }
            )
        return {
            "text": self.default_text,
            "lines": lines,
            "rotation": 0,
            "text_angle": 0.0,
        }


def make_temp_data_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="openthai_test_")
