import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ClassificationPanel from "../src/components/ClassificationPanel";
import type { Classification } from "../src/api/types";
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 106,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        start: index * 106,
      })),
    measureElement: vi.fn(),
  }),
}));
afterEach(cleanup);
it("plays source time and submits an explicit manual label without mutating candidate data", async () => {
  const c = {
    id: "classification",
    asset_id: "asset",
    source_id: "source",
    spans: [
      {
        id: "span",
        start_us: 6600000000,
        end_us: 6610000000,
        label: "mixed",
        requires_review: true,
      },
    ],
  } as Classification;
  const seek = vi.fn(),
    override = vi.fn().mockResolvedValue(undefined);
  render(
    <ClassificationPanel
      classifications={[c]}
      seek={seek}
      override={override}
    />,
  );
  expect(screen.getByText(/分類閾值尚未校準/)).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "01:50:00.000 → 01:50:10.000" }),
  );
  expect(seek).toHaveBeenCalledWith(6600000000);
  fireEvent.change(screen.getByLabelText("音訊區段 1 分類"), {
    target: { value: "speech" },
  });
  await waitFor(() =>
    expect(override).toHaveBeenCalledWith(c, c.spans[0], "speech"),
  );
  expect(c.spans[0].label).toBe("mixed");
});
