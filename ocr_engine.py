"""글자 인식(OCR) 엔진 모듈.

- 윈도우: 윈도우에 들어 있는 OCR(Windows.Media.Ocr)을 winrt 패키지로 부른다. 인터넷 연결이 필요 없고 별도 모델을 담지 않는다.
- 그 밖의 환경(개발용): pytesseract 가 있으면 그걸로 흉내 낸다. 배포 파일에는 쓰이지 않는다.

화면 코드는 이 파일의 recognize() 와 available_languages() 만 알면 된다.
"""
from __future__ import annotations

import asyncio
import io
import sys
import time
from dataclasses import dataclass, field

from PIL import Image

IS_WIN = sys.platform == "win32"

UPSCALE_BELOW = 1600  # 긴 변이 이보다 작으면 키워서 읽는다 (작은 글자는 인식이 약하다)
UPSCALE = 2.0
FALLBACK_MAX_DIM = 10000  # 윈도우가 알려주는 최대 크기를 못 읽을 때의 기본값

INSTALL_HELP = (
    "이 PC에 글자 인식(OCR) 기능이 설치되어 있지 않아요.\n"
    "윈도우 설정 → 시간 및 언어 → 언어 및 지역 → 한국어 → 언어 옵션에서 "
    "'광학 문자 인식'을 설치한 뒤 다시 시도해 주세요. 설치가 막혀 있으면 전산 담당자에게 문의해 주세요."
)


class OcrError(Exception):
    """사용자에게 그대로 보여줄 수 있는 인식 실패 사유."""


@dataclass
class OcrLine:
    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    seconds: float = 0.0
    engine: str = ""
    language: str = ""


# ---------------------------------------------------------------- 언어 목록
def available_languages() -> list[tuple[str, str]]:
    """[(언어 태그, 표시 이름)]. 비어 있으면 인식 기능을 쓸 수 없는 PC 다."""
    if IS_WIN:
        try:
            from winrt.windows.media.ocr import OcrEngine

            return [(lg.language_tag, lg.display_name) for lg in OcrEngine.available_recognizer_languages]
        except Exception:
            return []
    try:
        import pytesseract

        have = set(pytesseract.get_languages(config=""))
    except Exception:
        return []
    out = []
    if "kor" in have:
        out.append(("ko", "한국어 (개발용 엔진)"))
    if "eng" in have:
        out.append(("en", "English (개발용 엔진)"))
    return out


def default_language(langs: list[tuple[str, str]]) -> str:
    """한국어가 있으면 한국어, 없으면 첫 번째."""
    for tag, _name in langs:
        if tag.lower().startswith("ko"):
            return tag
    return langs[0][0] if langs else ""


# ---------------------------------------------------------------- 공통 전처리
def _prepare(img: Image.Image, max_dim: int) -> tuple[Image.Image, float]:
    """인식용 이미지(RGB)와 배율(원본 대비). 좌표는 나중에 이 배율로 나눠 되돌린다."""
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.split()[3])
        img = bg
    else:
        img = img.convert("RGB")
    scale = 1.0
    if max(img.size) < UPSCALE_BELOW:
        scale = UPSCALE
    if max(img.size) * scale > max_dim:
        scale = max_dim / max(img.size)
    if abs(scale - 1.0) > 1e-3:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    return img, scale


def recognize(img: Image.Image, lang: str = "", region: tuple[int, int, int, int] | None = None) -> OcrResult:
    """이미지(또는 그 안의 영역 region=(x,y,w,h))에서 글자를 읽는다. 좌표는 원본 이미지 기준."""
    t0 = time.perf_counter()
    ox = oy = 0
    if region is not None:
        x, y, w, h = region
        x, y = max(0, x), max(0, y)
        w, h = min(w, img.width - x), min(h, img.height - y)
        if w < 2 or h < 2:
            raise OcrError("선택한 영역이 너무 작아요.")
        img = img.crop((x, y, x + w, y + h))
        ox, oy = x, y
    if IS_WIN:
        lines, engine = _recognize_windows(img, lang)
    else:
        lines, engine = _recognize_dev(img, lang)
    for ln in lines:
        ln.x += ox
        ln.y += oy
    return OcrResult(lines=[ln for ln in lines if ln.text.strip()], seconds=time.perf_counter() - t0,
                     engine=engine, language=lang)


# ---------------------------------------------------------------- 윈도우 내장 OCR
def _init_apartment() -> None:
    """작업 스레드에서 윈도우 런타임을 쓸 수 있게 준비한다 (이미 되어 있으면 조용히 넘어간다)."""
    try:
        import winrt.runtime as rt

        rt.init_apartment(rt.ApartmentType.MULTI_THREADED)
    except Exception:
        pass


