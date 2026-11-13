"""AI 기사 초안 화면 부품 - 설정 창, 처리 방식 선택 창, 백그라운드 호출 스레드."""
from __future__ import annotations

import os

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

import aidraft


class AiWorker(QThread):
    ok = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, job: int, text: str, mode: str, english: bool, key: str, model: str, full: bool,
                 rate: float, file_name: str, parent=None) -> None:
        super().__init__(parent)
        self.job, self.text, self.mode, self.english = job, text, mode, english
        self.key, self.model, self.full, self.rate, self.file_name = key, model, full, rate, file_name

    def run(self) -> None:
        try:
            res = aidraft.generate(self.text, self.mode, self.english, self.key, self.model, self.full, self.rate, self.file_name)
            self.ok.emit(self.job, res)
        except aidraft.AiError as ex:
            self.failed.emit(self.job, str(ex))
        except Exception as ex:  # noqa: BLE001
            self.failed.emit(self.job, f"예상하지 못한 오류예요. ({type(ex).__name__}: {ex})")


def _title(text: str, sub: str = "") -> tuple[QLabel, QLabel | None]:
    t = QLabel(text)
    t.setObjectName("toolname")
    s = None
    if sub:
        s = QLabel(sub)
        s.setObjectName("hint")
        s.setWordWrap(True)
    return t, s


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


