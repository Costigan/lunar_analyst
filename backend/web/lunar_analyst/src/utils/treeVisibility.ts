import { allTokensMatch, tokenizeFilter } from "./filterMatch";

export type TreeRow = {
  id: string;
  parentId?: string;
  name?: string;
  searchText?: string;
  sortKey?: string;
};

export function buildVisibleTreeRowIds(rows: TreeRow[], filterText: string, expandedIds: Set<string>): string[] {
  const tokens = tokenizeFilter(filterText);
  const filtering = tokens.length > 0;

  const childrenByParent = new Map<string, TreeRow[]>();
  const rowById = new Map<string, TreeRow>();
  for (const row of rows) {
    const parentId = String(row.parentId || "");
    if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
    childrenByParent.get(parentId)?.push(row);
    rowById.set(row.id, row);
  }

  for (const children of childrenByParent.values()) {
    children.sort((a, b) => String(a.sortKey || a.name || "").localeCompare(String(b.sortKey || b.name || "")));
  }

  const visibleMemo = new Map<string, boolean>();
  const hasVisibleDescendant = (rowId: string): boolean => {
    if (visibleMemo.has(rowId)) return Boolean(visibleMemo.get(rowId));
    const row = rowById.get(rowId);
    if (!row) return false;
    const ownMatch = !filtering || allTokensMatch(tokens, String(row.searchText || row.name || "").toLowerCase());
    const children = childrenByParent.get(rowId) || [];
    const childMatch = children.some((child) => hasVisibleDescendant(child.id));
    const visible = ownMatch || childMatch;
    visibleMemo.set(rowId, visible);
    return visible;
  };

  const out: string[] = [];
  const visit = (row: TreeRow): void => {
    if (filtering && !hasVisibleDescendant(row.id)) return;
    out.push(row.id);
    const children = childrenByParent.get(row.id) || [];
    const isExpanded = filtering ? true : expandedIds.has(row.id);
    if (!isExpanded) return;
    for (const child of children) visit(child);
  };

  for (const root of childrenByParent.get("") || []) visit(root);
  return out;
}
