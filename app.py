"""Q 텍스트 추출 - 보도자료 문서(PDF·HWPX·HWP)의 글을 그대로 꺼내고, 이미지는 글자 인식(OCR)으로 읽어 복사할 수 있는 글로 만드는 윈도우 데스크톱 앱."""
from __future__ import annotations

import io
import os
import sys
import time

from PIL import Image, ImageOps
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRect, QRectF, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import aidraft
import aiui
import docextract
import ocr_engine
import textops
from feedback import APP_VERSION, ask_on_exit
from style import C_ACCENT, C_MUTED, C_ON_ACCENT, C_WARN, apply_theme

APP_NAME = "Q 텍스트 추출"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")
DOC_EXTS = docextract.DOC_EXTS  # .pdf .hwpx .hwp
MIN_REGION_PX = 8  # 영역으로 인정하는 최소 드래그 크기(화면 px)
OCR_NOTICE = ("이미지에서 글자를 읽은 결과예요. 숫자·고유명사가 틀릴 수 있으니 원본과 꼭 대조해 주세요. "
              "보도자료 원본(PDF·HWPX)이 있으면 그 파일을 열면 틀리지 않아요.")


def resource_path(*parts: str) -> str:
    """개발 중이든 PyInstaller 로 묶었든 같은 방식으로 리소스 경로를 찾는다."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_icon() -> QIcon:
    """창 제목줄/작업표시줄 아이콘. assets 폴더가 없어도 내장 데이터로 나오게 한다."""
    icon = QIcon()
    for n in (16, 24, 32, 48, 64, 128, 256):
        path = resource_path("assets", f"icon_{n}.png")
        if os.path.exists(path):
            icon.addFile(path, QSize(n, n))
    if icon.isNull():
        try:
            import base64

            from icon_data import ICON_PNG_B64

            for n, b64 in ICON_PNG_B64.items():
                pm = QPixmap()
                if pm.loadFromData(base64.b64decode(b64), "PNG"):
                    icon.addPixmap(pm)
        except Exception:
            pass
    return icon


def set_dark_titlebar(widget: QWidget) -> None:
    """Windows 10/11 에서 제목줄도 어둡게 (지원 안 되는 환경에서는 조용히 넘어간다)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


# ---------------------------------------------------------------- 이미지 읽기/변환
def load_image(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img.load()
    return img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    rgba = img.convert("RGBA")
    qimg = QImage(rgba.tobytes("raw", "RGBA"), rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def qimage_to_pil(qimg: QImage) -> Image.Image:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    qimg.save(buf, "PNG")
    buf.close()
    img = Image.open(io.BytesIO(bytes(ba)))
    img.load()
    return img


# ---------------------------------------------------------------- 인식 작업 스레드
class OcrWorker(QThread):
    ok = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, job: int, img: Image.Image, lang: str, region, parent=None) -> None:
        super().__init__(parent)
        self.job, self.img, self.lang, self.region = job, img, lang, region

    def run(self) -> None:
        try:
            res = ocr_engine.recognize(self.img, self.lang, self.region)
            self.ok.emit(self.job, res)
        except ocr_engine.OcrError as ex:
            self.failed.emit(self.job, str(ex))
        except Exception as ex:  # noqa: BLE001
            self.failed.emit(self.job, f"글자를 읽는 중 문제가 생겼어요. ({type(ex).__name__}: {ex})")


# ---------------------------------------------------------------- 문서 읽기 작업 스레드
class DocWorker(QThread):
    ok = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, job: int, path: str, parent=None) -> None:
        super().__init__(parent)
        self.job, self.path = job, path

    def run(self) -> None:
        try:
            self.ok.emit(self.job, docextract.extract_document(self.path))
        except docextract.DocError as ex:
            self.failed.emit(self.job, str(ex))
        except Exception as ex:  # noqa: BLE001
            self.failed.emit(self.job, f"문서를 읽는 중 문제가 생겼어요. ({type(ex).__name__}: {ex})")


