"""
快捷键映射模块
默认映射 + config.json「image_sorter」节 shortcuts 键覆盖

与源版本的差异：
- PyQt6 → PySide6
- 持久化从 settings/shortcuts.json 改为工具箱 config.json
  image_sorter 节（settings.get_dict / set_setting，见 settings.py）
- 删除 toggle_fullscreen（F11 全屏）动作——独立窗口时代的功能，页面化后无意义
"""

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt

from image_sorter.settings import get_dict, set_setting
from image_sorter._log import logger


class ShortcutManager:
    """快捷键映射管理器"""

    ACTION_LABELS: Dict[str, str] = {
        "undo": "撤销",
        "prev_file": "上一张",
        "next_file": "下一张",
        "delete_file": "删除文件",
        "keep_file": "保留文件",
        "play_or_keep": "播放/保留",
        "favorite_file": "收藏文件",
        "go_parent_folder": "返回上一级文件夹",
        "quick_classify_1": "快速分类到第1个文件夹",
        "quick_classify_2": "快速分类到第2个文件夹",
        "quick_classify_3": "快速分类到第3个文件夹",
        "quick_classify_4": "快速分类到第4个文件夹",
        "quick_classify_5": "快速分类到第5个文件夹",
        "quick_classify_6": "快速分类到第6个文件夹",
        "quick_classify_7": "快速分类到第7个文件夹",
        "quick_classify_8": "快速分类到第8个文件夹",
        "quick_classify_9": "快速分类到第9个文件夹",
        "quick_delete_0": "快速删除（主键盘0）",
        "enter_subfolder_1": "进入第1个子文件夹（小键盘）",
        "enter_subfolder_2": "进入第2个子文件夹（小键盘）",
        "enter_subfolder_3": "进入第3个子文件夹（小键盘）",
        "enter_subfolder_4": "进入第4个子文件夹（小键盘）",
        "enter_subfolder_5": "进入第5个子文件夹（小键盘）",
        "enter_subfolder_6": "进入第6个子文件夹（小键盘）",
        "enter_subfolder_7": "进入第7个子文件夹（小键盘）",
        "enter_subfolder_8": "进入第8个子文件夹（小键盘）",
        "enter_subfolder_9": "进入第9个子文件夹（小键盘）",
        "enter_parent_folder_keypad": "返回上一级（小键盘0）",
    }

    DEFAULT_SHORTCUTS: Dict[str, List[str]] = {
        "undo": ["Ctrl+Z"],
        "prev_file": ["Left", "A"],
        "next_file": ["Right", "D"],
        "delete_file": ["Up", "W", "Delete"],
        "keep_file": ["Down", "S"],
        "play_or_keep": ["Return", "Enter", "Space"],
        "favorite_file": ["F"],
        "go_parent_folder": ["Backspace"],
        "quick_classify_1": ["1"],
        "quick_classify_2": ["2"],
        "quick_classify_3": ["3"],
        "quick_classify_4": ["4"],
        "quick_classify_5": ["5"],
        "quick_classify_6": ["6"],
        "quick_classify_7": ["7"],
        "quick_classify_8": ["8"],
        "quick_classify_9": ["9"],
        "quick_delete_0": ["0"],
        "enter_subfolder_1": ["Num+1"],
        "enter_subfolder_2": ["Num+2"],
        "enter_subfolder_3": ["Num+3"],
        "enter_subfolder_4": ["Num+4"],
        "enter_subfolder_5": ["Num+5"],
        "enter_subfolder_6": ["Num+6"],
        "enter_subfolder_7": ["Num+7"],
        "enter_subfolder_8": ["Num+8"],
        "enter_subfolder_9": ["Num+9"],
        "enter_parent_folder_keypad": ["Num+0"],
    }

    KEY_NAME_MAP: Dict[str, Qt.Key] = {
        "LEFT": Qt.Key_Left,
        "RIGHT": Qt.Key_Right,
        "UP": Qt.Key_Up,
        "DOWN": Qt.Key_Down,
        "A": Qt.Key_A,
        "D": Qt.Key_D,
        "W": Qt.Key_W,
        "S": Qt.Key_S,
        "F": Qt.Key_F,
        "Z": Qt.Key_Z,
        "DELETE": Qt.Key_Delete,
        "SPACE": Qt.Key_Space,
        "RETURN": Qt.Key_Return,
        "ENTER": Qt.Key_Enter,
        "F11": Qt.Key_F11,
        "ESC": Qt.Key_Escape,
        "TAB": Qt.Key_Tab,
        "BACKSPACE": Qt.Key_Backspace,
        "HOME": Qt.Key_Home,
        "END": Qt.Key_End,
        "PAGEUP": Qt.Key_PageUp,
        "PAGEDOWN": Qt.Key_PageDown,
        "PGUP": Qt.Key_PageUp,
        "PGDN": Qt.Key_PageDown,
        "INSERT": Qt.Key_Insert,
        "0": Qt.Key_0,
        "1": Qt.Key_1,
        "2": Qt.Key_2,
        "3": Qt.Key_3,
        "4": Qt.Key_4,
        "5": Qt.Key_5,
        "6": Qt.Key_6,
        "7": Qt.Key_7,
        "8": Qt.Key_8,
        "9": Qt.Key_9,
    }

    MOD_NAME_MAP: Dict[str, Qt.KeyboardModifier] = {
        "CTRL": Qt.ControlModifier,
        "CONTROL": Qt.ControlModifier,
        "ALT": Qt.AltModifier,
        "SHIFT": Qt.ShiftModifier,
        "META": Qt.MetaModifier,
        "CMD": Qt.MetaModifier,
        "NUM": Qt.KeypadModifier,
        "KEYPAD": Qt.KeypadModifier,
        "NUMPAD": Qt.KeypadModifier,
    }

    def __init__(self):
        self._compiled: Dict[str, List[Tuple[int, int]]] = {}
        self._shortcuts: Dict[str, List[str]] = {}
        self._load_shortcuts()

    def _load_shortcuts(self):
        """加载快捷键映射（默认 + config.json「image_sorter」节覆盖）"""
        merged = {action: list(keys) for action, keys in self.DEFAULT_SHORTCUTS.items()}

        try:
            overrides = get_dict("shortcuts")
            if isinstance(overrides, dict):
                for action, keys in overrides.items():
                    if action not in self.DEFAULT_SHORTCUTS:
                        continue
                    if isinstance(keys, list) and keys:
                        merged[action] = [str(k) for k in keys if str(k).strip()]
        except Exception as e:
            logger.log_exception(e, "加载快捷键配置失败，回退默认映射")

        compiled: Dict[str, List[Tuple[int, int]]] = {}
        valid_shortcuts: Dict[str, List[str]] = {}
        for action, shortcuts in merged.items():
            items: List[Tuple[int, int]] = []
            valid_texts: List[str] = []
            for shortcut in shortcuts:
                normalized = str(shortcut).strip()
                parsed = self._parse_shortcut(normalized)
                if parsed is not None:
                    items.append(parsed)
                    valid_texts.append(normalized)
            if items:
                compiled[action] = items
                valid_shortcuts[action] = valid_texts

        self._compiled = compiled
        self._shortcuts = valid_shortcuts

    def matches(self, event, action: str) -> bool:
        """
        判断按键事件是否匹配指定动作

        Args:
            event: QKeyEvent
            action: 动作名
        """
        if action not in self._compiled:
            return False

        event_key = int(event.key())
        # PySide6：枚举/flags 对象 int() 不可用，统一走 .value 取整数值
        event_mods = (
            event.modifiers()
            & (
                Qt.ControlModifier
                | Qt.AltModifier
                | Qt.ShiftModifier
                | Qt.MetaModifier
                | Qt.KeypadModifier
            )
        ).value

        for mods, key in self._compiled[action]:
            if mods == event_mods and key == event_key:
                return True
        return False

    def get_shortcuts(self) -> Dict[str, List[str]]:
        """获取当前生效快捷键映射（文本形式）"""
        result: Dict[str, List[str]] = {}
        for action in self.DEFAULT_SHORTCUTS.keys():
            if action in self._shortcuts and self._shortcuts[action]:
                result[action] = list(self._shortcuts[action])
            else:
                result[action] = list(self.DEFAULT_SHORTCUTS.get(action, []))
        return result

    def save_shortcuts(self, mapping: Dict[str, List[str]]) -> None:
        """保存快捷键映射到 config.json 并立即生效"""
        payload: Dict[str, List[str]] = {}
        for action in self.DEFAULT_SHORTCUTS.keys():
            shortcuts = mapping.get(action, [])
            cleaned = [str(x).strip() for x in shortcuts if str(x).strip()]
            payload[action] = cleaned if cleaned else list(self.DEFAULT_SHORTCUTS.get(action, []))

        set_setting("shortcuts", payload)
        self._load_shortcuts()

    def reset_to_default(self) -> None:
        """重置为默认快捷键（清空覆盖，config 中回到全默认）"""
        set_setting("shortcuts", {})
        self._load_shortcuts()

    def validate_shortcut(self, shortcut: str) -> bool:
        """校验快捷键字符串是否合法"""
        return self._parse_shortcut(shortcut) is not None

    def _parse_shortcut(self, shortcut: str) -> Optional[Tuple[int, int]]:
        """解析单个快捷键字符串，如 Ctrl+Z"""
        text = shortcut.strip()
        if not text:
            return None

        parts = [p.strip() for p in text.split("+") if p.strip()]
        if not parts:
            return None

        key_name = parts[-1].upper()
        key_value = self.KEY_NAME_MAP.get(key_name)
        if key_value is None:
            dynamic_key = getattr(Qt.Key, f"Key_{key_name}", None)
            if dynamic_key is None:
                return None
            key_value = dynamic_key

        mods = Qt.KeyboardModifier.NoModifier
        for mod_name in parts[:-1]:
            mapped = self.MOD_NAME_MAP.get(mod_name.upper())
            if mapped is not None:
                mods |= mapped

        # mods 为枚举成员（无修饰/单个）或 OR 组合成员，.value 均可取整数
        return mods.value, int(key_value)
