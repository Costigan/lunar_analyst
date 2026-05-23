import React, { useMemo } from "react";
import { MenuItem } from "@blueprintjs/core";
import { allTokensMatch, highlightMaskForTokens, tokenizeFilter } from "../../utils/filterMatch";

export type FilteredListItem = {
  value: string;
  label: string;
  searchText: string;
};

type Props = {
  items: FilteredListItem[];
  filterText: string;
  value: string;
  emptyMessage?: string;
  onValueChange: (value: string) => void;
};

function Highlight({ text, tokens }: { text: string; tokens: string[] }): JSX.Element {
  const raw = String(text || "");
  const mask = highlightMaskForTokens(raw, tokens);
  const spans: Array<{ hit: boolean; text: string }> = [];
  let i = 0;
  while (i < raw.length) {
    const hit = Boolean(mask[i]);
    let j = i + 1;
    while (j < raw.length && Boolean(mask[j]) === hit) j += 1;
    spans.push({ hit, text: raw.slice(i, j) });
    i = j;
  }
  return (
    <>
      {spans.map((span, idx) =>
        span.hit ? (
          <mark key={idx} className="filter-hit">
            {span.text}
          </mark>
        ) : (
          <React.Fragment key={idx}>{span.text}</React.Fragment>
        ),
      )}
    </>
  );
}

export default function FilteredList(props: Props): JSX.Element {
  const { items, filterText, value, emptyMessage = "No matching items.", onValueChange } = props;
  const tokens = useMemo(() => tokenizeFilter(filterText), [filterText]);
  const filtered = useMemo(
    () => items.filter((item) => !tokens.length || allTokensMatch(tokens, item.searchText)),
    [items, tokens],
  );

  return (
    <div className="filtered-list bp6-filtered-list" style={{ padding: "4px" }}>
      {filtered.length ? (
        filtered.map((item) => (
          <MenuItem
            key={item.value}
            active={value === item.value}
            onClick={() => onValueChange(item.value)}
            text={<Highlight text={item.label} tokens={tokens} />}
          />
        ))
      ) : (
        <div className="filtered-list-empty">{emptyMessage}</div>
      )}
    </div>
  );
}
