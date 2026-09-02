import type { ReactNode } from "react";

interface AnswerContentProps {
  content: string;
}

const INLINE_MARKUP = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;

function renderInline(text: string): ReactNode[] {
  return text.split(INLINE_MARKUP).filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${index}:${part}`}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={`${index}:${part}`}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

function isBlockStart(line: string): boolean {
  return /^(#{1,3})\s+/.test(line)
    || /^\s*[-*]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^>\s?/.test(line)
    || /^```/.test(line);
}

export function AnswerContent({ content }: AnswerContentProps) {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.startsWith("```")) {
      const language = line.slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(<pre key={`code:${index}`} data-language={language || undefined}><code>{code.join("\n")}</code></pre>);
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      blocks.push(<h3 className={`answer-heading level-${heading[1].length}`} key={`heading:${index}`}>{renderInline(heading[2])}</h3>);
      index += 1;
      continue;
    }

    const unordered = /^\s*[-*]\s+(.+)$/.exec(line);
    const ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
    if (unordered || ordered) {
      const orderedList = Boolean(ordered);
      const items: ReactNode[] = [];
      const pattern = orderedList ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*]\s+(.+)$/;
      while (index < lines.length) {
        const item = pattern.exec(lines[index]);
        if (!item) break;
        items.push(<li key={`item:${index}`}>{renderInline(item[1])}</li>);
        index += 1;
      }
      blocks.push(orderedList ? <ol key={`list:${index}`}>{items}</ol> : <ul key={`list:${index}`}>{items}</ul>);
      continue;
    }

    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].startsWith(">")) quote.push(lines[index++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={`quote:${index}`}>{renderInline(quote.join(" "))}</blockquote>);
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`paragraph:${index}`}>{renderInline(paragraph.join(" "))}</p>);
  }

  return <div className="answer-content">{blocks}</div>;
}
