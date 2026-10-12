// Reading a tool's text reply.
//
// The server leads a reply with `_note: ..._` paragraphs when it has something
// to say about the answer -- most importantly that the index may be stale
// relative to the working tree. The JSON body follows them. This splits the
// two so the notes can be shown and the body parsed.

export interface ToolText {
  notes: string[];
  body: string;
}

/** A tool reply that is not the JSON it was asked for. */
export class ToolOutputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolOutputError";
  }
}

/** Split leading `_note: ..._` paragraphs from the body. */
export function splitNotes(text: string): ToolText {
  const notes: string[] = [];
  let body = text;
  while (body.startsWith("_note:")) {
    const end = body.indexOf("\n\n");
    const paragraph = end < 0 ? body : body.slice(0, end);
    notes.push(cleanNote(paragraph));
    body = end < 0 ? "" : body.slice(end + 2);
  }
  return { notes, body };
}

function cleanNote(paragraph: string): string {
  let note = paragraph.trim().replace(/^_note:\s*/, "");
  if (note.endsWith("_")) {
    note = note.slice(0, -1);
  }
  return note.trim();
}

/** The notes and the parsed JSON body of a reply. */
export function parseJsonReply<T>(text: string): { notes: string[]; value: T } {
  const { notes, body } = splitNotes(text);
  try {
    return { notes, value: JSON.parse(body) as T };
  } catch {
    throw new ToolOutputError(`expected JSON from repo2graph-mcp, got: ${body.slice(0, 200)}`);
  }
}

/** Whether a note is the server's stale-index warning. */
export function isStaleNote(note: string): boolean {
  return /index may be stale/i.test(note);
}
