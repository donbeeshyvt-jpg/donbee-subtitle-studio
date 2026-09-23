// 錯誤分類：只看 code（App 層測試的 API 替身有自己的 ApiError 類別，instanceof 不可靠）。
export const errorCode = (error: unknown): string | undefined =>
  typeof error === "object" && error !== null && "code" in error ? String((error as { code?: unknown }).code) : undefined;

// 這台電腦開不了原生選擇視窗（沒有桌面工作階段）：才改用手動輸入路徑／瀏覽器上傳。
// 其他錯誤（連線失效、服務沒開）照常顯示，不要誤導使用者去打字。
export const isDialogUnavailable = (error: unknown) => errorCode(error) === "DIALOG_UNAVAILABLE";

export const errorMessage = (error: unknown) => (error instanceof Error ? error.message : String(error));
