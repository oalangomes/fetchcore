# FetchCore visual identity (issue #106)

**Brand line:** **Repositories into evidence.**

The mark uses two explicit **bounds** around three compact source strokes and a single contrasting **typed evidence cell**. The brackets imply evidence is scoped and traceable, not that results are authoritative. The three strokes are acquisition cues, **not graph hops**, agent steps, a database logo or a vendor-specific index. The visual system is static; it does not assert ranking performance or backend availability.

## Positioning and voice

- **Product:** small, backend-neutral infrastructure for turning software repositories into searchable, typed, traceable technical evidence
- **Audience:** software engineers, coding-agent builders, MCP/tool authors, platform architects
- **Tone:** factual, precise, quiet confidence; document limits and provenance
- **Tagline:** Repositories into evidence.
- **Guardrail:** retrieval ≠ satisfaction ≠ authority; do not imply planning, execution, approvals or a magic "truth" ranker

## Palette

| Token | Hex | Role |
| --- | --- | --- |
| `ink` | `#12213A` | Primary type and mark on bright surfaces |
| `navy` | `#0C172A` | Dark brand surface / avatar |
| `paper` | `#F8FCFD` | Light surface |
| `mist` | `#E7F2F3` | Subtle light background |
| `secondary` | `#485C71` | Body labels on paper |
| `light-ink` | `#EAF7F8` | Type and mark on navy |
| `accent-light` | `#00A58E` | Large title highlight / graphic on light |
| `accent-dark` | `#32E2C1` | Type and graphic on navy |
| `navy-border` | `#27384F` | Avatar border on navy |

### WCAG 2 relative contrast ratios (computed)

| Foreground / background | Contrast | Recommendation |
| --- | ---: | --- |
| `ink` / `paper` | 15.59:1 | Normal and large text |
| `light-ink` / `navy` | 16.35:1 | Normal and large text |
| `secondary` / `paper` | 6.68:1 | Supporting small text |
| `accent-dark` / `navy` | 10.91:1 | Small text and graphic |
| `accent-light` / `paper` | 3.00:1 | **Large text or icons only; not 14px body copy** |

These values cover solid token pairs, not visual verification of every possible composition.

## Files (editable source first)

- [`mark.svg`](mark.svg): transparent primary mark for light surfaces
- [`mark-inverse.svg`](mark-inverse.svg): transparent inverse for dark surfaces
- [`mark-mono.svg`](mark-mono.svg) and [`mark-mono-inverse.svg`](mark-mono-inverse.svg): one-color fallbacks
- [`lockup-horizontal.svg`](lockup-horizontal.svg) and [`lockup-horizontal-inverse.svg`](lockup-horizontal-inverse.svg): mark + editable wordmark + tagline
- [`avatar.svg`](avatar.svg): 512×512 artboard with navy background, rounded corners and the inverse symbol
- Checked-in PNGs: `avatar-256.png`, `mark-128.png`, `mark-inverse-128.png`. Extra high-resolution outputs such as `avatar-512.png` are produced from the same SVG with `python scripts/brand/export.py` (optional `cairosvg` dependency)

**Visual preview:** [brand board](preview.svg), showing the icon and lockup on light/dark surfaces.

**Typography:** Inter if present, then IBM Plex Sans, then system sans. SVG wordmark text remains editable and uses font fallbacks; rendering may vary by environment. Do not embed or redistribute font files. Before use in a fixed print/media master, convert type to outlines in the chosen design application.

## Usage

- **GitHub organization/repository avatar:** `avatar-512.png` (upload manually when public launch configuration is approved)
- **README/docs light backgrounds:** `lockup-horizontal.svg` or `mark.svg`
- **Dark slide/social backgrounds:** `lockup-horizontal-inverse.svg` or `mark-inverse.svg`
- **One-color engraving/restricted palette:** appropriate monochrome mark
- **Minimum recommended size:** 32px for icon alone (24px only after checking render); prefer 200px+ wide horizontal wordmark
- Preserve clear space of at least one evidence-cell width around the mark; do not rotate, stretch, add glow or gradients, or change the central cell to an arbitrary backend color
- Provide meaningful alt text such as “FetchCore mark: bracketed source paths and typed evidence cell”; avoid redundant alt text when adjacent brand name already labels the image

## Design trade-offs and provenance

The mark uses **small, deterministic geometric SVG primitives** rather than gradients, stock graphics, photos, circuit-board or AI-brain artwork. This scales well and keeps the original editable. The compact source strokes can lose fine detail at very small sizes; prefer the avatar tile and do not claim the mark is legible at favicon sizes without testing. The wordmark is deliberately editable text with font fallbacks, rather than fixed outlined glyphs.

All artwork here was created expressly for the FetchCore identity, using authored vector geometry; no third-party graphics, logos, generated stock images or embedded font files were imported. On 2026-10-22, the maintainer explicitly confirmed the rights to redistribute the SVG logomark and seven launch renders under the repository's [MIT license](../../LICENSE), without unauthorized third-party elements or fonts. The authored brand artwork and its PNG exports are distributed under that license. Referencing a fallback font does not distribute or license that font. The exposure review is recorded in [#110](https://github.com/oalangomes/fetchcore/issues/110).

Follow-up [#107](https://github.com/oalangomes/fetchcore/issues/107) owns the GitHub social preview and public metadata. This issue does **not** change repo visibility or release state.
