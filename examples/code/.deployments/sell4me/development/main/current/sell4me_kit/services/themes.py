"""Themes: the palette, the type and the corners a storefront is drawn with.

A theme here is deliberately *not* a layout. Layout is the page builder's job —
what blocks a page holds and in what order — and a theme that also owned layout
would mean two places deciding what the homepage looks like, disagreeing the
first time either changed.

So a theme is the small set of values every block reads while rendering:
seven colours, two typefaces, one corner radius. That is enough to make two
shops built from the same blocks look nothing alike, and little enough that a
merchant can change all of it in a minute without breaking a page.

`PRESETS` are starting points, not modes. Applying one writes its values onto
the store's theme row and the merchant is then free to change any of them — a
preset that stayed "in effect" would make every later edit a fight with it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from . import sections as section_presets
from .builder import sanitise
from .. import q
from .catalog import unique_slug
from .storefront import FONTS, safe_color, safe_font

__all__ = [
    "CATEGORIES",
    "PALETTES",
    "PRESETS",
    "ColourPalette",
    "Preset",
    "ThemePage",
    "apply_palette",
    "apply_preset",
    "build_pages",
    "palette_props",
    "preset_props",
    "save_theme",
]


@dataclass(frozen=True, slots=True)
class Aesthetic:
    """How a template *carries* itself, as opposed to what colour it is.

    Two shops on the same palette and the same blocks can look nothing alike,
    and the difference is almost never the colour. It is how large the display
    type is, whether it is set tight or spaced out, how much air sits between
    the bands, and whether anything has a line drawn round it.

    Every field maps to a CSS custom property the block renderers read, so a
    template changes all of this without a single new component.
    """

    #: How large a display heading runs. `poster` is a magazine cover.
    scale: str = "normal"          # compact | normal | display | poster
    #: Letterform treatment on headings.
    tracking: str = "normal"       # tight | normal | wide
    case: str = "normal"           # normal | upper
    #: Vertical air between bands.
    rhythm: str = "normal"         # tight | normal | airy | vast
    #: Whether things have lines round them, and how heavy.
    edge: str = "hairline"         # none | hairline | bold
    #: A faint ground. `grid` suits a catalogue, `grain` a print shop.
    texture: str = "none"          # none | grain | grid
    #: How wide the reading column runs.
    measure: str = "normal"        # narrow | normal | wide


@dataclass(frozen=True, slots=True)
class ThemePage:
    """One page a theme ships.

    `sections` names presets from `app/services/sections.py` in order. Naming
    them rather than embedding their trees is what keeps a theme readable and
    what makes an improvement to a section reach every theme that uses it.
    """

    title: str
    slug: str
    kind: str
    sections: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Preset:
    """One starting point.

    `swatch` is the three colours the picker draws as a preview — chosen by
    hand rather than taken from the first three values, because what identifies
    a theme at a glance is its accent against its ground, not whatever happens
    to be first in the dictionary.
    """

    key: str
    name: str
    description: str
    #: What kind of shop it suits. Shown on the card, because "which of these
    #: ten" is answered by the shop you have, not by the colours.
    suits: str
    colors: dict[str, str]
    fonts: dict[str, str]
    corner_style: str
    swatch: tuple[str, str, str] = field(default=("#000000", "#ffffff", "#888888"))
    #: Which set in `app/services/imagery.py` its photographs come from. A
    #: linen shop and a tool shop need different pictures long before they need
    #: different fonts.
    imagery: str = "studio"
    #: How it carries itself. See `Aesthetic`.
    style: Aesthetic = field(default_factory=Aesthetic)
    #: The site. Empty means colours only, which no theme here is.
    pages: tuple[ThemePage, ...] = ()
    #: One of `CATEGORIES`. What the onboarding picker filters by — a coarser,
    #: clickable version of `suits`, which is prose written to be read rather
    #: than matched against a chip.
    category: str = "general"


#: The pages every template ships beyond its homepage.
#:
#: Six, not two. A template is meant to be a finished shop, and a finished shop
#: has somewhere to explain itself, somewhere to answer the questions that stop
#: a sale, somewhere to be found, and a page to send a campaign at. A merchant
#: who has to invent those from an empty canvas has been given a homepage and a
#: to-do list.
#:
#: Named once so the ten differ in the ways that matter rather than in whether
#: someone remembered a returns page.
def _standard_pages(
    home: tuple[str, ...],
    *,
    about: tuple[str, ...] = (
        "story_short", "story_image", "story_numbers", "press_row", "three_quotes", "signup",
    ),
    shop: tuple[str, ...] = (
        "category_intro", "best_sellers", "new_arrivals", "three_promises", "closing_cta",
    ),
    landing: tuple[str, ...] = (
        "hero_stated", "product_promises", "three_quotes", "questions", "signup",
    ),
) -> tuple[ThemePage, ...]:
    return (
        ThemePage("Home", "home", "home", home),
        ThemePage("Shop", "shop", "page", shop),
        ThemePage("About", "about", "page", about),
        ThemePage("Frequently asked", "faq", "page", ("questions_contact", "three_promises", "signup")),
        ThemePage("Delivery and returns", "delivery", "page", ("questions", "three_promises", "contact")),
        ThemePage("Contact", "contact", "page", ("contact_hours", "questions", "signup")),
        # Somewhere to point a campaign at, which every shop needs on the day
        # they run their first one and nobody builds before then.
        ThemePage("Offer", "offer", "page", landing),
    )


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="studio",
        name="Studio",
        description="Near-black on warm white. Gets out of the way of photography.",
        suits="Anything photographed well. The safe first choice.",
        imagery="studio",
        style=Aesthetic(scale="display", tracking="tight", rhythm="airy", edge="hairline", measure="normal"),
        colors={
            "primary": "#141414", "accent": "#8d3a63", "background": "#fbfaf8",
            "surface": "#f3f1ed", "text": "#141414", "muted": "#6f6a64", "border": "#e4e0d9",
        },
        fonts={"heading": "Instrument Sans", "body": "Inter"},
        corner_style="soft",
        swatch=("#141414", "#fbfaf8", "#8d3a63"),
        pages=_standard_pages(
            (
                "hero_split",
                "press_row",
                "three_promises",
                "best_sellers",
                "story_image",
                "gallery_three",
                "three_quotes",
                "category_grid",
                "signup_banded",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="press",
        name="Press",
        description="Serif headings on paper. For shops that write as much as they sell.",
        suits="Books, coffee, anything with a story to tell at length.",
        imagery="press",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="normal", edge="none", texture="grain", measure="narrow"),
        colors={
            "primary": "#1c1a17", "accent": "#8a5a2b", "background": "#faf7f0",
            "surface": "#f2ece1", "text": "#1c1a17", "muted": "#6d6459", "border": "#e3dbcc",
        },
        fonts={"heading": "Source Serif 4", "body": "Inter"},
        corner_style="sharp",
        swatch=("#1c1a17", "#faf7f0", "#8a5a2b"),
        pages=_standard_pages(
            (
                "announcement",
                "hero_stated",
                "story_short",
                "best_sellers",
                "single_image",
                "four_features",
                "one_quote",
                "new_arrivals",
                "press_row",
                "signup",
            ),
        ),
    ),
    Preset(
        key="bloom",
        name="Bloom",
        description="Soft, rounded and pale. Suits food, flowers and skincare.",
        suits="Skincare, flowers, food. Anything you want to feel gentle.",
        imagery="bloom",
        style=Aesthetic(scale="normal", tracking="wide", rhythm="airy", edge="none", measure="normal"),
        colors={
            "primary": "#2f2a33", "accent": "#b4527a", "background": "#fdfbfc",
            "surface": "#f8eff3", "text": "#2f2a33", "muted": "#7c7280", "border": "#eddde5",
        },
        fonts={"heading": "Plus Jakarta Sans", "body": "Plus Jakarta Sans"},
        corner_style="round",
        swatch=("#b4527a", "#fdfbfc", "#f8eff3"),
        pages=_standard_pages(
            (
                "hero_image",
                "category_grid",
                "three_promises",
                "best_sellers",
                "story_image",
                "gallery_three",
                "three_quotes",
                "numbers_band",
                "signup_banded",
            ),
        ),
    ),
    Preset(
        key="workshop",
        name="Workshop",
        description="Dark, square and industrial. Tools, parts, hardware.",
        suits="Tools, parts, hardware. Shops whose customers know what they want.",
        imagery="workshop",
        style=Aesthetic(scale="compact", tracking="wide", case="upper", rhythm="tight", edge="bold", texture="grid", measure="wide"),
        colors={
            "primary": "#e8e6e3", "accent": "#d97706", "background": "#17161a",
            "surface": "#211f25", "text": "#e8e6e3", "muted": "#9a958f", "border": "#312e36",
        },
        fonts={"heading": "IBM Plex Mono", "body": "Inter"},
        corner_style="sharp",
        swatch=("#17161a", "#d97706", "#e8e6e3"),
        pages=_standard_pages(
            (
                "announcement",
                "hero_stats",
                "four_features",
                "new_arrivals",
                "feature_image",
                "product_promises",
                "questions",
                "press_row",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="atelier",
        name="Atelier",
        description="High contrast display type. For a small, expensive collection.",
        suits="Jewellery, tailoring, a dozen pieces you want looked at slowly.",
        imagery="atelier",
        style=Aesthetic(scale="poster", tracking="tight", rhythm="vast", edge="none", measure="narrow"),
        colors={
            "primary": "#0f0f0f", "accent": "#3f4d3a", "background": "#ffffff",
            "surface": "#f4f4f2", "text": "#0f0f0f", "muted": "#71716d", "border": "#e6e6e2",
        },
        fonts={"heading": "Playfair Display", "body": "DM Sans"},
        corner_style="sharp",
        swatch=("#0f0f0f", "#ffffff", "#3f4d3a"),
        pages=_standard_pages(
            (
                "hero_image",
                "one_product",
                "story_image",
                "gallery_wide",
                "best_sellers",
                "one_quote",
                "story_numbers",
                "signup_banded",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="market",
        name="Market",
        description="Bright, plain and busy. Built for a wide catalogue.",
        suits="Hundreds of products. Gets people to a category fast.",
        imagery="market",
        style=Aesthetic(scale="compact", tracking="normal", rhythm="tight", edge="hairline", measure="wide"),
        colors={
            "primary": "#12233f", "accent": "#1d6fd0", "background": "#ffffff",
            "surface": "#f4f7fb", "text": "#12233f", "muted": "#657288", "border": "#dde5ef",
        },
        fonts={"heading": "DM Sans", "body": "DM Sans"},
        corner_style="soft",
        swatch=("#1d6fd0", "#ffffff", "#f4f7fb"),
        pages=_standard_pages(
            (
                "announcement",
                "hero_split",
                "category_wide",
                "best_sellers",
                "three_promises",
                "new_arrivals",
                "bundle_offer",
                "three_quotes",
                "numbers_band",
                "signup",
            ),
        ),
    ),
    Preset(
        key="grove",
        name="Grove",
        description="Green and unhurried, with generous space between things.",
        suits="Plants, outdoors, refills. Anything sold on being unhurried.",
        imagery="grove",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="vast", edge="none", measure="normal"),
        colors={
            "primary": "#20301f", "accent": "#4f7a45", "background": "#f8faf6",
            "surface": "#eef3ea", "text": "#20301f", "muted": "#67735f", "border": "#dbe4d5",
        },
        fonts={"heading": "Source Serif 4", "body": "DM Sans"},
        corner_style="round",
        swatch=("#4f7a45", "#f8faf6", "#20301f"),
        pages=_standard_pages(
            (
                "hero_image",
                "three_promises",
                "category_intro",
                "best_sellers",
                "story_image",
                "gallery_three",
                "one_quote",
                "story_banner",
                "signup_banded",
            ),
        ),
    ),
    Preset(
        key="signal",
        name="Signal",
        description="One loud accent on near-white. Modern and direct.",
        suits="One product line sold hard. Electronics, supplements, gear.",
        imagery="signal",
        style=Aesthetic(scale="poster", tracking="tight", case="upper", rhythm="normal", edge="bold", measure="wide"),
        colors={
            "primary": "#101014", "accent": "#e2483d", "background": "#fcfcfd",
            "surface": "#f2f2f5", "text": "#101014", "muted": "#6b6b76", "border": "#e3e3e9",
        },
        fonts={"heading": "Plus Jakarta Sans", "body": "Inter"},
        corner_style="soft",
        swatch=("#e2483d", "#fcfcfd", "#101014"),
        pages=_standard_pages(
            (
                "announcement",
                "hero_stats",
                "one_product",
                "gallery_wide",
                "four_features",
                "product_promises",
                "three_quotes",
                "story_image",
                "sale_band",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="archive",
        name="Archive",
        description="Monospaced labels on grey. Reads like a catalogue, not a shop.",
        suits="Vintage, records, prints. Stock that changes and wants listing.",
        imagery="archive",
        style=Aesthetic(scale="compact", tracking="wide", case="upper", rhythm="tight", edge="hairline", texture="grid", measure="wide"),
        colors={
            "primary": "#1a1a1a", "accent": "#3b5bdb", "background": "#f5f5f4",
            "surface": "#ffffff", "text": "#1a1a1a", "muted": "#6e6e69", "border": "#dedddb",
        },
        fonts={"heading": "IBM Plex Mono", "body": "IBM Plex Mono"},
        corner_style="sharp",
        swatch=("#f5f5f4", "#1a1a1a", "#3b5bdb"),
        pages=_standard_pages(
            (
                "hero_gallery",
                "new_arrivals",
                "category_grid",
                "story_short",
                "gallery_wide",
                "press_row",
                "best_sellers",
                "questions",
                "signup",
            ),
            about=("story_short", "press_row", "signup"),
        ),
    ),
    Preset(
        key="midnight",
        name="Midnight",
        description="Deep blue, low contrast, gold accents. Quiet and expensive.",
        suits="Spirits, watches, anything sold in the evening.",
        imagery="midnight",
        style=Aesthetic(scale="display", tracking="wide", rhythm="vast", edge="none", measure="narrow"),
        colors={
            "primary": "#e9e6df", "accent": "#c9a227", "background": "#0f1420",
            "surface": "#18202f", "text": "#e9e6df", "muted": "#98a1b3", "border": "#263042",
        },
        fonts={"heading": "Playfair Display", "body": "Inter"},
        corner_style="soft",
        swatch=("#0f1420", "#c9a227", "#e9e6df"),
        pages=_standard_pages(
            (
                "hero_image",
                "one_product",
                "story_banner",
                "best_sellers",
                "four_features",
                "one_quote",
                "gallery_three",
                "numbers_band",
                "signup_banded",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="kitchen",
        name="Kitchen",
        description="Near-black with hot red and orange, set loud on cream type. Always open.",
        suits="Food, meal kits, kitchens. Anything sold to be eaten today.",
        imagery="kitchen",
        style=Aesthetic(scale="display", tracking="tight", rhythm="normal", edge="bold", measure="normal"),
        colors={
            "primary": "#e40707", "accent": "#ff6b35", "background": "#0a0b08",
            "surface": "#16180f", "text": "#f3f0e6", "muted": "#a3a695", "border": "#262a1f",
        },
        fonts={"heading": "Plus Jakarta Sans", "body": "Inter"},
        corner_style="round",
        swatch=("#e40707", "#0a0b08", "#ff6b35"),
        pages=_standard_pages(
            (
                "announcement",
                "hero_stated",
                "three_promises",
                "best_sellers",
                "story_image",
                "category_grid",
                "one_quote",
                "numbers_band",
                "signup_banded",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="nursery",
        name="Nursery",
        description="Soft pink on cream, rounded everywhere. Gentle by design.",
        suits="Baby, kids and gifts for new parents. Nothing sharp on the page.",
        imagery="nursery",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="airy", edge="none", measure="narrow"),
        colors={
            "primary": "#33302c", "accent": "#f2a6b5", "background": "#fefbf7",
            "surface": "#fceef0", "text": "#2f2b27", "muted": "#8a8178", "border": "#ecdfd8",
        },
        fonts={"heading": "Libre Baskerville", "body": "Inter"},
        corner_style="round",
        swatch=("#f2a6b5", "#fefbf7", "#33302c"),
        pages=_standard_pages(
            (
                "hero_image",
                "three_promises",
                "best_sellers",
                "story_image",
                "category_grid",
                "one_quote",
                "gallery_three",
                "numbers_band",
                "signup_banded",
            ),
        ),
    ),
    Preset(
        key="pulse",
        name="Pulse",
        description="Black and neon, shot in a dark room. Trains hard, sells harder.",
        suits="Activewear, supplements, gym equipment. Shops that mean business.",
        imagery="pulse",
        style=Aesthetic(scale="poster", tracking="wide", case="upper", rhythm="tight", edge="bold", texture="grid", measure="wide"),
        colors={
            "primary": "#f5f5f0", "accent": "#c6ff3a", "background": "#0b0b0c",
            "surface": "#161617", "text": "#f5f5f0", "muted": "#8f8f8a", "border": "#28282a",
        },
        fonts={"heading": "IBM Plex Mono", "body": "Inter"},
        corner_style="sharp",
        swatch=("#0b0b0c", "#c6ff3a", "#f5f5f0"),
        pages=_standard_pages(
            (
                "announcement",
                "hero_stats",
                "four_features",
                "best_sellers",
                "how_it_works",
                "product_promises",
                "one_quote",
                "press_row",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="gloss",
        name="Gloss",
        description="Blush pink, flat light, everything laid out by hand.",
        suits="Cosmetics, skincare, fragrance. Shops sold on the packaging too.",
        imagery="gloss",
        style=Aesthetic(scale="display", tracking="normal", rhythm="normal", edge="none", measure="normal"),
        colors={
            "primary": "#2b2226", "accent": "#c98a9c", "background": "#fdf7f8",
            "surface": "#f8ebee", "text": "#2b2226", "muted": "#8a7377", "border": "#f0dde1",
        },
        fonts={"heading": "Playfair Display", "body": "DM Sans"},
        corner_style="round",
        swatch=("#c98a9c", "#fdf7f8", "#2b2226"),
        pages=_standard_pages(
            (
                "hero_split",
                "value_grid",
                "best_sellers",
                "story_image",
                "one_quote",
                "gallery_three",
                "category_grid",
                "signup_banded",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="paws",
        name="Paws",
        description="Warm terracotta and cream. Friendly, not precious.",
        suits="Pet food, toys and accessories. Shops for the whole household.",
        imagery="paws",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="normal", edge="hairline", measure="normal"),
        colors={
            "primary": "#2e2620", "accent": "#c9793f", "background": "#fbf6ef",
            "surface": "#f2e6d6", "text": "#2b2318", "muted": "#83745f", "border": "#e6d5bd",
        },
        fonts={"heading": "DM Sans", "body": "DM Sans"},
        corner_style="round",
        swatch=("#c9793f", "#fbf6ef", "#2e2620"),
        pages=_standard_pages(
            (
                "hero_image",
                "three_promises",
                "category_grid",
                "best_sellers",
                "story_short",
                "gallery_three",
                "three_quotes",
                "numbers_band",
                "signup_banded",
            ),
        ),
    ),
    Preset(
        key="haus",
        name="Haus",
        description="Warm neutrals, gallery-hung. Scandinavian and unhurried.",
        suits="Furniture, homeware, interiors. Rooms shown, not just objects.",
        imagery="haus",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="vast", edge="hairline", measure="wide"),
        colors={
            "primary": "#2c2a26", "accent": "#b6875a", "background": "#faf8f4",
            "surface": "#f0ece3", "text": "#282622", "muted": "#7c766b", "border": "#e2dcd0",
        },
        fonts={"heading": "Source Serif 4", "body": "Inter"},
        corner_style="soft",
        swatch=("#2c2a26", "#faf8f4", "#b6875a"),
        pages=_standard_pages(
            (
                "hero_editorial",
                "story_image",
                "feature_image",
                "best_sellers",
                "how_its_made",
                "one_quote",
                "gallery_wide",
                "press_row",
                "signup_banded",
            ),
        ),
    ),
    Preset(
        key="current",
        name="Current",
        description="Deep violet on near-black, one light source. Technical and precise.",
        suits="Audio, music gear, electronics with a fanbase.",
        imagery="current",
        style=Aesthetic(scale="compact", tracking="tight", case="upper", rhythm="tight", edge="hairline", texture="grid", measure="normal"),
        colors={
            "primary": "#e7e6f0", "accent": "#7c5cff", "background": "#0c0b14",
            "surface": "#16141f", "text": "#e7e6f0", "muted": "#928fa3", "border": "#242235",
        },
        fonts={"heading": "Instrument Sans", "body": "Inter"},
        corner_style="sharp",
        swatch=("#0c0b14", "#7c5cff", "#e7e6f0"),
        pages=_standard_pages(
            (
                "hero_stats",
                "one_product",
                "four_features",
                "gallery_wide",
                "product_promises",
                "three_quotes",
                "story_image",
                "numbers_band",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="playroom",
        name="Playroom",
        description="Navy and orange on white. Bold shapes, nothing precious.",
        suits="Toys, games and things built to be dropped.",
        imagery="playroom",
        style=Aesthetic(scale="poster", tracking="normal", rhythm="tight", edge="none", measure="wide"),
        colors={
            "primary": "#12203c", "accent": "#ff5a36", "background": "#fffdf7",
            "surface": "#eef6ff", "text": "#12203c", "muted": "#66707f", "border": "#dbe6f5",
        },
        fonts={"heading": "Plus Jakarta Sans", "body": "DM Sans"},
        corner_style="round",
        swatch=("#ff5a36", "#fffdf7", "#12203c"),
        pages=_standard_pages(
            (
                "hero_image",
                "category_grid",
                "best_sellers",
                "value_grid",
                "gallery_three",
                "new_arrivals",
                "three_quotes",
                "bundle_offer",
                "signup_banded",
            ),
        ),
    ),
    Preset(
        key="confetti",
        name="Confetti",
        description="Pink and mint pastel, held up to a window. Made to be given.",
        suits="Gifts, party supplies, stationery. Shops selling an occasion.",
        imagery="confetti",
        style=Aesthetic(scale="normal", tracking="wide", rhythm="airy", edge="none", measure="wide"),
        colors={
            "primary": "#3a2f36", "accent": "#e8a0c4", "background": "#fffaf9",
            "surface": "#eaf6f0", "text": "#352a30", "muted": "#8a7d84", "border": "#f0dde7",
        },
        fonts={"heading": "Instrument Sans", "body": "DM Sans"},
        corner_style="round",
        swatch=("#e8a0c4", "#fffaf9", "#3a2f36"),
        pages=_standard_pages(
            (
                "hero_split",
                "three_promises",
                "best_sellers",
                "category_intro",
                "gallery_three",
                "one_quote",
                "new_arrivals",
                "signup_banded",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="basecamp",
        name="Basecamp",
        description="Burnt orange on stone, built for weather. Rugged and plain.",
        suits="Camping, hiking and outdoor gear. Kit that gets used.",
        imagery="basecamp",
        style=Aesthetic(scale="compact", tracking="wide", case="upper", rhythm="normal", edge="bold", texture="grain", measure="wide"),
        colors={
            "primary": "#2a2620", "accent": "#d97a2b", "background": "#f6f3ec",
            "surface": "#eae4d5", "text": "#26221c", "muted": "#7a7263", "border": "#ddd4bd",
        },
        fonts={"heading": "IBM Plex Mono", "body": "Inter"},
        corner_style="sharp",
        swatch=("#2a2620", "#f6f3ec", "#d97a2b"),
        pages=_standard_pages(
            (
                "hero_stated",
                "four_features",
                "best_sellers",
                "how_it_works",
                "story_banner",
                "product_promises",
                "press_row",
                "gallery_wide",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="chrome",
        name="Chrome",
        description="White and one blue. Nothing on the page that isn't the product.",
        suits="Electronics, gadgets, tools you plug in. Minimal by default.",
        imagery="chrome",
        style=Aesthetic(scale="compact", tracking="normal", rhythm="airy", edge="hairline", measure="narrow"),
        colors={
            "primary": "#111214", "accent": "#3b82f6", "background": "#ffffff",
            "surface": "#f4f5f7", "text": "#111214", "muted": "#70747c", "border": "#e4e6ea",
        },
        fonts={"heading": "Inter", "body": "Inter"},
        corner_style="sharp",
        swatch=("#111214", "#ffffff", "#3b82f6"),
        pages=_standard_pages(
            (
                "hero_minimal",
                "one_product",
                "three_promises",
                "feature_image",
                "best_sellers",
                "one_quote",
                "story_short",
                "signup",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="linen",
        name="Linen",
        description="Oatmeal and clay on a pale ground. Unhurried, considered, warm.",
        suits="Furniture, textiles, interiors sold on how a room feels.",
        category="home",
        imagery="linen",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="airy", edge="hairline", texture="none", measure="wide"),
        colors={
            "primary": "#3f372c", "accent": "#b57a4f", "background": "#f8f4ec",
            "surface": "#efe7d8", "text": "#3f372c", "muted": "#8a7d69", "border": "#e2d8c3",
        },
        fonts={"heading": "Fraunces", "body": "Inter"},
        corner_style="soft",
        swatch=("#3f372c", "#f8f4ec", "#b57a4f"),
        pages=_standard_pages(
            (
                "hero_editorial",
                "story_image",
                "category_grid",
                "three_promises",
                "gallery_wide",
                "one_quote",
                "best_sellers",
                "press_row",
                "signup",
            ),
        ),
    ),
    Preset(
        key="neon",
        name="Neon",
        description="Black with one electric accent. Loud, fast, unmissable.",
        suits="Gaming gear, audio, anything sold on spec sheets and hype.",
        category="electronics",
        imagery="neon",
        style=Aesthetic(scale="poster", tracking="tight", case="upper", rhythm="tight", edge="bold", measure="normal"),
        colors={
            "primary": "#0a0a0c", "accent": "#39ff88", "background": "#0a0a0c",
            "surface": "#161619", "text": "#f5f7f5", "muted": "#8b9089", "border": "#2a2b2e",
        },
        fonts={"heading": "Space Grotesk", "body": "Inter"},
        corner_style="sharp",
        swatch=("#0a0a0c", "#39ff88", "#f5f7f5"),
        pages=_standard_pages(
            (
                "hero_stats",
                "one_product",
                "numbers_band",
                "best_sellers",
                "four_features",
                "story_short",
                "single_image",
                "closing_cta",
                "one_quote",
            ),
        ),
    ),
    Preset(
        key="terra",
        name="Terra",
        description="Terracotta and cream. Earthy, appetite-driven, hand-made.",
        suits="Bakeries, meal kits, ceramics — anything made by hand.",
        category="food",
        imagery="terra",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="normal", edge="none", texture="grain", measure="normal"),
        colors={
            "primary": "#5a2e1e", "accent": "#c96a3e", "background": "#fbf2e9",
            "surface": "#f3e3d2", "text": "#40241a", "muted": "#8a6b58", "border": "#e6d0ba",
        },
        fonts={"heading": "Fraunces", "body": "Inter"},
        corner_style="round",
        swatch=("#5a2e1e", "#fbf2e9", "#c96a3e"),
        pages=_standard_pages(
            (
                "hero_image",
                "category_intro",
                "best_sellers",
                "how_its_made",
                "story_short",
                "three_quotes",
                "press_row",
                "signup_banded",
                "four_features",
            ),
        ),
    ),
    Preset(
        key="velvet",
        name="Velvet",
        description="Deep plum on cream, set in a tall serif. Slow luxury.",
        suits="Jewellery, occasionwear, anything that should feel expensive.",
        category="fashion",
        imagery="velvet",
        style=Aesthetic(scale="display", tracking="tight", rhythm="vast", edge="hairline", measure="narrow"),
        colors={
            "primary": "#2c0f24", "accent": "#c9a15a", "background": "#faf6f3",
            "surface": "#f0e6df", "text": "#2c0f24", "muted": "#8c7a83", "border": "#e3d6cd",
        },
        fonts={"heading": "Cormorant Garamond", "body": "Inter"},
        corner_style="sharp",
        swatch=("#2c0f24", "#faf6f3", "#c9a15a"),
        pages=_standard_pages(
            (
                "hero_editorial",
                "single_image",
                "one_quote",
                "category_wide",
                "story_image",
                "press_row",
                "three_promises",
                "signup",
                "closing_cta",
            ),
        ),
    ),
    Preset(
        key="meadow",
        name="Meadow",
        description="Sage and cream, soft-edged. For anything grown, not made.",
        suits="Plants, gardens, refills, anything sold on being unhurried.",
        category="outdoors",
        imagery="meadow",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="airy", edge="none", measure="wide"),
        colors={
            "primary": "#2e3b2a", "accent": "#6f8f5a", "background": "#f6f7f0",
            "surface": "#eaeee1", "text": "#2e3b2a", "muted": "#748169", "border": "#dbe1cd",
        },
        fonts={"heading": "Fraunces", "body": "Inter"},
        corner_style="round",
        swatch=("#2e3b2a", "#f6f7f0", "#6f8f5a"),
        pages=_standard_pages(
            (
                "hero_split",
                "value_grid",
                "best_sellers",
                "story_image",
                "how_its_made",
                "three_quotes",
                "closing_cta",
                "signup",
                "press_row",
            ),
        ),
    ),
    Preset(
        key="arcade",
        name="Arcade",
        description="Primary colours, rounded and bold. Built to make kids point.",
        suits="Toys, games, anything for kids or the people buying for them.",
        category="kids",
        imagery="arcade",
        style=Aesthetic(scale="display", tracking="normal", rhythm="normal", edge="bold", measure="normal"),
        colors={
            "primary": "#1c1c1e", "accent": "#ff5a3c", "background": "#fffaf0",
            "surface": "#ffe9d6", "text": "#1c1c1e", "muted": "#7a7268", "border": "#f6d9b8",
        },
        fonts={"heading": "Baloo 2", "body": "Inter"},
        corner_style="round",
        swatch=("#1c1c1e", "#fffaf0", "#ff5a3c"),
        pages=_standard_pages(
            (
                "hero_stated",
                "category_grid",
                "best_sellers",
                "four_features",
                "gallery_three",
                "story_short",
                "signup",
                "closing_cta",
                "press_row",
            ),
        ),
    ),
    Preset(
        key="slate",
        name="Slate",
        description="Cool grey and steel blue. Industrial, exact, no ornament.",
        suits="Parts, tools, anything bought by spec rather than by feel.",
        category="hardware",
        imagery="slate",
        style=Aesthetic(scale="compact", tracking="normal", rhythm="tight", edge="bold", measure="narrow"),
        colors={
            "primary": "#1e242b", "accent": "#4c7a9c", "background": "#f2f4f6",
            "surface": "#e4e8ec", "text": "#1e242b", "muted": "#697682", "border": "#d3dbe1",
        },
        fonts={"heading": "IBM Plex Mono", "body": "Inter"},
        corner_style="sharp",
        swatch=("#1e242b", "#f2f4f6", "#4c7a9c"),
        pages=_standard_pages(
            (
                "hero_product",
                "product_row_three",
                "four_features",
                "best_sellers",
                "how_it_works",
                "gallery_wide",
                "story_short",
                "closing_cta",
                "signup",
            ),
        ),
    ),
    Preset(
        key="coral",
        name="Coral",
        description="Warm coral and cream, softly rounded. Cosmetics, sold gently.",
        suits="Skincare, fragrance, cosmetics sold on packaging and feel.",
        category="beauty",
        imagery="coral",
        style=Aesthetic(scale="normal", tracking="normal", rhythm="airy", edge="none", measure="normal"),
        colors={
            "primary": "#4a2430", "accent": "#f0806a", "background": "#fdf3ef",
            "surface": "#fbe4dc", "text": "#4a2430", "muted": "#977d80", "border": "#f3d6cb",
        },
        fonts={"heading": "Fraunces", "body": "Inter"},
        corner_style="round",
        swatch=("#4a2430", "#fdf3ef", "#f0806a"),
        pages=_standard_pages(
            (
                "hero_image",
                "value_grid",
                "best_sellers",
                "one_quote",
                "gallery_three",
                "story_short",
                "press_row",
                "signup",
                "closing_cta",
            ),
        ),
    ),
)

#: Every category a template can be tagged with, in the order the onboarding
#: picker shows its filter chips.
CATEGORIES: tuple[str, ...] = (
    "general", "fashion", "beauty", "food", "home", "kids",
    "electronics", "outdoors", "pets", "gifts", "fitness", "hardware", "media",
)

#: Categories for the presets above that a plain `general` default undersells.
#: Kept as a lookup rather than a field on every literal so tagging twenty-one
#: existing templates was one table, not twenty-one edits.
_CATEGORY_BY_KEY: dict[str, str] = {
    "press": "media",
    "bloom": "beauty",
    "workshop": "hardware",
    "atelier": "fashion",
    "grove": "outdoors",
    "signal": "electronics",
    "archive": "media",
    "midnight": "fashion",
    "kitchen": "food",
    "nursery": "kids",
    "pulse": "fitness",
    "gloss": "beauty",
    "paws": "pets",
    "haus": "home",
    "current": "electronics",
    "playroom": "kids",
    "confetti": "gifts",
    "basecamp": "outdoors",
    "chrome": "electronics",
}

PRESETS = tuple(
    replace(preset, category=_CATEGORY_BY_KEY.get(preset.key, preset.category))
    for preset in PRESETS
)

_BY_KEY = {preset.key: preset for preset in PRESETS}

#: The colour slots a theme has. Named here so the route, the picker and the
#: storefront agree on the set — adding an eighth colour is one line, not four.
COLOR_SLOTS = ("primary", "accent", "background", "surface", "text", "muted", "border")

CORNER_STYLES = ("sharp", "soft", "round")


# -- colour palettes ---------------------------------------------------------
#
# A palette is *just* colour, type and corners — not a site. The ten `PRESETS`
# above are whole shops and live on the template gallery; these are the swatch a
# merchant flips through beside the page they are building, and applying one
# never touches a single block. Thirty of them because "nearly the right blue"
# is a real complaint and the fix should be one click, not the custom editor.


@dataclass(frozen=True, slots=True)
class ColourPalette:
    key: str
    name: str
    description: str
    colors: dict[str, str]
    fonts: dict[str, str]
    corner_style: str

    @property
    def swatch(self) -> tuple[str, str, str]:
        # Accent against the ground, then the primary — what the eye reads a
        # palette by. See the note on `Preset.swatch`.
        return (self.colors["accent"], self.colors["background"], self.colors["primary"])


def _pal(
    key: str,
    name: str,
    description: str,
    *,
    primary: str,
    accent: str,
    background: str,
    surface: str,
    text: str,
    muted: str,
    border: str,
    heading: str = "Inter",
    body: str = "Inter",
    corner: str = "soft",
) -> ColourPalette:
    return ColourPalette(
        key=key,
        name=name,
        description=description,
        colors={
            "primary": primary, "accent": accent, "background": background,
            "surface": surface, "text": text, "muted": muted, "border": border,
        },
        fonts={"heading": heading, "body": body},
        corner_style=corner,
    )


PALETTES: tuple[ColourPalette, ...] = (
    # neutrals ------------------------------------------------------------
    _pal("studio_mono", "Studio", "Near-black on warm white. Photography first.",
         primary="#141414", accent="#8d3a63", background="#fbfaf8", surface="#f3f1ed",
         text="#141414", muted="#6f6a64", border="#e4e0d9",
         heading="Instrument Sans", corner="soft"),
    _pal("ink", "Ink", "True black on white, hard edges. Nothing decorative.",
         primary="#000000", accent="#1a1a1a", background="#ffffff", surface="#f4f4f4",
         text="#0a0a0a", muted="#666666", border="#e2e2e2",
         heading="Inter", corner="sharp"),
    _pal("paper", "Paper", "Serif headings on cream. For shops that write.",
         primary="#1c1a17", accent="#8a5a2b", background="#faf7f0", surface="#f2ece1",
         text="#1c1a17", muted="#6d6459", border="#e3dbcc",
         heading="Source Serif 4", body="Inter", corner="sharp"),
    _pal("bone", "Bone", "Warm greige, low contrast, calm.",
         primary="#3a352f", accent="#9c6f4a", background="#f6f2ec", surface="#ede7dd",
         text="#332f2a", muted="#7c7368", border="#ddd4c6", corner="soft"),
    _pal("fog", "Fog", "Cool neutral greys. Quiet and modern.",
         primary="#2b2f36", accent="#4b5563", background="#f7f8f9", surface="#eef0f2",
         text="#20242b", muted="#727a85", border="#e0e3e7",
         heading="DM Sans", corner="soft"),
    _pal("linen", "Linen", "Soft ivory with a taupe accent.",
         primary="#33302b", accent="#a08262", background="#f8f5ef", surface="#efe9df",
         text="#2f2c27", muted="#82786a", border="#e4ddce",
         heading="Libre Baskerville", body="Inter", corner="round"),
    _pal("porcelain", "Porcelain", "Bright white, faint blue-grey shadows.",
         primary="#1f2933", accent="#3d5a80", background="#ffffff", surface="#f2f5f8",
         text="#1b232b", muted="#6b7785", border="#e3e8ee",
         heading="Plus Jakarta Sans", corner="soft"),
    # cool --------------------------------------------------------------
    _pal("slate", "Slate", "Blue-grey through and through.",
         primary="#334155", accent="#0ea5e9", background="#f8fafc", surface="#eef2f7",
         text="#1e293b", muted="#64748b", border="#dbe3ec",
         heading="DM Sans", corner="soft"),
    _pal("denim", "Denim", "Indigo on pale blue. Workwear.",
         primary="#26355c", accent="#3b5bdb", background="#f4f6fb", surface="#e8ecf6",
         text="#1f2a45", muted="#5f6b8a", border="#d6ddec", corner="soft"),
    _pal("cobalt", "Cobalt", "White ground, one loud blue.",
         primary="#111827", accent="#2547ff", background="#ffffff", surface="#f3f5fb",
         text="#0f1522", muted="#5b6472", border="#e3e6ef",
         heading="Instrument Sans", corner="sharp"),
    _pal("ocean", "Ocean", "Teal and deep sea-blue.",
         primary="#0f3d3e", accent="#0f8b8d", background="#f3f8f7", surface="#e5f0ee",
         text="#0c2f30", muted="#5c7877", border="#d3e4e1", corner="round"),
    _pal("mint", "Mint", "Pale green, airy, fresh.",
         primary="#1f3a34", accent="#2fae7f", background="#f2faf6", surface="#e4f4ec",
         text="#1b332e", muted="#5f7d74", border="#d0e8dd",
         heading="Plus Jakarta Sans", corner="round"),
    # green / earth ---------------------------------------------------
    _pal("forest", "Forest", "Deep evergreen, warm cream.",
         primary="#1e3a2b", accent="#3f7d54", background="#f6f4ec", surface="#eae7d9",
         text="#1b3327", muted="#5f7266", border="#dcd8c6",
         heading="Source Serif 4", body="Inter", corner="sharp"),
    _pal("moss", "Moss", "Muted olive and stone.",
         primary="#3a3d2a", accent="#7d8a4f", background="#f6f5ee", surface="#ebe9dd",
         text="#333624", muted="#77785f", border="#dcd9c6", corner="soft"),
    _pal("sage", "Sage", "Soft grey-green, gentle.",
         primary="#3d4a41", accent="#7fa08a", background="#f4f7f3", surface="#e7ede7",
         text="#354036", muted="#748078", border="#d9e2da",
         heading="DM Sans", corner="round"),
    _pal("clay", "Clay", "Earthen brown-orange, hand-thrown.",
         primary="#433024", accent="#b5642f", background="#f7f1e9", surface="#ece0d3",
         text="#3a2b20", muted="#7f6b5b", border="#e0d1bf", corner="round"),
    _pal("camel", "Camel", "Tan and cocoa. Leather goods.",
         primary="#3d2f22", accent="#a9743f", background="#f7f2ea", surface="#ece1d2",
         text="#352920", muted="#7d6b58", border="#e1d4c1",
         heading="Libre Baskerville", body="Inter", corner="sharp"),
    _pal("sand", "Sand", "Desert neutrals, sun-bleached.",
         primary="#4a4033", accent="#c19a6b", background="#faf6ee", surface="#f0e9db",
         text="#403829", muted="#867a65", border="#e6dcc7", corner="soft"),
    # warm / red ------------------------------------------------------
    _pal("ember", "Ember", "Terracotta on warm white.",
         primary="#3a2320", accent="#c65f3c", background="#faf4f0", surface="#f0e3db",
         text="#331f1c", muted="#7f665f", border="#e7d6cd", corner="round"),
    _pal("coral", "Coral", "Bright warm red, playful.",
         primary="#2c1a1a", accent="#f2603c", background="#fff6f3", surface="#ffe8e1",
         text="#241413", muted="#7d5b55", border="#f4d7cd",
         heading="Plus Jakarta Sans", corner="round"),
    _pal("wine", "Wine", "Deep burgundy, dim and rich.",
         primary="#3a1220", accent="#8e2c47", background="#faf3f1", surface="#efe0dd",
         text="#33101c", muted="#7a5860", border="#e6d1d0",
         heading="Playfair Display", body="Inter", corner="sharp"),
    _pal("rose", "Rose", "Dusty pink and warm grey.",
         primary="#3f2d30", accent="#b76e79", background="#faf4f4", surface="#f1e5e6",
         text="#382829", muted="#82706f", border="#e8d8d8",
         heading="Libre Baskerville", body="Inter", corner="round"),
    _pal("blush", "Blush", "Pale pink pastel, soft everywhere.",
         primary="#4a3a3f", accent="#e29aa8", background="#fdf6f7", surface="#fbe9ec",
         text="#403236", muted="#8a757b", border="#f3dde1",
         heading="DM Sans", corner="round"),
    _pal("berry", "Berry", "Magenta-plum accent on light.",
         primary="#2a1830", accent="#a5308a", background="#faf5fa", surface="#efe2ee",
         text="#251529", muted="#75627a", border="#e6d5e6", corner="soft"),
    # yellow / bright ------------------------------------------------
    _pal("honey", "Honey", "Warm amber, inviting.",
         primary="#3a2f18", accent="#d99a2b", background="#fdf8ec", surface="#f6ecd4",
         text="#332a16", muted="#7f7150", border="#ecdfc0", corner="round"),
    _pal("mustard", "Mustard", "Ochre against charcoal text.",
         primary="#2c2a22", accent="#c9a227", background="#faf7ee", surface="#efe9d7",
         text="#26241d", muted="#77725f", border="#e4ddc6",
         heading="Instrument Sans", corner="sharp"),
    _pal("citrus", "Citrus", "Yellow-green pop on white.",
         primary="#20240f", accent="#8bbf20", background="#fbfdf3", surface="#f0f6dd",
         text="#1c1f0d", muted="#6c7355", border="#e2ead0",
         heading="Plus Jakarta Sans", corner="round"),
    # dark ----------------------------------------------------------
    _pal("midnight", "Midnight", "Light text on deep navy.",
         primary="#e8eaf0", accent="#6c8cff", background="#0e1526", surface="#18213a",
         text="#e8eaf0", muted="#9aa3bb", border="#26304c",
         heading="Instrument Sans", corner="soft"),
    _pal("noir", "Noir", "Near-black with a gold line.",
         primary="#f4f1e8", accent="#c9a227", background="#111111", surface="#1c1c1c",
         text="#f4f1e8", muted="#9c968a", border="#2c2c2c",
         heading="Playfair Display", body="Inter", corner="sharp"),
    _pal("graphite", "Graphite", "Cool dark grey, cyan accent.",
         primary="#eef1f3", accent="#38bdf8", background="#15181c", surface="#20242a",
         text="#eef1f3", muted="#93a0ab", border="#2e343c",
         heading="DM Sans", corner="soft"),
)

_PALETTES_BY_KEY = {palette.key: palette for palette in PALETTES}


def palette_props() -> list[dict[str, Any]]:
    """The colour palettes, for the builder's swatch list.

    Colour, type and corners only — no page trees, because a palette is not a
    site. Thirty of them, so "nearly the right shade" is a click rather than a
    trip to the custom editor.
    """
    return [
        {
            "key": palette.key,
            "name": palette.name,
            "description": palette.description,
            "colors": palette.colors,
            "fonts": palette.fonts,
            "corner_style": palette.corner_style,
            "swatch": list(palette.swatch),
        }
        for palette in PALETTES
    ]


async def apply_palette(db: Any, theme: q.Row, key: str) -> q.Row:
    """Recolour the store's theme from a palette. Blocks are never touched (distinct from ``apply_preset``, a whole site)."""
    palette = _PALETTES_BY_KEY.get(key)
    if palette is None:
        raise KeyError(f"No palette {key!r}. Known: {', '.join(sorted(_PALETTES_BY_KEY))}.")
    for slot, value in palette.colors.items():
        theme[f"color_{slot}"] = safe_color(value, theme.get(f"color_{slot}"))
    theme["font_heading"] = safe_font(palette.fonts["heading"], theme.font_heading)
    theme["font_body"] = safe_font(palette.fonts["body"], theme.font_body)
    if palette.corner_style in CORNER_STYLES:
        theme["corner_style"] = palette.corner_style
    theme["name"] = palette.name
    return await q.save(db, "themes", theme)


