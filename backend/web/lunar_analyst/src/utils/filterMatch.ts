/**
 * Shared filter matching utilities for token-based substring matching.
 */

export interface MatchResult {
  matched: boolean;
  indices: number[][]; // Array of [start, end) pairs for highlighting
}

type TokenSpan = {
  tokenLower: string;
  start: number;
  end: number;
};

/**
 * Splits a filter string into lower-cased tokens.
 */
export function tokenizeFilter(filterText: string): string[] {
  return (filterText || "").toLowerCase().split(/\s+/).filter((token) => token.length > 0);
}

function tokenizeTextWithSpans(text: string): TokenSpan[] {
  const raw = String(text || "");
  const spans: TokenSpan[] = [];
  const regex = /\S+/g;
  let match: RegExpExecArray | null = regex.exec(raw);
  while (match) {
    const token = String(match[0] || "");
    const start = Number(match.index || 0);
    spans.push({
      tokenLower: token.toLowerCase(),
      start,
      end: start + token.length,
    });
    match = regex.exec(raw);
  }
  return spans;
}

/**
 * Returns true if every query token appears as a substring of at least one text token.
 */
export function allTokensMatch(tokens: string[], text: string): boolean {
  if (tokens.length === 0) return true;
  const spans = tokenizeTextWithSpans(text);
  if (spans.length === 0) return false;
  for (const token of tokens) {
    const query = String(token || "").toLowerCase();
    if (!query) continue;
    const found = spans.some((span) => span.tokenLower.includes(query));
    if (!found) return false;
  }
  return true;
}

/**
 * Returns a boolean mask for each character in text indicating if it's part of a token match.
 */
export function highlightMaskForTokens(text: string, tokens: string[]): boolean[] {
  const raw = String(text || "");
  const mask = new Array(raw.length).fill(false);
  if (tokens.length === 0 || raw.length === 0) return mask;

  const spans = tokenizeTextWithSpans(raw);
  for (const token of tokens) {
    const query = String(token || "").toLowerCase();
    if (!query) continue;

    for (const span of spans) {
      let fromIndex = 0;
      while (fromIndex <= span.tokenLower.length - query.length) {
        const rel = span.tokenLower.indexOf(query, fromIndex);
        if (rel < 0) break;
        const start = span.start + rel;
        const end = start + query.length;
        for (let idx = start; idx < end && idx < mask.length; idx += 1) {
          mask[idx] = true;
        }
        fromIndex = rel + Math.max(1, query.length);
      }
    }
  }

  return mask;
}

/**
 * Checks if a string matches all provided tokens with token-substring semantics.
 * Returns the match status and highlight ranges for matching substrings.
 */
export function filterMatch(text: string, filterText: string): MatchResult {
  const tokens = tokenizeFilter(filterText);
  if (tokens.length === 0) {
    return { matched: true, indices: [] };
  }

  const raw = String(text || "");
  const spans = tokenizeTextWithSpans(raw);
  if (spans.length === 0) {
    return { matched: false, indices: [] };
  }

  const ranges: number[][] = [];
  for (const token of tokens) {
    const query = String(token || "").toLowerCase();
    if (!query) continue;

    let matchedRange: number[] | null = null;
    for (const span of spans) {
      const rel = span.tokenLower.indexOf(query);
      if (rel >= 0) {
        const start = span.start + rel;
        matchedRange = [start, start + query.length];
        break;
      }
    }

    if (!matchedRange) {
      return { matched: false, indices: [] };
    }
    ranges.push(matchedRange);
  }

  ranges.sort((a, b) => a[0] - b[0]);
  const merged: number[][] = [];
  for (const range of ranges) {
    if (merged.length === 0) {
      merged.push([range[0], range[1]]);
      continue;
    }
    const last = merged[merged.length - 1];
    if (range[0] <= last[1]) {
      last[1] = Math.max(last[1], range[1]);
    } else {
      merged.push([range[0], range[1]]);
    }
  }

  return { matched: true, indices: merged };
}
