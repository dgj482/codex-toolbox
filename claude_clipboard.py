"""Temporary console paste, with the original clipboard preserved on the UI thread."""
from __future__ import annotations


class ConsoleClipboard:
    def __init__(self, window):
        if window is None or window.native is None:
            raise RuntimeError("请从工具箱窗口启动 Claude 页面翻译")
        self.native = window.native
        self.previous = None
        self.owned_text = set()

    def _ui(self, callback):
        from System import Action
        result, errors = [], []

        def run():
            try:
                result.append(callback())
            except Exception as error:
                errors.append(error)

        self.native.Invoke(Action(run))
        if errors:
            raise RuntimeError("剪贴板正被其他程序占用，请稍后重试") from errors[0]
        return result[0] if result else None

    def __enter__(self):
        from System.Windows.Forms import Clipboard
        self.clipboard = Clipboard
        self.previous = self._ui(Clipboard.GetDataObject)
        return self

    def read(self):
        return str(self._ui(self.clipboard.GetText) or "")

    def set(self, text):
        self._ui(lambda: self.clipboard.SetText(text))
        self.owned_text.add(text)

    def copied(self):
        text = self.read()
        self.owned_text.add(text)
        return text

    def __exit__(self, *_):
        def restore():
            # Keep a newer clipboard item if the user copied it during loading.
            if str(self.clipboard.GetText() or "") not in self.owned_text:
                return
            if self.previous is None:
                self.clipboard.Clear()
            else:
                self.clipboard.SetDataObject(self.previous, True)
        self._ui(restore)