def template_props(store_name: str = "Our shop") -> list[dict[str, Any]]:
    """The gallery: every template, with its homepage rendered small.

    Carries the *home* tree only. Ten whole sites is most of a megabyte on a
    screen where nine of them will never be applied; the other six pages are
    fetched when someone opens one.
    """
    return [
        {
            **entry,
            "home": next(
                (
                    page["tree"]
                    for page in build_pages(preset, store_name)
                    if page["slug"] == "home"
                ),
                [],
            ),
        }
        for preset, entry in zip(PRESETS, preset_props(), strict=True)
    ]


def template_pages(key: str, store_name: str = "Our shop") -> list[dict[str, Any]]:
    """Every page of one template, as trees. For the detail preview."""
    preset = _BY_KEY.get(key)
    if preset is None:
        raise KeyError(f"No template {key!r}.")
    return build_pages(preset, store_name)


def preset_props() -> list[dict[str, Any]]:
    """The themes, for the picker.

    Carries what a merchant chooses on — the look, what kind of shop it suits,
    and which pages come with it — but not the page trees themselves. Ten
    themes' worth of trees is most of a megabyte on a screen where nine of them
    will never be applied.
    """
    return [
        {
            "key": preset.key,
            "name": preset.name,
            "description": preset.description,
            "suits": preset.suits,
            "category": preset.category,
            "colors": preset.colors,
            "fonts": preset.fonts,
            "corner_style": preset.corner_style,
            "swatch": list(preset.swatch),
            "imagery": preset.imagery,
            "style": asdict(preset.style),
            "pages": [
                {"title": entry.title, "slug": entry.slug, "sections": len(entry.sections)}
                for entry in preset.pages
            ],
            # The homepage's first two real blocks — sanitised, rendered by the
            # same renderer the storefront uses. Not a screenshot and not a
            # redrawn approximation: a merchant picking a template in
            # onboarding sees the actual thing, just the top of it.
            "home_preview": _home_preview(preset),
        }
        for preset in PRESETS
    ]


