import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import AnalysisOptions, {
  defaultAnalysisPolicies,
} from "../src/components/AnalysisOptions";
afterEach(cleanup);
it("maps visible Japanese and disabled classification to API policy values without resetting the other choice", () => {
  const change = vi.fn();
  render(
    <AnalysisOptions
      prefix="下載"
      value={defaultAnalysisPolicies}
      change={change}
    />,
  );
  fireEvent.change(screen.getByLabelText("下載辨識語言"), {
    target: { value: "ja" },
  });
  expect(change).toHaveBeenLastCalledWith({
    language_policy: "ja",
    music_policy: "conservative",
  });
  fireEvent.change(screen.getByLabelText("下載音樂策略"), {
    target: { value: "off" },
  });
  expect(change).toHaveBeenLastCalledWith({
    language_policy: "auto_ja_zh_en",
    music_policy: "off",
  });
});
