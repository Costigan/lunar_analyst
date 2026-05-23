# ADR 0010: Adoption of Blueprint JS 6 for Application Shell and Desktop Controls

- Status: Accepted
- Date: 2026-02-19
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/ADR.0005.web_ui_component_toolkit.md`, `docs/BLUEPRINT_MIGRATION.md`, `docs/NEW_DESIGN.md`, `docs/PLAN.md`
- Supersedes: `docs/ADR.0005.web_ui_component_toolkit.md` (regarding primary UI toolkit direction)

## Context

As the Lunar Analyst application evolves from a simple map milestone to a comprehensive mission analysis suite, the requirement for a robust, desktop-oriented UI toolkit has become critical. The application now includes a complex Scenario Explorer, a Jobs Manager, and advanced layer management features that require sophisticated layout and data-grid capabilities.

While `ADR 0005` selected Shoelace for immediate map controls, its primary focus was on framework-agnostic web components for a lightweight milestone. The broader application shell and data-heavy panes now require a more integrated React-first component library.

## Decision

Adopt **Blueprint JS 6** (`@blueprintjs/core`, `@blueprintjs/icons`, `@blueprintjs/select`) as the primary UI toolkit for the Lunar Analyst React application.

**Scope of implementation:**
- **Application Shell:** Main layout, navigation, and top-level toolbar.
- **Scenario Explorer:** Hierarchical tree-grid with multi-column metadata.
- **Jobs Manager:** Job monitoring, progress, and control panels.
- **Layer Management:** Primary layer list and ordering controls (incremental migration).

**Coexistence Policy:**
- **Shoelace:** Remains in use only where already embedded and stable within map-specific control overlays, or where it provides specific functionality not yet migrated to Blueprint. No new Shoelace dependencies should be added for shell or management components.
- **Web Awesome:** The previous planned migration path to Web Awesome (from `ADR 0005`) is deprecated in favor of Blueprint JS 6 for the application shell.

## Rationale

- **Desktop-First Design:** Blueprint is specifically optimized for complex, data-dense desktop applications, which matches the "Lunar Analyst" mission profile.
- **Tree-Grid Capabilities:** Blueprint's `Tree` and layout primitives are better suited for the high-risk Scenario Explorer migration described in `docs/BLUEPRINT_MIGRATION.md`.
- **React Integration:** Being a native React library, Blueprint provides better state management integration and performance for the complex UI interactions required by the Jobs and Layer managers.
- **Visual Maturity:** Blueprint 6 (`bp6-dark`) provides a professional, cohesive dark theme that aligns with GIS and scientific visualization norms.

## Alternatives Considered

### Continue with Shoelace/Web Awesome
- **Pros:** Already partially implemented in map controls.
- **Cons:** Less mature for complex desktop layouts like tree-grids; requires more custom CSS to achieve the same "professional tool" density as Blueprint.

### MUI (Material UI)
- **Pros:** Massive ecosystem and stability.
- **Cons:** Design language is highly mobile/consumer-web oriented; achieving a data-dense "expert tool" aesthetic requires significant customization.

### Mantine
- **Pros:** Excellent React integration and modern feature set.
- **Cons:** Blueprint's specific focus on "building data-dense interfaces for the web" (per their own mission) remains a better semantic match for this project.

## Consequences

**Positive:**
- More consistent and professional appearance for the application shell and data panes.
- Reduced custom CSS boilerplate for complex layouts.
- Better keyboard navigation and accessibility for expert users.

**Negative/Risks:**
- Potential for "UI fragmentation" during the migration phase where both Shoelace and Blueprint components are visible.
- Risk of regressions in the Explorer tree-grid (mitigated by the "Explorer Spike" in the migration plan).

## Out of Scope

- Immediate replacement of specialized OpenLayers map interaction code.
- Backend API changes.
- Porting the legacy .NET WinForms UI to web (this ADR only concerns the React application).

## Follow-on Tasks

- Implement the `VITE_USE_BLUEPRINT_UI` feature flag (`docs/BLUEPRINT_MIGRATION.md` Phase 0).
- Establish the `bp6-dark` root container.
- Execute the Explorer Spike (`docs/BLUEPRINT_MIGRATION.md` Phase 2).