def _build_home_preview(preset: Preset) -> list[dict[str, Any]]:
    """The first two blocks of a preset's real homepage, for the onboarding
    picker — computed once at import and cached in `_HOME_PREVIEWS` below,
    since a `Preset` holds plain dicts and can't be an `lru_cache` key."""
    home = next((page for page in preset.pages if page.kind == "home"), None)
    if home is None:
        return []
    tree: list[dict[str, Any]] = []
    for key in home.sections[:2]:
        tree.extend(section_presets.build(key, "Your Store", preset.imagery))
    return sanitise(tree)


#: Built once, right after the function above is defined — a `Preset` holds
#: plain dicts, so it can't be an `lru_cache` key, and recomputing this on
#: every load of the onboarding picker would sanitise nine sections of markup
#: for each of twenty-nine templates on every request.
_HOME_PREVIEWS: dict[str, list[dict[str, Any]]] = {preset.key: _build_home_preview(preset) for preset in PRESETS}


def _home_preview(preset: Preset) -> list[dict[str, Any]]:
    return _HOME_PREVIEWS.get(preset.key, [])


def build_pages(preset: Preset, store_name: str) -> list[dict[str, Any]]:
    """A theme's pages, as trees, ready to store.

    Assembled from the section presets and then run through `sanitise` — the
    same door a merchant's own save goes through. A theme is content like any
    other, and content that skipped validation because we wrote it is exactly
    the content nobody checks again.
    """
    built: list[dict[str, Any]] = []
    for entry in preset.pages:
        tree: list[dict[str, Any]] = []
        for key in entry.sections:
            # The template's own imagery set, so a tool shop gets workbenches
            # and a perfumer gets glass — from the same section presets.
            tree.extend(section_presets.build(key, store_name, preset.imagery))
        built.append(
            {
                "title": entry.title,
                "slug": entry.slug,
                "kind": entry.kind,
                "tree": sanitise(tree),
            }
        )
    return built



