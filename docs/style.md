# Style Reference — "Editorial Dusk & Dawn"

A warm, editorial aesthetic available in two themes that share one design language. **Editorial Dusk** is the dark theme: serif display type over a near-black surface with ambient amber and sage glows — a late-night reading room. **Editorial Dawn** is the light theme: the same type and structure on a warm paper-cream surface — a sunlit study, not a sterile dashboard.

Both themes use generous whitespace, restrained ornamentation, monospace for anything data-flavored, and sharp `2px` corners. The two themes are not "a dark mode bolted on" — they are one system expressed through a single set of semantic tokens.

This reference is the canonical description of the system. In practice the tools that use it share the **palette, type, motifs, and component shapes** but not always the same token *architecture*. Two reference implementations bracket the range:

- **BookShelf** (a local-first catalogue) is the full expression: semantic tokens, both themes, RGB triples for composable alpha. It is the model this guide prescribes.
- **YTArchive** (a live download manager) is a leaner, **Dusk-only** sibling: it ships the same hexes and look, but uses *literal* token names (`--amber`, `--cream`, `--sage`), no Dawn theme, and no RGB triples. It does one thing the prescribed model should adopt — it tokenizes the font stacks.

Both are "Editorial Dusk & Dawn" to the eye. Where they diverge in code, this guide gives the canonical (semantic, dual-theme) form and notes the leaner profile in §1.4. Shipping a single-theme tool is allowed; shipping raw colors that bypass tokens is not.

---

## 1. Theming Architecture

Every color is a **CSS custom property (token)** defined in two places:

- **`:root`** holds the Dusk (dark) palette — this is the default.
- **`[data-theme="light"]`** overrides the same token names with the Dawn (light) palette.

Switching themes means setting or removing one attribute on `<html>`. Nothing else in the CSS changes — every rule references `var(--token)`, never a raw color.

```css
:root {
  color-scheme: dark;
  --text-heading: #f5ecd9;
  /* …all dark tokens… */
}

[data-theme="light"] {
  color-scheme: light;
  --text-heading: #211d17;
  /* …all light tokens… */
}
```

```js
// Toggle — persist the choice, then reflect it on <html>
function applyTheme(mode) {
  if (mode === 'light') document.documentElement.setAttribute('data-theme', 'light');
  else                  document.documentElement.removeAttribute('data-theme');
}

// First load: honour the stored choice, else the OS preference
const stored = localStorage.getItem('theme');           // 'light' | 'dark' | null
const prefersLight = matchMedia('(prefers-color-scheme: light)').matches;
applyTheme(stored || (prefersLight ? 'light' : 'dark'));
```

**Rule:** never write a raw hex or `rgba()` in a component rule. If you need a new color, add a token to *both* theme blocks. A literal color anywhere outside the `:root` / `[data-theme="light"]` blocks is a bug.

### Naming convention

Tokens are **semantic, not literal** — named for their role, not their hue. `--text-heading`, not `--cream`. This is what lets one rule serve both themes: "heading text" is meaningful in Dusk and Dawn; "cream" is not.

For colors that need composable opacity (glows, shadows, tinted borders), the RGB triple is stored separately as `--accent-amber-rgb: 212, 165, 116` and consumed as `rgba(var(--accent-amber-rgb), 0.25)`.

### 1.2 Font tokens

Tokenize the three font stacks too — define them once and reference `var(--serif/--sans/--mono)` everywhere. Put the web font (if any) at the **head** of each stack and the system stack behind it, so the same token serves online and offline builds with no separate "online alternative":

```css
:root {
  --serif: "Fraunces", "Iowan Old Style", "Apple Garamond", Baskerville, Georgia, "Times New Roman", serif;
  --sans:  "IBM Plex Sans", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono:  "JetBrains Mono", ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
}
```

An offline tool simply omits the web-font `@import`/`<link>`; the stack falls through to Iowan Old Style / system-ui / SF Mono with no code change. See §5 for which face is which.

### 1.3 The one rule

Never write a raw hex or `rgba()` in a component rule. If you need a new color, add a token (to *both* theme blocks if the tool is dual-theme). A literal color outside the token-definition block is a bug — this holds even for single-theme tools, so a Dawn theme can be added later without hunting hardcoded values.

### 1.4 Single-theme ("Dusk-only") profile

A tool may legitimately ship Dusk only — YTArchive does. The rules relax as follows: there's no `[data-theme="light"]` block, `color-scheme` is just `dark`, and RGB triples are optional (you can inline `rgba(212,165,116,…)` in glows since they'll never need to flip). Everything else still routes through tokens.

One freedom this buys: a single-theme tool **may use a text color as a fill** where a dual-theme tool cannot. YTArchive's active segment is a near-white `--text-high` fill with dark text — fine in Dusk, but it would collapse to dark-on-dark in Dawn. If there's any chance the tool gains a light theme, prefer the dual-theme-safe choice (a bright-accent fill) from the start. See §8.4.

**Token-name map** — the two implementations use different names for the same roles. When reading either codebase:

| Role | Canonical (BookShelf) | Dusk-only (YTArchive) |
| :---- | :---- | :---- |
| Gradient stops | `--bg-grad-top/mid/bot` | `--surface-1/2/3` |
| Brightest text | `--text-strong` | `--text-high` |
| Heading text | `--text-heading` | *(uses `--cream` directly)* |
| Body text | `--text-body` | `--text` |
| Secondary text | `--text-muted` | `--text-body` |
| Label / dim text | `--text-dim` | `--text-mute` |
| Decorative / faint | `--text-faint` | `--text-decor` |
| Primary accent | `--accent-amber` | `--amber` |
| Bright accent / CTA | `--accent-bright` | `--amber-bright` |
| Secondary accent | `--accent-sage` | `--sage` |
| Error text / fill | `--danger` / `--error-bg` | `--err-fg` / `--err-bg` |
| Notice fill | `--notice-bg` | `--notice-fg` / `--notice-bg` |
| Faint hairline | `--border-faint` | `--border-hair` |

Note the trap in the leaner scheme: YTArchive's `--text-strong` (`#e7e5e4`) is **not** the brightest text — `--text-high` is. The literal/positional names invite exactly this kind of collision, which is why the canonical scheme is role-named. Don't copy the literal names into new tools; map onto the semantic set.

---

## 2. Color Palette

### 2.1 Surface

The page background is a subtle vertical three-stop gradient, never a flat fill. `background-attachment: fixed` so it doesn't shift on scroll.

| Token | Dusk (dark) | Dawn (light) | Role |
| :---- | :---- | :---- | :---- |
| `--bg-grad-top` | `#14110e` | `#f3ece0` | Top of background gradient (warmest) |
| `--bg-grad-mid` | `#0f0d0b` | `#efe6d8` | Mid background |
| `--bg-grad-bot` | `#0c0a08` | `#e9dfce` | Bottom of background gradient (coolest) |

```css
html, body {
  background: linear-gradient(180deg,
    var(--bg-grad-top) 0%, var(--bg-grad-mid) 50%, var(--bg-grad-bot) 100%);
  background-attachment: fixed;
}
```

Dawn is a **warm paper cream**, never stark white (`#ffffff` is forbidden as a surface). The slight gradient gives the "page" a sense of light falling across it. The mid stop (`--bg-grad-mid`) is also the value used for the `theme-color` meta tag — see §17.

### 2.2 Text hierarchy

