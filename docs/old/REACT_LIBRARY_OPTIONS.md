# React Component Library Migration Options

This document analyzes several widely-used, open-source, and free React component libraries for the migration of the `lunar_analyst` web application.

## 1. Project Characteristics & Requirements

The decision for a component library should be influenced by the following characteristics of the `lunar_analyst` application:

- **GIS-Centric:** The UI must complement an OpenLayers map, requiring lightweight overlays, high-performance rendering, and a layout that prioritizes the map viewport.
- **Desktop-Style Interface:** Needs robust sidebars, resizable panes, toolbars, and collapsible panels.
- **Data-Dense:** The Scenario Explorer requires a tree-grid (FilteredTreeTable) that supports filtering, expansion, and drag-and-drop.
- **Custom Search Logic:** The application uses specialized "gap-aware subsequence token matching" for filtering scenarios and layers, which should be preserved.
- **Technical Aesthetic:** As a mission analysis tool, the aesthetic should be clean, high-tech, and professional.

## 2. Library Options Analysis

| Library | Popularity | License | Strengths | Weaknesses |
| :--- | :--- | :--- | :--- | :--- |
| **MUI (Material UI)** | Very High | MIT | Huge ecosystem, mature components, excellent documentation. | Opinionated "Material" aesthetic; "Tree Data" in DataGrid is a paid feature (requires Pro version). |
| **Mantine** | High | MIT | Modern, highly customizable, great hooks, built-in Tree and Table components, excellent developer experience. | Newer ecosystem than MUI; TreeTable requires custom implementation (combining Tree and Table). |
| **Blueprint JS** | Medium | BSD-3 | Specifically designed for data-dense desktop-style apps (Palantir); powerful Table and Omnibar components. | Aesthetic is somewhat fixed/industrial; React-only (not a problem here but limited for other platforms). |
| **Ant Design** | Very High | MIT | Extremely comprehensive set of components; robust Tree-Table support out of the box. | Very heavy; opinionated enterprise aesthetic; can be complex to customize deeply. |
| **Shadcn UI** | High | MIT | Modern "copy-paste" model using Radix UI primitives; full control over code; extremely high-quality aesthetics. | Requires Tailwind CSS; not a "library" in the traditional sense, which may increase initial setup work. |

### 2.1 Implementation of Custom Components

#### PatternCombobox (Pulldown)
- **MUI:** Easily replaced by `Autocomplete` with a custom `filterOptions` function to preserve the gap-aware matching.
- **Mantine:** Can use `Select` or `Autocomplete` with the `filter` property.
- **Blueprint:** The `Select` or `Suggest` components are ideal for this.
- **Shadcn UI:** Uses Radix `Combobox`, allowing full control over the filtering logic.

#### FilteredTreeTable (Filtered List)
- **MUI:** The free `TreeView` lacks multi-column support. `DataGrid` (free) does not support tree structures. Migration would likely require a custom implementation using the MUI `Table` and `Collapse` components.
- **Mantine:** Provides a `Tree` component and a `Table` component. Combining them into a tree-grid is straightforward but requires some custom layout logic.
- **Blueprint:** Has a very powerful `Table` component, but hierarchical tree structures within the table are not a primary feature and require custom handling.
- **Ant Design:** One of the few libraries that supports "Tree Data" in its standard `Table` component for free, making it the easiest for this specific component.

## 3. Difficulty of Migration

- **Low (Mantine/MUI):** Both have very intuitive APIs and standard "Prop-driven" models that align well with the current React state-based code.
- **Medium (Blueprint/Ant):** These libraries have more complex APIs for their advanced components (especially Tables), which might require more refactoring of the current data transformation logic.
- **Medium (Shadcn UI):** Requires setting up Tailwind CSS and manually adding components to the project, but offers the most flexibility for preserving the exact current behavior.

## 4. Recommendation

### Path Forward: **Mantine**

**Rationale:**
1. **Modern Aesthetic:** Mantine's clean and professional look fits the "Lunar Mission" vibe better than the enterprise-heavy Ant Design or the mobile-focused Material Design.
2. **Developer Experience:** It provides a rich set of hooks (e.g., `use-disclosure` for panels, `use-move` for resizers) that will simplify the current manual state management in `App.tsx` and `LayerManagerPane.tsx`.
3. **Flexibility:** It is unopinionated about how you structure your data, making it easier to port the "gap-aware" filtering and the custom drag-and-drop logic in the `FilteredTreeTable`.
4. **Performance:** It is lightweight and built for performance, which is critical when running alongside a GPU-intensive OpenLayers map.

### Migration Strategy:
1. **Setup:** Install Mantine and its dependencies. Define a custom theme that matches the existing dark, high-tech color palette.
2. **Phase 1: Layout & Primitives:** Replace the manual grid layout in `App.tsx` with Mantine's `AppShell`, `Navbar`, and `Aside`. Replace standard buttons and inputs (switches, sliders) with Mantine equivalents.
3. **Phase 2: Pulldown:** Refactor `PatternCombobox` to use Mantine's `Select` or `Autocomplete`, passing the existing `tokenizeFilter` logic to the filtering prop.
4. **Phase 3: Filtered List:** Refactor `FilteredTreeTable` using Mantine's `Table` component. Use Mantine's `Collapse` or the `Tree` component logic to handle the hierarchy.
5. **Phase 4: Refinement:** Use Mantine's `ScrollArea` for sidebars and `Tooltip` for better user guidance on controls.

**Alternative Recommendation:** If a "desktop-first, data-first" approach is the absolute priority over modern aesthetics, **Blueprint JS** is the secondary recommendation due to its proven track record in similar GIS/Data applications.
