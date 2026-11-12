"""Q 텍스트 추출 - 이미지 속 글자를 읽어 복사할 수 있는 글로 바꿔 주는 윈도우 데스크톱 앱."""
from __future__ import annotations

import io
import os
import sys

from PIL import Image, ImageOps
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRect, QRectF, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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

import ocr_engine
import textops
from feedback import APP_VERSION, ask_on_exit
from style import C_ACCENT, C_MUTED, C_WARN, apply_theme

APP_NAME = "Q 텍스트 추출"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")
MIN_REGION_PX = 8  # 영역으로 인정하는 최소 드래그 크기(화면 px)


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
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumSize(340, 320)
        self.setFocusPolicy(Qt.StrongFocus)

    # --- 상태
    def set_image(self, img: Image.Image) -> None:
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

    def _paint_empty(self, p: QPainter) -> None:
        r = QRectF(self.rect()).adjusted(22, 22, -22, -22)
        p.setPen(QPen(QColor("#3a3d47"), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)
        c = r.center()
        p.setPen(QColor("#e8e9ed"))
        p.setFont(QFont("Malgun Gothic", 13, QFont.Bold))
        p.drawText(QRectF(r.left(), c.y() - 44, r.width(), 28), Qt.AlignCenter, "이미지를 여기에 끌어다 놓으세요")
        p.setPen(QColor(C_MUTED))
        p.setFont(QFont("Malgun Gothic", 10))
        p.drawText(QRectF(r.left(), c.y() - 12, r.width(), 22), Qt.AlignCenter, "또는 Ctrl+V 로 붙여넣기 · 클릭해서 파일 열기")
        p.setPen(QColor("#767b86"))
        p.drawText(QRectF(r.left(), c.y() + 18, r.width(), 22), Qt.AlignCenter, "화면 캡처는 Win + Shift + S")

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
        self.lines: list[ocr_engine.OcrLine] = []
        self.region = None
        self._job = 0
        self._workers: list[OcrWorker] = []
        self._dirty = False  # 사용자가 결과 글을 직접 고쳤는지
        self._setting_text = False
        self._used = False  # 한 번이라도 글자를 읽었는지 (종료 때 의견 팝업 조건)
        self._busy = False

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
        self.btn_open = QPushButton("이미지 열기")
        self.btn_paste = QPushButton("붙여넣기  Ctrl+V")
        self.btn_region = QPushButton("전체 영역으로")
        self.btn_region.setObjectName("ghost")
        self.btn_region.setToolTip("드래그로 지정한 영역을 지우고 이미지 전체를 다시 읽어요. (우클릭 / Esc)")
        for b in (self.btn_open, self.btn_paste, self.btn_region):
            b.setCursor(Qt.PointingHandCursor)
            bl.addWidget(b)
        bl.addStretch(1)
        lb = QLabel("인식 언어")
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
        self.hint = QLabel("이미지 위에서 드래그하면 그 부분만 다시 읽어요.")
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

        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        self.warn.hide()
        rl.addWidget(self.warn)

        self.edit = ResultEdit()
        self.edit.setObjectName("result")
        self.edit.setPlaceholderText("이미지를 열면 읽은 글이 여기에 나와요. 바로 고칠 수도 있어요.")
        f = QFont("Malgun Gothic", 10)
        f.setStyleHint(QFont.SansSerif)
        self.edit.setFont(f)
        rl.addWidget(self.edit, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_join = self._chip("줄바꿈 정리", "문단 안의 줄바꿈을 없애고 이어 붙여요.", "join", True)
        self.chip_space = self._chip("공백 정리", "겹친 공백과 괄호·쉼표 주변 공백을 정리해요.", "space", True)
        self.chip_num = self._chip("숫자 강조", "숫자와 단위에 색을 입혀 원본과 대조하기 쉽게 해요.", "num", True)
        for c in (self.chip_join, self.chip_space, self.chip_num):
            chips.addWidget(c)
        chips.addStretch(1)
        rl.addLayout(chips)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_copy = QPushButton("전체 복사")
        self.btn_copy.setObjectName("primary")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_save = QPushButton("txt 저장")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet("padding: 11px 16px;")
        btns.addWidget(self.btn_copy, 1)
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
        self._set_state("이미지를 열거나 붙여넣어 주세요.")
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
        self.btn_paste.setEnabled(not self._busy)
        self.btn_open.setEnabled(not self._busy)

    def _current_lang(self) -> str:
        return str(self.cmb_lang.currentData() or "")

    # --- 이미지 넣기
    def open_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in IMAGE_EXTS)
        path, _ = QFileDialog.getOpenFileName(self, "이미지 열기", self.settings.value("last_dir", ""), f"이미지 ({exts})")
        if path:
            self.open_paths([path])

    def open_paths(self, paths: list[str]) -> None:
        paths = [p for p in paths if p.lower().endswith(IMAGE_EXTS)]
        if not paths:
            self._set_state("이미지 파일(jpg, png 등)만 열 수 있어요.", warn=True)
            return
        try:
            img = load_image(paths[0])
        except Exception as ex:  # noqa: BLE001
            self._set_state(f"이미지를 열 수 없어요. ({ex})", warn=True)
            return
        self.settings.setValue("last_dir", os.path.dirname(paths[0]))
        self._set_image(img, paths[0])
        if len(paths) > 1:
            self._set_state(f"한 번에 한 장씩 읽어요. 첫 번째 이미지만 열었어요. ({len(paths) - 1}장 제외)", warn=True)

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
        self.warn.hide()
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
        self._dirty = True
        self._apply_highlight()
        self._refresh_enabled()
        self.lbl_count.setText(f"글자 수 {len(self.edit.toPlainText().replace(chr(10), '')):,}자")

    def _apply_highlight(self) -> None:
        sels = []
        if self.chip_num.isChecked():
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
    def copy_all(self) -> None:
        text = self.edit.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        self._set_state(f"복사했어요. ({len(text.replace(chr(10), '')):,}자)")
        self.btn_copy.setText("복사했어요")
        QTimer.singleShot(1500, lambda: self.btn_copy.setText("전체 복사"))

    def save_txt(self) -> None:
        text = self.edit.toPlainText()
        if not text.strip():
            return
        base = os.path.splitext(os.path.basename(self.img_path))[0] if self.img_path else "추출텍스트"
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

    # --- 끌어다 놓기(창 어디에 놓아도) / 종료
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        self.open_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def closeEvent(self, e) -> None:  # noqa: N802
        for w in list(self._workers):
            w.wait(3000)
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
