"""檔案系統小工具。

Windows 上剛寫好的檔案常被防毒即時掃描／搜尋索引短暫開啟，這時把「暫存檔→正式檔」改名會得到
WinError 32（正由另一個程序使用）或 5（存取被拒）。這是暫時性的：有限次重試即可，其他錯誤照常拋出。
所有「先寫暫存、校驗後改名公開」的地方都要走 `replace_with_retry`，不要直接呼叫 os.replace／Path.replace；
馬上要把剛寫好的檔案讀回來（算雜湊、校驗）時用 `open_with_retry`。"""
from __future__ import annotations

import os
import time

# 5＝存取被拒、32＝正由另一個程序使用、33＝另一個程序鎖定檔案的一部分
SHARING_VIOLATIONS = {5, 32, 33}


def is_sharing_violation(error) -> bool:
    """是不是「檔案暫時被別的程序占用」這一類錯誤（不含檔案已存在、路徑不存在、磁碟已滿）。"""
    if isinstance(error, (FileExistsError, FileNotFoundError)):
        return False
    return getattr(error, "winerror", None) in SHARING_VIOLATIONS or isinstance(error, PermissionError)


def open_with_retry(path, mode: str = "rb", attempts: int = 10, delay: float = 0.05, **kwargs):
    """開啟剛寫好／剛改名的檔案：暫時性占用有限次重試（同 replace_with_retry），仍被占用就把原錯誤拋出。"""
    for attempt in range(attempts):
        try:
            return open(path, mode, **kwargs)
        except OSError as error:
            if not is_sharing_violation(error) or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def replace_with_retry(source, target, attempts: int = 10, delay: float = 0.05, replace=None):
    """把 source 改名成 target（預設 os.replace：原子覆寫；傳 os.rename 則 Windows 上同名不覆蓋）。

    暫時性占用最多重試 attempts 次，間隔遞增（0.05、0.10…，總計約 2.3 秒）；仍被占用就把原錯誤拋出。"""
    action = replace or os.replace
    for attempt in range(attempts):
        try:
            return action(source, target)
        except OSError as error:
            if not is_sharing_violation(error) or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))
