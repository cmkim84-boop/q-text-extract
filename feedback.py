"""종료할 때 뜨는 '사용 의견 보내기' 팝업.

- 별점 / 자주 쓴 기능 / 한 줄 의견만 보낸다. 이미지, 인식된 글, 파일 이름, 사용자 정보는 보내지 않는다.
- 받는 곳은 구글 시트에 연결한 '앱스 스크립트 웹앱' 주소(FEEDBACK_URL)다.
  주소가 비어 있으면 팝업 자체가 뜨지 않는다. (주소 만드는 법: feedback_apps_script.gs 맨 위 안내)
- 다음 경우에는 다시 뜨지 않는다: 의견을 한 번 보냈을 때 / '다시 보지 않기'를 체크했을 때 /
  '건너뛰고 종료'를 SKIP_LIMIT번 눌렀을 때.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# 구글 앱스 스크립트 '웹 앱' 주소. 예) "https://script.google.com/macros/s/AKfy.../exec"
FEEDBACK_URL = "https://script.google.com/macros/s/AKfycbw8rEsLlYWcQ4gsJqUHT8XanHCxAspgXsIGqCDWjog0zoE6hYngnncX--iyRLgFS05Y/exec"
APP_VERSION = "1.0.0"

APP_ID = "Q 텍스트 추출"  # 같은 의견 시트를 여러 Q 프로그램이 함께 쓸 때 구분하는 이름
FEATURES = ["이미지 열기", "붙여넣기", "영역 선택", "줄바꿈 정리", "복사"]
MAX_COMMENT = 300
SKIP_LIMIT = 3
SEND_TIMEOUT = 10  # 초

K_SENT = "feedback/sent"
K_NEVER = "feedback/never"
K_SKIPS = "feedback/skips"

ACCENT = "#1fc98e"


# ---------- 표시 조건 ----------
def _flag(settings, key: str) -> bool:
    v = settings.value(key, False)
    return v is True or str(v).lower() == "true"


def should_ask(settings, url: str | None = None) -> bool:
    """이번 종료 때 팝업을 띄울지."""
    url = FEEDBACK_URL if url is None else url
    if not url.strip():
        return False
    if _flag(settings, K_SENT) or _flag(settings, K_NEVER):
        return False
    try:
        skips = int(settings.value(K_SKIPS, 0))
    except (TypeError, ValueError):
        skips = 0
    return skips < SKIP_LIMIT


# ---------- 전송 ----------
def build_payload(rating: int, features: list[str], comment: str) -> dict:
    return {
        "rating": int(rating),
        "features": list(features),
        "comment": comment.strip()[:MAX_COMMENT],
        "version": APP_VERSION,
        "app": APP_ID,
    }


def send_feedback(url: str, payload: dict, timeout: float = SEND_TIMEOUT) -> tuple[bool, str]:
    """앱스 스크립트로 POST. 응답 JSON 에 ok:true 가 있어야 성공으로 본다."""
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            ok = bool(json.loads(body).get("ok"))
        except (ValueError, AttributeError):
            ok = False
        return (True, "") if ok else (False, "받는 쪽 응답이 올바르지 않아요.")
    except urllib.error.URLError as ex:
        return False, str(getattr(ex, "reason", ex))
    except Exception as ex:  # 시간 초과, 인증서 오류 등
        return False, str(ex)


class _SendThread(QThread):
    done = Signal(bool, str)

    def __init__(self, url: str, payload: dict, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._payload = payload

    def run(self) -> None:
        ok, msg = send_feedback(self._url, self._payload)
        self.done.emit(ok, msg)


# ---------- 별점 위젯 ----------
class StarRating(QWidget):
    changed = Signal(int)
    CELL = 34

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0
        self._hover = 0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.CELL * 5, self.CELL)

    def value(self) -> int:
        return self._value

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)

    def _index_at(self, x: float) -> int:
        i = int(x // self.CELL) + 1
        return i if 1 <= i <= 5 else 0

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        self._hover = self._index_at(e.position().x())
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = 0
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        v = self._index_at(e.position().x())
        if v:
            self._value = v
            self.changed.emit(v)
            self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        import math

        shown = self._hover or self._value
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i in range(5):
            cx = self.CELL * i + self.CELL / 2
            cy = self.CELL / 2
            ro, ri = 13.0, 5.8
            pts = []
            for k in range(10):
                r = ro if k % 2 == 0 else ri
                a = -math.pi / 2 + k * math.pi / 5
                pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
            if i < shown:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(ACCENT))
            else:
                p.setPen(QPen(QColor("#4a4f5c"), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF(pts))
        p.end()


# ---------- 팝업 ----------
class FeedbackDialog(QDialog):
    """accept() 로 닫히면 앱을 종료해도 된다. outcome 은 'sent' 또는 'skipped'."""

    def __init__(self, parent, settings, url: str | None = None, app_name: str = "Q 텍스트 추출",
                 on_shown: Callable[[QWidget], None] | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.url = FEEDBACK_URL if url is None else url
        self.outcome = ""
        self._on_shown = on_shown
        self._thread: _SendThread | None = None
        self._sending = False
        self._spin_angle = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(40)
        self._spin_timer.timeout.connect(self._spin_tick)
        self.setWindowTitle(app_name)
        self.setModal(True)
        self.setFixedWidth(450)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(12)
        badge = QLabel("Q")
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"background:{ACCENT}; color:#0b1f17; border-radius:9px; font-size:19px; font-weight:bold;")
        head.addWidget(badge, 0, Qt.AlignTop)
        tbox = QVBoxLayout()
        tbox.setSpacing(2)
        t = QLabel(f"{app_name}, 어떠셨어요?")
        t.setObjectName("toolname")
        s = QLabel("1분이면 돼요. 의견은 개선에 큰 도움이 돼요.")
        s.setObjectName("muted")
        tbox.addWidget(t)
        tbox.addWidget(s)
        head.addLayout(tbox, 1)
        lay.addLayout(head)
        lay.addSpacing(16)

        lay.addWidget(self._title("만족도"))
        lay.addSpacing(6)
        self.stars = StarRating()
        self.stars.changed.connect(lambda _v: self._hide_warn())
        lay.addWidget(self.stars)
        lay.addSpacing(14)

        lay.addWidget(self._title("자주 쓴 기능 (여러 개 선택)"))
        lay.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.chips: list[QPushButton] = []
        for name in FEATURES:
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.toggled.connect(lambda _c: self._hide_warn())
            self.chips.append(b)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(14)

        lay.addWidget(self._title("불편한 점이나 바라는 점"))
        lay.addSpacing(6)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("예) 표 안의 글자도 잘 읽히면 좋겠어요.")
        self.edit.setFixedHeight(84)
        self.edit.setTabChangesFocus(True)
        self.edit.textChanged.connect(self._on_text)
        lay.addWidget(self.edit)
        self.count = QLabel(f"0 / {MAX_COMMENT}자")
        self.count.setObjectName("hint")
        self.count.setAlignment(Qt.AlignRight)
        lay.addWidget(self.count)

        priv = QLabel("별점과 입력한 글만 전송돼요. 이미지와 인식된 글은 보내지 않아요.")
        priv.setObjectName("hint")
        priv.setWordWrap(True)
        lay.addSpacing(4)
        lay.addWidget(priv)

        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        self.warn.hide()
        lay.addSpacing(10)
        lay.addWidget(self.warn)

        lay.addSpacing(14)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_send = QPushButton("보내고 종료")
        self.btn_send.setObjectName("primary")
        self.btn_send.setDefault(True)
        self.btn_skip = QPushButton("건너뛰고 종료")
        self.btn_skip.setStyleSheet("padding: 11px;")
        btns.addWidget(self.btn_send, 1)
        btns.addWidget(self.btn_skip, 1)
        lay.addLayout(btns)

        lay.addSpacing(12)
        self.never = QCheckBox("다시 보지 않기")
        self.never.setStyleSheet("color:#9096a3;")
        lay.addWidget(self.never)

        self.btn_send.clicked.connect(self._on_send)
        self.btn_skip.clicked.connect(self._on_skip)

    # --- 부품 ---
    @staticmethod
    def _title(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("title")
        return lb

    def _hide_warn(self) -> None:
        if not self._sending:
            self.warn.hide()

    def _show_warn(self, text: str) -> None:
        self.warn.setText(text)
        self.warn.show()

    def _on_text(self) -> None:
        txt = self.edit.toPlainText()
        if len(txt) > MAX_COMMENT:
            self.edit.blockSignals(True)
            self.edit.setPlainText(txt[:MAX_COMMENT])
            c = self.edit.textCursor()
            c.movePosition(c.MoveOperation.End)
            self.edit.setTextCursor(c)
            self.edit.blockSignals(False)
            txt = txt[:MAX_COMMENT]
        self.count.setText(f"{len(txt)} / {MAX_COMMENT}자")
        self._hide_warn()

    @staticmethod
    def _spinner_icon(angle: int) -> QIcon:
        """버튼 안에 그리는 원형 로딩 표시 (연한 원 + 돌아가는 흰 호)."""
        size, dpr = 18, 2
        pm = QPixmap(size * dpr, size * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(2, 2, size - 4, size - 4)
        pen = QPen(QColor(255, 255, 255, 70), 2.2)
        p.setPen(pen)
        p.drawEllipse(rect)
        pen = QPen(QColor("#ffffff"), 2.2, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, int(-angle * 16), 100 * 16)
        p.end()
        return QIcon(pm)

    def _spin_tick(self) -> None:
        self._spin_angle = (self._spin_angle + 30) % 360
        self.btn_send.setIcon(self._spinner_icon(self._spin_angle))

    def _lock(self, on: bool) -> None:
        self._sending = on
        # 보내기 버튼은 비활성으로 만들지 않는다(비활성이면 회색으로 바뀌어 로딩 표시가 흐려진다). 중복 클릭은 _on_send 에서 막는다.
        for w in (self.stars, self.edit, self.btn_skip, self.never, *self.chips):
            w.setEnabled(not on)
        if on:
            self.btn_send.setText("보내는 중…")
            self.btn_send.setIconSize(QSize(18, 18))
            self._spin_angle = 0
            self.btn_send.setIcon(self._spinner_icon(0))
            self._spin_timer.start()
        else:
            self._spin_timer.stop()
            self.btn_send.setIcon(QIcon())
            self.btn_send.setText("보내고 종료")

    # --- 동작 ---
    def selected_features(self) -> list[str]:
        return [b.text() for b in self.chips if b.isChecked()]

    def _on_send(self) -> None:
        if self._sending:
            return
        rating = self.stars.value()
        feats = self.selected_features()
        comment = self.edit.toPlainText().strip()
        if rating == 0 and not feats and not comment:
            self._show_warn("별점이나 의견을 하나 이상 남겨 주세요.")
            return
        self.warn.hide()
        self._lock(True)
        self._thread = _SendThread(self.url, build_payload(rating, feats, comment), self)
        self._thread.done.connect(self._on_sent)
        self._thread.start()

    def _on_sent(self, ok: bool, msg: str) -> None:
        self._lock(False)
        if not ok:
            self._show_warn("전송하지 못했어요. 인터넷 연결을 확인하고 다시 보내거나, 건너뛰고 종료해 주세요.")
            return
        self.settings.setValue(K_SENT, True)
        if self.never.isChecked():
            self.settings.setValue(K_NEVER, True)
        self.outcome = "sent"
        self.accept()

    def _on_skip(self) -> None:
        if self._sending:
            return
        try:
            skips = int(self.settings.value(K_SKIPS, 0))
        except (TypeError, ValueError):
            skips = 0
        self.settings.setValue(K_SKIPS, skips + 1)
        if self.never.isChecked():
            self.settings.setValue(K_NEVER, True)
        self.outcome = "skipped"
        self.accept()

    def reject(self) -> None:  # X 버튼 / Esc = 건너뛰고 종료
        if self._sending:
            return
        self._on_skip()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self._on_shown:
            cb, self._on_shown = self._on_shown, None
            cb(self)


def ask_on_exit(parent, settings, used: bool, on_shown=None, url: str | None = None) -> None:
    """종료 직전에 호출. 조건이 맞을 때만 팝업을 띄운다. 어떤 경우에도 앱 종료는 막지 않는다."""
    try:
        if not used or not should_ask(settings, url):
            return
        dlg = FeedbackDialog(parent, settings, url=url, on_shown=on_shown)
        dlg.exec()
    except Exception:
        pass  # 의견 팝업 때문에 종료가 안 되는 일은 없게 한다