# ---------------------------------------------------------------- 설정 창
class AiSettingsDialog(QDialog):
    def __init__(self, parent, key: str, model: str, full: bool, rate: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 기사 초안 설정")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(8)
        t, s = _title("AI 기사 초안 설정", "내 Claude API 키를 한 번만 넣으면 돼요. 키는 이 PC에만 저장돼요. (암호화되지 않으니 공용 PC에서는 쓰지 마세요)")
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addSpacing(6)

        lay.addWidget(self._muted("API 키"))
        row = QHBoxLayout()
        self.ed_key = QLineEdit(key)
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("sk-ant-...")
        self.btn_test = QPushButton("확인")
        self.btn_test.clicked.connect(self._test)
        row.addWidget(self.ed_key, 1)
        row.addWidget(self.btn_test)
        lay.addLayout(row)
        self.lbl_test = QLabel("")
        self.lbl_test.setObjectName("hint")
        lay.addWidget(self.lbl_test)

        lay.addWidget(self._muted("사용 모델"))
        self.cmb = QComboBox()
        self.cmb.setEditable(True)
        for label, mid in aidraft.MODELS:
            self.cmb.addItem(label, mid)
        i = self.cmb.findData(model)
        if i >= 0:
            self.cmb.setCurrentIndex(i)
        else:
            self.cmb.setEditText(model)
        self.cmb.currentTextChanged.connect(self._price)
        lay.addWidget(self.cmb)
        self.lbl_price = QLabel("")
        self.lbl_price.setObjectName("hint")
        lay.addWidget(self.lbl_price)

        self.chk_full = QCheckBox("스타일북·초안 지침까지 함께 보내기 (더 정확해요 · 입력이 늘어 비용이 조금 늘어요)")
        self.chk_full.setChecked(full)
        lay.addWidget(self.chk_full)

        rr = QHBoxLayout()
        rr.addWidget(self._muted("비용 환산 환율 (1달러 = 원)"))
        self.sp_rate = QSpinBox()
        self.sp_rate.setRange(500, 3000)
        self.sp_rate.setValue(int(rate))
        self.sp_rate.setFixedWidth(90)
        rr.addStretch(1)
        rr.addWidget(self.sp_rate)
        lay.addLayout(rr)

        fr = QHBoxLayout()
        b1 = QPushButton("지침 폴더 열기")
        b1.setToolTip("아주뉴스 지침 파일(.md)이 들어 있어요. 스킬이 바뀌면 이 폴더의 파일만 바꿔 넣으면 돼요.")
        b1.clicked.connect(lambda: open_folder(aidraft.ensure_user_prompts()))
        b2 = QPushButton("사용 기록 폴더 열기")
        b2.setToolTip("호출마다 토큰 수와 비용이 한 줄씩 쌓이는 ai_usage.csv 가 있어요.")
        b2.clicked.connect(lambda: open_folder(aidraft.data_dir()))
        fr.addWidget(b1)
        fr.addWidget(b2)
        fr.addStretch(1)
        lay.addSpacing(4)
        lay.addLayout(fr)

        br = QHBoxLayout()
        br.addStretch(1)
        ok = QPushButton("저장")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("닫기")
        cancel.clicked.connect(self.reject)
        br.addWidget(ok)
        br.addWidget(cancel)
        lay.addSpacing(6)
        lay.addLayout(br)
        self._price()

    @staticmethod
    def _muted(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("muted")
        return lb

    def model_id(self) -> str:
        text = self.cmb.currentText().strip()
        i = self.cmb.findText(text)
        if i >= 0:
            return str(self.cmb.itemData(i))
        return text

    def _price(self) -> None:
        p = aidraft.PRICES.get(aidraft._base_model(self.model_id()))
        self.lbl_price.setText(f"단가: 입력 ${p[0]:g} · 출력 ${p[1]:g} (100만 토큰당)" if p
                               else "단가가 등록되지 않은 모델이에요. 토큰 수만 보여주고 비용은 계산하지 않아요.")

    def _test(self) -> None:
        key = self.ed_key.text().strip()
        if not key:
            self.lbl_test.setText("키를 먼저 넣어 주세요.")
            return
        self.lbl_test.setText("확인하는 중…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            aidraft.test_connection(key, self.model_id())
            self.lbl_test.setText("✓ 연결됐어요.")
            self.lbl_test.setStyleSheet("color:#1fc98e;")
        except aidraft.AiError as ex:
            self.lbl_test.setText(str(ex))
            self.lbl_test.setStyleSheet("color:#ffb454;")
        finally:
            QApplication.restoreOverrideCursor()

    def values(self) -> tuple[str, str, bool, float]:
        return self.ed_key.text().strip(), self.model_id(), self.chk_full.isChecked(), float(self.sp_rate.value())


# ---------------------------------------------------------------- 처리 방식 선택 창
class AiOptionsDialog(QDialog):
    def __init__(self, parent, model: str, n_files: int, n_chars: int, english: bool, redraft: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("기사 초안 만들기")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(8)
        t, s = _title("기사 초안 만들기" if not redraft else "기사 초안 다시 쓰기",
                      "보도자료를 읽고 기사로 새로 써요. 보통 20~60초 걸려요.")
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addSpacing(6)
        lb = QLabel("처리 방식")
        lb.setObjectName("muted")
        lay.addWidget(lb)
        self.group = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        for i, (key, (label, desc, _how)) in enumerate(aidraft.MODES.items()):
            r = QRadioButton(f"{label}{'  (권장)' if i == 0 else ''}  —  {desc}")
            r.setChecked(i == 0)
            self.group.addButton(r)
            self._radios[key] = r
            lay.addWidget(r)
        lay.addSpacing(4)
        self.chk_en = QCheckBox("영문 초안도 함께 만들기")
        self.chk_en.setChecked(english)
        lay.addWidget(self.chk_en)
        info = QLabel(f"모델 {model} · 지침 {n_files}개 ({n_chars:,}자)")
        info.setObjectName("hint")
        lay.addWidget(info)
        warn = QLabel("원문이 Anthropic API로 전송돼요. 엠바고 자료는 회사 정책을 확인하세요.")
        warn.setObjectName("warn")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        br = QHBoxLayout()
        br.addStretch(1)
        ok = QPushButton("초안 만들기")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton("닫기")
        cancel.clicked.connect(self.reject)
        br.addWidget(ok)
        br.addWidget(cancel)
        lay.addSpacing(6)
        lay.addLayout(br)

    def values(self) -> tuple[str, bool]:
        mode = next((k for k, r in self._radios.items() if r.isChecked()), "standard")
        return mode, self.chk_en.isChecked()
