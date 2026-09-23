import { render, fireEvent, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import TimelineSeg from "../src/components/TimelineSeg";
import { createSegment } from "../src/domain/segments";
afterEach(cleanup);
describe("時間軸可操作性", () => {
  it("局部素材仍保留來源時間，但以素材視窗繪圖，提示顯示素材相對時間", () => {
    const { container } = render(
      <TimelineSeg
        seg={createSegment({
          start_us: 6600000000,
          end_us: 6660000000,
          name: "來源",
        })}
        index={0}
        duration={60000000}
        startUs={6600000000}
        active={true}
        onSelect={() => {}}
        onBoundary={() => {}}
      />,
    );
    const segment = container.querySelector(".timeline-segment") as HTMLElement;
    expect(segment.style.left).toBe("0%");
    expect(segment.style.width).toBe("100%");
    // 2026-09-18：資料仍是來源時間（6600 秒起），介面一律顯示素材相對時間（00:00:00 起）
    expect(segment.title).toContain("00:00:00.000 → 00:01:00.000");
  });
  it("鍵盤微調邊界且使用來源微秒", () => {
    const boundary = vi.fn();
    render(
      <TimelineSeg
        seg={createSegment({
          name: "測試",
          start_us: 6600000000,
          end_us: 6660000000,
        })}
        index={0}
        duration={7200000000}
        active={true}
        onSelect={() => {}}
        onBoundary={boundary}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button", { name: "測試 入點" }), {
      key: "ArrowRight",
    });
    expect(boundary).toHaveBeenCalledWith("start_us", 6600100000);
  });
  it("marker只有標記按鈕，不產生出點控制", () => {
    render(
      <TimelineSeg
        seg={createSegment({ start_us: 6600000000, name: "重點" })}
        index={0}
        duration={7200000000}
        active={false}
        onSelect={() => {}}
        onBoundary={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "標記 1 重點" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /出點/ })).toBeNull();
  });
});

// ---- 2026-09-18 使用者：入點／出點按了看不到效果、片段條不能按中間拖動 ----
import Timeline from "../src/components/Timeline";

function pointerPolyfill() {
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
  if (!proto.setPointerCapture) {
    proto.setPointerCapture = () => {};
    proto.releasePointerCapture = () => {};
    proto.hasPointerCapture = () => true;
  }
}
// jsdom 沒有 PointerEvent：用 MouseEvent 送出 pointer 事件並帶 pointerId，React 會照常派給 onPointer*
function pointer(el: Element, type: string, clientX: number) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX });
  Object.defineProperty(event, "pointerId", { value: 1 });
  el.dispatchEvent(event);
}

describe("時間軸操作", () => {
  it("在時間軸上畫出入點到出點的範圍", () => {
    const { container } = render(
      <Timeline
        duration={60_000_000}
        startUs={0}
        time={0}
        inUs={12_000_000}
        outUs={30_000_000}
        items={[]}
        active=""
        seek={() => {}}
        select={() => {}}
        boundary={() => {}}
      />,
    );
    const band = container.querySelector(".io-range") as HTMLElement;
    expect(band).toBeTruthy();
    expect(band.style.left).toBe("20%");
    expect(band.style.width).toBe("30%");
  });

  it("按住片段中間拖動可整段移動（保持長度）", () => {
    pointerPolyfill();
    const move = vi.fn();
    const { container } = render(
      <div style={{ width: "1000px" }}>
        <TimelineSeg
          seg={createSegment({ name: "片段", start_us: 10_000_000, end_us: 20_000_000 })}
          index={0}
          duration={100_000_000}
          startUs={0}
          active={true}
          onSelect={() => {}}
          onBoundary={() => {}}
          onMove={move}
        />
      </div>,
    );
    const body = container.querySelector(".segment-title") as HTMLElement;
    const parent = container.querySelector(".timeline-segment")!.parentElement as HTMLElement;
    parent.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 40, right: 1000, bottom: 40, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    pointer(body, "pointerdown", 150);
    pointer(body, "pointermove", 400); // 右移 25% = 25 秒
    pointer(body, "pointerup", 400);
    expect(move).toHaveBeenCalledWith(35_000_000, 45_000_000);
  });

  it("自動整段片段不擋操作且以虛線呈現", () => {
    const { container } = render(
      <TimelineSeg
        seg={createSegment({ name: "整段", start_us: 0, end_us: 100_000_000, tags: { auto: "full" } })}
        index={0}
        duration={100_000_000}
        startUs={0}
        active={false}
        onSelect={() => {}}
        onBoundary={() => {}}
      />,
    );
    expect((container.querySelector(".timeline-segment") as HTMLElement).classList.contains("auto-full")).toBe(true);
  });
});

