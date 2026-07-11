# FTMON web mark

These files are local variants of a new FTMON mark generated for the v2 web
interface. The dial and pulse line describe monitoring; the three small status
dots connect it to FTMON's clear, warning, and error states. Lavender deliberately
echoes the original interface without reusing its small raster artwork.

The header keeps `FTMON` as HTML text and treats the image as decorative. This is
intentional: the home link remains understandable to assistive technology and
when an image is unavailable. Separate ICO, 64 px PNG, and 180 px touch variants
avoid asking browsers to download and resize the larger header asset.

Generation mode: built-in image generation, followed by local chroma-key removal
and deterministic resizing. Prompt summary: a compact square FTMON monitor/radar
dial with an interlocking `ft`, pulse trace, green/amber/red status dots, dark navy
and lavender palette, flat vector-friendly geometry, and no surrounding text.
