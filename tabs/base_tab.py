import tkinter as tk
from abc import ABC, abstractmethod
from tkinter import ttk
from typing import Any, Callable, NamedTuple


class TabCallbacks(NamedTuple):
    root: tk.Tk
    config: Callable[[], dict]
    save_config: Callable[[dict], None]
    log: Callable[[str, str], None]
    record_download: Callable[[dict], None]
    coordinator: Any
    get_weekday_names: Callable[[], list]


class BaseTab(ABC):
    def __init__(self, parent, callbacks: TabCallbacks):
        self.parent = parent
        self.cb = callbacks
        self.frame = ttk.Frame(parent)

    @abstractmethod
    def build_ui(self):
        ...

    def show(self):
        self.frame.pack(fill=tk.BOTH, expand=True)

    def hide(self):
        self.frame.pack_forget()