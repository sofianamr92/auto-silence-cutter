from types import SimpleNamespace
from unittest.mock import patch

from ui.main_window import MainWindow


class FakeProgressBar:
    def __init__(self):
        self.visible = None

    def setVisible(self, visible):
        self.visible = visible


def test_stale_analysis_result_is_ignored():
    window = SimpleNamespace(_analysis_generation=2)

    MainWindow.on_analysis_finished(window, 1, [], [])


def test_current_analysis_error_hides_progress_bar():
    progress_bar = FakeProgressBar()
    window = SimpleNamespace(_analysis_generation=2, progress_bar=progress_bar)

    with patch("ui.main_window.QMessageBox.warning") as warning:
        MainWindow.on_analysis_error(window, 2, "analysis failed")

    assert progress_bar.visible is False
    warning.assert_called_once()


def test_stale_analysis_error_does_not_hide_current_progress():
    progress_bar = FakeProgressBar()
    window = SimpleNamespace(_analysis_generation=2, progress_bar=progress_bar)

    with patch("ui.main_window.QMessageBox.warning") as warning:
        MainWindow.on_analysis_error(window, 1, "stale failure")

    assert progress_bar.visible is None
    warning.assert_not_called()
