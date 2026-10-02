"""Prebuilt sections — whole bands of a page, ready to drop in.

A block is a noun: a heading, a button, a grid. A *section* is a paragraph
written in blocks — a hero with its eyebrow, its heading, its copy and its
button already arranged and worded. The difference matters because nobody
builds a page out of nouns. They start from something that already looks like a
page and change the words.

**Every preset is composed of real blocks.** A hero is a `section` holding an
`eyebrow`, a `heading`, a `text` and a `button`, not a `hero` block with four
props. That is the whole design: a merchant can click the button *inside* the
hero and restyle it, move it, or delete it, because it is a block like any
other. A preset made of props would be a preset you can only edit through one
settings panel, and every builder that does it that way is one people outgrow
in a week.

This is the vocabulary two things are written in:

* **Themes** (`app/services/themes.py`) are whole sites assembled from these,
  which is why there are ten themes rather than ten thousand lines of JSON.
* **The builder's Sections panel** offers the same list to a merchant adding a
  band to a page they already have.

One definition serving both is the point. A section that looked one way inside
a theme and another way when dropped in by hand would be two things sharing a
name.

Every preset is a *function* returning a fresh tree, never a shared constant.
A dict returned twice is the same dict, and a merchant editing one page would
edit every other page built from the same preset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import imagery
from .builder import new_block

__all__ = ["GROUPS", "SECTIONS", "SectionPreset", "build", "catalogue"]

#: The twelve shelves a merchant browses. Ordered the way a page is built —
#: what opens it, what sells, what reassures, what closes it — rather than
#: alphabetically, because "which section do I need" is a question about
#: position on the page.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("header", "Headers", "Navigation bars — logo, links, search, cart."),
    ("hero", "Heroes", "The first thing anyone sees."),
    ("feature", "Features", "What it is and why it is good."),
    ("product", "Products", "Grids, single products, collections."),
    ("collection", "Collections", "Ways into your categories."),
    ("story", "Story", "Who you are and how you work."),
    ("proof", "Proof", "Reviews, press, numbers."),
    ("gallery", "Gallery", "Pictures, in a grid or a band."),
    ("offer", "Offers", "Sales, bundles, announcements."),
    ("faq", "Questions", "What people ask before buying."),
    ("signup", "Sign-up", "Email capture."),
    ("contact", "Contact", "How to reach you."),
    ("advanced", "Advanced", "Bigger bands with several parts working together."),
    ("layout", "Layout", "Spacers, dividers, plain bands."),
)

_GROUP_LABELS = {key: label for key, label, _ in GROUPS}


@dataclass(frozen=True, slots=True)
class Context:
    """What a preset is built against.

    The store's name so copy can be about the store, and a set of photographs
    so a template's pictures suit its subject. A preset takes the whole thing
    rather than a name and five URLs, because the next slot added is then one
    field here instead of a signature change in forty-seven functions.
    """

    store: str
    images: imagery.ImagerySet

    @property
    def hero(self) -> str:
        return imagery.hero(self.images.hero)

    @property
    def secondary(self) -> str:
        return imagery.hero(self.images.secondary)

    @property
    def portrait(self) -> str:
        return imagery.portrait(self.images.portrait)

    @property
    def detail(self) -> str:
        return imagery.wide(self.images.detail)

    @property
    def lifestyle(self) -> str:
        return imagery.wide(self.images.lifestyle)

    def tiles(self, count: int = 3, shape: str = "square") -> list[str]:
        """`count` gallery pictures, at one shape."""
        pool = self.images.gallery
        return [imagery.url(pool[index % len(pool)], shape) for index in range(count)]


def context(store_name: str = "Our shop", imagery_set: str = "studio") -> Context:
    return Context(store=store_name, images=imagery.set_for(imagery_set))


@dataclass(frozen=True, slots=True)
class SectionPreset:
    """One ready-made band of a page."""

    key: str
    name: str
    description: str
    #: Which of `GROUPS` it belongs on.
    group: str
    #: Builds the tree, given a `Context`.
    make: Any


def _block(block_type: str, props: dict[str, Any] | None = None, children: list | None = None):
    """A block with its registry defaults, overridden where we care.

    Built through `new_block` rather than as a literal so a preset can never
    drift from the schema: a field renamed in the registry shows up here as a
    default rather than as a key the sanitiser silently drops.
    """
    block = new_block(block_type)
    if props:
        block["props"].update(props)
    if children is not None:
        block["children"] = children
    return block


def _section(props: dict[str, Any], children: list) -> dict[str, Any]:
    return _block("section", props, children)


def _block(block_type: str, props: dict[str, Any] | None = None, children: list | None = None):
    """A block with its registry defaults, overridden where we care.

    Built through `new_block` rather than as a literal so a preset can never
    drift from the schema: a field renamed in the registry shows up here as a
    default rather than as a key the sanitiser silently drops.
    """
    block = new_block(block_type)
    if props:
        block["props"].update(props)
    if children is not None:
        block["children"] = children
    return block


def _section(props: dict[str, Any], children: list) -> dict[str, Any]:
    return _block("section", props, children)


# Presets carry photographs, drawn from the template's own imagery set.
#
# Which pictures depends on the template — see `app/services/imagery.py`. A
# preset asks for a *slot* ("the tall one", "the close one") rather than a
# photograph, so the same Split hero is linen in one template and a workbench
# in another without a second copy of the preset existing.
#
# Every one is an ordinary image field. A merchant replaces it with a picture
# of the thing they actually sell, which outsells stock photography every time;
# the stock is there so the layout is legible before they do.


# -- headers ----------------------------------------------------------------
#
# Each is a `header_bar` holding real parts, so nothing about them is fixed:
# swap the search for a button, move the cart out of the bar, restyle any piece.
# They go at the top of the Header page; the same parts also work anywhere else.


def _bar(children: list, **props: Any) -> dict[str, Any]:
    return _block("header_bar", props, children)


def _row(children: list, **props: Any) -> dict[str, Any]:
    return _block("row", {"wrap": False, "gap": "sm", **props}, children)


def _header_classic(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _block("site_logo"),
        _block("nav_links"),
        _block("flex_space"),
        _block("search_box", {"search_style": "bar"}),
        _block("cart_button"),
    ], justify="start", gap="lg")]


def _header_split(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _row([_block("nav_links", {"collapse": "mobile"})], justify="start", styles="flex-1"),
        _block("site_logo", {"logo_size": "lg"}),
        _row([
            _block("search_box", {"search_style": "icon"}),
            _block("icon_button", {"icon": "user", "label": "Account", "url": "/"}),
            _block("cart_button"),
        ], justify="end", styles="flex-1"),
    ])]


def _header_stacked(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _row([
            _block("search_box", {"search_style": "bar", "width": "md"}),
            _block("site_logo", {"logo_size": "xl"}),
            _block("cart_button"),
        ], justify="between", styles="w-full"),
        _block("nav_links", {"link_style": "caps", "gap": "lg"}),
    ], direction="column", gap="sm", height="auto")]


def _header_minimal(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _row([_block("nav_links", {"collapse": "always"})], justify="start", styles="flex-1"),
        _block("site_logo"),
        _row([_block("cart_button")], justify="end", styles="flex-1"),
    ], border="none")]


def _header_search_first(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _row([
            _block("site_logo"),
            _block("search_box", {"search_style": "bar", "width": "full", "search_placeholder": "What are you looking for?"}),
            _block("icon_button", {"icon": "user", "label": "Account", "url": "/"}),
            _block("cart_button", {"cart_label": "Cart", "cart_style": "outline"}),
        ], gap="md", styles="w-full"),
        _block("nav_links", {"link_style": "underline"}),
    ], direction="column", justify="start", valign="start", gap="sm", height="auto")]


def _header_pill(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _block("site_logo"),
        _block("nav_links", {"link_style": "pill", "gap": "sm"}),
        _block("flex_space"),
        _block("cart_button", {"cart_style": "filled"}),
    ], shape="pill", background="surface", border="shadow", height="compact", justify="start", gap="md")]


def _header_dark(c: Context) -> list[dict[str, Any]]:
    return [_bar([
        _block("site_logo"),
        _block("nav_links", {"link_style": "caps", "text_size": "sm", "gap": "lg"}),
        _block("flex_space"),
        _block("search_box", {"search_style": "icon"}),
        _block("cart_button"),
    ], background="primary", border="none", justify="start", gap="lg")]


def _header_announced(c: Context) -> list[dict[str, Any]]:
    return [
        _block("announcement", {"text": "Free delivery on orders over 50"}),
        *_header_classic(c),
    ]


def _header_floating_cart(c: Context) -> list[dict[str, Any]]:
    return [
        _bar([_block("site_logo"), _block("nav_links"), _block("flex_space"), _block("search_box", {"search_style": "icon"})],
             justify="start", gap="lg"),
        _block("cart_button", {"cart_position": "bottom-right", "cart_style": "filled", "cart_size": "lg"}),
    ]


def _header_logo_only(c: Context) -> list[dict[str, Any]]:
    return [
        _bar([_block("site_logo", {"logo_size": "xl"})], justify="center", height="tall", border="none",
             background="transparent"),
        _block("cart_button", {"cart_position": "top-right", "cart_style": "outline"}),
    ]


# -- heroes -----------------------------------------------------------------
#
# Ten of them, and the variety is the point: a hero is the one section a shop
# is judged on in the first second, and one shape does not suit a workshop, a
# perfumer and a record shop alike.


def _hero_stated(c: Context) -> list[dict[str, Any]]:
    """A plain statement, large. Works for almost any shop."""
    return [
        _section({"padding": "xl", "width": "wide", "min_height": "md"}, [
            _block("eyebrow", {"text": "New collection"}),
            _block("heading", {"text": "Made to last", "level": "h1"}),
            _block("text", {"body": f"A small collection from {c.store}, chosen to be kept "
                                    "rather than replaced.", "size": "lg"}),
            _block("button", {"label": "Shop the collection", "url": "/products"}),
        ])
    ]


def _hero_image(c: Context) -> list[dict[str, Any]]:
    """Text over a full-bleed photograph."""
    return [
        _section({
            "padding": "xl", "width": "wide", "min_height": "lg", "align": "center",
            "background_image": c.hero, "overlay": True,
        }, [
            _block("eyebrow", {"text": "Autumn", "align": "center"}),
            _block("heading", {"text": "The new season", "level": "h1", "align": "center"}),
            _block("text", {"body": "Twelve pieces. Nothing else until spring.",
                            "size": "lg", "align": "center"}),
            _block("button", {"label": "See what's new", "url": "/products", "align": "center"}),
        ])
    ]


def _hero_split(c: Context) -> list[dict[str, Any]]:
    """Words on the left, a picture on the right."""
    return [
        _section({"padding": "xl", "width": "wide", "min_height": "md"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": c.store}),
                    _block("heading", {"text": "Everything we make, made properly", "level": "h1"}),
                    _block("text", {"body": "Free delivery over 50. Thirty-day returns, "
                                            "no questions asked."}),
                    _block("button", {"label": "Browse everything", "url": "/products"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.portrait, "alt": f"{c.store}",
                                     "ratio": "portrait", "rounded": True}),
                ]),
            ]),
        ])
    ]


def _hero_split_reverse(c: Context) -> list[dict[str, Any]]:
    """The same, mirrored. A page with two splits should alternate."""
    return [
        _section({"padding": "xl", "width": "wide", "min_height": "md"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.detail, "alt": "Materials",
                                     "ratio": "wide", "rounded": True}),
                ]),
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("heading", {"text": "Start with the material", "level": "h1"}),
                    _block("text", {"body": f"{c.store} buys from four mills and names all "
                                            "of them. You should know what you are wearing."}),
                    _block("button", {"label": "How we choose", "url": "/pages/about",
                                      "style": "outline"}),
                ]),
            ]),
        ])
    ]


def _hero_minimal(c: Context) -> list[dict[str, Any]]:
    """A line and a link, centred. For a shop that needs no persuading."""
    return [
        _section({"padding": "xl", "width": "narrow", "align": "center", "min_height": "md"}, [
            _block("heading", {"text": c.store, "level": "h1", "align": "center"}),
            _block("text", {"body": "A shop.", "size": "lg", "align": "center"}),
            _block("button", {"label": "Enter", "url": "/products",
                              "style": "link", "align": "center"}),
        ])
    ]


def _hero_with_stats(c: Context) -> list[dict[str, Any]]:
    """A claim, backed with figures underneath it, over a photograph."""
    return [
        _section({"padding": "xl", "width": "wide", "min_height": "md",
                  "background_image": c.hero, "overlay": True}, [
            _block("heading", {"text": "Built once, properly", "level": "h1"}),
            _block("text", {"body": f"{c.store} has been making the same six things since "
                                    "2014, and improving them.", "size": "lg"}),
            _block("button", {"label": "Shop now", "url": "/products"}),
            _block("stats", {"align": "left", "items": [
                {"value": "11 yrs", "label": "Making them"},
                {"value": "40,000", "label": "Sold"},
                {"value": "4.9", "label": "Average review"},
            ]}),
        ])
    ]


def _hero_announcement(c: Context) -> list[dict[str, Any]]:
    """A bar, then the hero. What a sale week looks like."""
    return [
        _block("announcement", {"text": "Winter sale — 25% off everything until Sunday",
                                "url": "/products"}),
        *_hero_stated(c),
    ]


def _hero_product(c: Context) -> list[dict[str, Any]]:
    """One product as the hero. For a shop that sells mostly one thing."""
    return [
        _section({"padding": "xl", "width": "wide", "background": ""}, [
            _block("featured_product", {"eyebrow": "The one to buy", "layout": "split"}),
        ])
    ]


def _hero_video(c: Context) -> list[dict[str, Any]]:
    """A film above the fold."""
    return [
        _section({"padding": "lg", "width": "wide", "align": "center"}, [
            _block("heading", {"text": "See how it is made", "level": "h1", "align": "center"}),
            _block("video", {"url": "", "caption": "Two minutes in the workshop"}),
            _block("button", {"label": "Shop the range", "url": "/products", "align": "center"}),
        ])
    ]


def _hero_gallery(c: Context) -> list[dict[str, Any]]:
    """A wall of pictures, then one line."""
    return [
        _section({"padding": "lg", "width": "wide", "align": "center"}, [
            _block("heading", {"text": "This season", "level": "h1", "align": "center"}),
            _block("gallery", {"columns": 3, "ratio": "portrait", "items": [
                {"image": src, "alt": ""} for src in c.tiles(3, "portrait")
            ]}),
            _block("button", {"label": "Shop it all", "url": "/products", "align": "center"}),
        ])
    ]


def _hero_split_banner(c: Context) -> list[dict[str, Any]]:
    """Two doors, side by side. For a shop with two clear halves."""
    return [
        _section({"padding": "md", "width": "wide"}, [
            _block("split_banner", {"height": "lg", "items": [
                {"image": c.portrait, "eyebrow": "New", "title": "This season",
                 "label": "Shop new in", "url": "/products"},
                {"image": c.detail, "eyebrow": "Always", "title": "The classics",
                 "label": "Shop the range", "url": "/collections"},
            ]}),
        ])
    ]


def _hero_editorial(c: Context) -> list[dict[str, Any]]:
    """A wide photograph with the story sitting over its corner."""
    return [
        _section({"padding": "md", "width": "wide"}, [
            _block("editorial", {
                "image": c.hero,
                "eyebrow": c.store,
                "heading": "Made properly, or not at all",
                "body": "A small collection, made in one workshop, sold direct.",
                "cta_label": "Start here",
                "cta_url": "/products",
                "side": "left",
            }),
        ])
    ]


# -- features ---------------------------------------------------------------


def _three_promises(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("columns", {"count": 3, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "truck", "title": "Free delivery",
                                            "body": "On every order over 50, everywhere."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "refund", "title": "Thirty-day returns",
                                            "body": "No questions, no restocking fee."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "leaf", "title": "Made responsibly",
                                            "body": "Traceable materials, fair factories."}),
                ]),
            ]),
        ])
    ]


def _four_features(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("heading", {"text": "Why people keep buying", "level": "h2"}),
            _block("columns", {"count": 4, "gap": "md"}, [
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "shield", "title": "Two-year guarantee",
                                            "body": "Repaired or replaced."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "clock", "title": "Ships same day",
                                            "body": "Ordered before 2pm."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "globe", "title": "Worldwide",
                                            "body": "Tracked, everywhere."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "star", "title": "Rated 4.9",
                                            "body": "From 3,000 reviews."}),
                ]),
            ]),
        ])
    ]


def _feature_beside_image(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": "How it works"}),
                    _block("heading", {"text": "Three steps and it is yours", "level": "h2"}),
                    _block("icon_feature", {"icon": "check", "title": "Choose",
                                            "body": "Pick a size and a colour."}),
                    _block("icon_feature", {"icon": "truck", "title": "We send it",
                                            "body": f"{c.store} ships within a day."}),
                    _block("icon_feature", {"icon": "refund", "title": "Change your mind",
                                            "body": "Thirty days, on us."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.portrait, "alt": "", "ratio": "portrait",
                                     "rounded": True}),
                ]),
            ]),
        ])
    ]


def _value_grid(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("value_props", {"items": [
                {"title": "Free shipping over 50", "body": "On every order, everywhere."},
                {"title": "Thirty-day returns", "body": "No questions, no restocking fee."},
                {"title": "Made responsibly", "body": "Traceable materials, fair factories."},
            ]}),
        ])
    ]


def _how_it_works(c: Context) -> list[dict[str, Any]]:
    _ = c
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("steps", {"heading": "How it works", "layout": "row", "items": [
                {"title": "Choose", "body": "Pick a size and a colour. Every one is in stock."},
                {"title": "We make it up", "body": "Packed the same day if you order before two."},
                {"title": "Thirty days", "body": "Change your mind and send it back, on us."},
            ]}),
        ])
    ]


def _how_its_made(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.detail, "alt": "", "ratio": "portrait",
                                     "rounded": True}),
                ]),
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("steps", {"heading": "How it's made", "layout": "stack", "items": [
                        {"title": "The material", "body": f"{c.store} buys from four mills "
                                                          "and names all of them."},
                        {"title": "The cut", "body": "Patterns drawn once and kept for years."},
                        {"title": "The check", "body": "Every piece passes six pairs of hands."},
                    ]}),
                ]),
            ]),
        ])
    ]


def _promise_marquee(c: Context) -> list[dict[str, Any]]:
    _ = c
    return [
        _block("marquee", {"speed": "slow", "items": [
            {"text": "Free delivery over 50"},
            {"text": "Thirty-day returns"},
            {"text": "Made in one workshop"},
            {"text": "Two-year guarantee"},
            {"text": "Shipped worldwide"},
        ]}),
    ]


def _two_doors(c: Context) -> list[dict[str, Any]]:
    """A split banner as a mid-page section rather than a hero."""
    return [
        _section({"padding": "md", "width": "wide"}, [
            _block("split_banner", {"height": "md", "items": [
                {"image": c.lifestyle, "eyebrow": "For her", "title": "Womenswear",
                 "label": "Shop", "url": "/collections"},
                {"image": c.secondary, "eyebrow": "For him", "title": "Menswear",
                 "label": "Shop", "url": "/collections"},
            ]}),
        ])
    ]


def _editorial_band(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "md", "width": "wide"}, [
            _block("editorial", {
                "image": c.lifestyle,
                "eyebrow": "The story",
                "heading": "Eleven years in one room",
                "body": f"{c.store} has made the same six things since 2014, "
                        "and improved them every year.",
                "cta_label": "Read more",
                "cta_url": "/pages/about",
                "side": "right",
            }),
        ])
    ]


# -- products ---------------------------------------------------------------


def _best_sellers(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("product_grid", {"heading": "Best sellers", "limit": 4, "columns": 4}),
        ])
    ]


def _new_arrivals(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("eyebrow", {"text": "Just in"}),
            _block("heading", {"text": "New this week", "level": "h2"}),
            _block("product_grid", {"heading": "", "limit": 8, "columns": 4}),
            _block("gallery", {"columns": 4, "ratio": "square", "items": [
                {"image": src, "alt": ""} for src in c.tiles(4)
            ]}),
            _block("button", {"label": "See everything", "url": "/products", "style": "outline"}),
        ])
    ]


def _product_row_three(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("product_grid", {"heading": "Picked for you", "limit": 3, "columns": 3}),
        ])
    ]


def _one_product(c: Context) -> list[dict[str, Any]]:
    _ = c
    return [
        _section({"padding": "xl", "width": "wide", "background": ""}, [
            _block("eyebrow", {"text": "This month"}),
            _block("featured_product", {"eyebrow": "", "layout": "split"}),
        ])
    ]


def _product_with_promises(c: Context) -> list[dict[str, Any]]:
    """A grid, and the reassurance that makes people click it."""
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("product_grid", {"heading": "Shop the range", "limit": 4, "columns": 4}),
            _block("divider", {}),
            _block("columns", {"count": 3, "gap": "md"}, [
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "truck", "title": "Free delivery",
                                            "body": "Over 50.", "align": "center"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "refund", "title": "Easy returns",
                                            "body": "Thirty days.", "align": "center"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("icon_feature", {"icon": "shield", "title": "Guaranteed",
                                            "body": "Two years.", "align": "center"}),
                ]),
            ]),
        ])
    ]


# -- collections ------------------------------------------------------------


def _category_grid(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("collection_list", {"heading": "Shop by category", "limit": 3, "columns": 3}),
        ])
    ]


def _category_wide(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "full"}, [
            _block("collection_list", {"heading": "", "limit": 4, "columns": 4}),
        ])
    ]


def _category_with_intro(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide", "align": "center"}, [
            _block("heading", {"text": "Where to start", "level": "h2", "align": "center"}),
            _block("text", {"body": f"Everything {c.store} makes, in four groups.",
                            "align": "center"}),
            _block("image", {"src": c.lifestyle, "alt": "", "ratio": "wide", "rounded": True}),
            _block("collection_list", {"heading": "", "limit": 4, "columns": 4}),
        ])
    ]


# -- story ------------------------------------------------------------------


def _story_short(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "narrow"}, [
            _block("eyebrow", {"text": "About us"}),
            _block("heading", {"text": "Why we started", "level": "h2"}),
            _block("text", {"body": (
                f"{c.store} began with one problem and a stubborn refusal to solve it "
                "cheaply. We make a small number of things, we make them well, and we "
                "tell you exactly what they are made of and by whom."
            )}),
            _block("image", {"src": c.detail, "alt": "", "ratio": "wide", "rounded": True}),
        ])
    ]


def _story_beside_image(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": "In the workshop"}),
                    _block("heading", {"text": "How it's made", "level": "h2"}),
                    _block("text", {"body": (
                        f"Every {c.store} piece is cut, assembled and checked in one workshop. "
                        "Nothing is finished by a machine that cannot be stopped by hand."
                    )}),
                    _block("button", {"label": "Read more", "url": "/pages/about",
                                      "style": "outline"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.lifestyle, "alt": "Our workshop",
                                     "ratio": "wide", "rounded": True}),
                ]),
            ]),
        ])
    ]


def _story_with_numbers(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "wide", "align": "center"}, [
            _block("heading", {"text": f"{c.store} in numbers", "level": "h2", "align": "center"}),
            _block("stats", {"align": "center", "items": [
                {"value": "2014", "label": "Started"},
                {"value": "6", "label": "People"},
                {"value": "1", "label": "Workshop"},
                {"value": "40k", "label": "Orders"},
            ]}),
        ])
    ]


def _story_banner(c: Context) -> list[dict[str, Any]]:
    """A full-bleed photograph with a line over it."""
    return [
        _section({
            "padding": "xl", "width": "narrow", "align": "center", "min_height": "md",
            "background_image": c.lifestyle, "overlay": True,
        }, [
            _block("heading", {"text": "Six people, one room, eleven years",
                               "level": "h2", "align": "center"}),
            _block("button", {"label": "Read our story", "url": "/pages/about",
                              "style": "outline", "align": "center"}),
        ])
    ]


# -- proof ------------------------------------------------------------------


def _one_quote(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "narrow", "align": "center"}, [
            _block("testimonial", {
                "quote": "Three years of daily use and it still looks new. "
                         "I have bought four as gifts.",
                "author": "Imani B.",
                "role": "Bought the Field Notebook",
            }),
        ])
    ]


def _three_quotes(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("heading", {"text": "What people say", "level": "h2", "align": "center"}),
            _block("columns", {"count": 3, "gap": "md"}, [
                _block("column", {"span": 1}, [
                    _block("testimonial", {"quote": "Exactly as described, and faster than "
                                                    "I expected.", "author": "Cleo M."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("testimonial", {"quote": "I have replaced everything else with "
                                                    "these.", "author": "Kira N."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("testimonial", {"quote": "Worth twice the price.",
                                           "author": "Sam O."}),
                ]),
            ]),
        ])
    ]


def _press_row(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "md", "width": "wide"}, [
            _block("logos", {"heading": "As seen in", "items": [
                {"name": "Kinfolk"}, {"name": "Monocle"},
                {"name": "The Gentlewoman"}, {"name": "Wallpaper*"},
            ]}),
        ])
    ]


def _numbers_band(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("stats", {"align": "center", "items": [
                {"value": "4.9/5", "label": "Average review"},
                {"value": "40,000", "label": "Orders shipped"},
                {"value": "98%", "label": "Would buy again"},
            ]}),
        ])
    ]


# -- gallery ----------------------------------------------------------------


def _gallery_three(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("heading", {"text": "In use", "level": "h2"}),
            _block("gallery", {"columns": 3, "ratio": "square", "items": [
                {"image": src, "alt": ""} for src in c.tiles(3)
            ]}),
        ])
    ]


def _gallery_wide(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "sm", "width": "full"}, [
            _block("gallery", {"columns": 4, "ratio": "square", "items": [
                {"image": src, "alt": ""} for src in c.tiles(4)
            ]}),
        ])
    ]


def _single_image(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "md", "width": "wide"}, [
            _block("image", {"src": c.lifestyle, "alt": "", "ratio": "wide",
                             "rounded": True}),
        ])
    ]


# -- offers -----------------------------------------------------------------


def _announcement_bar(c: Context) -> list[dict[str, Any]]:
    return [_block("announcement", {"text": "Free delivery on orders over 50 · Thirty-day returns"})]


def _sale_band(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "narrow", "align": "center",
                  "background_image": c.detail, "overlay": True}, [
            _block("eyebrow", {"text": "This week only", "align": "center"}),
            _block("heading", {"text": "25% off everything", "level": "h2", "align": "center"}),
            _block("text", {"body": "No code needed. Ends Sunday at midnight.",
                            "align": "center"}),
            _block("button", {"label": "Shop the sale", "url": "/products", "align": "center"}),
        ])
    ]


def _bundle_offer(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": "Save 20"}),
                    _block("heading", {"text": "Buy the set", "level": "h2"}),
                    _block("text", {"body": "All three, for less than two of them "
                                            "bought separately."}),
                    _block("button", {"label": "Get the set", "url": "/products"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.detail, "alt": "", "ratio": "wide",
                                     "rounded": True}),
                ]),
            ]),
        ])
    ]


def _closing_cta(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "narrow", "align": "center"}, [
            _block("heading", {"text": "Ready when you are", "level": "h2", "align": "center"}),
            _block("button", {"label": "Shop everything", "url": "/products", "align": "center"}),
        ])
    ]


# -- questions --------------------------------------------------------------


def _questions(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "narrow"}, [
            _block("heading", {"text": "Questions", "level": "h2"}),
            _block("faq", {"items": [
                {"question": "How long does delivery take?",
                 "answer": "Two to four working days in the UK, five to ten elsewhere."},
                {"question": "Can I return something?",
                 "answer": "Within thirty days, unused, for a full refund including the "
                           "shipping you paid."},
                {"question": "Do you ship internationally?",
                 "answer": "Yes, everywhere we can get a tracked service to."},
            ]}),
        ])
    ]


def _questions_beside_contact(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("heading", {"text": "Questions", "level": "h2"}),
                    _block("faq", {"items": [
                        {"question": "How long does delivery take?",
                         "answer": "Two to four working days."},
                        {"question": "Can I return something?",
                         "answer": "Within thirty days, for a full refund."},
                    ]}),
                ]),
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("image", {"src": c.detail, "alt": "", "ratio": "square",
                                     "rounded": True}),
                    _block("heading", {"text": "Still stuck?", "level": "h3"}),
                    _block("text", {"body": f"Email us and a person at {c.store} will answer."}),
                    _block("button", {"label": "Email us", "url": "mailto:hello@example.com",
                                      "style": "outline"}),
                ]),
            ]),
        ])
    ]


# -- sign-up ----------------------------------------------------------------


def _signup(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "narrow", "align": "center"}, [
            _block("newsletter", {
                "heading": "Stay in touch",
                "body": "One email a month. New pieces, restocks, nothing else.",
                "button_label": "Subscribe",
            }),
        ])
    ]


def _signup_banded(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "narrow", "align": "center",
                  "background_image": c.detail, "overlay": True}, [
            _block("newsletter", {
                "heading": "Ten percent off your first order",
                "body": "Join the list. One email a month, no more.",
                "button_label": "Send me the code",
            }),
        ])
    ]


# -- contact ----------------------------------------------------------------


def _contact(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "narrow"}, [
            _block("heading", {"text": "Get in touch", "level": "h1"}),
            _block("text", {"body": (
                f"Email us and a person at {c.store} will answer — usually the same day, "
                "always within two."
            )}),
            _block("button", {"label": "Email us", "url": "mailto:hello@example.com",
                              "style": "outline"}),
        ])
    ]


def _contact_with_hours(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("heading", {"text": "Come and see us", "level": "h2"}),
                    _block("text", {"body": "12 Long Road, Leeds LS1 1AA\n"
                                            "Tuesday to Saturday, 10 until 6."}),
                    _block("button", {"label": "Directions", "url": "https://maps.google.com",
                                      "style": "outline"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.lifestyle, "alt": "The shop",
                                     "ratio": "wide", "rounded": True}),
                ]),
            ]),
        ])
    ]


# -- layout -----------------------------------------------------------------


def _plain_band(c: Context) -> list[dict[str, Any]]:
    return [_section({"padding": "lg", "width": "wide"}, [])]


def _two_columns(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, []),
                _block("column", {"span": 1}, []),
            ]),
        ])
    ]


def _three_columns(c: Context) -> list[dict[str, Any]]:
    return [
        _section({"padding": "lg", "width": "wide"}, [
            _block("columns", {"count": 3, "gap": "md"}, [
                _block("column", {"span": 1}, []),
                _block("column", {"span": 1}, []),
                _block("column", {"span": 1}, []),
            ]),
        ])
    ]


def _rule(c: Context) -> list[dict[str, Any]]:
    return [_section({"padding": "sm", "width": "wide"}, [_block("divider", {})])]


# -- advanced -------------------------------------------------------------------
#
# Multi-part bands: a photograph and a process, a grid and its reassurance, an
# editorial card sitting above a product row. Each is several sections' worth of
# structure that a merchant would otherwise assemble by hand — dropped in once,
# still fully editable piece by piece.


def _adv_feature_steps(c: Context) -> list[dict[str, Any]]:
    """A tall photograph on one side, a numbered process on the other."""
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("image", {"src": c.portrait, "alt": "", "ratio": "portrait",
                                     "rounded": True}),
                ]),
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": "How it works"}),
                    _block("heading", {"text": "From cart to doorstep", "level": "h2"}),
                    _block("steps", {"heading": "", "layout": "stack", "items": [
                        {"title": "Choose", "body": "Pick a size and a finish. Everything shown is in stock."},
                        {"title": "We pack it", "body": f"{c.store} ships the same day on orders before 2pm."},
                        {"title": "Thirty days", "body": "Not right? Send it back for a full refund, on us."},
                    ]}),
                    _block("button", {"label": "Start shopping", "url": "/products"}),
                ]),
            ]),
        ])
    ]


def _adv_story_stats(c: Context) -> list[dict[str, Any]]:
    """The story in words on the left, the figures and a photo on the right."""
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": "Since 2014"}),
                    _block("heading", {"text": "Small on purpose", "level": "h2"}),
                    _block("text", {"body": (
                        f"{c.store} makes a short list of things and makes them properly. "
                        "One workshop, materials we can name, and no season we are chasing."
                    )}),
                    _block("button", {"label": "Read our story", "url": "/pages/about",
                                      "style": "outline"}),
                ]),
                _block("column", {"span": 1}, [
                    _block("stats", {"align": "left", "items": [
                        {"value": "11 yrs", "label": "In one workshop"},
                        {"value": "6", "label": "People, all told"},
                        {"value": "40k", "label": "Orders shipped"},
                        {"value": "4.9", "label": "Average review"},
                    ]}),
                    _block("image", {"src": c.detail, "alt": "", "ratio": "wide",
                                     "rounded": True}),
                ]),
            ]),
        ])
    ]


def _adv_editorial_products(c: Context) -> list[dict[str, Any]]:
    """An editorial card over a photo, then the products it is talking about."""
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("editorial", {
                "image": c.hero,
                "eyebrow": "This season",
                "heading": "The pieces we keep coming back to",
                "body": "A tight edit for the months ahead — the ones our own team wears.",
                "cta_label": "See the edit",
                "cta_url": "/products",
                "side": "left",
            }),
            _block("product_grid", {"heading": "", "limit": 4, "columns": 4}),
        ])
    ]


def _adv_compare_columns(c: Context) -> list[dict[str, Any]]:
    """Two labelled feature lists side by side — what's included, what's optional."""
    _ = c
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("heading", {"text": "What comes with every order", "level": "h2",
                               "align": "center"}),
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("heading", {"text": "Always", "level": "h4"}),
                    _block("icon_feature", {"icon": "truck", "title": "Tracked delivery",
                                            "body": "Free over 50, everywhere we ship."}),
                    _block("icon_feature", {"icon": "refund", "title": "Thirty-day returns",
                                            "body": "No restocking fee, no questions."}),
                    _block("icon_feature", {"icon": "shield", "title": "Two-year guarantee",
                                            "body": "Repaired or replaced if it fails."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("heading", {"text": "On request", "level": "h4"}),
                    _block("icon_feature", {"icon": "check", "title": "Gift wrapping",
                                            "body": "Recycled paper and a hand-written card."}),
                    _block("icon_feature", {"icon": "clock", "title": "Next-day dispatch",
                                            "body": "Order before noon, pay a little more."}),
                    _block("icon_feature", {"icon": "globe", "title": "Customs handled",
                                            "body": "Duties prepaid to most countries."}),
                ]),
            ]),
        ])
    ]


def _adv_gallery_copy(c: Context) -> list[dict[str, Any]]:
    """A small gallery beside a block of copy and a button."""
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("gallery", {"columns": 2, "ratio": "square", "items": [
                        {"image": src, "alt": ""} for src in c.tiles(4)
                    ]}),
                ]),
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("eyebrow", {"text": "In the wild"}),
                    _block("heading", {"text": "Made to be used, not shelved", "level": "h2"}),
                    _block("text", {"body": (
                        "Photographs from customers, mostly. The patina is the point — "
                        "these get better with a few years on them."
                    )}),
                    _block("button", {"label": "Shop the range", "url": "/products",
                                      "style": "outline"}),
                ]),
            ]),
        ])
    ]


def _adv_proof_stack(c: Context) -> list[dict[str, Any]]:
    """Three reviews, a rule, and a row of press names — a full trust band."""
    _ = c
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("heading", {"text": "Trusted by 40,000 people", "level": "h2",
                               "align": "center"}),
            _block("columns", {"count": 3, "gap": "md"}, [
                _block("column", {"span": 1}, [
                    _block("testimonial", {"quote": "Three years of daily use and it still "
                                                    "looks new.", "author": "Imani B."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("testimonial", {"quote": "I have replaced everything else with "
                                                    "these.", "author": "Kira N."}),
                ]),
                _block("column", {"span": 1}, [
                    _block("testimonial", {"quote": "Worth twice the price, honestly.",
                                           "author": "Sam O."}),
                ]),
            ]),
            _block("divider", {}),
            _block("logos", {"heading": "As seen in", "items": [
                {"name": "Kinfolk"}, {"name": "Monocle"},
                {"name": "The Gentlewoman"}, {"name": "Wallpaper*"},
            ]}),
        ])
    ]


def _adv_cta_numbers(c: Context) -> list[dict[str, Any]]:
    """A dark, centred call to action with three figures under it."""
    return [
        _section({
            "padding": "xl", "width": "narrow", "align": "center",
            "background": "#111111", "text_color": "#f5f5f5",
        }, [
            _block("eyebrow", {"text": "Join the list", "align": "center"}),
            _block("heading", {"text": "Get first pick and 10% off", "level": "h2",
                               "align": "center"}),
            _block("text", {"body": "One email a month. New releases, restocks, nothing else.",
                            "align": "center"}),
            _block("stats", {"align": "center", "items": [
                {"value": "12k", "label": "Subscribers"},
                {"value": "1/mo", "label": "Emails, at most"},
                {"value": "10%", "label": "Off your first order"},
            ]}),
            _block("button", {"label": "Sign up", "url": "/pages/newsletter",
                              "align": "center"}),
        ])
    ]


def _adv_faq_signup(c: Context) -> list[dict[str, Any]]:
    """Common questions on one side, an email sign-up on the other."""
    _ = c
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("columns", {"count": 2, "gap": "lg"}, [
                _block("column", {"span": 1}, [
                    _block("heading", {"text": "Before you ask", "level": "h3"}),
                    _block("faq", {"items": [
                        {"question": "How long is delivery?",
                         "answer": "Two to three working days in the UK, tracked."},
                        {"question": "Can I return it?",
                         "answer": "Within thirty days, unworn, for a full refund."},
                        {"question": "Do you ship abroad?",
                         "answer": "Yes — to most of Europe and North America, duties prepaid."},
                    ]}),
                ]),
                _block("column", {"span": 1, "align": "middle"}, [
                    _block("newsletter", {
                        "heading": "Still deciding?",
                        "body": "Join the list for the occasional email and 10% off your first order.",
                        "button_label": "Subscribe",
                    }),
                ]),
            ]),
        ])
    ]


def _adv_steps_over_photo(c: Context) -> list[dict[str, Any]]:
    """A three-step process laid over a full-width photograph."""
    return [
        _section({
            "padding": "xl", "width": "wide", "align": "center", "min_height": "md",
            "background_image": c.lifestyle, "overlay": True, "text_color": "#ffffff",
        }, [
            _block("heading", {"text": "Three steps, then it's yours", "level": "h2",
                               "align": "center"}),
            _block("steps", {"heading": "", "layout": "row", "items": [
                {"title": "Choose", "body": "Pick a size and a colour."},
                {"title": "We make it up", "body": "Packed the same day, before two."},
                {"title": "Thirty days", "body": "Change your mind, send it back."},
            ]}),
            _block("button", {"label": "Start now", "url": "/products",
                              "style": "outline", "align": "center"}),
        ])
    ]


def _adv_featured_valueprops(c: Context) -> list[dict[str, Any]]:
    """One product shown large, with the promises that close the sale beneath it."""
    _ = c
    return [
        _section({"padding": "xl", "width": "wide"}, [
            _block("eyebrow", {"text": "Product of the month"}),
            _block("featured_product", {"eyebrow": "", "layout": "split"}),
            _block("divider", {}),
            _block("value_props", {"items": [
                {"title": "Free delivery over 50", "body": "Tracked, two to three days."},
                {"title": "Thirty-day returns", "body": "No questions, no restocking fee."},
                {"title": "Guaranteed two years", "body": "We repair it or replace it."},
            ]}),
        ])
    ]


SECTIONS: tuple[SectionPreset, ...] = (
    SectionPreset("header_classic", "Classic", "Logo, links, search bar and cart in one row.", "header", _header_classic),
    SectionPreset("header_split", "Split", "Links left, logo centred, icons right.", "header", _header_split),
    SectionPreset("header_stacked", "Stacked", "Logo centred over a row of small-capital links.", "header", _header_stacked),
    SectionPreset("header_minimal", "Minimal", "A menu button, the logo, the cart.", "header", _header_minimal),
    SectionPreset("header_search_first", "Search first", "A wide search bar between logo and cart, links below.", "header", _header_search_first),
    SectionPreset("header_pill", "Floating pill", "A rounded bar that hovers over the page.", "header", _header_pill),
    SectionPreset("header_dark", "Brand colour", "A solid bar in your brand colour.", "header", _header_dark),
    SectionPreset("header_announced", "With announcement", "A promo line above a classic bar.", "header", _header_announced),
    SectionPreset("header_floating_cart", "Cart outside the bar", "A slim bar, with the cart floating in the corner of every page.", "header", _header_floating_cart),
    SectionPreset("header_logo_only", "Logo only", "Just the logo, centred, with a corner cart.", "header", _header_logo_only),
    SectionPreset("hero_stated", "Statement", "A large claim, a line of copy, one button.", "hero", _hero_stated),
    SectionPreset("hero_image", "Photograph", "Centred over a full-bleed image.", "hero", _hero_image),
    SectionPreset("hero_split", "Split", "Words left, picture right.", "hero", _hero_split),
    SectionPreset("hero_split_reverse", "Split, mirrored", "Picture left, words right.", "hero", _hero_split_reverse),
    SectionPreset("hero_minimal", "Minimal", "A name and a link. Nothing else.", "hero", _hero_minimal),
    SectionPreset("hero_stats", "With numbers", "A claim, backed by three figures.", "hero", _hero_with_stats),
    SectionPreset("hero_announcement", "With a bar", "An announcement above the hero.", "hero", _hero_announcement),
    SectionPreset("hero_product", "One product", "A single product as the opening.", "hero", _hero_product),
    SectionPreset("hero_video", "Video", "A film above the fold.", "hero", _hero_video),
    SectionPreset("hero_gallery", "Gallery", "Three pictures and a line.", "hero", _hero_gallery),
    SectionPreset("hero_split_banner", "Two doors", "Two half-width picture panels.", "hero", _hero_split_banner),
    SectionPreset("hero_editorial", "Editorial", "A card of text over a wide photograph.", "hero", _hero_editorial),

    SectionPreset("three_promises", "Three promises", "Delivery, returns, and what you stand for.", "feature", _three_promises),
    SectionPreset("four_features", "Four features", "A wider row, with icons.", "feature", _four_features),
    SectionPreset("feature_image", "Steps beside an image", "Three steps and a photograph.", "feature", _feature_beside_image),
    SectionPreset("value_grid", "Value grid", "Three short promises, plainly.", "feature", _value_grid),
    SectionPreset("how_it_works", "How it works", "Three numbered steps, across.", "feature", _how_it_works),
    SectionPreset("how_its_made", "How it's made", "Numbered steps beside a photograph.", "feature", _how_its_made),
    SectionPreset("promise_marquee", "Scrolling promises", "A band of phrases moving across.", "feature", _promise_marquee),

    SectionPreset("best_sellers", "Best sellers", "Four products, your top performers.", "product", _best_sellers),
    SectionPreset("new_arrivals", "New in", "Eight products and a link to the rest.", "product", _new_arrivals),
    SectionPreset("product_row_three", "Three products", "A narrower row.", "product", _product_row_three),
    SectionPreset("one_product", "Single product", "One product, large, with its own button.", "product", _one_product),
    SectionPreset("product_promises", "Products and promises", "A grid, with reassurance under it.", "product", _product_with_promises),

    SectionPreset("category_grid", "Category grid", "Your collections as three cards.", "collection", _category_grid),
    SectionPreset("category_wide", "Wide categories", "Four, edge to edge.", "collection", _category_wide),
    SectionPreset("category_intro", "Categories with an intro", "A line of copy above them.", "collection", _category_with_intro),
    SectionPreset("two_doors", "Two doors", "Two picture panels into two halves of the shop.", "collection", _two_doors),

    SectionPreset("story_short", "Short story", "A heading and a paragraph.", "story", _story_short),
    SectionPreset("story_image", "Story beside an image", "Two columns: words and a picture.", "story", _story_beside_image),
    SectionPreset("story_numbers", "Story in numbers", "Four figures about the business.", "story", _story_with_numbers),
    SectionPreset("story_banner", "Photo banner", "One line over a full-width photograph.", "story", _story_banner),
    SectionPreset("editorial_band", "Editorial", "A card of story over a wide photograph.", "story", _editorial_band),

    SectionPreset("one_quote", "One review", "A single testimonial, centred.", "proof", _one_quote),
    SectionPreset("three_quotes", "Three reviews", "A row of short quotes.", "proof", _three_quotes),
    SectionPreset("press_row", "As seen in", "A line of press mentions.", "proof", _press_row),
    SectionPreset("numbers_band", "Numbers", "Three figures that build trust.", "proof", _numbers_band),

    SectionPreset("gallery_three", "Three pictures", "A square grid with a heading.", "gallery", _gallery_three),
    SectionPreset("gallery_wide", "Full-width gallery", "Four pictures, edge to edge.", "gallery", _gallery_wide),
    SectionPreset("single_image", "One picture", "A single wide image.", "gallery", _single_image),

    SectionPreset("announcement", "Announcement bar", "One line across the top.", "offer", _announcement_bar),
    SectionPreset("sale_band", "Sale band", "A dark band with an offer on it.", "offer", _sale_band),
    SectionPreset("bundle_offer", "Bundle", "An offer beside a picture.", "offer", _bundle_offer),
    SectionPreset("closing_cta", "Closing call to action", "A last heading and button.", "offer", _closing_cta),

    SectionPreset("questions", "Questions", "Three common questions answered.", "faq", _questions),
    SectionPreset("questions_contact", "Questions and contact", "Answers beside a way to ask.", "faq", _questions_beside_contact),

    SectionPreset("signup", "Email sign-up", "A newsletter band.", "signup", _signup),
    SectionPreset("signup_banded", "Sign-up over a photo", "An offer for joining the list.", "signup", _signup_banded),

    SectionPreset("contact", "Contact", "A heading, a line of copy, an email button.", "contact", _contact),
    SectionPreset("contact_hours", "Address and hours", "Where you are, beside a picture.", "contact", _contact_with_hours),

    SectionPreset("adv_feature_steps", "Photo and a process", "A tall image beside three numbered steps and a button.", "advanced", _adv_feature_steps),
    SectionPreset("adv_story_stats", "Story and figures", "Copy on the left, four numbers and a photo on the right.", "advanced", _adv_story_stats),
    SectionPreset("adv_editorial_products", "Editorial over a product row", "A story card on a photo, then the products it names.", "advanced", _adv_editorial_products),
    SectionPreset("adv_compare_columns", "Two feature lists", "Included on one side, optional on the other.", "advanced", _adv_compare_columns),
    SectionPreset("adv_gallery_copy", "Gallery and copy", "A four-up gallery beside a paragraph and a button.", "advanced", _adv_gallery_copy),
    SectionPreset("adv_proof_stack", "Full trust band", "Three reviews, a rule, and a row of press names.", "advanced", _adv_proof_stack),
    SectionPreset("adv_cta_numbers", "Dark call to action with numbers", "A centred offer on a dark band, backed by three figures.", "advanced", _adv_cta_numbers),
    SectionPreset("adv_faq_signup", "Questions and sign-up", "Common questions beside an email capture.", "advanced", _adv_faq_signup),
    SectionPreset("adv_steps_over_photo", "Steps over a photograph", "A three-step process laid over a full-width image.", "advanced", _adv_steps_over_photo),
    SectionPreset("adv_featured_valueprops", "Featured product with promises", "One product large, with the reassurance beneath it.", "advanced", _adv_featured_valueprops),

    SectionPreset("plain_band", "Empty band", "A section to fill yourself.", "layout", _plain_band),
    SectionPreset("two_columns", "Two columns", "An empty two-up layout.", "layout", _two_columns),
    SectionPreset("three_columns", "Three columns", "An empty three-up layout.", "layout", _three_columns),
    SectionPreset("rule", "Divider", "A hairline between two bands.", "layout", _rule),
)

_BY_KEY = {preset.key: preset for preset in SECTIONS}


def build(
    key: str, store_name: str = "Our shop", imagery_set: str = "studio"
) -> list[dict[str, Any]]:
    """One preset, as a fresh tree.

    Fresh every time — see the module docstring. Ids are regenerated by
    `new_block`, so two copies of the same section on one page do not collide.
    """
    preset = _BY_KEY.get(key)
    if preset is None:
        raise KeyError(f"No section preset {key!r}. Known: {', '.join(sorted(_BY_KEY))}.")
    return preset.make(context(store_name, imagery_set))


def catalogue(
    store_name: str = "Our shop", imagery_set: str = "studio"
) -> list[dict[str, Any]]:
    """Every preset, with its tree, for the builder's Sections panel.

    The trees come down with the page rather than being fetched when the panel
    opens. They are what the panel *renders* — the preview a merchant browses
    is the section itself, drawn small, not a screenshot of one — so they have
    to be here anyway, and forty-odd small trees is a few tens of kilobytes.
    """
    ctx = context(store_name, imagery_set)
    return [
        {
            "key": preset.key,
            "name": preset.name,
            "description": preset.description,
            "group": preset.group,
            "group_label": _GROUP_LABELS.get(preset.group, preset.group.title()),
            "blocks": preset.make(ctx),
        }
        for preset in SECTIONS
    ]