Six steps, from highest emphasis to decorative. Dawn inverts the scale into warm browns.

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--text-strong` | `#f5f5f4` | `#1c1917` | Highest emphasis — input text, active labels |
| `--text-heading` | `#f5ecd9` | `#211d17` | Headings, high-emphasis serif display |
| `--text-body` | `#e8e1d5` | `#3a342c` | Default body text |
| `--text-muted` | `#d6d3d1` | `#4a443b` | Secondary text, inline strong |
| `--text-dim` | `#a8a29e` | `#6b6358` | Labels, hints, sublabels, footer |
| `--text-faint` | `#78716c` | `#938a7c` | Decorative separators, serial numbers, disabled text |

**Rule of thumb:** `--text-dim` is the dimmest token allowed for actual readable text. `--text-faint` is reserved for non-essential decoration (dots, separators, serial numbers, disabled states, placeholder italics).

### 2.3 Accents

Accents stay quiet — small doses only: italic words in headlines, stat numbers, icon tints, the active control, a live status. They never dominate.

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--accent-amber` | `#d4a574` | `#b07d3e` | Primary accent — italic emphasis, icons, active tab |
| `--accent-bright` | `#fcd34d` | `#e0a92a` | CTAs, active states, highlights, FAB |
| `--accent-bright-hover` | `#fef3c7` | `#c89320` | Hover state of bright CTAs |
| `--accent-sage` | `#9fb3a0` | `#6f8770` | Secondary accent — success/"done" states, calm tints |
| `--on-accent` | `#1c1917` | `#1c1917` | Text placed on a bright-accent fill |
| `--danger` | `#f87171` | `#c0392b` | Destructive actions, errors, failure states |

**RGB triples** (for composable alpha):

| Token | Dusk | Dawn |
| :---- | :---- | :---- |
| `--accent-amber-rgb` | `212, 165, 116` | `176, 125, 62` |
| `--accent-sage-rgb` | `159, 179, 160` | `111, 135, 112` |
| `--cream-rgb` | `245, 236, 217` | `120, 95, 50` |

Note `--on-accent` is the *same* near-black in both themes — because the bright accent stays light-ish in both, dark text always sits on it. The Dawn accents are **deepened** (amber `#d4a574` → `#b07d3e`) so they hold contrast against cream rather than washing out.

**Why amber + sage:** they are complementary warm/cool muted tones — never saturated. The pairing reads as "considered" rather than "decorative." In practice amber/bright carry *attention and activity* (the live control, the CTA, in-progress) while sage carries *resolution* (success, "completed," "done").

### 2.4 Lines & borders

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--border` | `#44403c` | `#cdbfa6` | Default solid borders (inputs, toggles, buttons) |
| `--border-strong` | `rgba(68,64,60,0.6)` | `rgba(120,105,80,0.45)` | Emphasized hairlines |
| `--border-soft` | `rgba(68,64,60,0.5)` | `rgba(120,105,80,0.32)` | Panel borders, section dividers |
| `--border-faint` | `rgba(68,64,60,0.4)` | `rgba(120,105,80,0.22)` | Subtle internal dividers, card borders |

### 2.5 Surfaces (panels, inputs, chips)

Translucent layers with backdrop blur — "reading zones" that lift content off the ambient background.

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--panel-bg` | `rgba(20,17,14,0.6)` | `rgba(255,251,243,0.7)` | Standard panel / card |
| `--panel-bg-faint` | `rgba(20,17,14,0.4)` | `rgba(255,251,243,0.45)` | Lighter panel (segmented track, choice tiles) |
| `--input-bg` | `rgba(12,10,8,0.8)` | `rgba(255,252,245,0.9)` | Text inputs, textareas, thumb wells — darker/denser than parent |
| `--chip-bg` | `rgba(28,25,23,0.6)` | `rgba(245,238,226,0.85)` | Option chips, tags |
| `--chip-bg-hover` | `rgba(28,25,23,0.85)` | `rgba(238,229,213,0.95)` | Chip hover |
| `--hover-bg` | `rgba(41,37,36,0.4)` | `rgba(205,191,166,0.3)` | Generic hover wash |
| `--disabled-bg` | `rgba(41,37,36,0.6)` | `rgba(220,210,192,0.6)` | Disabled control fill |
| `--modal-bg` | `rgba(20,17,14,0.96)` | `rgba(252,248,240,0.98)` | Modal card surface |
| `--modal-backdrop` | `rgba(12,10,8,0.82)` | `rgba(60,52,40,0.45)` | Dimming layer behind a modal |

In Dusk, inputs are *darker* than their parent panel (a recessed feel). In Dawn, inputs are *lighter/whiter* than the cream panel — the same recession achieved by going the other direction.

### 2.6 Effects

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--focus-ring` | `rgba(168,162,158,0.5)` | `rgba(120,105,80,0.5)` | Keyboard focus outline |
| `--shadow-rgb` | `0, 0, 0` | `90, 72, 44` | Drop-shadow color, consumed as `rgba(var(--shadow-rgb), a)` |
| `--shadow-strength` | `1` | `0.4` | Shadow opacity multiplier |

Dawn shadows are a **warm brown**, not black — a black shadow on cream looks like dirt. They are also softer. Always fold the multiplier into the alpha so one shadow rule works in both themes:

```css
box-shadow: 0 8px 24px rgba(var(--shadow-rgb), calc(0.25 * var(--shadow-strength)));
```

### 2.7 Semantic-state tokens

Two background tints ship as tokens so error and notice surfaces theme correctly:

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--error-bg` | `rgba(69,10,10,0.2)` | `rgba(192,57,43,0.12)` | Fill behind error/destructive messages |
| `--notice-bg` | `rgba(252,211,77,0.08)` | `rgba(224,169,42,0.12)` | Fill behind notices/highlights |

Beyond these fills, semantic state is expressed through the **accent roles**, not new hues — see the status palette in §10.

---

## 3. JS-Drawn Surfaces (component-specific tokens)

Some surfaces are drawn in JavaScript — an SVG wheel, a `<canvas>` chart — with explicit color attributes, so CSS `var()` cannot cascade into them directly. Such a surface gets its **own token set**, and the JS reads them live via `getComputedStyle` whenever it draws or the theme changes. The decision wheel from the original toolset is the worked example:

| Token | Dusk | Dawn | Role |
| :---- | :---- | :---- | :---- |
| `--wheel-slice-1` | `rgba(212,165,116,0.22)` | `rgba(176,125,62,0.30)` | Slice tint — amber |
| `--wheel-slice-2` | `rgba(159,179,160,0.20)` | `rgba(111,135,112,0.30)` | Slice tint — sage |
| `--wheel-slice-3` | `rgba(245,236,217,0.10)` | `rgba(120,95,50,0.12)` | Slice tint — cream/neutral |
| `--wheel-slice-4` | `rgba(252,211,77,0.16)` | `rgba(224,169,42,0.26)` | Slice tint — bright amber |
| `--wheel-label` | `#f5ecd9` | `#2a2620` | Slice label text |
| `--wheel-num` | `rgba(245,236,217,0.5)` | `rgba(60,52,40,0.55)` | Slice serial numbers |
| `--wheel-slice-edge` | `rgba(12,10,8,0.8)` | `rgba(255,252,245,0.85)` | Stroke between slices |
| `--wheel-inner-edge` | `rgba(245,236,217,0.08)` | `rgba(90,72,44,0.10)` | Faint inner highlight ring |
| `--wheel-empty-fill` | `rgba(20,17,14,0.5)` | `rgba(245,238,226,0.7)` | Empty-wheel placeholder fill |
| `--hub-grad-top` | `#1c1917` | `#fffdf8` | Center hub gradient (inner) |
| `--hub-grad-bot` | `#0c0a08` | `#ece2d0` | Center hub gradient (outer) |
| `--needle-line` | `rgba(20,17,14,0.4)` | `rgba(255,252,245,0.5)` | Center line on the pointer needle |