async def apply_preset(db: Any, theme: q.Row, key: str, *, store: q.Row | None = None, replace_pages: bool = False) -> tuple[q.Row, int]:
    """Apply a theme: its look, and, if asked, its whole site. Returns the theme and how many pages were written.

    The look always applies (only visual values: the logo, announcement, navigation and SEO are the merchant's own and
    survive a change). Pages are opt-in: with ``replace_pages`` off a theme's page is written only where the store has
    nothing at that slug; with it on, every page is overwritten *as a draft*, so nothing reaches a customer until published.
    """
    preset = _BY_KEY.get(key)
    if preset is None:
        raise KeyError(f"No theme preset {key!r}. Known: {', '.join(sorted(_BY_KEY))}.")
    for slot, value in preset.colors.items():
        theme[f"color_{slot}"] = value
    theme["font_heading"] = preset.fonts["heading"]
    theme["font_body"] = preset.fonts["body"]
    theme["corner_style"] = preset.corner_style
    theme["style"] = asdict(preset.style)
    theme["name"] = preset.name
    await q.save(db, "themes", theme)
    if store is None:
        return theme, 0
    written = 0
    for entry in build_pages(preset, store.name):
        existing = await q.first(db, "storefront_pages", {"store_id": store.pk, "slug": entry["slug"]})
        if existing is not None:
            if not replace_pages:
                continue
            await q.update(db, "storefront_pages", existing.pk, {"draft": entry["tree"]})  # into the draft, never straight to what is live
            written += 1
            continue
        slug = entry["slug"]
        if entry["kind"] != "home":
            slug = await unique_slug(db, "storefront_pages", store, slug)
        await q.insert(db, "storefront_pages", {"store_id": store.pk, "title": entry["title"], "slug": slug, "kind": entry["kind"],
                                                 "draft": entry["tree"], "is_published": False, "noindex": False, "settings": {}})
        written += 1
    return theme, written


