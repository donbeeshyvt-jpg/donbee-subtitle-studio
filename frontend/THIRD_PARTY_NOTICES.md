# 第三方授權與修改通知

本網頁程式包含由 LosslessCut 修改的程式，依 GNU GPL version 2 only 授權。完整授權見 LICENSE。

- 上游：https://github.com/mifi/lossless-cut
- 著作權：Mikael Finstad 與 LosslessCut contributors。各來源既有通知維持適用。
- 來源基準：20f2e34687a691dd15b18c030d108b3b66d44098。使用者提供 docs/lossless-cut-master，參考目錄保持唯讀。
- 修改日期：2026-09-16；修改者：冬比字幕工作室開發工作。

## 實際移植對映

| 正式檔案 | 來源 | 修改 |
|---|---|---|
| src/domain/segments.ts | renderer/src/segments.ts | createSegment 欄位建立與字串 tags、combineOverlappingSegments 排序掃描演算法；微秒、UUID、marker 明示與新的邊界操作 |
| src/domain/time.ts | renderer/src/util/duration.ts | 解析／格式化步驟，改為微秒並增加非法分鐘與安全整數檢查 |
| src/hooks/useSegments.ts | renderer/src/hooks/useSegments.tsx | 抽出 100 步片段歷史，明確 reducer，移除所有原生 I/O 與媒體副作用 |
| src/components/TimelineSeg.tsx | renderer/src/TimelineSeg.tsx | SegmentOrMarker 分支、百分比 left/width 幾何與 active/selected，新增 Pointer 拖邊界與鍵盤微調 |
| src/components/Timeline.tsx | renderer/src/Timeline.tsx | 分層波形、縮放視窗、片段與獨立遊標，波形改 API |
| src/components/SegmentList.tsx | renderer/src/SegmentList.tsx | memo row、dnd-kit 排序及 TanStack 虛擬列表結構；原生選單改明確操作按鈕 |

這些是抽取與修改，並非完整 renderer 原封移植；未移植上游全部功能。其餘網頁程式為本專案新增。散佈本衍生網頁程式時，須提供 GPL v2 授權及對應原始碼；本目錄內原始碼即本版本對應來源，不以必要通知作產品行銷。

## 依賴

React／React DOM、Vite、Vitest、TypeScript、dnd-kit、TanStack Virtual 為其各自套件授權；lucide-react 為 ISC。完整版本鎖定於 package-lock.json，各依賴原文 LICENSE 隨 node_modules 安裝保留。未把所有依賴或後端模型概括為同一授權。
