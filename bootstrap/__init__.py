"""啟動自檢與依賴安裝（純標準函式庫，可用系統 Python 3.12 直接執行）。

職責：檢查環境類軟體（只檢查與指引，不安裝系統軟體）、建立專案 .venv、依 requirements.lock 只補缺、
比對 models.manifest.json、以 JSON 回報。模型下載由 M0-P3 補上。
"""
