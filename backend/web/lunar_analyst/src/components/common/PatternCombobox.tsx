import React, { useEffect, useMemo, useState } from "react";
import { allTokensMatch, tokenizeFilter } from "../../utils/filterMatch";

type Item = {
  value: string;
  label: string;
  searchText: string;
};

type PatternComboboxProps = {
  id?: string;
  label: string;
  placeholder?: string;
  items: Item[];
  value: string;
  onValueChange: (value: string) => void;
};

export default function PatternCombobox(props: PatternComboboxProps): JSX.Element {
  const { id, label, placeholder, items, value, onValueChange } = props;
  const [inputValue, setInputValue] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const selected = items.find((item) => item.value === value);
    setInputValue(selected ? selected.label : "");
  }, [items, value]);

  const filtered = useMemo(() => {
    const tokens = tokenizeFilter(inputValue);
    if (!tokens.length) return items;
    return items.filter((item) => allTokensMatch(tokens, item.searchText));
  }, [items, inputValue]);

  return (
    <div id={id} className="pattern-combobox">
      <label className="pattern-combobox-label">{label}</label>
      <input
        className="pattern-combobox-input"
        placeholder={placeholder}
        value={inputValue}
        onChange={(event) => {
          setInputValue(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      <div className={`pattern-combobox-menu ${open ? "open" : ""}`}>
        {filtered.map((item) => (
          <button
            key={item.value}
            type="button"
            className={`pattern-combobox-option ${item.value === value ? "selected" : ""}`}
            onClick={() => {
              onValueChange(item.value);
              setInputValue(item.label);
              setOpen(false);
            }}
          >
            {item.label}
          </button>
        ))}
        {!filtered.length ? <div className="pattern-combobox-option">No matching items.</div> : null}
      </div>
    </div>
  );
}
