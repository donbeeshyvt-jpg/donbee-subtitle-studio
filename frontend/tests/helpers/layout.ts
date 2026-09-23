// jsdom 沒有版面尺寸：TanStack 虛擬列表用 offsetWidth／offsetHeight 決定可視範圍，全為 0 時一列都不渲染。
// 測試需要看到列時呼叫 mockLayout()，測試結束呼叫回傳的函式還原。
export function mockLayout(width = 400, height = 600) {
  const proto = HTMLElement.prototype;
  const original = {
    width: Object.getOwnPropertyDescriptor(proto, "offsetWidth"),
    height: Object.getOwnPropertyDescriptor(proto, "offsetHeight"),
  };
  Object.defineProperty(proto, "offsetWidth", { configurable: true, get: () => width });
  Object.defineProperty(proto, "offsetHeight", { configurable: true, get: () => height });
  return () => {
    if (original.width) Object.defineProperty(proto, "offsetWidth", original.width);
    else delete (proto as unknown as Record<string, unknown>).offsetWidth;
    if (original.height) Object.defineProperty(proto, "offsetHeight", original.height);
    else delete (proto as unknown as Record<string, unknown>).offsetHeight;
  };
}
