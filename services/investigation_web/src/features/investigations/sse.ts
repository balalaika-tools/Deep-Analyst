export interface SseFrame {
  event: string;
  data: string;
}

interface PendingEvent {
  event: string;
  data: string[];
}

function nextLine(buffer: string, final: boolean): [string, string] | null {
  for (let index = 0; index < buffer.length; index += 1) {
    const character = buffer[index];
    if (character !== "\r" && character !== "\n") continue;
    if (character === "\r" && index === buffer.length - 1 && !final) return null;
    const width = character === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
    return [buffer.slice(0, index), buffer.slice(index + width)];
  }
  if (final && buffer.length > 0) return [buffer, ""];
  return null;
}

function addLine(pending: PendingEvent, line: string): SseFrame | null {
  if (line === "") {
    if (pending.data.length === 0) {
      pending.event = "";
      return null;
    }
    const frame = { event: pending.event || "message", data: pending.data.join("\n") };
    pending.event = "";
    pending.data = [];
    return frame;
  }
  if (line.startsWith(":")) return null;
  const separator = line.indexOf(":");
  const field = separator === -1 ? line : line.slice(0, separator);
  let value = separator === -1 ? "" : line.slice(separator + 1);
  if (value.startsWith(" ")) value = value.slice(1);
  if (field === "event") pending.event = value;
  if (field === "data") pending.data.push(value);
  return null;
}

export async function* decodeSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame, void, void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const pending: PendingEvent = { event: "", data: [] };
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let line = nextLine(buffer, done);
      while (line) {
        buffer = line[1];
        const frame = addLine(pending, line[0]);
        if (frame) yield frame;
        line = nextLine(buffer, done);
      }
      if (done) break;
    }
    if (pending.data.length > 0) {
      throw new Error("SSE stream ended in the middle of an event");
    }
  } finally {
    reader.releaseLock();
  }
}