The Dawn tints are **deeper and more opaque** (0.30 vs 0.22) because faint tints vanish against a light hub — the general rule for any JS-drawn tint in Dawn.

```js
// JS reads tokens live so the surface always matches the active theme
function themeVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// On theme change: redraw anything that isn't pure CSS
function applyTheme(mode) {
  /* set/remove data-theme … */
  renderWheel();   // re-reads themeVar(...) for every color
  renderHub();
}
```

**Rule:** any pixel drawn by script must be re-read and re-drawn on theme change. A surface that doesn't update on toggle is the most common theming bug in this system.

---

## 4. Background Atmosphere

The trademark visual element: three large radial gradients in a fixed layer behind content, heavily blurred. They create depth and warmth without texture noise that would hurt text contrast. Present in **both** themes — the glow tints come from accent RGB tokens, so they shift automatically.

```css
.glow-layer { position: fixed; inset: 0; pointer-events: none; overflow: hidden; z-index: 0; }
.glow { position: absolute; border-radius: 50%; }

.glow-1 { /* warm amber, top-right */
  top: -20%; right: -15%; width: 900px; height: 900px;
  background: radial-gradient(circle,
    rgba(var(--accent-amber-rgb), 0.10) 0%,
    rgba(var(--accent-amber-rgb), 0.03) 30%,
    transparent 60%);
  filter: blur(40px);
}
.glow-2 { /* sage, bottom-left */
  bottom: -30%; left: -20%; width: 1000px; height: 1000px;
  background: radial-gradient(circle,
    rgba(var(--accent-sage-rgb), 0.08) 0%,
    rgba(var(--accent-sage-rgb), 0.02) 35%,
    transparent 65%);
  filter: blur(50px);
}
.glow-3 { /* cream, centered */
  top: 40%; left: 30%; width: 600px; height: 600px;
  background: radial-gradient(circle,
    rgba(var(--cream-rgb), 0.03) 0%, transparent 60%);
  filter: blur(60px);
}
```

Content sits above this layer at `position: relative; z-index: 1`. **Avoid:** noise overlays, grain textures, animated backgrounds. They interfere with text contrast and break the calm.

---

## 5. Typography

Type is identical in both themes — only color changes. The system stacks are deliberate: the page renders with literary character on any OS, fully offline, with no web-font request.

### 5.1 Display — serif (`var(--serif)`)

```css
--serif: "Fraunces", "Iowan Old Style", "Apple Garamond", Baskerville,
         Georgia, "Times New Roman", serif;
font-weight: 400;
letter-spacing: -0.01em;
```