def _recognize_windows(img: Image.Image, lang: str) -> tuple[list[OcrLine], str]:
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    except Exception as ex:  # 패키지가 빠졌거나 윈도우 버전이 너무 낮을 때
        raise OcrError(f"윈도우 글자 인식 구성요소를 불러오지 못했어요. ({ex})") from ex
    _init_apartment()
    try:
        max_dim = int(OcrEngine.max_image_dimension)
    except Exception:
        max_dim = FALLBACK_MAX_DIM
    prepared, scale = _prepare(img, max_dim)
    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    raw = asyncio.run(_win_recognize(buf.getvalue(), lang))
    lines = [OcrLine(t, x / scale, y / scale, w / scale, h / scale) for (t, x, y, w, h) in raw]
    return lines, "windows"


async def _win_recognize(png: bytes, lang: str) -> list[tuple[str, float, float, float, float]]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = None
    if lang:
        try:
            engine = OcrEngine.try_create_from_language(Language(lang))
        except Exception:
            engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrError(INSTALL_HELP)

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED)
    result = await engine.recognize_async(bitmap)

    out: list[tuple[str, float, float, float, float]] = []
    for ln in result.lines:
        xs, ys, xe, ye = [], [], [], []
        for wd in ln.words:
            r = wd.bounding_rect
            xs.append(r.x)
            ys.append(r.y)
            xe.append(r.x + r.width)
            ye.append(r.y + r.height)
        if not xs:
            continue
        x0, y0 = min(xs), min(ys)
        out.append((ln.text, x0, y0, max(xe) - x0, max(ye) - y0))
    return out


# ---------------------------------------------------------------- 개발용(비 윈도우) 엔진
def _recognize_dev(img: Image.Image, lang: str) -> tuple[list[OcrLine], str]:
    try:
        import pytesseract
    except Exception as ex:
        raise OcrError("이 환경에는 글자 인식 엔진이 없어요. (윈도우에서 실행해 주세요)") from ex
    prepared, scale = _prepare(img.convert("L") if img.mode != "RGB" else img, FALLBACK_MAX_DIM)
    tl = {"ko": "kor+eng", "en": "eng"}.get(lang.split("-")[0], "kor+eng")
    data = pytesseract.image_to_data(prepared, lang=tl, config="--psm 6", output_type=pytesseract.Output.DICT)
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, txt in enumerate(data["text"]):
        if txt.strip() and float(data["conf"][i]) >= 0:
            groups.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    lines = []
    for idxs in groups.values():
        x0 = min(data["left"][i] for i in idxs)
        y0 = min(data["top"][i] for i in idxs)
        x1 = max(data["left"][i] + data["width"][i] for i in idxs)
        y1 = max(data["top"][i] + data["height"][i] for i in idxs)
        lines.append(OcrLine(" ".join(data["text"][i] for i in idxs), x0 / scale, y0 / scale,
                             (x1 - x0) / scale, (y1 - y0) / scale))
    lines.sort(key=lambda ln: (round(ln.y / 8), ln.x))
    return lines, "dev-tesseract"


# ---------------------------------------------------------------- 점검용 (빌드 직후 자동 확인)
def selftest(path: str) -> int:
    """`QTextExtract.exe --selftest 결과파일` — 묶인 프로그램에서 글자 인식 부품이 실제로 도는지 확인한다."""
    log: list[str] = []
    ok = True
    try:
        langs = available_languages()
        log.append(f"languages: {langs}")
        if not langs:
            log.append("SKIP: 이 PC에는 인식 언어가 없어요 (구성요소 불러오기는 성공)")
        else:
            from PIL import ImageDraw, ImageFont

            im = Image.new("RGB", (900, 220), "white")
            d = ImageDraw.Draw(im)
            font = None
            for name in ("arial.ttf", "malgun.ttf", "DejaVuSans.ttf"):
                try:
                    font = ImageFont.truetype(name, 64)
                    break
                except Exception:
                    continue
            d.text((30, 60), "Hello OCR 12345", fill="black", font=font or ImageFont.load_default())
            tag = next((t for t, _ in langs if t.lower().startswith("en")), langs[0][0])
            res = recognize(im, tag)
            text = " ".join(ln.text for ln in res.lines)
            log.append(f"recognized({tag}): {text!r} in {res.seconds:.2f}s")
            if "hello" not in text.lower():
                ok = False
                log.append("FAIL: 'Hello' 가 인식되지 않았어요")
    except Exception as ex:  # noqa: BLE001
        ok = False
        log.append(f"FAIL: {type(ex).__name__}: {ex}")
    log.append("RESULT: PASS" if ok else "RESULT: FAIL")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0 if ok else 1
