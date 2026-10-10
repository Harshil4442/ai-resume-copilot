# Packaged HireWiz fonts

The root layout uses supported `next/font/local` so a production build reads these
reviewed assets directly. It retains DM Sans variable normal (100–1000), Crimson
Text 400/600 normal and italic, and Roboto Mono normal 400/500. The existing CSS
variables, `display: swap`, DM Sans/Crimson preload intent, and non-preloaded Mono
remain. Next still emits self-hosted, content-versioned font URLs and automatic
fallback metrics: Arial for DM Sans/Mono, Times New Roman for Crimson Text.

This removes the generated Google font-file query/acquisition dependency. The
original 7d07b5575e9a6a1189dc55b71bea648e497d313c hosted frontend build recorded
12 Roboto Mono generated-CSS resolution failures and the Next 16.4.0/Turbopack
single-entry Google font query refusal. It did not establish a TLS or network
failure. Default Turbopack, Next version, lockfiles and all CI gates remain.
`next build --webpack` is a supported alternative, but would retain the Google
build acquisition dependency and change the current engine.

## Source and license

All originals came from the official [Google Fonts repository](https://github.com/google/fonts/tree/bd8f81ddb5c74d5c8897b36ad88b440266245103/ofl)
at immutable commit `bd8f81ddb5c74d5c8897b36ad88b440266245103`. Exact upstream
file URLs, Git blob identities, source/output SHA256 values, table hashes, glyph
counts, axes, names and metrics are in [provenance.json](./provenance.json).
The three original SIL Open Font License 1.1 files are retained byte-for-byte:

- [DM Sans license](./dmsans-OFL.txt): Copyright 2014 The DM Sans Project Authors.
- [Crimson Text license](./crimsontext-OFL.txt): Copyright 2010 The Crimson Text Project Authors.
- [Roboto Mono license](./robotomono-OFL.txt): Copyright 2015 The Roboto Mono Project Authors.

These fonts remain under their original licenses and copyright/name metadata.
They are bundled with the application and are not sold separately. The OFL
[format conversion FAQ](https://openfontlicense.org/ofl-faq/) (2.2) and
[webfont guidance](https://openfontlicense.org/webfonts-and-reserved-font-names/)
allow equivalent compressed webfont wrapping without renaming when original
font data is retained and optional WOFF metadata is equivalent or omitted.

## Reproducible compression

Original full TTFs were wrapped with Python 3.14.6, fontTools 4.66.1 and Brotli
1.2.0 using:

```python
from fontTools.ttLib.woff2 import compress
compress(original_ttf, output_woff2, transform_tables=set())
```

There is no subsetting, axis instantiation, outline processing, family renaming,
hint removal, name/metric editing, or optional WOFF metadata/private block.
Every decompressed table byte matches its original except the `head` checksum
adjustment and compression flag bit 11. The [WOFF2 specification](https://www.w3.org/TR/WOFF2/)
requires that flag; the checksum changes with the container representation.
The retained verification normalizes only those fields and compares all other
bytes, including all glyphs, shaping tables, cmap, axes, names and timestamps.

## Optical design and measured limits

The full upstream DM Sans font has an optical-size axis 9–40, default 9. The old
Google configuration requests only weight, and Next's cached font metadata gives
the unrequested optical axis default as 14. A fresh equivalent Google response
has no optical axis and retains the name “DM Sans 9pt”; that name alone does not
prove its effective design. Actual glyph advances match upstream optical size
14 closely, rather than 9. The body therefore uses `font-optical-sizing: none`
and only `font-variation-settings: "opsz" 14`, preserving normal CSS weight
selection. This inherited setting has no effect on the packaged Crimson/Mono
fonts, which contain no optical axis. Full binary axes remain unchanged.

In the retained Chromium comparison, 30 samples covered 14/32/48px sizes, DM Sans
weights 400/500/600/700, four Crimson styles and two Mono weights. All 18 Crimson/
Mono screenshots matched the fresh reference exactly. Twelve DM screenshots
differed; maximum sample width difference was 0.140625px after fixing the optical
setting, consistent with static Google-axis rounding. The rejected default-9
comparison reached 17.203125px. This is current-reference evidence, not proof of
historical hosted font bytes or exact rendering across every browser/platform.

Automatic fallback percentages also differ: local fonts derive them from the
actual files rather than Google's cached metrics. Generated production CSS keeps
Arial/Times and metric overrides, but exact old fallback percentages are not
claimed. Five-width loaded-font checks cover current layout and overflow.

| Family | Old cached size-adjust | Local measured size-adjust |
| --- | ---: | ---: |
| DM Sans / Arial | 104.53% | 106.22% |
| Crimson Text / Times New Roman | 97.36% | 96.55% |
| Roboto Mono / Arial | 134.61% | 131.51% |

No comparative cumulative-layout-shift improvement is claimed.

## Download tradeoff

Full glyph data costs more than Google's Latin subsets. Actual WOFF2 bytes:

| Asset | Bytes |
| --- | ---: |
| DM Sans variable | 91,708 |
| Crimson Text regular | 48,756 |
| Crimson Text semibold | 50,016 |
| Crimson Text italic | 49,892 |
| Crimson Text semibold italic | 50,892 |
| Roboto Mono variable | 111,036 |
| Total | 402,300 |

The five preloaded DM/Crimson files total 291,264 bytes, versus 140,244 in the
fresh comparable Google Latin response: 151,020 additional first-load bytes.
Mono remains lazy and is not preloaded. All six files total 229,260 more bytes
than that response's 173,040 unique Latin bytes. These comparisons exclude
HTTP headers/compression/caches and do not identify historical CI payloads.
Both old and new Next loaders self-host; this change promises deterministic
build acquisition, not faster first rendering. Production font responses use
same-origin immutable caching. Any future subsetting or preload change needs
separate glyph/layout/performance review.

See official [Next font documentation](https://nextjs.org/docs/app/api-reference/components/font)
for loader configuration and [Next 16 engine guidance](https://nextjs.org/docs/app/guides/upgrading/version-16)
for the retained default Turbopack contract.
