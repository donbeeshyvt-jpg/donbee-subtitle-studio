export interface AnalysisPolicies {
  language_policy: "auto_ja_zh_en" | "zh" | "ja" | "en";
  music_policy: "conservative" | "off";
}
export const defaultAnalysisPolicies: AnalysisPolicies = {
  language_policy: "auto_ja_zh_en",
  music_policy: "conservative",
};
export default function AnalysisOptions({
  value,
  change,
  prefix,
}: {
  value: AnalysisPolicies;
  change: (value: AnalysisPolicies) => void;
  prefix: string;
}) {
  return (
    <>
      <label>
        辨識語言
        <select
          aria-label={`${prefix}辨識語言`}
          value={value.language_policy}
          onChange={(e) =>
            change({
              ...value,
              language_policy: e.target
                .value as AnalysisPolicies["language_policy"],
            })
          }
        >
          <option value="auto_ja_zh_en">自動辨識中／日／英</option>
          <option value="zh">中文</option>
          <option value="ja">日文</option>
          <option value="en">英文</option>
        </select>
      </label>
      <label>
        音樂區段
        <select
          aria-label={`${prefix}音樂策略`}
          value={value.music_policy}
          onChange={(e) =>
            change({
              ...value,
              music_policy: e.target.value as AnalysisPolicies["music_policy"],
            })
          }
        >
          <option value="conservative">保守分類，保留語音候選</option>
          <option value="off">不執行音訊分類</option>
        </select>
      </label>
    </>
  );
}