_TAGS = re.compile(r"<[^>]*>?")


def _plain(value: Any, limit: int) -> str | None:
    """Plain text only. These fields are rendered as text, but they are also read by API clients that may not escape: markup is removed at the door."""
    return _TAGS.sub("", str(value or "")).strip()[:limit] or None


async def save_theme(db: Any, theme: q.Row, store: q.Row, data: dict[str, Any]) -> q.Row:
    """Apply a merchant's own edits. Every value is sanitised on the way in *and* again on the way out (``theme_prop``): this stops a
    bad value being stored, that one stops a value stored before the rule existed from reaching a rendered page."""
    colors = data.get("colors")
    if isinstance(colors, dict):
        for slot in COLOR_SLOTS:
            if slot in colors:
                theme[f"color_{slot}"] = safe_color(colors[slot], theme.get(f"color_{slot}"))
    fonts = data.get("fonts")
    if isinstance(fonts, dict):
        theme["font_heading"] = safe_font(fonts.get("heading"), theme.font_heading)
        theme["font_body"] = safe_font(fonts.get("body"), theme.font_body)
    if data.get("corner_style") in CORNER_STYLES:
        theme["corner_style"] = data["corner_style"]
    if "announcement" in data:
        theme["announcement"] = _plain(data["announcement"], 200)
    if "footer_text" in data:
        theme["footer_text"] = _plain(data["footer_text"], 500)
    if "logo_url" in data:
        theme["logo_url"] = _url_or_none(data["logo_url"])
    for key in ("header_links", "footer_links"):
        links = data.get(key)
        if isinstance(links, list):
            theme[key] = _clean_links(links)
    social = data.get("social_links")
    if isinstance(social, dict):
        theme["social_links"] = {name: url for name, value in social.items() if (url := _url_or_none(value)) and name.isalpha()}
    if isinstance(data.get("name"), str) and _plain(data["name"], 100):
        theme["name"] = _plain(data["name"], 100)
    return await q.save(db, "themes", theme)


def _clean_links(links: list[Any]) -> list[dict[str, str]]:
    """Navigation links, with anything unroutable dropped.

    A link is a label and a URL, and the URL has to be one a browser will
    follow to somewhere we meant — so the same allowlist the page builder uses.
    """
    cleaned: list[dict[str, str]] = []
    for entry in links[:12]:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()[:60]
        url = _url_or_none(entry.get("url"))
        if label and url:
            cleaned.append({"label": label, "url": url})
    return cleaned


def _url_or_none(value: Any) -> str | None:
    """A URL a shop may link to, or nothing.

    `http`, `https`, a root-relative path, `mailto:` and `tel:`. Not
    `javascript:` — a merchant's own footer link is still a script running on
    their customers' machines.
    """
    url = str(value or "").strip()
    if not url:
        return None
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "mailto:", "tel:")) or url.startswith("/"):
        return url[:1000]
    return None


_ = FONTS  # re-exported by intent: the picker reads it through this module
