import { describe, expect, it } from "vitest";

import { decodeSseStream } from "./sse";

function chunked(parts: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(part);
      controller.close();
    },
  });
}

describe("decodeSseStream", () => {
  it("handles split UTF-8, CRLF, comments, and multiline data", async () => {
    const bytes = new TextEncoder().encode(
      ": heartbeat\r\nevent: answer.delta\r\ndata: {\"text\":\"Δ\",\r\ndata: \"index\":0}\r\n\r\n",
    );
    const frames = [];
    for await (const frame of decodeSseStream(
      chunked([bytes.slice(0, 3), bytes.slice(3, bytes.length - 7), bytes.slice(bytes.length - 7)]),
    )) {
      frames.push(frame);
    }
    expect(frames).toEqual([
      { event: "answer.delta", data: "{\"text\":\"Δ\",\n\"index\":0}" },
    ]);
  });

  it("supports LF and CR line endings and ignores fields it does not consume", async () => {
    const stream = chunked([
      new TextEncoder().encode("id: 4\nevent: one\ndata: a\n\nevent: two\rdata: b\r\r"),
    ]);
    const frames = [];
    for await (const frame of decodeSseStream(stream)) frames.push(frame);
    expect(frames).toEqual([
      { event: "one", data: "a" },
      { event: "two", data: "b" },
    ]);
  });

  it("rejects an event without its blank-line terminator", async () => {
    const stream = chunked([new TextEncoder().encode("event: one\ndata: a")]);
    await expect(async () => {
      for await (const frame of decodeSseStream(stream)) void frame;
    }).rejects.toThrow(/middle of an event/);
  });
});
