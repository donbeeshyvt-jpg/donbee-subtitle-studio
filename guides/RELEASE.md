# 第一版與 GitHub CLI 發布

版本：`v2.0.0-preview.1`，第一個可分享的網頁工作臺程式快照。`frontend/package.json` 的產品主版本為 2.0.0；此 tag 明確標示預覽版，不代表全面驗收完成。

## 版本內容

區段下載、本機轉錄、可配置的本機／遠端 AI 校字、字幕預覽、時間軸及輸出；提供繁中與英文介紹、API／CLI 手冊、操作 SKILL，以及可直接使用的前端打包。

已知邊界：需自行準備系統工具與模型；遠端轉錄仍含本機草稿；AI 可能錯改；逐詞對齊失敗會有降級提示；工作計畫未涵蓋完整校字流程。VibeVoice 選用環境不隨包提供。本機既有環境的測試不能視為乾淨 Windows 首次安裝已驗收。

## 只發布程式與使用文件

根目錄 `.gitignore` 採白名單。`docs/`、data、素材、模型、tools、虛擬環境、快取、測試產物、個人 agent 設定及口吻包都不提交。兩份依賴私有文件／參考目錄的測試也只留在本機；公開測試為程式碼，非測試產物。

公開文件放 `guides/`；必要第三方 LICENSE／NOTICE 隨程式保留。不要為了「只推程式」移除授權通知。不要把全部模型或依賴宣稱為同一授權；見根目錄第三方通知。

```powershell
git config core.hooksPath scripts/git-hooks
git ls-files
python scripts/check-public-release.py
git status --short
git log -1 --oneline
git tag --list
```

核對 `git ls-files docs data uploads downloads artifacts models tools` 應無輸出。檢查會掃描 Git 暫存／追蹤內容及常見金鑰格式，但不是完整秘密偵測保證。未列在白名單的新檔案，先人工確認用途再調整，不使用 `git add -f` 繞過。

## 準備推上 GitHub

下列命令會登入／建立遠端／上傳，**由擁有者確認帳號、名稱與可見性後才執行**。準備階段不會自動推送。

```powershell
gh auth login
gh auth status
# OWNER/REPO 換成確定的目標。先 private；確定要公開才改 --public。
gh repo create OWNER/REPO --private --source . --remote origin --push
git push origin v2.0.0-preview.1
```

若已有遠端，不要重建或覆寫：先 `git remote -v` 核對，再對正確目標推送目前分支與 tag。不要 force push，不要把本機 docs 另做附件上傳。GitHub Release 頁面是另外的發布動作，本版準備不會自動建立。
