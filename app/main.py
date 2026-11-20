# -*- coding: utf-8 -*-
import sys
import shutil
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
from ui.main_window import MainWindow

def verify_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[!] تحذير: لم يتم العثور على FFmpeg أو FFprobe في متغيرات PATH.")

def main():
    verify_ffmpeg()
    import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("autosilencecutter.app")
    app = QApplication(sys.argv)
    from PySide6.QtGui import QIcon
    import os
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app.setWindowIcon(QIcon(os.path.join(app_dir, "assets", "icon.ico")))
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)

    # تطبيق السمة الداكنة الاحترافية
    dark_stylesheet = """
        QWidget {
            background-color: #0c0d11;
            color: #f1f5f9;
            font-family: 'Segoe UI', 'Cairo', Tahoma, sans-serif;
            font-size: 13px;
        }
        QPushButton {
            background-color: #1e2433;
            border: 1px solid #2d3548;
            padding: 6px 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #2b344a;
            border-color: #3b82f6;
        }
        QComboBox, QLineEdit {
            background-color: #141720;
            border: 1px solid #272c3d;
            padding: 5px;
            border-radius: 4px;
            color: #fff;
        }
    """
    app.setStyleSheet(dark_stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
