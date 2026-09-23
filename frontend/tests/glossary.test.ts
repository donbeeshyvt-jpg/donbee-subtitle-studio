// 校字詞彙提示：文字框內容 → 送給後端的詞彙陣列（與後端契約：最多 100 個、每個最多 80 字）。
import { expect, it } from "vitest";
import { glossaryTerms } from "../src/App";

it("splits by newline, comma and ideographic separators, trims, dedupes and drops blanks", () => {
  expect(glossaryTerms("彭彭\n 斯斯 ，海海、PICO PARK,彭彭\n\n")).toEqual(["彭彭", "斯斯", "海海", "PICO PARK"]);
  expect(glossaryTerms("")).toEqual([]);
});

it("caps the list at 100 terms and each term at 80 characters", () => {
  const many = Array.from({ length: 150 }, (_, i) => `詞${i}`).join("\n");
  expect(glossaryTerms(many)).toHaveLength(100);
  expect(glossaryTerms("字".repeat(200))[0]).toHaveLength(80);
});

// 2026-09-20 使用者：用空格隔開也可以；有逗號時保留「PICO PARK」這種含空格的名稱（與後端 terms.split_terms 相同）
it("splits on spaces only when there are no commas, newlines or ideographic separators", () => {
  expect(glossaryTerms("彭彭 斯斯  海海")).toEqual(["彭彭", "斯斯", "海海"]);
  expect(glossaryTerms("PICO PARK, 天照堂；彭彭")).toEqual(["PICO PARK", "天照堂", "彭彭"]);
});