// ---- 2026-09-18 使用者：刻度列拖動＝拉播放頭；波形按下放開＝入出點、範圍外點一下取消；片段右鍵刪除 ----
describe("時間軸操作（拖曳與選取）", () => {
  function mount(extra: Record<string, unknown> = {}, items: ReturnType<typeof createSegment>[] = []) {
    pointerPolyfill();
    const seek = vi.fn();
    const onRange = vi.fn();
    const onClearRange = vi.fn();
    const remove = vi.fn();
    const utils = render(
      <Timeline duration={60_000_000} startUs={0} time={0} items={items} active="" seek={seek} select={() => {}} boundary={() => {}} onRange={onRange} onClearRange={onClearRange} remove={remove} {...extra} />,
    );
    const surface = utils.container.querySelector(".timeline-surface") as HTMLElement;
    surface.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 142, right: 1000, bottom: 142, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    return { ...utils, seek, onRange, onClearRange, remove };
  }

  it("刻度列按住拖動會連續拉動播放頭", () => {
    const { container, seek } = mount();
    const ruler = container.querySelector(".ruler") as HTMLElement;
    pointer(ruler, "pointerdown", 250);
    expect(seek).toHaveBeenLastCalledWith(15_000_000);
    pointer(ruler, "pointermove", 500);
    expect(seek).toHaveBeenLastCalledWith(30_000_000);
    pointer(ruler, "pointerup", 500);
  });

  it("波形按下是入點、放開是出點；範圍外點一下取消；範圍內點一下跳到該處", () => {
    const { container, onRange, onClearRange, seek, rerender } = mount();
    const wave = container.querySelector(".wave-area") as HTMLElement;
    pointer(wave, "pointerdown", 200);
    pointer(wave, "pointermove", 300);
    pointer(wave, "pointerup", 300);
    expect(onRange).toHaveBeenLastCalledWith(12_000_000, 18_000_000);
    // 反向拖也成立（放開在左邊）
    pointer(wave, "pointerdown", 300);
    pointer(wave, "pointermove", 200);
    pointer(wave, "pointerup", 200);
    expect(onRange).toHaveBeenLastCalledWith(12_000_000, 18_000_000);
    // 有選取時：範圍外點一下 → 取消；範圍內點一下 → 跳播放
    rerender(
      <Timeline duration={60_000_000} startUs={0} time={0} items={[]} active="" seek={seek} select={() => {}} boundary={() => {}} onRange={onRange} onClearRange={onClearRange} inUs={12_000_000} outUs={18_000_000} />,
    );
    pointer(wave, "pointerdown", 800);
    pointer(wave, "pointerup", 800);
    expect(onClearRange).toHaveBeenCalledTimes(1);
    pointer(wave, "pointerdown", 250);
    pointer(wave, "pointerup", 250);
    expect(seek).toHaveBeenLastCalledWith(15_000_000);
  });

  it("片段條按右鍵會刪除該片段（不跳出瀏覽器選單）", () => {
    const seg = createSegment({ name: "片段", start_us: 10_000_000, end_us: 20_000_000 });
    const { container, remove } = mount({}, [seg]);
    const bar = container.querySelector(".timeline-segment") as HTMLElement;
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    bar.dispatchEvent(event);
    expect(remove).toHaveBeenCalledWith(seg.id);
    expect(event.defaultPrevented).toBe(true);
  });
});

describe("片段邊界拖曳", () => {
  it("拖出點把手後放開會提交最後位置（不依賴渲染時機），之後仍可拖動整段", () => {
    pointerPolyfill();
    const boundary = vi.fn();
    const move = vi.fn();
    const { container } = render(
      <div>
        <TimelineSeg seg={createSegment({ name: "片段", start_us: 10_000_000, end_us: 20_000_000 })} index={0} duration={100_000_000} startUs={0} active onSelect={() => {}} onBoundary={boundary} onMove={move} />
      </div>,
    );
    const parent = container.querySelector(".timeline-segment")!.parentElement as HTMLElement;
    parent.getBoundingClientRect = () => ({ left: 0, width: 1000, top: 0, height: 40, right: 1000, bottom: 40, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    const outHandle = container.querySelectorAll(".trim-handle")[1] as HTMLElement;
    pointer(outHandle, "pointerdown", 200);
    pointer(outHandle, "pointermove", 400);
    pointer(outHandle, "pointerup", 400);
    expect(boundary).toHaveBeenCalledWith("end_us", 40_000_000);
    const body = container.querySelector(".segment-title") as HTMLElement;
    pointer(body, "pointerdown", 150);
    pointer(body, "pointermove", 250);
    pointer(body, "pointerup", 250);
    expect(move).toHaveBeenCalledWith(20_000_000, 30_000_000);
  });
});
