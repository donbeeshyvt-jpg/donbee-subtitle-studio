import { afterEach, expect, it, vi } from "vitest";
import { installAppMock } from "./helpers/appMock";

afterEach(() => vi.resetModules());

it("returns a valid empty subtitle preview by default", async () => {
  installAppMock({});
  const { api } = await import("../src/api/client");
  const preview = await api.subtitlePreview("p1", {
    source_id: "s1", transcript_revision: "tr1", ranges: [],
    sentences_per_cue: 1, keep_punctuation: false, subtitle_timebase: "sequence", grouping: "merge",
  });
  expect(preview.entries).toEqual([]);
  expect(preview.srt).toBe("");
});
