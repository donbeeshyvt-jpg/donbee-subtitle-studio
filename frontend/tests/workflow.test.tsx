import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import WorkflowPanel from "../src/components/WorkflowPanel";
import { request } from "../src/api/client";
vi.mock("../src/api/client", () => ({
  request: vi.fn(async (_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? { plan_id: "plan", dependency_graph: [] }
      : { items: [] },
  ),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const props = {
  project: "p",
  sourceId: "s",
  ranges: "00:01-00:04",
  provider: "",
  run: (fn: () => Promise<unknown>) => {
    void fn();
  },
};
it("exports source captions without implicitly downloading audio", async () => {
  render(<WorkflowPanel {...props} sourceSubtitlesEnabled outputRoots={[]} />);
  fireEvent.change(screen.getByLabelText("字幕方式"), { target: { value: "source" } });
  fireEvent.change(screen.getByLabelText("分析"), { target: { value: "none" } });
  fireEvent.click(screen.getByLabelText("取得選段影片"));
  fireEvent.click(screen.getByRole("button", { name: "建立流程快照" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/projects/p/plans", expect.objectContaining({ method: "POST" })));
  const call = vi.mocked(request).mock.calls.find(([, init]) => init?.method === "POST")!;
  const body = JSON.parse(call[1]!.body as string);
  expect(body.deliverables.formats).toEqual(["srt"]);
  expect(body.ranges).toEqual([{ start_us: 1000000, end_us: 4000000 }]);
});
it("requires explicit full-source scope when the download range is blank", () => {
  render(<WorkflowPanel {...props} ranges="" sourceSubtitlesEnabled outputRoots={[]} />);
  const create = screen.getByRole("button", { name: "建立流程快照" }) as HTMLButtonElement;
  expect(create.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("使用完整來源"));
  expect(create.disabled).toBe(false);
});
it("can request audio and video together", async () => {
  render(<WorkflowPanel {...props} sourceSubtitlesEnabled outputRoots={[]} />);
  fireEvent.click(screen.getByLabelText("輸出音檔"));
  fireEvent.click(screen.getByRole("button", { name: "建立流程快照" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/projects/p/plans", expect.objectContaining({ method: "POST" })));
  const call = vi.mocked(request).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string).deliverables.formats).toEqual(["mp4", "audio", "srt"]);
});
it("disables source policies until the server advertises support", () => {
  render(
    <WorkflowPanel
      {...props}
      sourceSubtitlesEnabled={false}
      outputRoots={[]}
    />,
  );
  expect(
    (screen.getByRole("option", { name: "使用來源字幕" }) as HTMLOptionElement)
      .disabled,
  ).toBe(true);
  expect(
    (
      screen.getByRole("option", {
        name: "比對來源與辨識字幕",
      }) as HTMLOptionElement
    ).disabled,
  ).toBe(true);
});
it("captures source language, origin preference, segment timing and allowed roots in the immutable plan", async () => {
  render(
    <WorkflowPanel
      {...props}
      sourceSubtitlesEnabled={true}
      outputRoots={[{ id: "allowed", name: "交付目錄" }]}
    />,
  );
  fireEvent.change(screen.getByLabelText("字幕方式"), {
    target: { value: "source" },
  });
  fireEvent.change(screen.getByLabelText("來源字幕語言"), {
    target: { value: "ja" },
  });
  fireEvent.change(screen.getByLabelText("來源字幕種類"), {
    target: { value: "manual" },
  });
  fireEvent.change(screen.getByLabelText("流程輸出位置"), {
    target: { value: "allowed" },
  });
  expect(
    (screen.getByRole("option", { name: "要求逐詞對齊" }) as HTMLOptionElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "建立流程快照" }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      "/projects/p/plans",
      expect.objectContaining({ method: "POST" }),
    ),
  );
  const call = vi
    .mocked(request)
    .mock.calls.find(([, init]) => init?.method === "POST")!;
  const body = JSON.parse(call[1]!.body as string);
  expect(body.subtitles).toMatchObject({
    policy: "source",
    source_language: "ja",
    source_kind: "manual",
    alignment: "allow_segment",
  });
  expect(body.acquisition.output_root_id).toBe("allowed");
  expect(body.deliverables.output_root_id).toBe("allowed");
});
it("offers a refinement engine defaulting to WhisperX and sends it with the analysis block", async () => {
  render(<WorkflowPanel {...props} sourceSubtitlesEnabled outputRoots={[]} />);
  const engine = screen.getByLabelText("精修引擎") as HTMLSelectElement;
  expect(engine.value).toBe("whisperx");
  expect(screen.getByRole("option", { name: /VibeVoice/ }).textContent).toContain("11.5 GB");
  fireEvent.change(screen.getByLabelText("分析"), { target: { value: "quality" } });
  fireEvent.change(engine, { target: { value: "vibevoice" } });
  fireEvent.click(screen.getByRole("button", { name: "建立流程快照" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/projects/p/plans", expect.objectContaining({ method: "POST" })));
  const call = vi.mocked(request).mock.calls.find(([, init]) => init?.method === "POST")!;
  const body = JSON.parse(call[1]!.body as string);
  expect(body.analysis.mode).toBe("quality");
  expect(body.analysis.engine).toBe("vibevoice");
  expect(body.analysis.fallback_engine).toBe("whisperx");
});
it("labels analysis modes by the route they actually run", () => {
  render(<WorkflowPanel {...props} sourceSubtitlesEnabled outputRoots={[]} />);
  expect(screen.getByRole("option", { name: "平衡：草稿＋選段精修" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "精修：草稿＋全段精修" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "混合模型平衡" })).toBeNull();
});

it("registers a pasted source before creating the snapshot when nothing has been downloaded yet", async () => {
  // 貼了連結、還沒按「加入下載」：快照按鈕可用，先登記來源（不下載）再送出快照
  const registerSource = vi.fn(async () => "s_new");
  render(<WorkflowPanel {...props} sourceId="" registerSource={registerSource} sourceSubtitlesEnabled outputRoots={[]} />);
  const create = screen.getByRole("button", { name: "建立流程快照" }) as HTMLButtonElement;
  expect(create.disabled).toBe(false);
  fireEvent.click(create);
  await waitFor(() => expect(request).toHaveBeenCalledWith("/projects/p/plans", expect.objectContaining({ method: "POST" })));
  expect(registerSource).toHaveBeenCalledTimes(1);
  const call = vi.mocked(request).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string).source_id).toBe("s_new");
});
it("keeps the snapshot disabled and explains why when there is neither a source nor a link", () => {
  render(<WorkflowPanel {...props} sourceId="" registerSource={null} sourceSubtitlesEnabled outputRoots={[]} />);
  expect((screen.getByRole("button", { name: "建立流程快照" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/請先貼上來源連結或選擇素材/)).toBeTruthy();
});

it("sends the same boundary policy as the manual download so the workflow can reuse it", async () => {
  // 2026-09-18：快照與「加入下載」都用 accurate（source_seek 起點會提前約 10 秒）；兩邊一致流程才能重用已下載素材
  render(<WorkflowPanel {...props} sourceSubtitlesEnabled outputRoots={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "建立流程快照" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/projects/p/plans", expect.objectContaining({ method: "POST" })));
  const call = vi.mocked(request).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string).acquisition.boundary_policy).toBe("accurate");
});