# ---------------------------------------------------------------- 이미지 보기 + 영역 선택
class ImageView(QWidget):
    region_changed = Signal(object)  # (x, y, w, h) 이미지 좌표, 지우면 None
    files_dropped = Signal(list)
    open_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._pm: QPixmap | None = None
        self._size = (0, 0)
        self._lines: list[ocr_engine.OcrLine] = []
        self._region: QRect | None = None
        self._drag_a: QPointF | None = None
        self._drag_b: QPointF | None = None
        self._busy = False
        self._doc: tuple[str, str, str] | None = None  # (파일 이름, 종류 표시, 안내 문구) 문서를 열었을 때의 카드
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(340, 320)
        self.setFocusPolicy(Qt.StrongFocus)

    # --- 상태
    def set_document(self, name: str, kind: str, info: str) -> None:
        """문서(PDF·HWPX·HWP)를 열었을 때: 이미지 대신 파일 카드를 보여준다."""
        self._pm = None
        self._doc = (name, kind, info)
        self._lines, self._region, self._drag_a, self._drag_b = [], None, None, None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def set_image(self, img: Image.Image) -> None:
        self._doc = None
        self._pm = pil_to_qpixmap(img)
        self._size = img.size
        self._lines, self._region, self._drag_a, self._drag_b = [], None, None, None
        self.setCursor(Qt.CrossCursor)
        self.update()

    def set_lines(self, lines) -> None:
        self._lines = list(lines)
        self.update()

    def set_busy(self, on: bool) -> None:
        self._busy = on
        self.update()

    def region(self):
        return self._region

    def clear_region(self) -> None:
        self._region = None
        self.update()

    # --- 좌표
    def _fit(self) -> QRectF:
        m = 14
        avail = QRectF(self.rect()).adjusted(m, m, -m, -m)
        if not self._pm or self._size[0] == 0:
            return avail
        s = min(avail.width() / self._size[0], avail.height() / self._size[1])
        w, h = self._size[0] * s, self._size[1] * s
        return QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h)

    def _to_img(self, p: QPointF) -> QPointF:
        f = self._fit()
        x = (p.x() - f.x()) / f.width() * self._size[0]
        y = (p.y() - f.y()) / f.height() * self._size[1]
        return QPointF(min(max(x, 0), self._size[0]), min(max(y, 0), self._size[1]))

    def _to_view(self, x: float, y: float) -> QPointF:
        f = self._fit()
        return QPointF(f.x() + x / self._size[0] * f.width(), f.y() + y / self._size[1] * f.height())

    # --- 그리기
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1d1e23"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        if self._doc:
            self._paint_doc(p)
            return
        if not self._pm:
            self._paint_empty(p)
            return
        f = self._fit()
        p.drawPixmap(f, self._pm, QRectF(self._pm.rect()))
        p.setBrush(Qt.NoBrush)
        # 인식된 줄 표시
        p.setPen(QPen(QColor(31, 201, 142, 150), 1))
        for ln in self._lines:
            a, b = self._to_view(ln.x, ln.y), self._to_view(ln.x + ln.w, ln.y + ln.h)
            p.drawRect(QRectF(a, b))
        # 선택 영역: 바깥은 어둡게, 안쪽은 점선
        r = self._current_rect()
        if r is not None:
            a, b = self._to_view(r.left(), r.top()), self._to_view(r.right(), r.bottom())
            rr = QRectF(a, b)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 120))
            for part in (QRectF(f.left(), f.top(), f.width(), rr.top() - f.top()),
                         QRectF(f.left(), rr.bottom(), f.width(), f.bottom() - rr.bottom()),
                         QRectF(f.left(), rr.top(), rr.left() - f.left(), rr.height()),
                         QRectF(rr.right(), rr.top(), f.right() - rr.right(), rr.height())):
                p.drawRect(part)
            pen = QPen(QColor(C_ACCENT), 1.6, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(31, 201, 142, 22))
            p.drawRect(rr)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C_ACCENT))
            tag = QRectF(rr.left(), max(rr.top() - 20, f.top() - 6), 50, 18)
            p.drawRoundedRect(tag, 3, 3)
            p.setPen(QColor("#0b1f17"))
            p.setFont(QFont("Malgun Gothic", 9))
            p.drawText(tag, Qt.AlignCenter, "영역 1")
        if self._busy:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(21, 22, 26, 150))
            p.drawRect(f)
            p.setPen(QColor("#e8e9ed"))
            p.setFont(QFont("Malgun Gothic", 11))
            p.drawText(f, Qt.AlignCenter, "글자를 읽는 중…")

    def _paint_doc(self, p: QPainter) -> None:
        name, kind, info = self._doc
        r = QRectF(self.rect()).adjusted(22, 22, -22, -22)
        c = r.center()
        # 문서 모양 카드 (한 귀퉁이가 접힌 종이)
        w, h = 150.0, 190.0
        page = QRectF(c.x() - w / 2, c.y() - h / 2 - 40, w, h)
        fold = 34.0
        path = QPainterPath()
        path.moveTo(page.left(), page.top())
        path.lineTo(page.right() - fold, page.top())
        path.lineTo(page.right(), page.top() + fold)
        path.lineTo(page.right(), page.bottom())
        path.lineTo(page.left(), page.bottom())
        path.closeSubpath()
        p.setPen(QPen(QColor("#3a3d47"), 1.5))
        p.setBrush(QColor("#26282f"))
        p.drawPath(path)
        tri = QPolygonF([QPointF(page.right() - fold, page.top()), QPointF(page.right() - fold, page.top() + fold),
                         QPointF(page.right(), page.top() + fold)])
        p.setBrush(QColor("#343740"))
        p.drawPolygon(tri)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C_ACCENT))
        badge = QRectF(page.left() + 16, page.top() + 70, 74, 28)
        p.drawRoundedRect(badge, 6, 6)
        p.setPen(QColor(C_ON_ACCENT))
        p.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        p.drawText(badge, Qt.AlignCenter, kind)
        p.setBrush(QColor("#3a3d47"))
        p.setPen(Qt.NoPen)
        for i, ww in enumerate((118, 96, 108, 70)):
            p.drawRoundedRect(QRectF(page.left() + 16, page.top() + 116 + i * 16, ww, 6), 3, 3)
        p.setPen(QColor("#e8e9ed"))
        p.setFont(QFont("Malgun Gothic", 12, QFont.Bold))
        fm = p.fontMetrics()
        shown = fm.elidedText(name, Qt.ElideMiddle, int(r.width() - 40))
        p.drawText(QRectF(r.left(), page.bottom() + 22, r.width(), 26), Qt.AlignCenter, shown)
        p.setPen(QColor(C_MUTED))
        p.setFont(QFont("Malgun Gothic", 10))
        p.drawText(QRectF(r.left() + 10, page.bottom() + 52, r.width() - 20, 44), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, info)
        if self._busy:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(21, 22, 26, 170))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
            p.setPen(QColor("#e8e9ed"))
            p.setFont(QFont("Malgun Gothic", 12))
            p.drawText(QRectF(self.rect()), Qt.AlignCenter, "문서를 읽는 중…")

    def _paint_empty(self, p: QPainter) -> None:
        r = QRectF(self.rect()).adjusted(22, 22, -22, -22)
        p.setPen(QPen(QColor("#3a3d47"), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)
        c = r.center()
        p.setPen(QColor("#e8e9ed"))
        p.setFont(QFont("Malgun Gothic", 13, QFont.Bold))
        p.drawText(QRectF(r.left(), c.y() - 44, r.width(), 28), Qt.AlignCenter, "PDF·HWPX 문서나 이미지를 끌어다 놓으세요")
        p.setPen(QColor(C_MUTED))
        p.setFont(QFont("Malgun Gothic", 10))
        p.drawText(QRectF(r.left(), c.y() - 12, r.width(), 22), Qt.AlignCenter, "문서는 원본 글을 그대로 가져와요 · 이미지는 Ctrl+V 로 붙여넣기 · 클릭해서 파일 열기")
        p.setPen(QColor("#767b86"))
        p.drawText(QRectF(r.left(), c.y() + 18, r.width(), 22), Qt.AlignCenter, "보도자료는 PDF·HWPX 원본이 있으면 이미지보다 정확해요")

    # --- 영역 드래그
    def _current_rect(self) -> QRect | None:
        if self._drag_a is not None and self._drag_b is not None:
            a, b = self._drag_a, self._drag_b
            return QRect(int(min(a.x(), b.x())), int(min(a.y(), b.y())), int(abs(a.x() - b.x())), int(abs(a.y() - b.y())))
        return self._region

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self._pm:
            if e.button() == Qt.LeftButton:
                self.open_requested.emit()
            return
        if e.button() == Qt.RightButton:
            if self._region is not None:
                self._region = None
                self.update()
                self.region_changed.emit(None)
            return
        if e.button() == Qt.LeftButton and not self._busy:
            self._drag_a = self._drag_b = self._to_img(e.position())
            self._press_pos = e.position()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag_a is not None:
            self._drag_b = self._to_img(e.position())
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag_a is None or e.button() != Qt.LeftButton:
            return
        moved = (e.position() - self._press_pos).manhattanLength()
        r = self._current_rect()
        self._drag_a = self._drag_b = None
        if moved >= MIN_REGION_PX and r is not None and r.width() >= 4 and r.height() >= 4:
            self._region = r
            self.update()
            self.region_changed.emit((r.x(), r.y(), r.width(), r.height()))
        else:
            self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape and self._region is not None:
            self._region = None
            self.update()
            self.region_changed.emit(None)
            return
        super().keyPressEvent(e)

    # --- 끌어다 놓기
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        e.acceptProposedAction()


# ---------------------------------------------------------------- 결과 글 칸 (이미지를 붙여넣어도 받는다)
class ResultEdit(QPlainTextEdit):
    image_pasted = Signal(object)  # QImage

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if source.hasImage() and not source.hasText():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.image_pasted.emit(img)
                return
        super().insertFromMimeData(source)


# ---------------------------------------------------------------- 메인 창
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("QTextExtract", "QTextExtract")
        self.setWindowTitle(APP_NAME)
        self.resize(1040, 660)
        self.setMinimumSize(860, 520)
        self.setAcceptDrops(False)

        self.img: Image.Image | None = None
        self.img_path = ""
        self.doc_path = ""  # 문서(PDF·HWPX·HWP)를 열었을 때의 경로
        self.lines: list[ocr_engine.OcrLine] = []
        self.region = None
        self._job = 0
        self._workers: list[OcrWorker] = []
        self._dirty = False  # 사용자가 결과 글을 직접 고쳤는지
        self._setting_text = False
        self._used = False  # 한 번이라도 글자를 읽었는지 (종료 때 의견 팝업 조건)
        self._busy = False
        self._tab = "src"  # 지금 보는 탭: src(추출한 글) / ko / en / chk / title
        self._texts: dict[str, str] = {}  # 다른 탭으로 옮길 때 각 탭의 글을 보관
        self._draft = None  # 마지막 AI 초안 결과
        self._scope_src = ""  # 추출한 글 탭의 머리말(원본 텍스트 · n쪽)

        self._build_ui()
        self._load_languages()
        self._bind()

    # --- 화면 구성
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("toolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(8)
        self.btn_open = QPushButton("파일 열기")
        self.btn_paste = QPushButton("붙여넣기  Ctrl+V")
        self.btn_region = QPushButton("전체 영역으로")
        self.btn_region.setObjectName("ghost")
        self.btn_region.setToolTip("드래그로 지정한 영역을 지우고 이미지 전체를 다시 읽어요. (우클릭 / Esc)")
        self.btn_ai_set = QPushButton("AI 설정")
        self.btn_ai_set.setObjectName("ghost")
        self.btn_ai_set.setToolTip("AI 기사 초안에 쓰는 API 키와 모델을 설정해요.")
        for b in (self.btn_open, self.btn_paste, self.btn_region, self.btn_ai_set):
            b.setCursor(Qt.PointingHandCursor)
            bl.addWidget(b)
        bl.addStretch(1)
        lb = QLabel("이미지 인식 언어")
        lb.setObjectName("muted")
        self.cmb_lang = QComboBox()
        self.cmb_lang.setMinimumWidth(150)
        bl.addWidget(lb)
        bl.addWidget(self.cmb_lang)
        outer.addWidget(bar)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setContentsMargins(14, 14, 14, 10)
        outer.addWidget(split, 1)

        # 왼쪽: 이미지
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(8)
        self.view = ImageView()
        ll.addWidget(self.view, 1)
        self.hint = QLabel("PDF·HWPX·HWP 문서는 원본 글을 그대로 가져오고, 이미지는 글자 인식(OCR)으로 읽어요.")
        self.hint.setObjectName("hint")
        ll.addWidget(self.hint)
        split.addWidget(left)

        # 오른쪽: 결과
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.setSpacing(10)
        head = QHBoxLayout()
        t = QLabel("추출된 텍스트")
        t.setObjectName("toolname")
        t.setStyleSheet("font-size:14px;")
        self.lbl_scope = QLabel("")
        self.lbl_scope.setObjectName("hint")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.lbl_scope)
        rl.addLayout(head)

        # AI 초안이 만들어지면 나타나는 탭 (처음에는 숨김)
        self.tab_btns: dict[str, QPushButton] = {}
        tabrow = QHBoxLayout()
        tabrow.setSpacing(6)
        tabrow.setContentsMargins(0, 0, 0, 0)
        for key, label in ("src", "추출한 글"), ("ko", "국문 초안"), ("en", "영문 초안"), ("chk", "분류·검증"), ("title", "제목 3안"):
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self._show_tab(k))
            self.tab_btns[key] = b
            tabrow.addWidget(b)
        tabrow.addStretch(1)
        self.tab_wrap = QWidget()
        self.tab_wrap.setLayout(tabrow)
        self.tab_wrap.hide()
        self.tab_btns["src"].setChecked(True)
        rl.addWidget(self.tab_wrap)

        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        self.warn.hide()
        rl.addWidget(self.warn)

        self.edit = ResultEdit()
        self.edit.setObjectName("result")
        self.edit.setPlaceholderText("문서나 이미지를 열면 글이 여기에 나와요. 바로 고칠 수도 있어요.")
        f = QFont("Malgun Gothic", 10)
        f.setStyleHint(QFont.SansSerif)
        self.edit.setFont(f)
        rl.addWidget(self.edit, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.setContentsMargins(0, 0, 0, 0)
        self.chip_join = self._chip("줄바꿈 정리", "문단 안의 줄바꿈을 없애고 이어 붙여요.", "join", True)
        self.chip_space = self._chip("공백 정리", "겹친 공백과 괄호·쉼표 주변 공백을 정리해요.", "space", True)
        self.chip_num = self._chip("숫자 강조", "숫자와 단위에 색을 입혀 원본과 대조하기 쉽게 해요.", "num", True)
        for c in (self.chip_join, self.chip_space, self.chip_num):
            chips.addWidget(c)
        chips.addStretch(1)
        self.chip_wrap = QWidget()
        self.chip_wrap.setLayout(chips)
        rl.addWidget(self.chip_wrap)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_copy = QPushButton("전체 복사")
        self.btn_copy.setObjectName("primary")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_ai = QPushButton("기사 초안 → AI")
        self.btn_ai.setObjectName("accent")
        self.btn_ai.setCursor(Qt.PointingHandCursor)
        self.btn_ai.setStyleSheet("padding: 11px 14px;")
        self.btn_ai.setToolTip("추출한 글을 AI가 읽고 아주뉴스 지침에 맞춰 기사 초안을 써요.")
        self.btn_save = QPushButton("txt 저장")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet("padding: 11px 16px;")
        btns.addWidget(self.btn_copy, 1)
        btns.addWidget(self.btn_ai)
        btns.addWidget(self.btn_save)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setStretchFactor(0, 6)
        split.setStretchFactor(1, 5)
        split.setSizes([560, 460])

        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("count")
        self.lbl_state.setContentsMargins(12, 0, 0, 0)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("count")
        self.lbl_ver = QLabel(f"v{APP_VERSION} 베타")
        self.lbl_ver.setObjectName("beta")
        self.lbl_count.setContentsMargins(0, 0, 10, 0)
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.addWidget(self.lbl_state, 1)
        sb.addPermanentWidget(self.lbl_count)
        sb.addPermanentWidget(self.lbl_ver)
        self._set_state("문서(PDF·HWPX)를 열거나 이미지를 붙여넣어 주세요.")
        self._refresh_enabled()

    def _chip(self, text: str, tip: str, key: str, default: bool) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("chip")
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        v = self.settings.value(f"opt/{key}", default)
        b.setChecked(v is True or str(v).lower() == "true")
        b.setProperty("key", key)
        return b

    def _load_languages(self) -> None:
        self.langs = ocr_engine.available_languages()
        self.cmb_lang.clear()
        for tag, name in self.langs:
            self.cmb_lang.addItem(name, tag)
        if not self.langs:
            self.cmb_lang.addItem("사용할 수 없음", "")
            self.cmb_lang.setEnabled(False)
            self._show_warn(ocr_engine.INSTALL_HELP)
            return
        want = str(self.settings.value("lang", "")) or ocr_engine.default_language(self.langs)
        i = self.cmb_lang.findData(want)
        self.cmb_lang.setCurrentIndex(i if i >= 0 else max(0, self.cmb_lang.findData(ocr_engine.default_language(self.langs))))

    def _bind(self) -> None:
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_paste.clicked.connect(self.paste_image)
        self.btn_region.clicked.connect(self._clear_region)
        self.btn_copy.clicked.connect(self.copy_all)
        self.btn_save.clicked.connect(self.save_txt)
        self.btn_ai.clicked.connect(self.start_ai)
        self.btn_ai_set.clicked.connect(self.open_ai_settings)
        self.view.region_changed.connect(self._on_region)
        self.view.files_dropped.connect(self.open_paths)
        self.view.open_requested.connect(self.open_dialog)
        self.edit.image_pasted.connect(self.paste_qimage)
        self.edit.textChanged.connect(self._on_edit)
        self.cmb_lang.currentIndexChanged.connect(self._on_lang)
        for c in (self.chip_join, self.chip_space, self.chip_num):
            c.toggled.connect(lambda _on, ch=c: self._on_chip(ch))
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self.paste_image)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_dialog)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, activated=self.copy_all)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_txt)
        self.setAcceptDrops(True)

    # --- 작은 도우미
    def _set_state(self, text: str, warn: bool = False) -> None:
        self.lbl_state.setText(text)
        self.lbl_state.setStyleSheet(f"color:{C_WARN};" if warn else "")

    def _show_warn(self, text: str) -> None:
        self.warn.setText(text)
        self.warn.show()

    def _refresh_enabled(self) -> None:
        has = self.img is not None
        self.btn_region.setEnabled(has and self.region is not None)
        self.btn_copy.setEnabled(bool(self.edit.toPlainText().strip()))
        self.btn_save.setEnabled(bool(self.edit.toPlainText().strip()))
        self.btn_ai.setEnabled(bool(self._src_text().strip()) and not self._busy)
        self.btn_paste.setEnabled(not self._busy)
        self.btn_open.setEnabled(not self._busy)

    def _current_lang(self) -> str:
        return str(self.cmb_lang.currentData() or "")

    # --- 이미지 넣기
    def open_dialog(self) -> None:
        docs = " ".join(f"*{e}" for e in DOC_EXTS)
        imgs = " ".join(f"*{e}" for e in IMAGE_EXTS)
        path, _ = QFileDialog.getOpenFileName(
            self, "파일 열기", self.settings.value("last_dir", ""),
            f"문서·이미지 ({docs} {imgs});;문서 ({docs});;이미지 ({imgs})")
        if path:
            self.open_paths([path])

    def open_paths(self, paths: list[str]) -> None:
        paths = [p for p in paths if p.lower().endswith(DOC_EXTS + IMAGE_EXTS)]
        if not paths:
            self._set_state("PDF, HWPX, HWP 문서나 이미지 파일(jpg, png 등)만 열 수 있어요.", warn=True)
            return
        if self._busy:
            self._set_state("지금 읽는 중이에요. 끝난 뒤에 다시 열어 주세요.", warn=True)
            return
        first = paths[0]
        self.settings.setValue("last_dir", os.path.dirname(first))
        if first.lower().endswith(DOC_EXTS):
            self._open_document(first)
        else:
            try:
                img = load_image(first)
            except Exception as ex:  # noqa: BLE001
                self._set_state(f"이미지를 열 수 없어요. ({ex})", warn=True)
                return
            self._set_image(img, first)
        if len(paths) > 1:
            self._set_state(f"한 번에 하나씩 읽어요. 첫 번째 파일만 열었어요. ({len(paths) - 1}개 제외)", warn=True)

    # --- 문서(PDF·HWPX·HWP) 넣기: 원본 글을 그대로 꺼낸다
    def _open_document(self, path: str) -> None:
        self.img, self.img_path, self.region, self.lines = None, "", None, []
        self.doc_path = path
        self._reset_ai()
        ext = os.path.splitext(path)[1].upper().lstrip(".")
        self.view.set_document(os.path.basename(path), ext, "원본 파일 안의 글을 그대로 가져와요.")
        self.hint.setText("문서는 원본 글을 그대로 가져와서 글자가 틀리지 않아요. 다른 파일을 끌어다 놓으면 바꿔서 읽어요.")
        self._set_text("", dirty=False)
        self.warn.hide()
        self.lbl_scope.setText("")
        self._job += 1
        job = self._job
        self._busy = True
        self.view.set_busy(True)
        self._set_state("문서를 읽는 중… (쪽이 많으면 몇 초 걸려요)")
        self._refresh_enabled()
        self._t_doc = time.perf_counter()
        w = DocWorker(job, path, self)
        w.ok.connect(self._on_doc_ok)
        w.failed.connect(self._on_doc_fail)
        w.finished.connect(lambda ww=w: self._workers.remove(ww) if ww in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_doc_ok(self, job: int, res) -> None:
        if job != self._job:
            return
        self._finish_busy()
        self._used = True
        self.lines = res.lines
        kind = res.kind.upper()
        pages = f" · {res.pages}쪽" if res.pages else ""
        self.view.set_document(os.path.basename(self.doc_path), kind, f"원본 글을 그대로 가져왔어요{pages}")
        self.lbl_scope.setText(f"원본 텍스트{pages}")
        self._render_text()
        if res.notes:
            self._show_warn(" ".join(res.notes))
        else:
            self.warn.hide()
        sec = time.perf_counter() - getattr(self, "_t_doc", time.perf_counter())
        if res.lines:
            self._set_state(f"문서에서 글을 가져왔어요 · {sec:.1f}초 · 원본 그대로예요. 쪽번호는 빼고 줄바꿈을 정리했어요.")
        else:
            self._set_state("가져온 글이 없어요.", warn=True)

    def _on_doc_fail(self, job: int, msg: str) -> None:
        if job != self._job:
            return
        self._finish_busy()
        self._show_warn(msg)
        self._set_state("문서를 읽지 못했어요.", warn=True)

    def paste_image(self) -> None:
        md = QApplication.clipboard().mimeData()
        if md.hasImage():
            img = md.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.paste_qimage(img)
                return
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self.open_paths(paths)
                return
        self._set_state("클립보드에 이미지가 없어요. Win + Shift + S 로 화면을 캡처한 뒤 붙여넣어 보세요.", warn=True)

    def paste_qimage(self, qimg: QImage) -> None:
        self._set_image(qimage_to_pil(qimg), "")

    def _set_image(self, img: Image.Image, path: str) -> None:
        self.img, self.img_path, self.region = img, path, None
        self.doc_path = ""
        self._reset_ai()
        self.lines = []
        self.view.set_image(img)
        self.hint.setText("이미지 위에서 드래그하면 그 부분만 다시 읽어요. (우클릭 또는 Esc 로 전체 영역)")
        self._set_text("", dirty=False)
        self._run_ocr()

    # --- 영역
    def _on_region(self, region) -> None:
        self.region = region
        self._run_ocr()

    def _clear_region(self) -> None:
        if self.region is None:
            return
        self.region = None
        self.view.clear_region()
        self._run_ocr()

    # --- 인식
    def _run_ocr(self) -> None:
        if self.img is None:
            return
        if not self.langs:
            self._set_state("글자 인식 기능을 쓸 수 없어요.", warn=True)
            return
        if self._dirty and self.edit.toPlainText().strip() and not self._confirm_discard():
            self.view.clear_region() if self.region else None
            return
        self._job += 1
        job = self._job
        self._busy = True
        self.view.set_busy(True)
        self._set_state("글자를 읽는 중…")
        self._refresh_enabled()
        w = OcrWorker(job, self.img, self._current_lang(), self.region, self)
        w.ok.connect(self._on_ocr_ok)
        w.failed.connect(self._on_ocr_fail)
        w.finished.connect(lambda ww=w: self._workers.remove(ww) if ww in self._workers else None)
        self._workers.append(w)
        w.start()

    def _finish_busy(self) -> None:
        self._busy = False
        self.view.set_busy(False)
        self._refresh_enabled()

    def _on_ocr_ok(self, job: int, res) -> None:
        if job != self._job:
            return
        self._finish_busy()
        self._used = True
        self._show_warn(OCR_NOTICE)
        self.lines = res.lines
        self.view.set_lines(res.lines)
        self.lbl_scope.setText("선택 영역" if self.region else "이미지 전체")
        self._render_text()
        n = len(self.edit.toPlainText().replace("\n", ""))
        if not res.lines:
            self._set_state("읽힌 글자가 없어요. 이미지가 작거나 흐리면 글자 부분만 드래그해서 다시 읽어 보세요.", warn=True)
        else:
            self._set_state(f"인식 완료 · {res.seconds:.1f}초 · 원본과 꼭 대조해 주세요.")
        self.lbl_count.setText(f"글자 수 {n:,}자" if res.lines else "")

    def _on_ocr_fail(self, job: int, msg: str) -> None:
        if job != self._job:
            return
        self._finish_busy()
        self._show_warn(msg)
        self._set_state("글자를 읽지 못했어요.", warn=True)

    # --- 결과 글
    def _render_text(self) -> None:
        text = textops.build_text(self.lines, self.chip_join.isChecked(), self.chip_space.isChecked())
        self._set_text(text, dirty=False)

    def _set_text(self, text: str, dirty: bool) -> None:
        self._setting_text = True
        self.edit.setPlainText(text)
        self._setting_text = False
        self._dirty = dirty
        self._apply_highlight()
        self._refresh_enabled()
        self.lbl_count.setText(f"글자 수 {len(text.replace(chr(10), '')):,}자" if text else "")

    def _on_edit(self) -> None:
        if self._setting_text:
            return
        if self._tab == "src":
            self._dirty = True
        self._apply_highlight()
        self._refresh_enabled()
        self.lbl_count.setText(f"글자 수 {len(self.edit.toPlainText().replace(chr(10), '')):,}자")

    def _apply_highlight(self) -> None:
        sels = []
        if self.chip_num.isChecked() and self._tab == "src":
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(31, 201, 142, 60))
            fmt.setForeground(QColor("#bff5df"))
            for a, b in textops.number_spans(self.edit.toPlainText()):
                cur = QTextCursor(self.edit.document())
                cur.setPosition(a)
                cur.setPosition(b, QTextCursor.KeepAnchor)
                s = QTextEdit.ExtraSelection()
                s.cursor, s.format = cur, fmt
                sels.append(s)
        self.edit.setExtraSelections(sels)

    def _confirm_discard(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Question)
        box.setText("직접 고친 내용이 사라져요. 계속할까요?")
        yes = box.addButton("계속", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _on_chip(self, chip: QPushButton) -> None:
        key = chip.property("key")
        if key in ("join", "space"):
            if self._dirty and self.edit.toPlainText().strip() and not self._confirm_discard():
                chip.blockSignals(True)
                chip.setChecked(not chip.isChecked())
                chip.blockSignals(False)
                return
            self._render_text()
        else:
            self._apply_highlight()
        self.settings.setValue(f"opt/{key}", chip.isChecked())

    def _on_lang(self) -> None:
        self.settings.setValue("lang", self._current_lang())
        if self.img is not None and not self._busy:
            self._run_ocr()

    # --- 복사 / 저장
    TAB_NAMES = {"src": "", "ko": "_국문초안", "en": "_영문초안", "chk": "_분류검증", "title": "_제목"}

    def _copy_label(self) -> str:
        return "전체 복사" if self._tab == "src" else "이 탭 복사"

    def copy_all(self) -> None:
        text = self.edit.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        self._set_state(f"복사했어요. ({len(text.replace(chr(10), '')):,}자)")
        self.btn_copy.setText("복사했어요")
        QTimer.singleShot(1500, lambda: self.btn_copy.setText(self._copy_label()))

    def save_txt(self) -> None:
        text = self.edit.toPlainText()
        if not text.strip():
            return
        src = self.doc_path or self.img_path
        base = (os.path.splitext(os.path.basename(src))[0] if src else "추출텍스트") + self.TAB_NAMES.get(self._tab, "")
        start = os.path.join(str(self.settings.value("last_dir", "")), base + ".txt")
        path, _ = QFileDialog.getSaveFileName(self, "txt 저장", start, "텍스트 파일 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:  # 메모장에서도 한글이 깨지지 않게
                f.write(text)
            self._set_state(f"저장했어요. ({os.path.basename(path)})")
        except OSError as ex:
            self._set_state(f"저장하지 못했어요. ({ex})", warn=True)

    # --- AI 기사 초안
    def _src_text(self) -> str:
        """AI 에 보낼 '추출한 글'. 다른 탭을 보고 있어도 추출한 글을 돌려준다."""
        return self.edit.toPlainText() if self._tab == "src" else self._texts.get("src", "")

    def _ai_key(self) -> str:
        return str(self.settings.value("ai/key", "")).strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()

    def _ai_prefs(self) -> tuple[str, bool, float]:
        model = str(self.settings.value("ai/model", "")) or aidraft.DEFAULT_MODEL
        full = str(self.settings.value("ai/full", "true")).lower() != "false"
        try:
            rate = float(self.settings.value("ai/rate", aidraft.DEFAULT_KRW_RATE))
        except (TypeError, ValueError):
            rate = aidraft.DEFAULT_KRW_RATE
        return model, full, rate

    def open_ai_settings(self) -> bool:
        model, full, rate = self._ai_prefs()
        dlg = aiui.AiSettingsDialog(self, self._ai_key(), model, full, rate)
        if dlg.exec() != QDialog.Accepted:
            return False
        key, model, full, rate = dlg.values()
        self.settings.setValue("ai/key", key)
        self.settings.setValue("ai/model", model)
        self.settings.setValue("ai/full", full)
        self.settings.setValue("ai/rate", rate)
        self._set_state("AI 설정을 저장했어요.")
        return True

    def start_ai(self) -> None:
        src = self._src_text().strip()
        if not src or self._busy:
            return
        if not self._ai_key():
            if not self.open_ai_settings() or not self._ai_key():
                return
        model, full, rate = self._ai_prefs()
        _g, n_files, n_chars = aidraft.load_guidelines(full)
        eng = str(self.settings.value("ai/english", "true")).lower() != "false"
        dlg = aiui.AiOptionsDialog(self, model, n_files, n_chars, eng, redraft=self._draft is not None)
        if dlg.exec() != QDialog.Accepted:
            return
        mode, english = dlg.values()
        self.settings.setValue("ai/english", english)
        self._job += 1
        job = self._job
        self._busy = True
        self.view.set_busy(True)
        self.btn_ai.setText("초안 쓰는 중…")
        self._set_state("AI가 기사 초안을 쓰는 중이에요… (보통 20~60초)")
        self._refresh_enabled()
        name = os.path.basename(self.doc_path or self.img_path)
        w = aiui.AiWorker(job, src, mode, english, self._ai_key(), model, full, rate, name, self)
        w.ok.connect(self._on_ai_ok)
        w.failed.connect(self._on_ai_fail)
        w.finished.connect(lambda ww=w: self._workers.remove(ww) if ww in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_ai_ok(self, job: int, res) -> None:
        if job != self._job:
            return
        self._finish_busy()
        if self._tab == "src":
            self._texts["src"] = self.edit.toPlainText()
            self._scope_src = self.lbl_scope.text()
        self._draft = res
        for k in aidraft.SECTION_ORDER:
            self._texts[k] = res.sections.get(k, "")
        for k, b in self.tab_btns.items():
            b.setVisible(k == "src" or bool(self._texts.get(k)))
        self.tab_wrap.show()
        self.btn_ai.setText("다시 쓰기")
        self._tab = ""  # 강제로 다시 그리도록
        self._show_tab("ko" if self._texts.get("ko") else next((k for k in aidraft.SECTION_ORDER if self._texts.get(k)), "src"))
        self._show_warn(" ".join(["AI 초안이에요. 숫자·인용·사실은 원문과 꼭 대조해 주세요."] + res.notes))
        self._set_state(f"AI 초안 완료 · {res.seconds:.0f}초 · {aidraft.format_cost(res)} · {res.model}")

    def _on_ai_fail(self, job: int, msg: str) -> None:
        if job != self._job:
            return
        self._finish_busy()
        self.btn_ai.setText("다시 쓰기" if self._draft else "기사 초안 → AI")
        self._show_warn(msg)
        self._set_state("AI 초안을 만들지 못했어요.", warn=True)

    def _reset_ai(self) -> None:
        """새 파일을 열면 이전 AI 초안과 탭을 지운다."""
        self._draft = None
        self._texts = {}
        self._tab = "src"
        for k, b in self.tab_btns.items():
            b.setChecked(k == "src")
        self.tab_wrap.hide()
        self.chip_wrap.show()
        self.btn_ai.setText("기사 초안 → AI")
        self.btn_copy.setText("전체 복사")

    def _show_tab(self, key: str) -> None:
        if key == self._tab:
            self.tab_btns[key].setChecked(True)
            return
        if self._tab:
            self._texts[self._tab] = self.edit.toPlainText()
        if self._tab == "src":
            self._scope_src = self.lbl_scope.text()
        self._tab = key
        self._setting_text = True
        self.edit.setPlainText(self._texts.get(key, ""))
        self._setting_text = False
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        self.chip_wrap.setVisible(key == "src")
        self.btn_copy.setText(self._copy_label())
        if key == "src":
            self.lbl_scope.setText(self._scope_src)
        else:
            self.lbl_scope.setText("AI 초안 · 확인 필요")
        self._apply_highlight()
        self._refresh_enabled()
        text = self.edit.toPlainText()
        self.lbl_count.setText(f"글자 수 {len(text.replace(chr(10), '')):,}자" if text else "")

    # --- 끌어다 놓기(창 어디에 놓아도) / 종료
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        self.open_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def closeEvent(self, e) -> None:  # noqa: N802
        for w in list(self._workers):
            if not w.wait(3000):  # AI 호출이 길어져도 창을 닫을 수 있게
                w.terminate()
                w.wait(500)
        ask_on_exit(self, self.settings, self._used, on_shown=set_dark_titlebar)
        e.accept()


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return ocr_engine.selftest(sys.argv[2])
    if sys.platform == "win32":
        try:  # 작업표시줄에 파이썬이 아니라 우리 아이콘이 묶이도록
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QTextExtract.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    apply_theme(app)
    win = MainWindow()
    win.setWindowIcon(app_icon())
    win.show()
    set_dark_titlebar(win)
    if len(sys.argv) > 1:
        win.open_paths(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
