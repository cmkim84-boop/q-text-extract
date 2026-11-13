"""Q 텍스트 추출 - 화면 색과 스타일시트 (Q 이미지 편집기와 같은 다크 에디터 톤, 포인트 컬러만 민트)."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

C_BG = "#15161a"
C_PANEL = "#1d1e23"
C_LINE = "#2a2c33"
C_RAISED = "#26282f"
C_TEXT = "#e8e9ed"
C_MUTED = "#9096a3"
C_DIM = "#767b86"
C_ACCENT = "#1fc98e"
C_ACCENT_LIGHT = "#7ee3bd"
C_ON_ACCENT = "#0b1f17"
C_WARN = "#ffb454"

STYLE = """
* { font-family: 'Segoe UI', 'Malgun Gothic', sans-serif; font-size: 13px; }
QMainWindow, QWidget#root, QDialog { background: #15161a; }
QWidget { color: #e8e9ed; }
QToolTip { background: #26282f; color: #e8e9ed; border: 1px solid #3a3d47; padding: 5px 8px; }
QStatusBar { background: #111215; color: #767b86; }
QStatusBar::item { border: none; }
QFrame#panel { background: #1d1e23; border: 1px solid #2a2c33; border-radius: 14px; }
QFrame#sep { background: #2a2c33; border: none; min-height: 1px; max-height: 1px; }
QLabel { background: transparent; }
QLabel#title { font-size: 12px; font-weight: bold; color: #9096a3; }
QLabel#hint { color: #767b86; font-size: 12px; }
QLabel#muted { color: #9096a3; }
QLabel#toolname { font-size: 16px; font-weight: bold; }
QLabel#toolsub { color: #8d919b; font-size: 12px; }
QLabel#info { color: #9096a3; padding: 2px 6px; }
QLabel#count { color: #767b86; }
QLabel#beta { color: #6b7080; font-size: 11px; padding-right: 10px; }
QLabel#thumb { background: #15161a; border: 1px solid #343740; border-radius: 8px; color: #6b7080; }
QLabel#warn { background: rgba(255,180,84,30); color: #ffb454; border: 1px solid rgba(255,180,84,90);
  border-radius: 8px; padding: 8px 10px; }
QPushButton { background: #26282f; border: 1px solid #343740; border-radius: 8px; padding: 7px 12px; color: #e8e9ed; }
QPushButton:hover { background: #2e3038; border-color: #444853; }
QPushButton:pressed { background: #23252b; }
QPushButton:checked { background: #1fc98e; border-color: #1fc98e; color: #0b1f17; font-weight: bold; }
QPushButton:disabled { color: #5d6170; background: #202126; border-color: #2a2c33; }
QPushButton#primary { background: #1fc98e; color: #0b1f17; border: none; font-weight: bold; padding: 11px; }
QPushButton#primary:hover { background: #4bd9a6; }
QPushButton#primary:pressed { background: #17a874; }
QPushButton#primary:disabled { background: #1d4a3b; color: #6aa892; }
QPushButton#ghost { background: transparent; border: 1px solid transparent; color: #b4b9c4; padding: 6px 10px; }
QPushButton#ghost:hover { background: #26282f; color: white; }
QPushButton#ghost:disabled { color: #5d6170; background: transparent; }
QPushButton#accent { background: rgba(31,201,142,38); color: #7ee3bd; border: 1px solid rgba(31,201,142,90);
  font-weight: bold; padding: 6px 12px; }
QPushButton#accent:hover { background: rgba(31,201,142,66); }
QToolButton#tool { background: transparent; border: none; border-radius: 12px; color: #9096a3; font-size: 11px; padding: 8px 0 6px 0; }
QToolButton#tool:hover { background: #26282f; color: #e8e9ed; }
QToolButton#tool:checked { background: rgba(31,201,142,44); color: #7ee3bd; font-weight: bold; }
QToolButton#gear { background: transparent; border: none; border-radius: 12px; padding: 8px; }
QToolButton#gear:hover { background: #26282f; }
QListWidget#strip { background: transparent; border: none; outline: none; }
QListWidget#strip::item { border-radius: 10px; border: 2px solid transparent; padding: 4px; color: #9096a3; }
QListWidget#strip::item:hover { background: #26282f; }
QListWidget#strip::item:selected { background: rgba(31,201,142,40); border: 2px solid #1fc98e; color: #e8e9ed; }
QComboBox, QSpinBox { background: #26282f; border: 1px solid #343740; border-radius: 8px; padding: 6px 8px;
  min-height: 20px; selection-background-color: #1fc98e; }
QComboBox:hover, QSpinBox:hover { border-color: #444853; }
QComboBox:focus, QSpinBox:focus { border-color: #1fc98e; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; border: none; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #26282f; border: 1px solid #3a3d47; selection-background-color: #1fc98e;
  selection-color: #0b1f17; outline: 0; padding: 4px; }
QSlider::groove:horizontal { height: 4px; background: #343740; border-radius: 2px; }
QSlider::handle:horizontal { background: white; width: 10px; height: 10px; margin: -6px 0; border-radius: 7px;
  border: 2px solid #1fc98e; }
QSlider::sub-page:horizontal { background: #1fc98e; border-radius: 2px; }
QSlider::handle:horizontal:disabled { border-color: #4a4f5c; background: #8d919b; }
QSlider::sub-page:horizontal:disabled { background: #4a4f5c; }
QScrollBar:horizontal { height: 12px; background: transparent; margin: 0; }
QScrollBar::handle:horizontal { background: #3a3d47; border-radius: 4px; min-width: 40px; margin: 3px 2px; }
QScrollBar::handle:horizontal:hover { background: #4a4f5c; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QScrollBar:vertical { width: 12px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: #3a3d47; border-radius: 4px; min-height: 40px; margin: 2px 3px; }
QScrollBar::handle:vertical:hover { background: #4a4f5c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QTabWidget::pane { border: none; }
QMessageBox { background: #1d1e23; }
QPushButton#chip { background: #26282f; border: 1px solid #2a2c33; border-radius: 14px; padding: 4px 10px; color: #9096a3; font-size: 12px; }
QPushButton#chip:hover { border-color: #444853; color: #e8e9ed; }
QPushButton#chip:checked { background: rgba(31,201,142,38); border: 1px solid #1fc98e; color: #7ee3bd; font-weight: normal; }
QPushButton#chip:disabled { color: #5d6170; }
QPushButton#seg { background: transparent; border: 1px solid #2a2c33; border-radius: 8px; padding: 5px 11px; color: #9096a3; font-size: 12px; }
QPushButton#seg:hover { border-color: #444853; color: #e8e9ed; }
QPushButton#seg:checked { background: rgba(31,201,142,38); border: 1px solid #1fc98e; color: #7ee3bd; font-weight: bold; }
QLineEdit { background: #26282f; border: 1px solid #343740; border-radius: 8px; padding: 6px 8px; min-height: 20px;
  selection-background-color: #1fc98e; selection-color: #0b1f17; }
QLineEdit:focus { border-color: #1fc98e; }
QCheckBox, QRadioButton { spacing: 8px; }
QPlainTextEdit { background: #15161a; border: 1px solid #2a2c33; border-radius: 8px; padding: 6px 8px;
  selection-background-color: #1fc98e; }
QPlainTextEdit:focus { border-color: #1fc98e; }

QSplitter::handle { background: transparent; }
QFrame#toolbar { background: #15161a; border: none; border-bottom: 1px solid #2a2c33; }
QPlainTextEdit#result { background: #1d1e23; border: 1px solid #2a2c33; border-radius: 10px; padding: 10px 12px;
  line-height: 170%; selection-background-color: #1fc98e; selection-color: #0b1f17; }
QPlainTextEdit#result:focus { border-color: #1fc98e; }
"""


def apply_theme(app: QApplication) -> None:
    """Fusion 스타일 + 어두운 팔레트 + 스타일시트."""
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: C_BG, QPalette.WindowText: C_TEXT, QPalette.Base: C_RAISED, QPalette.AlternateBase: C_PANEL,
        QPalette.Text: C_TEXT, QPalette.Button: C_RAISED, QPalette.ButtonText: C_TEXT, QPalette.ToolTipBase: C_RAISED,
        QPalette.ToolTipText: C_TEXT, QPalette.Highlight: C_ACCENT, QPalette.HighlightedText: C_ON_ACCENT,
        QPalette.PlaceholderText: "#6b7080", QPalette.Link: C_ACCENT_LIGHT,
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#5d6170"))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
