// 即時字幕（M7）：瀏覽器的音訊（通常 48 kHz 浮點）→ 16 kHz 16 位元 PCM（服務端 faster-whisper 的輸入格式）。
export const TARGET_RATE = 16000;

export function downsampleToPcm16(input: Float32Array, inputRate: number): Int16Array {
  const ratio = inputRate / TARGET_RATE;
  const length = Math.floor(input.length / ratio);
  const output = new Int16Array(length);
  for (let i = 0; i < length; i += 1) {
    // 取這段區間的平均（簡單低通），再轉成 16 位元整數
    const from = Math.floor(i * ratio);
    const to = Math.max(from + 1, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = from; j < to && j < input.length; j += 1) sum += input[j];
    const value = Math.max(-1, Math.min(1, sum / (to - from)));
    output[i] = Math.max(-32768, Math.min(32767, Math.round(value * 32768)));
  }
  return output;
}

// 累積 PCM，湊滿 chunkSamples 才交出去（每段 0.5 秒）
export class PcmChunker {
  private parts: Int16Array[] = [];
  private size = 0;
  constructor(private chunkSamples = TARGET_RATE / 2) {}
  push(samples: Int16Array): Int16Array[] {
    this.parts.push(samples);
    this.size += samples.length;
    const ready: Int16Array[] = [];
    while (this.size >= this.chunkSamples) ready.push(this.take(this.chunkSamples));
    return ready;
  }
  flush(): Int16Array | null {
    return this.size ? this.take(this.size) : null;
  }
  private take(count: number): Int16Array {
    const out = new Int16Array(count);
    let filled = 0;
    while (filled < count) {
      const head = this.parts[0];
      const use = Math.min(head.length, count - filled);
      out.set(head.subarray(0, use), filled);
      filled += use;
      if (use === head.length) this.parts.shift();
      else this.parts[0] = head.subarray(use);
    }
    this.size -= count;
    return out;
  }
}