Used for: page title, section headings, stat values, the verdict, card titles, queue-item titles, the hub mark, the human status verb, "term" labels in footer/hint notes, big decorative quote glyphs. An italic variant carries emphasis — a single italic word inside a headline (e.g. "Book*Shelf*", or YTArchive's "A reading-room for *channel archival*.").

**The stack is web-font-first.** **Fraunces** (Google Fonts) is the ideal display face — distinctive, warm, variable optical-size axis — so it leads the stack. Behind it, **Iowan Old Style** is a warm book serif shipped with macOS/iOS; Baskerville and Georgia are ubiquitous fallbacks with similar proportions; Times New Roman is last-resort only. An offline build just drops the Fraunces `<link>` and falls through gracefully — no code change.

### 5.2 Body — sans (`var(--sans)`)

```css
--sans: "IBM Plex Sans", system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", Roboto, sans-serif;
```

Used for: paragraphs, buttons, form labels, checkbox labels, the all-caps eyebrow and field labels (tracked). **IBM Plex Sans** leads (it pairs cleanly with Fraunces); the system stack carries offline builds and looks native on every OS.

### 5.3 Mono — monospace (`var(--mono)`)

```css
--mono: "JetBrains Mono", ui-monospace, SFMono-Regular, "SF Mono", Menlo,
        Consolas, "Liberation Mono", monospace;
```

Used for: serial numbers, timing/size data, numeric inputs (concurrency, paths), thumbnail duration badges, metadata chips, progress percentages, inline `<code>`-ish values (`127.0.0.1`, `archive.txt`, `--cookies-from-browser`). **JetBrains Mono** leads; the system stack covers offline. Gives technical content a distinct texture against the serif/sans.

### 5.4 Type scale

| Element | Size / line-height | Notes |
| :---- | :---- | :---- |
| Page title | `3rem`–`4rem` / `0.95` (desktop), `2.25rem`–`3rem` (mobile) | Serif, `letter-spacing: -0.02em`. Exact size is tool-dependent. |
| Section heading | `1.875rem`, italic | Serif |
| Modal / verdict title | `2rem`–`3rem` (desktop), `1.625rem`–`2.25rem` (mobile) | Serif |
| Stat / hub numbers | `1.875rem` | Serif |
| Pull-quote text | `1.3125rem`, italic | Serif, in modals |
| Body paragraph | `1rem / 1.65` | Sans |
| Tagline | `1.125rem`, italic | Serif |
| Controls, buttons | `0.75rem`–`0.875rem` | Sans (buttons are uppercase/tracked) |
| Section labels (all-caps) | `0.6875rem`, `letter-spacing 0.25em` | Mono, `--text-dim` |
| Field labels (all-caps) | `0.625rem`, `letter-spacing 0.2em` | Mono, `--text-dim` |
| Micro-labels (chips, type tags) | `0.58rem`–`0.62rem`, `letter-spacing 0.14em`–`0.22em` | Mono, `--text-faint` |
| Hints, footer | `0.75rem / 1.6` | Serif italic |

**All-caps tracked labels** with wide letter-spacing (`0.2em`–`0.3em`) are a core motif: eyebrow text, form labels, status chips. Never use all-caps at body size. The label *face* differs by tool — **mono** in BookShelf, **sans** in YTArchive — but both read as the same "tracked editorial label." Pick one per tool and hold it.

### 5.5 Section markers — the italic "term." eyebrow

A signature device: a section is introduced by a serif-**italic**, period-terminated word in `--text-mute`, sitting inline *before* the section's serif title.

```css
.marker { font-family: var(--serif); font-style: italic; font-size: 1.25rem; color: var(--text-mute); margin-right: 0.5rem; }
```

```html
<div class="section-head">
  <span class="marker">Source.</span>
  <h2 class="section-title">Channel or playlist URL</h2>
</div>
```

The words are nouns of *function* — `Source.` `Configuration.` `Destination.` `Queue.` — quiet section eyebrows that read like marginalia in a manual. This is the same italic-term grammar used in hints and footers (`*Algorithm.*`, `*Privacy.*`), promoted to a navigational role. It pairs with, and is distinct from, the all-caps eyebrow (`.eyebrow`, used once at the masthead) and the intro **lede** paragraph (`.lede`, `1.125rem` serif-or-sans, `--text-mute`) that sits under the page title.

### 5.6 Connective punctuation

Two pieces of typographic connective tissue recur and should be used consistently:

- **Middle dot ` · ` (U+00B7)** joins inline metadata: `Author · 2021`, `Audio · M4A`, `YTArchive · v1`, `12 books · 4 movies`. Surround with spaces. As a structural alternative, a `1px` vertical hairline (`--border-soft`, `height: 0.75em`) separates stat-bar items. It also reads naturally inside placeholders (`https://…/@channel  ·  or any playlist URL`).
- **Em dash `— `** opens an attribution or citation: `— George Orwell, 1984`. Used for quote citations and authored subtitles.

These are quiet but load-bearing; mixing in commas or pipes where a `·` belongs reads as a different product.

---

## 6. Layout

- Content max-width: `880px`, centered (`.app-shell`).
- Page padding: `4rem 1.5rem 6rem` (desktop), `1.75rem 1rem 5rem` (mobile), with safe-area insets folded in (see §17).
- Body content sits at `position: relative; z-index: 1` above the glow layer.

```css
.app-shell {
  position: relative; z-index: 1;
  max-width: 880px; margin: 0 auto;
  padding: 4rem 1.5rem 6rem;
  padding-left:  max(1.5rem, env(safe-area-inset-left));
  padding-right: max(1.5rem, env(safe-area-inset-right));
  padding-bottom: max(6rem, calc(env(safe-area-inset-bottom) + 2rem));
}
@media (max-width: 640px) { .app-shell { padding: 1.75rem 1rem 5rem; } }
```

### Vertical rhythm

- Header margin-bottom: `2rem`–`3.5rem`.
- Section spacing: `2.5rem`–`3rem` between major sections.
- Section dividers: `1px` line in `--border-soft`.
- Tighter internal groups: `1rem` or `0.5rem` gaps.

### Grids

- Poster/card grids: `repeat(auto-fill, minmax(155px, 1fr))` with `1rem` gap (drop to `minmax(128px, 1fr)` below `480px`).
- Wide cards (quotes, queue rows): `repeat(auto-fill, minmax(280px, 1fr))`.
- Paired form fields: `repeat(auto-fit, minmax(280px, 1fr))` (collapses to one column on narrow screens).
- Hairline-divided grids: `1px` gaps over a `--border-faint` background — creates single-pixel dividers without double-borders.

---

## 7. Surfaces (panels, cards)

Panels sit on `--panel-bg` with backdrop blur, forming reading zones.

```css
.panel {
  background: var(--panel-bg);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  border: 1px solid var(--border-soft);
  border-radius: 2px;
  padding: 1.75rem;   /* 1.25rem in denser tools (YTArchive) — keep 1.25–1.75rem */
}
```

**Blur scale** — heavier blur for heavier surfaces:

- Cards, chips, panels, segmented tracks, queue items → `blur(4px)`
- Standard panels (roomy variant) and the modal backdrop → `blur(6px)`; toasts → `blur(8px)`

Surface weights, lightest → densest:

- Faint panels / control tracks / choice tiles → `--panel-bg-faint`
- Standard panel / card → `--panel-bg`
- Inputs (recessed) → `--input-bg`
- Modal card (densest) → `--modal-bg`

**Border radius: always `2px`.** Sharp corners are core to the editorial feel. Never pills, never heavily rounded cards. The only "round" things in the system are intentional circles (glows, the spinner, rating stars, the card-selection check mark).

---

## 8. Interactive Elements

### 8.1 Primary button (CTA)

```css
.btn-primary {
  background: var(--accent-bright);
  color: var(--on-accent);
  box-shadow: 0 0 32px rgba(var(--accent-amber-rgb), 0.25);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  font-weight: 500;
  padding: 0.7rem 1.25rem;
  border-radius: 2px;
}
.btn-primary:hover { background: var(--accent-bright-hover); box-shadow: 0 0 48px rgba(var(--accent-amber-rgb), 0.35); }
.btn-primary:disabled { background: var(--disabled-bg); color: var(--text-faint); box-shadow: none; cursor: not-allowed; }
```

The glow is a soft amber halo, roughly `0 0 32–48px` at `0.20–0.25` alpha. Two valid behaviors: it can **grow on hover** (BookShelf: `32px → 48px`) or sit **static** (YTArchive: a steady `0 0 40px`, with only the fill lightening on hover). Either way, disabled drops the glow entirely (`--disabled-bg` fill, `--text-faint` text). The fill is always `--accent-bright`/`--amber-bright` with near-black `--on-accent` text.

### 8.2 Secondary button (outlined)

Border `1px solid var(--text-strong)` (or `--text-high`), transparent background, matching text. Hover **inverts**: the text color becomes the background, text flips to `--on-accent`. Disabled fades border to `--border` and text to `--text-faint`.

### 8.3 Quiet / outlined button (`btn-ghost` = `btn-muted`)

The default "quiet" button — Export, Clear, Save, pagination, Pause. Two names for one role across the tools: BookShelf calls it `.btn-muted`, YTArchive `.btn-ghost`. Transparent, `1px solid var(--border)`, text `--text-dim`/`--text-body`; hover lifts text to `--text-heading`/`--text-high` and washes the background with `--hover-bg`. Pick one class name per tool.

### 8.4 Segmented toggle

Container: `1px solid var(--border)` over `--panel-bg-faint`, `blur(4px)`, `3px` padding, `2px` gap. Inactive options: mono uppercase `--text-muted`; hover adds `--hover-bg` + `--text-heading`.

**Active option uses `--accent-bright` background with `--on-accent` text** — never `--text-strong` as a background, which collapses to invisible in light mode (dark-on-dark). The active fill is the only divider between options. A slight `font-weight: 500` reinforces the active state.

```css
.segmented button.active {
  background: var(--accent-bright);
  color: var(--on-accent);
  font-weight: 500;
}
```

Wrap a horizontally-scrollable segmented control in a `.seg-scroll` with hidden scrollbars so it never wraps on mobile.

**Lesson encoded here:** when a token is used as a *background*, verify its paired text token contrasts in *both* themes. `--text-strong` flips from near-white to near-black; as a fill it only works in one theme.

**The single-theme exception** (see §1.4): YTArchive, being Dusk-only, *does* fill its active segment with `--text-high` (near-white) and dark text — a higher-contrast "white tab" than the amber pill. That's legal precisely because there's no Dawn to break. In a dual-theme tool, reach for `--accent-bright` instead. Also note the segment label face follows the tool's label convention (mono in BookShelf, sans in YTArchive).

### 8.5 Inputs & textareas

Background `--input-bg`, border `1px solid var(--border)`, `2px` radius, text `--text-strong`, focus border `--text-dim`. Mono font (`--input-mono`) for numeric/technical/key inputs. Placeholder is `--text-faint`, italic. Always set `-webkit-appearance: none` and never ship browser-default styling. Textareas: `min-height` set explicitly, `resize: vertical`.

### 8.6 Select (custom dropdown)

Native `<select>` with `appearance: none`, wrapped in `.select-wrap` whose `::after` draws the chevron so the arrow themes correctly:

```css
.select-wrap { position: relative; display: block; }
.select-wrap::after {
  content: "▾";
  position: absolute; right: 0.875rem; top: 50%;
  transform: translateY(-50%);
  color: var(--text-dim); pointer-events: none;
}
.select { padding-right: 2.25rem; appearance: none; /* + shared input styling */ }
```

A small variant (`.select-sm`) uses mono type at `0.75rem` for compact tool rows. A bare `<select>` (no wrapper) styled like an input is also fine for inline rows — see §8.7.

### 8.7 Custom checkbox

The native checkbox is visually hidden (not `display:none` — keep it focusable/operable) and replaced by a `16px` box that fills with the bright accent and reveals an inline check SVG when `:checked`. The label is a flex row so the box and text stay aligned; the whole label is the hit area.

```css
.check { display: flex; align-items: center; gap: 0.6rem; cursor: pointer; user-select: none; }
.check input { position: absolute; opacity: 0; width: 0; height: 0; }     /* hidden, still tabbable */
.check .box {
  width: 16px; height: 16px;
  border: 1px solid var(--border); border-radius: 2px;
  background: var(--input-bg);
  display: inline-flex; align-items: center; justify-content: center;
  transition: background 0.15s, border-color 0.15s;
}
.check .box svg { opacity: 0; color: var(--on-accent); }                   /* check glyph */
.check input:checked + .box { background: var(--accent-bright); border-color: var(--accent-bright); }
.check input:checked + .box svg { opacity: 1; }
.check:hover .box { border-color: var(--text-dim); }
```

```html
<label class="check" for="x">
  <input type="checkbox" id="x" />
  <span class="box"><svg …><path d="m5 12 5 5L20 7"/></svg></span>
  Ignore Shorts
</label>
```

Checkboxes sit in a `repeat(2, 1fr)` `.check-grid` for option lists (SponsorBlock segments, embed targets). Add `:focus-visible` styling on the box so keyboard users see focus.

### 8.8 Labeled form rows

For settings with a label on the left and a control on the right, a `space-between` flex row keeps a clean ledger rhythm:

```css
.row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.row > label { font-size: 0.875rem; color: var(--text-body); }
```

Numeric/path controls in these rows take the mono font and a recessed `--input-bg` (e.g. a narrow `width: 4.5rem; text-align: center` for a concurrency counter). A history-backed text input pairs with a `<datalist>` so prior entries autocomplete without a custom dropdown.

### 8.9 Theme toggle (switch)

A `2px`-cornered track (`--input-bg`, `--border`, `60×30px`) holding a moon icon and a sun icon, with a sliding `--accent-amber` knob (`24×24px`). The knob slides left (Dusk) or right (`translate(32px)` for Dawn). The icon *under* the knob uses `--on-accent`; the inactive icon uses `--text-faint`. `role="switch"`, keyboard-operable (Space / Enter), `aria-checked` reflects state. Knob transition: `0.22s cubic-bezier(0.4,0,0.2,1)`. (Dual-theme tools only.)

### 8.10 Icon buttons (transparent)

`36×36px`, no background, no border. Color `--text-dim`, hover `--text-heading` + `--hover-bg` wash. SVG `18px`, stroke `2`. Focus-visible adds a `--focus-ring` outline. Used for settings, fullscreen, image upload, reset, modal close, modal back. **Row-action variant** (queue items): smaller, *bordered* (`1px solid var(--border)`, `0.35rem 0.5rem` padding), with a `.danger` variant (`--danger` border/text, faint red wash on hover) for remove/cancel.

### 8.11 Floating action button (FAB)

The single most prominent control for the primary "add/create" action. Fixed to the bottom-right, safe-area aware, `56×56px`, `2px` radius, `--accent-bright` fill, and a **double shadow** — a soft drop shadow plus an amber glow:

```css
.fab {
  position: fixed;
  right: max(1.5rem, env(safe-area-inset-right));
  bottom: max(1.5rem, calc(env(safe-area-inset-bottom) + 1rem));
  z-index: 150;
  width: 56px; height: 56px; border-radius: 2px;
  background: var(--accent-bright); color: var(--on-accent);
  box-shadow:
    0 8px 24px rgba(var(--shadow-rgb), calc(0.4 * var(--shadow-strength))),
    0 0 40px  rgba(var(--accent-amber-rgb), 0.35);
}
.fab svg { width: 26px; height: 26px; stroke-width: 2.2; }
```

Hover deepens both shadows. There is at most **one** FAB on screen — it is the screen's verb.

### 8.12 Tabs / underline navigation

For top-level navigation between views, a row with a hairline bottom border and an amber active underline (rather than a filled pill):

```css
.tabs { display: flex; border-bottom: 1px solid var(--border-soft); overflow-x: auto; }
.tabs button {
  padding: 1rem 1.5rem;
  font: 0.6875rem/1 ui-monospace, monospace;
  letter-spacing: 0.25em; text-transform: uppercase;
  color: var(--text-dim);
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tabs button:hover  { color: var(--text-heading); }
.tabs button.active { color: var(--accent-amber); border-bottom-color: var(--accent-amber); }
```

Note the active *tab* uses `--accent-amber` (quiet, ink-like), whereas the active *segmented* option uses a `--accent-bright` fill. Tabs underline; segments fill.

### 8.13 Star rating

Five `★` buttons over a clear control. Filled stars use `--accent-bright` (fill *and* color via `fill: currentColor`); empty use `--text-faint`. Tapping a filled star's own position clears down to it; a separate "Clear" link in mono `--text-faint` resets. Stars are `30×30px` tap targets with `20px` glyphs.

In compact contexts (cards, list rows), rating renders as text instead — `★★★★☆` in mono `--accent-amber` (`.entry-rating-line`); the unrated state shows the word `unrated` in serif italic `--text-faint`.

### 8.14 Destructive actions & confirm-in-place

Destructive buttons use `.btn-danger`: transparent with a `1px solid var(--danger)` border and `--danger` text; hover fills `--danger` with `--on-accent` text.

Destructive actions confirm **in place**, not via a second modal: the first tap relabels the button to "Tap again to confirm" for a ~3s window, then the second tap commits (and the timeout reverts the label). This keeps the calm — no stacked dialogs — while still guarding the action.

### 8.15 Hover & transitions

- Standard transition: `0.15s`–`0.2s` on `color`, `background`, `border-color`, `box-shadow`.
- Hover = color/background/border shift and, for cards, a shadow lift — **never `scale()` transforms** on controls or cards.
- Theme switch: tokens transition implicitly where the property animates; the knob slides on its own cubic-bezier.

---

## 9. Cards & Grids

Cards are the system's primary content unit (catalogue entries, results, history items). They are panels that respond to hover with an **amber border and a lift shadow** — the reason the "no scale transforms" rule holds: depth comes from light, not size.

```css
.card {
  background: var(--panel-bg);
  backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
  border: 1px solid var(--border-faint);
  border-radius: 2px; overflow: hidden;
  text-align: left;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.card:hover {
  border-color: rgba(var(--accent-amber-rgb), 0.6);
  box-shadow:
    0 8px 24px rgba(var(--shadow-rgb), calc(0.25 * var(--shadow-strength))),
    0 0 0 1px rgba(var(--accent-amber-rgb), 0.12);
}
.card:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }
```

**Hover vs. selection — two different jobs.** The amber border + lift can signal *hover* (BookShelf cards) **or** be reserved for a persistent *selected* state (YTArchive's grid, where you pick videos to queue). When a card is selectable, keep hover quiet (`border-color: var(--text-dim)` only) and spend the amber on selection:

```css
.card:hover { border-color: var(--text-dim); }          /* quiet when selectable */
.card.selected {
  border-color: var(--accent-bright);
  background: var(--chip-bg);                            /* a touch lighter */
  box-shadow: 0 0 30px rgba(var(--accent-amber-rgb), 0.15);
}
.card.disabled,
.card.queued { opacity: 0.55; cursor: default; }        /* already-acted-on */
```

A selected card also reveals a **selection mark**: a small amber-filled circle with a check, absolutely placed top-left over the thumbnail (`display:none` until `.selected`). This is the one sanctioned circle on a card.

**Aspect ratio follows content.** Poster cards (books, films) use `2 / 3`; video cards use `16 / 9`. The thumbnail well is `--input-bg`/`--surface-3` behind the image. Overlay metadata directly on the thumb when it belongs to the image — e.g. a **duration badge**: mono `0.6875rem`, `--cream` text on a `rgba(12,10,8,0.85)` chip, bottom-right.

**Body block** stacks: a micro-label for type/meta (mono, `--text-faint`), a serif title clamped to two lines (`--text-heading`/`--text-strong`), an italic serif sub-line clamped to one (`--text-dim`), then a rating or mono meta line (`--text-mute`).

**Missing images** fall back to a placeholder: a faint outline icon plus the title in serif italic — never a broken-image glyph. Wire `img.onerror` to swap in the placeholder.

**Badges on cards** (e.g. "in library", status) sit absolutely in a corner: `--accent-bright` fill with `--on-accent` text for a positive badge, or the status-chip treatment from §10.

### 9.1 Stat / meta grid (hairline-divided)

For a compact run of figures (channel name, page, counts, selected), use the hairline-grid technique: cells over a `--border-faint`/`--border-hair` background with `1px` gaps, so the dividers are single pixels with no double borders.

```css
.meta-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1px; background: var(--border-faint);
  border: 1px solid var(--border-soft); border-radius: 2px;
}
.meta-cell  { background: var(--panel-bg); padding: 1rem 1.1rem; display: flex; flex-direction: column; gap: 0.35rem; }
.meta-value { font-family: var(--serif); font-size: 1.5rem; color: var(--text-heading); letter-spacing: -0.01em; }
.meta-value.mono { font-family: var(--mono); font-size: 1.25rem; }
```

Each cell pairs an all-caps label (§5.4) with a serif figure; counts and IDs take the mono variant. This is the editorial answer to a "stats dashboard" — figures set like a table of contents, not like gauges.

---

## 10. Status, Badges & Live Progress

Tools in this system surface *state* — a catalogue entry's progress, a download's lifecycle. State is communicated through the existing accent roles, never through new saturated colors.

### 10.1 Status palette

This is the verified mapping from YTArchive's queue (class → color), and it governs any lifecycle UI:

| State (`.qstatus.*`) | Color role | Reads as |
| :---- | :---- | :---- |
| `pending` / queued | `--text-dim` (`--text-mute`) | waiting, neutral |
| `downloading` / active | `--accent-bright` (`--amber-bright`) | live, attention |
| Wishlist / want *(catalogue)* | `--accent-amber` | noted, not yet started |
| `paused` | `--text-faint` (`--text-decor`) | suspended |
| `completed` / done | `--accent-sage` | resolved, calm success |
| `failed` / error | `--danger` (`--err-fg`) | needs attention |
| `cancelled` | `--text-faint` (`--text-decor`) | abandoned, dimmed |

The throughline: **bright amber = the one thing happening now; sage = resolution; danger = failure; dim/faint = inactive or abandoned.** A queue full of sage means the run is done; a single bright row is what's live.

### 10.2 Status as structure — the lifecycle row

A queue/list item encodes its state in **three coordinated places**, so status is legible at a glance and never relies on color alone:

1. **Left border of the row** takes the state color (`border-color`), turning the whole row into a status indicator.
2. **The status word** is rendered as a serif-**italic** verb — not a mono chip. This `.status-verb` (serif italic, `~0.8125rem`, no tracking) is deliberately editorial, the human-readable counterpart to the machine data beside it.
3. **Mono chips** carry the machine data (size, quality, percent) — `0.625rem`, tracked, hairline border; a `.mono` chip variant drops tracking/caps for values like `1080p`.

```css
.qitem { display: grid; grid-template-columns: 88px 1fr auto; gap: 1rem;
         background: var(--panel-bg); backdrop-filter: blur(4px);
         border: 1px solid var(--border-soft); border-radius: 2px; padding: 0.75rem; }
.qitem.downloading { border-color: var(--accent-amber); }
.qitem.completed   { border-color: var(--accent-sage); }
.qitem.failed      { border-color: var(--danger); }
.qitem.paused      { border-color: var(--text-faint); }

.qstatus.downloading { color: var(--accent-bright); }
.qstatus.completed   { color: var(--accent-sage); }
.qstatus.failed      { color: var(--danger); }
.qstatus.pending,
.qstatus.paused,
.qstatus.cancelled   { color: var(--text-faint); }
```

The row is a grid of `[thumb] [body] [actions]`; the body holds the serif title (single line, ellipsis), then a wrapping `.qmeta` line of the status verb + chips, then the progress bar. Actions are the bordered icon-button variant from §8.10.

### 10.3 Status chips

A chip is an all-caps, tracked micro-label with a tinted hairline border in the state's color (mono in BookShelf, sans in YTArchive):

```css
.status-chip {
  font: 0.58rem var(--mono);            /* or var(--sans) */
  letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--accent-sage);
  border: 1px solid rgba(var(--accent-sage-rgb), 0.35);   /* or var(--border-soft) */
  border-radius: 2px; padding: 0.1rem 0.45rem;
}
```

Swap the color/border per state. Tag chips (genres, categories) are the neutral variant: `--chip-bg` fill, `--border-faint` border, `--text-muted` text. A `.mono` modifier turns a chip into a plain value badge (`letter-spacing: 0; text-transform: none`).

### 10.4 Progress bars

For live, determinate progress (downloads, imports), a **thin** track — `3px`–`6px` tall — over `--border-faint`, with a bright-accent fill whose width transitions smoothly. The fill **recolors to match status**: sage on completion, danger on failure. This is *responsiveness*, not decoration.

```css
.progress { height: 3px; background: var(--border-faint); border-radius: 1px; overflow: hidden; }
.progress > .bar { height: 100%; width: 0%; background: var(--accent-bright); transition: width 0.25s linear; }
.qitem.completed .progress .bar { background: var(--accent-sage); }
.qitem.failed    .progress .bar { background: var(--danger); }
```

When progress is **indeterminate**, use a **sweep**, not the spinner: a ~40%-wide gradient bar translating across the track on a slow ease-in-out loop. (The spinner is for *loading rows*, §10.5; the sweep is for *progress tracks*.)

```css
.progress.indeterminate .bar {
  width: 40%;
  background: linear-gradient(90deg, transparent 0%, var(--accent-bright) 50%, transparent 100%);
  animation: progress-sweep 1.4s ease-in-out infinite; transition: none;
}
@keyframes progress-sweep { from { transform: translateX(-100%); } to { transform: translateX(350%); } }
```

A mono percentage label (`--text-dim`) may sit in the `.qmeta` line as a chip rather than on the bar itself.

### 10.5 Loading row & spinner

A loading state is an all-caps, tracked line in `--text-dim` preceded by a small amber spinner — never a full-screen blocker:

```css
.loading-row {
  display: flex; align-items: center; gap: 0.65rem;
  font: 0.65rem var(--mono);
  letter-spacing: 0.2em; text-transform: uppercase;
  color: var(--text-dim);
}
.loading-row::before {
  content: ""; width: 12px; height: 12px;
  border: 1.5px solid var(--accent-amber); border-top-color: transparent;
  border-radius: 50%; animation: spin 0.75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
```

### 10.6 Live updates (streaming)

Tools that stream state (e.g. via Server-Sent Events) push small typed events — `progress`, `update`, `state`, `queued`, `removed`, `settings` — and patch only the affected row. The UI never re-renders wholesale on each tick; a row's progress bar, status verb, and left-border color update in place. Visually, the only motion is the bar advancing and a row's accent changing. Status copy stays in the system's verb set: *Queued, Downloading, Paused, Completed, Failed, Cancelled* (see §15).

---

## 11. Feedback & Empty States

### 11.1 Notice

Low-key informational/highlight block: `--notice-bg` fill, `1px` amber-tinted border, body text, with `--accent-bright` for any inline strong or link.

### 11.2 Error message

`--error-bg` fill, a faint amber border, and a **`3px` left border in `--danger`** as the signal; text in `--danger`. The left rule is the tell — it reads as a margin mark, editorial rather than alarmist.

```css
.msg-error {
  background: var(--error-bg);
  border: 1px solid rgba(var(--accent-amber-rgb), 0.2);
  border-left: 3px solid var(--danger);
  color: var(--danger);
  padding: 0.9rem 1.1rem; border-radius: 2px;
}
```

### 11.3 Empty state

A centered, generous, **dashed-border** zone — visibly a placeholder, not a card. A faint outline icon, a serif-italic heading (`--text-heading`), and a serif-italic line of guidance (`--text-dim`). Copy points to the next action ("Search for a book to get started"), never a dead end.

```css
.empty-state {
  text-align: center; padding: 5rem 2rem;
  border: 1px dashed var(--border-soft); border-radius: 2px;
}
.empty-state h3 { font-style: italic; color: var(--text-heading); /* serif */ }
.empty-state p  { font-style: italic; color: var(--text-dim);     /* serif */ }
```

### 11.4 Toast (transient status)

For ephemeral confirmations and errors that shouldn't occupy layout space (a save landed, a fetch failed), a single fixed toast at bottom-center: a dark, heavily-blurred slab that themes its border/text/fill by variant.

```css
.toast {
  position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%);
  background: var(--modal-bg); border: 1px solid var(--border);
  border-radius: 2px; padding: 0.75rem 1.25rem;
  color: var(--text-body); font-size: 0.8125rem;
  backdrop-filter: blur(8px); z-index: 100; max-width: 80%;
}
.toast.error  { border-color: var(--danger);    color: var(--danger);       background: var(--error-bg); }
.toast.notice { border-color: var(--accent-bright); color: var(--accent-bright); background: var(--notice-bg); }
```

One toast at a time; it fades after a few seconds. Reserve it for *outcomes* — inline `.notice`/`.msg-error` blocks remain the place for persistent, in-context guidance. Note `--notice-bg` is tool-dependent: a low-opacity amber tint in BookShelf, a warm dark slab (`rgba(41,37,30,0.6)`) in YTArchive — both reading as "a quiet aside."

---

## 12. Modal

Centered card on a blurred `--modal-backdrop` (`blur(6px)`). On short viewports the backdrop aligns to the top with a safe-area-aware top padding; it centers once there's room (`@media (min-height: 640px)`). The card uses `--modal-bg`, a `rgba(var(--accent-amber-rgb), 0.28)` border, `2px` radius, `max-width: 680px`, and `max-height: calc(100dvh - 2.5rem)` with internal scroll.

```css
.modal-card {
  background: var(--modal-bg);
  border: 1px solid rgba(var(--accent-amber-rgb), 0.28);
  border-radius: 2px;
  box-shadow:
    0 32px 80px rgba(var(--shadow-rgb), calc(0.65 * var(--shadow-strength))),
    0 0 80px    rgba(var(--accent-amber-rgb), 0.15),
    inset 0 1px 0 rgba(var(--cream-rgb), 0.06);
  animation: modal-in 0.45s cubic-bezier(0.2, 0.8, 0.3, 1);
}
@keyframes modal-in {
  from { opacity: 0; transform: translateY(20px) scale(0.96); }
  to   { opacity: 1; transform: translateY(0)    scale(1); }
}
```

Decorative **`1px` corner brackets** (`14×14px`, `rgba(var(--accent-amber-rgb), 0.45)`) sit at the top-left and bottom-right via `::before`/`::after` — a quiet "viewfinder" framing. The backdrop fades in over `0.2s`.

**Anatomy**, top to bottom (use what the content needs):

- **Close** (`.modal-close`) top-right icon button; **Back** (`.modal-back`) top-left for multi-step flows.
- **Eyebrow** — mono, uppercase, tracked, in `--accent-amber` (note: amber here, not dim — the eyebrow is the one accented label).
- **Hero** — optional `2 / 3` poster (`140px`) beside a heading block (title, italic creators, year, tag row).
- **Sections** — each separated by a `1px` `--border-faint` top border, led by a mono `--modal-section-label`.
- **Actions** — centered button row with a `--border-faint` top border.

**Pull-quote modal** is a distinct layout: an oversized serif quote glyph (see §below) over a serif-italic blockquote (`1.3125rem`, `--text-heading`) and a mono em-dash citation (`--text-dim`).

**Decorative quote glyph** — a signature flourish for quotes: a single large serif `"` in `--accent-amber` at low opacity, sized `2.5rem` on cards and up to `5rem` in modals (`opacity: 0.3`–`0.5`, `user-select: none`).

Dismissible by close button, backdrop click, or Escape. Trap focus while open; restore it on close.

---

## 13. Icons

Lucide icon set (MIT licensed), inlined as SVG for self-contained, offline files. A tiny helper builds them so size and stroke stay consistent:

```js
const icon = (paths, size = 16, stroke = 2) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="${stroke}"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
```

Sizes by context:

- Inline with text: `12px`
- In buttons and labels: `14`–`16px`
- Icon buttons: `18px`; FAB: `26px`
- Banners / status / empty-state: `16`–`40px` (placeholder icons run large and faint)

Stroke width `2` by default (`2.2` for toggles/FAB to stay crisp; `1.25`–`1.5` for large faint placeholder icons). Icons inherit color via `stroke="currentColor"`.

---

## 14. Motion

Almost none. This is a deliberately calm aesthetic — motion means responsiveness, not decoration.

- Hover transitions: `0.15s`–`0.2s`.
- Theme-toggle knob: `0.22s` eased slide.
- Modal entrance: `0.45s` fade + scale; backdrop fade `0.2s`.
- Accordion/disclosure chevrons rotate `180deg` on open.
- Spinner: `0.75s` linear loop (the "working" signal for loading rows).
- Determinate progress: width transitions `0.2s`–`0.25s` linear as values stream in.
- Indeterminate progress: a `1.4s` ease-in-out **sweep** across the track (not the spinner).
- The one expressive exception is a deliberate hero gesture where a tool has one (e.g. a `requestAnimationFrame` ease-out wheel spin).

**Avoid:** entrance animations on page load, scroll-triggered effects, parallax, anything that competes with reading.

---

## 15. Voice & Copy

The writing matches the visual language:

- **Restrained and precise.** No exclamation marks, no "Let's get started!", no marketing voice.
- **Contextual definitions** below fields, not tooltips.
- **Technical terms used plainly** — respect the reader's intelligence.
- **Short, dignified labels:** "Candidates", "Past Rulings", "Add to catalog", "Remove from catalog".
- **Consistent status verbs.** Pick one word per state and keep it everywhere: *Queued, Downloading, Paused, Completed, Failed, Cancelled* for activity; *Wishlist, Progressing, Completed* for reading/watching progress. Don't drift between "Done" and "Finished".
- **Metadata joined with ` · `**, citations opened with `— ` (see §5.6).
- **Destructive confirmation reads as instruction**, not alarm: "Tap again to confirm".
- **The italic-term grammar is the house voice**, used at three scales: section markers (`*Source.*`, `*Configuration.*`), inline hint openers (`*Algorithm.* Segments are looked up…`, `*Why.* YouTube blocks…`), and footer notes (`*Privacy.* No telemetry.`). One italicized noun, a period, then the explanation. It reads like a well-set manual and quietly states values — these tools are local-first (see §17), and the copy says so plainly (`*Reach.* The bridge binds to 127.0.0.1.`) rather than boasting.
- **Name the engine, not the magic.** Copy can say *yt-dlp fetches; ffmpeg muxes and trims* — naming real tools respects the reader more than "powered by AI"-style vagueness.

---

## 16. Responsive Behavior

Single column below `640px`. Key adaptations:

- Title shrinks (`3rem` → `2.25rem` is typical); modal titles `2rem` → `1.625rem`.
- Container padding reduces; modal sections and hero padding tighten.
- Header row may stack; segmented controls become full-width and horizontally scroll rather than wrap.
- `auto-fill`/`auto-fit` grids reflow naturally; poster grids drop their min column width (`155px` → `128px`).
- Two-column modal blocks collapse to one column.
- Button labels may hide, leaving icon-only buttons (`.btn-label { display: none }` under `640px`).
- List/ledger rows collapse their timestamp onto its own line.

---

## 17. Mobile & PWA

These tools ship as self-contained, often app-wrapped (Android/iOS) experiences, so the system bakes in device-shell concerns:

- **Safe-area insets.** Page padding, the FAB, and the modal's top alignment all use `max(…, env(safe-area-inset-*))` so content clears notches, home indicators, and rounded corners. Set `viewport-fit=cover` in the viewport meta for the insets to resolve.
- **Theme-color meta.** The browser/OS chrome color matches the surface: `<meta name="theme-color" content="#0f0d0b">` (Dusk `--bg-grad-mid`). Update it on theme switch so the status bar follows Dawn (`#efe6d8`).
- **`color-scheme` per theme** (set in `:root` and `[data-theme="light"]`) so native scrollbars, form controls, and overscroll glow match.
- **`100dvh`** (dynamic viewport height) for full-height surfaces like modals, so mobile browser chrome doesn't clip them.
- **Tap targets** are generous (`36px`+ for icon buttons, `30px` rating stars, `56px` FAB) and `-webkit-tap-highlight-color: transparent` removes the default flash; hover styling stands in.
- **Local-first by default.** Data lives on-device — `IndexedDB` (e.g. via Dexie) for records, namespaced `localStorage` (`bookshelf:` / `theme`) for settings. Network is for fetching *new* data, not for storing the user's. Plain-text **Markdown import/export** is the portable backup format, which also makes the data inspectable and ownable. This is the technical face of the "Private." copy motif (§15).

---

## 18. Accessibility

- `--text-dim` is the dimmest readable text in **both** themes — verified for contrast against panel surfaces. `--text-faint` is decoration only.
- Any token used as a *background* must contrast with its paired text token in *both* themes (see §8.4) — the most common theming contrast trap.
- All controls are keyboard-operable. Switches use `role="switch"` + `aria-checked`; toggles, disclosures, and the ledger expose `aria-label` / `aria-expanded`.
- Focus-visible states use a `2px` `--focus-ring` outline with a small `outline-offset`; never `outline: none` alone.
- Modals set `role="dialog"` + `aria-modal="true"`, trap focus, close on Escape, and restore focus to the trigger.
- Decorative elements (glow layer, quote glyphs, corner brackets, separators) are `aria-hidden`.
- Status is never color-only: a status chip carries its **word**, not just its hue, so the state survives for color-blind users and in monochrome.
- `color-scheme` is set per theme so native UI matches. First load honors `prefers-color-scheme`.

---

## 19. Quick-Start Checklist

Building a new tool or component in this style:

1. Define the token set first. Dual-theme tools write **both** blocks — `:root` (Dusk) and `[data-theme="light"]` (Dawn) — with the full *semantic* set, `--error-bg`/`--notice-bg`, and RGB triples. Single-theme tools may ship Dusk-only and skip the triples, but still route every color through a token (§1.4).
2. Tokenize the **fonts** too (`--serif`/`--sans`/`--mono`), web-font-first so one stack serves online and offline.
3. Set up the three-glow background layer. It does 80% of the atmospheric work.
4. Gradient background surface, not flat. Cream paper in Dawn, near-black in Dusk — never pure white or pure black. Mirror `--bg-grad-mid` into the `theme-color` meta and update it on toggle.
5. Serif display + sans body + mono for data. Join metadata with ` · `, citations with `— `. Introduce sections with the italic `*Marker.*` and write hints/footers in the same italic-term grammar.
6. All-caps tracked labels above fields and sections (mono *or* sans — pick one per tool and hold it); micro-labels on chips and type tags.
7. `2px` border radius everywhere. No pills. The only circles are glows, spinner, rating stars, and the card-selection check.
8. Translucent panels with backdrop blur for every content zone — `4px` for cards/chips/panels, `6px` for roomy panels/modal backdrop, `8px` for toasts.
9. Keep accents sparse — italic words, numbers, icon tints, the one active control, a single live item. Bright amber = the live thing; sage = done; danger = failure; dim/faint = inactive.
10. Decide hover vs. selection up front: amber border + glow signals *hover* on simple cards, but is reserved for *selection* on pickable grids (hover stays a quiet border there). Never `scale()`.
11. Encode lifecycle state in structure, not just hue: a status-colored **left border**, a serif-italic **status verb**, and mono **data chips**. Progress bars are slim, recolor to match status, and sweep (don't spin) when indeterminate.
12. Stream updates row-by-row, never full re-renders. Use a single bottom-center toast for transient outcomes.
13. Every component rule references `var(--token)`. A raw color outside the token block is a bug.
14. When a token is a *background*, check its text pairing in both themes — unless the tool is provably Dusk-only (§8.4).
15. For SVG/canvas drawn in JS, read tokens via `getComputedStyle` and redraw on theme change.
16. Fold `--shadow-strength` into every shadow's alpha so one rule themes correctly.
17. Respect the device shell: safe-area insets, `100dvh`, generous tap targets, `viewport-fit=cover`.
18. Keep data local-first; offer Markdown export. Name the real engine; say "Privacy." and mean it.
19. `--text-dim`/`--text-mute` is the dimmest readable text. `--text-faint`/`--text-decor` is decoration only.
20. Write copy like an editor, not a marketer. One word per state, used everywhere. Resist the urge to animate.
