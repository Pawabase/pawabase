"""The block registry, and the rules a page tree has to obey.

A builder that trusts its client is a builder that stores whatever the browser
sent — including whatever an attacker sent instead. So the server owns the
schema: what block types exist, what fields each has, what type each field is,
and which blocks may contain which others.

`sanitise` is the single door. It walks an incoming tree and rebuilds it from
the registry rather than filtering it, which is the important distinction:

* An unknown block type is **dropped**, not kept-and-ignored. A tree that
  round-trips a type the renderer has never heard of is a tree that will
  surprise someone later.
* An unknown field is dropped. A known field is coerced to its declared type,
  so `"limit": "abc"` becomes the default rather than reaching a slice.
* A URL field accepts only `http`, `https` and a root-relative path. A
  `javascript:` URL in a merchant's own hero button is stored XSS against their
  own customers.
* Nesting is checked against `accepts`, and depth is capped — a tree nested
  four hundred deep is a stack overflow in whatever renders it.

The registry is also what the builder's UI is generated from, so a new block
type is one entry here plus one renderer. There is no third place to update.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any

__all__ = [
    "BLOCKS",
    "Block",
    "BlockField",
    "MAX_DEPTH",
    "catalogue",
    "default_header",
    "default_tree",
    "expand_legacy",
    "new_block",
    "sanitise",
]

#: How deep a tree may nest. Eight: section, columns, column, box, table, row,
#: cell is seven, and a merchant composing cards inside a grid inside a band
#: reaches six. Still shallow enough that recursion cannot blow up.
MAX_DEPTH = 8

#: Most blocks a page may hold, at any level. A merchant who has hit this has a
#: page nobody will scroll to the bottom of.
MAX_BLOCKS = 300

#: A table's size limits. A size chart is a dozen rows; a thousand is a
#: spreadsheet pasted by mistake.
MAX_TABLE_ROWS = 60
MAX_TABLE_COLUMNS = 12

_URL = re.compile(r"^(https?://|/|mailto:|tel:)", re.I)
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass(frozen=True, slots=True)
class BlockField:
    """One editable setting on a block.

    `kind` drives three things at once: how the value is coerced here, which
    control the inspector renders, and what the renderer can assume. Keeping
    them derived from one declaration is what stops the three drifting.
    """

    name: str
    label: str
    #: text | textarea | richtext | number | boolean | url | image | color |
    #: select | collection | product | list | table
    kind: str = "text"
    default: Any = ""
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()
    #: For `number`.
    minimum: int | None = None
    maximum: int | None = None
    #: For `list` — the fields each entry has.
    fields: tuple[BlockField, ...] = ()


@dataclass(frozen=True, slots=True)
class Block:
    """One block type."""

    type: str
    label: str
    description: str
    #: Groups the block library. `layout`, `content`, `commerce`, `media`.
    group: str
    icon: str
    fields: tuple[BlockField, ...] = ()
    #: Block types this one may contain. Empty means it is a leaf.
    accepts: tuple[str, ...] = ()
    #: Whether a merchant may add it themselves. `column` is placed by its
    #: parent, never dragged in on its own.
    insertable: bool = True
    #: Whether it appears in the builder's Add panel. A table row can be moved,
    #: duplicated and deleted like any block, but it only makes sense inside a
    #: table, so it is reached through one rather than offered on its own.
    listed: bool = True


def _text(name: str, label: str, default: str = "", help: str = "") -> BlockField:
    return BlockField(name, label, "text", default, help)


def _align(default: str = "left", right: bool = False) -> BlockField:
    options = [("left", "Left"), ("center", "Centre")]
    if right:
        options.append(("right", "Right"))
    return BlockField("align", "Align", "select", default, options=tuple(options))


_SIZE = BlockField("size", "Size", "select", "md",
                   options=(("sm", "Small"), ("md", "Medium"), ("lg", "Large")))

_TONES: tuple[tuple[str, str], ...] = (("accent", "Accent"), ("text", "Text"), ("muted", "Muted"))

_GAP = BlockField("gap", "Gap", "select", "md",
                  options=(("none", "None"), ("sm", "Tight"), ("md", "Normal"), ("lg", "Wide")))

#: The glyphs the `icon` and `icon_feature` blocks can draw.
ICON_CHOICES: tuple[tuple[str, str], ...] = (
    ("check", "Tick"), ("truck", "Delivery"), ("refund", "Returns"), ("shield", "Guarantee"),
    ("leaf", "Sustainable"), ("star", "Quality"), ("clock", "Fast"), ("globe", "Worldwide"),
    ("heart", "Heart"), ("gift", "Gift"), ("mail", "Email"), ("phone", "Phone"),
    ("pin", "Location"), ("lock", "Secure"), ("sparkle", "New"), ("arrow", "Arrow"),
)

#: The navbar's layouts. Same fields, genuinely different arrangements — the
#: point is that two stores no longer share one header.
NAVBAR_VARIANTS: tuple[tuple[str, str], ...] = (
    ("classic", "Classic — logo left, links, actions right"),
    ("split", "Split — links left, logo centre, actions right"),
    ("stacked", "Stacked — logo centred, links underneath"),
    ("minimal", "Minimal — logo and a menu button"),
    ("floating", "Floating — a rounded bar that hovers over the page"),
)

#: Icons a merchant can choose for the navbar's buttons.
NAVBAR_CART_ICONS: tuple[tuple[str, str], ...] = (
    ("cart", "Cart"), ("bag", "Bag"), ("basket", "Basket"),
)
NAVBAR_SEARCH_ICONS: tuple[tuple[str, str], ...] = (
    ("search", "Magnifier"), ("search-alt", "Magnifier (bold)"),
)
NAVBAR_MENU_ICONS: tuple[tuple[str, str], ...] = (
    ("menu", "Three lines"), ("menu-alt", "Two lines"),
)
NAVBAR_ACTION_ICONS: tuple[tuple[str, str], ...] = (
    ("user", "Account"), ("heart", "Wishlist"), ("phone", "Phone"), ("mail", "Email"),
    ("pin", "Location"), ("store", "Store"), ("home", "Home"),
)

_STRENGTH = (("sm", "Small"), ("md", "Medium"), ("lg", "Large"))
_BUTTON_STYLES = (("plain", "Plain icon"), ("outline", "Outlined"), ("filled", "Filled"))

# -- placement: where a block sits on the page --------------------------------
#
# Every block carries these, like `styles`, so any block — a button, a banner, a
# cart, a whole section — can be left in the flow, stuck to the top while
# scrolling, or pinned to a corner of its section or of the window. They are not
# in any block's own field list for the same reason `styles` is not: writing
# them onto sixty blocks would be sixty places to forget one. The sanitiser and
# the catalogue add them everywhere.
#
# Bounded on purpose. Offsets are pixels within a range that keeps a block on
# screen, and the layer is capped below the shop header's own, so a fixed block
# can be placed but cannot bury the navigation or the cart.
PLACEMENT_FIELDS: tuple[BlockField, ...] = (
    BlockField("position", "Position", "select", "flow",
               "Flow keeps it in the page. Sticky holds it at the top while you scroll. "
               "Absolute pins it inside its section. Fixed pins it to the window, on every screen.",
               options=(("flow", "In the flow"), ("sticky", "Sticky"),
                        ("absolute", "Absolute — inside its section"), ("fixed", "Fixed — to the window"))),
    BlockField("anchor", "Anchor", "select", "br", "Which corner or edge to measure from.",
               options=(("tl", "Top left"), ("tc", "Top centre"), ("tr", "Top right"),
                        ("bl", "Bottom left"), ("bc", "Bottom centre"), ("br", "Bottom right"),
                        ("center", "Centre"))),
    BlockField("offset_x", "Distance from the side (px)", "number", 20, minimum=-200, maximum=1200),
    BlockField("offset_y", "Distance from top or bottom (px)", "number", 20, minimum=-200, maximum=1200),
    BlockField("layer", "Layer", "number", 10, "Higher sits in front. Capped so it stays behind the header.",
               minimum=0, maximum=25),
    BlockField("show_on", "Show on", "select", "all",
               options=(("all", "Every screen"), ("desktop", "Desktop only"), ("mobile", "Phones only"))),
)

# -- what may go where ------------------------------------------------------
#
# Three families, and every container accepts the same flow of them. That
# uniformity is what makes the primitives composable: a card is a `box` holding
# an image, a heading and a button, and it can sit in a section, a column, a
# grid, a row or another box without a rule written for each pairing.

#: Single elements.
PRIMITIVES: tuple[str, ...] = (
    "heading", "text", "eyebrow", "quote", "link", "button", "badge", "code",
    "image", "video", "audio", "avatar", "icon", "map", "rating",
    "social_links", "callout", "spacer", "divider",
)

#: Blocks that hold other blocks.
CONTAINERS: tuple[str, ...] = ("box", "row", "grid", "columns", "list", "table", "accordion")

#: Whole composed blocks, older than the primitives and still offered.
COMPOSITES: tuple[str, ...] = (
    "product_grid", "collection_list", "featured_product", "newsletter",
    "testimonial", "faq", "stats", "logos", "icon_feature", "gallery",
    "value_props", "split_banner", "editorial", "steps", "marquee", "announcement",
    "navbar",
    "header_bar", "site_logo", "nav_links", "search_box", "cart_button", "icon_button",
    "flex_space",
)

FLOW: tuple[str, ...] = PRIMITIVES + CONTAINERS + COMPOSITES


BLOCKS: dict[str, Block] = {
    # -- layout ------------------------------------------------------------
    "section": Block(
        type="section",
        label="Section",
        description="A full-width band. Everything else goes inside one.",
        group="layout",
        icon="layout",
        # `hero` only so a page saved before heroes were composed keeps its
        # hero until the editor expands it — see `expand_legacy`.
        accepts=FLOW + ("hero",),
        fields=(
            BlockField("background", "Background", "color", "", "Blank inherits the page."),
            BlockField("background_image", "Background image", "image", "",
                       "Turns this band into a hero."),
            BlockField("overlay", "Darken the image", "boolean", True,
                       "Keeps text readable over a busy photo."),
            BlockField("text_color", "Text colour", "color", "",
                       "Blank inherits. Set it light over a dark image."),
            BlockField(
                "min_height", "Height", "select", "auto",
                options=(("auto", "Fits its content"), ("md", "Half screen"),
                         ("lg", "Most of the screen"), ("xl", "Full screen")),
            ),
            BlockField(
                "padding", "Vertical space", "select", "lg",
                options=(("sm", "Tight"), ("md", "Normal"), ("lg", "Generous"), ("xl", "Spacious")),
            ),
            BlockField(
                "width", "Content width", "select", "wide",
                options=(("narrow", "Narrow — reading width"), ("wide", "Wide"), ("full", "Full bleed")),
            ),
            BlockField("align", "Align", "select", "left",
                       options=(("left", "Left"), ("center", "Centre"))),
        ),
    ),
    "columns": Block(
        type="columns",
        label="Columns",
        description="Two or three columns side by side, stacking on a phone.",
        group="layout",
        icon="layout",
        accepts=("column",),
        fields=(
            BlockField("count", "Columns", "number", 2, minimum=2, maximum=4),
            BlockField("gap", "Gap", "select", "md",
                       options=(("sm", "Tight"), ("md", "Normal"), ("lg", "Wide"))),
        ),
    ),
    "column": Block(
        type="column",
        label="Column",
        description="One column of a row.",
        group="layout",
        icon="layout",
        insertable=False,
        accepts=FLOW,
        fields=(
            BlockField("span", "Width", "number", 1, minimum=1, maximum=3),
            BlockField("align", "Align content", "select", "top",
                       options=(("top", "Top"), ("middle", "Middle"), ("bottom", "Bottom"))),
        ),
    ),
    "spacer": Block(
        type="spacer", label="Spacer", description="Vertical breathing room.",
        group="layout", icon="layout",
        fields=(BlockField("height", "Height", "select", "md",
                           options=(("xs", "Tiny"), ("sm", "Small"), ("md", "Medium"),
                                    ("lg", "Large"), ("xl", "Extra large"))),
                BlockField("mobile_hide", "Hide on a phone", "boolean", False)),
    ),
    "divider": Block(
        type="divider", label="Divider", description="A hairline rule.",
        group="layout", icon="layout",
        fields=(
            BlockField("style", "Style", "select", "solid",
                       options=(("solid", "Solid"), ("dashed", "Dashed"), ("dotted", "Dotted"))),
            BlockField("weight", "Thickness", "select", "thin",
                       options=(("thin", "Hairline"), ("thick", "Thick"))),
            BlockField("space", "Space around", "select", "md",
                       options=(("none", "None"), ("sm", "Small"), ("md", "Medium"), ("lg", "Large"))),
        ),
    ),

    # -- content -----------------------------------------------------------
    "hero": Block(
        type="hero",
        label="Hero",
        description="The big opening statement: a small label, a heading, a line and two buttons.",
        group="content",
        icon="image",
        fields=(
            _text("heading", "Heading", "Made to last"),
            BlockField("body", "Body", "textarea", "A small collection of things worth keeping."),
            _text("cta_label", "Button label", "Shop the collection"),
            BlockField("cta_url", "Button link", "url", "/products"),
            BlockField("image", "Background image", "image", ""),
            BlockField("layout", "Layout", "select", "left",
                       options=(("left", "Text left"), ("center", "Text centred"),
                                ("split", "Text beside image"))),
            BlockField("overlay", "Darken image", "boolean", True,
                       help="Keeps text readable over a busy photo."),
            BlockField("height", "Height", "select", "md",
                       options=(("sm", "Compact"), ("md", "Standard"), ("lg", "Full screen"))),
        ),
    ),
    "heading": Block(
        type="heading", label="Heading", description="A section title.",
        group="text", icon="layout",
        fields=(
            _text("text", "Text", "A heading"),
            BlockField("level", "Size", "select", "h2",
                       options=(("h1", "Extra large"), ("h2", "Large"),
                                ("h3", "Medium"), ("h4", "Small"))),
            _align(right=True),
            BlockField("tone", "Colour", "select", "text",
                       options=(("text", "Text"), ("accent", "Accent"), ("muted", "Muted"))),
            BlockField("weight", "Weight", "select", "semibold",
                       options=(("regular", "Regular"), ("medium", "Medium"),
                                ("semibold", "Semibold"), ("bold", "Bold"))),
            BlockField("balance", "Even out line lengths", "boolean", True),
        ),
    ),
    "text": Block(
        type="text", label="Text", description="A paragraph.",
        group="text", icon="layout",
        fields=(
            BlockField("body", "Text", "textarea", ""),
            BlockField("size", "Size", "select", "md",
                       options=(("sm", "Small"), ("md", "Normal"), ("lg", "Large"),
                                ("xl", "Extra large"))),
            _align(right=True),
            BlockField("tone", "Colour", "select", "soft",
                       options=(("soft", "Soft"), ("text", "Full strength"), ("muted", "Muted"),
                                ("accent", "Accent"))),
            BlockField("width", "Line length", "select", "full",
                       options=(("full", "Fill the space"), ("reading", "Comfortable reading"))),
        ),
    ),
    "button": Block(
        type="button", label="Button", description="A call to action.",
        group="text", icon="zap",
        fields=(
            _text("label", "Label", "Shop now"),
            BlockField("url", "Link", "url", "/products"),
            BlockField("style", "Style", "select", "primary",
                       options=(("primary", "Filled"), ("accent", "Accent"), ("outline", "Outline"),
                                ("soft", "Soft"), ("link", "Plain link"))),
            BlockField("size", "Size", "select", "md",
                       options=(("sm", "Small"), ("md", "Medium"), ("lg", "Large"))),
            BlockField("arrow", "Show an arrow", "boolean", False),
            BlockField("full_width", "Full width", "boolean", False),
            _align(right=True),
        ),
    ),
    "value_props": Block(
        type="value_props",
        label="Value propositions",
        description="Two to four short promises, side by side.",
        group="content",
        icon="check",
        fields=(
            BlockField(
                "items", "Items", "list", [],
                fields=(_text("title", "Title"), BlockField("body", "Body", "textarea")),
            ),
        ),
    ),
    "testimonial": Block(
        type="testimonial", label="Testimonial", description="A quote from a customer.",
        group="content", icon="customers",
        fields=(
            BlockField("quote", "Quote", "textarea", ""),
            _text("author", "Attributed to"),
            _text("role", "Their role or location"),
            BlockField("avatar", "Photo", "image", ""),
        ),
    ),
    "faq": Block(
        type="faq", label="Questions", description="Common questions, in a list.",
        group="content", icon="file",
        fields=(
            BlockField(
                "items", "Questions", "list", [],
                fields=(_text("question", "Question"), BlockField("answer", "Answer", "textarea")),
            ),
        ),
    ),
    "newsletter": Block(
        type="newsletter", label="Newsletter", description="An email sign-up.",
        group="content", icon="mail",
        fields=(
            _text("heading", "Heading", "Stay in touch"),
            BlockField("body", "Body", "textarea", "Occasional emails. No noise."),
            _text("button_label", "Button label", "Subscribe"),
        ),
    ),

    # -- media -------------------------------------------------------------
    "image": Block(
        type="image", label="Image", description="A single picture.",
        group="media", icon="image",
        fields=(
            BlockField("src", "Image", "image", ""),
            _text("alt", "Alt text", "", "What it shows, for screen readers."),
            BlockField("url", "Links to", "url", ""),
            BlockField("ratio", "Shape", "select", "auto",
                       options=(("auto", "As uploaded"), ("square", "Square"),
                                ("wide", "16:9"), ("portrait", "4:5"))),
            BlockField("rounded", "Rounded corners", "boolean", True),
            _text("caption", "Caption", ""),
            BlockField("fit", "Fit", "select", "cover",
                       options=(("cover", "Fill the shape"), ("contain", "Show all of it"))),
        ),
    ),
    "video": Block(
        type="video", label="Video", description="An embedded video.",
        group="media", icon="image",
        fields=(
            BlockField("url", "Video URL", "url", "", "A YouTube or Vimeo link."),
            _text("caption", "Caption"),
        ),
    ),

    # -- commerce ----------------------------------------------------------
    "product_grid": Block(
        type="product_grid",
        label="Product grid",
        description="A row of products, drawn from a collection or your best sellers.",
        group="commerce",
        icon="product",
        fields=(
            _text("heading", "Heading", "Featured"),
            BlockField("collection", "From collection", "collection", "",
                       help="Blank uses your best sellers."),
            BlockField("limit", "How many", "number", 4, minimum=1, maximum=12),
            BlockField("columns", "Per row", "number", 4, minimum=2, maximum=6),
            BlockField("show_price", "Show prices", "boolean", True),
        ),
    ),
    "featured_product": Block(
        type="featured_product",
        label="Featured product",
        description="One product, large, with its own buy button.",
        group="commerce",
        icon="product",
        fields=(
            BlockField("product", "Product", "product", ""),
            _text("eyebrow", "Small label above", ""),
            BlockField("layout", "Layout", "select", "split",
                       options=(("split", "Image beside text"), ("stacked", "Image above text"))),
        ),
    ),
    "collection_list": Block(
        type="collection_list",
        label="Collections",
        description="Your categories, as a grid of cards.",
        group="commerce",
        icon="collection",
        fields=(
            _text("heading", "Heading", "Shop by category"),
            BlockField("limit", "How many", "number", 3, minimum=1, maximum=12),
            BlockField("columns", "Per row", "number", 3, minimum=2, maximum=4),
        ),
    ),
    "eyebrow": Block(
        type="eyebrow", label="Eyebrow", description="A small line above a heading.",
        group="text", icon="layout",
        fields=(
            _text("text", "Text", "New this season"),
            BlockField("align", "Align", "select", "left",
                       options=(("left", "Left"), ("center", "Centre"))),
        ),
    ),
    "stats": Block(
        type="stats",
        label="Numbers",
        description="Two to four figures with labels — years, customers, ratings.",
        group="content",
        icon="chart",
        fields=(
            BlockField(
                "items", "Figures", "list", [],
                fields=(_text("value", "Number"), _text("label", "Label")),
            ),
            BlockField("align", "Align", "select", "center",
                       options=(("left", "Left"), ("center", "Centre"))),
        ),
    ),
    "logos": Block(
        type="logos",
        label="Logo row",
        description="Press mentions or stockists, as images or names.",
        group="content",
        icon="image",
        fields=(
            _text("heading", "Heading", ""),
            BlockField(
                "items", "Logos", "list", [],
                fields=(_text("name", "Name"), BlockField("image", "Image", "image")),
            ),
        ),
    ),
    "icon_feature": Block(
        type="icon_feature",
        label="Feature",
        description="One icon, a title and a line of copy. Put three in a row.",
        group="content",
        icon="check",
        fields=(
            BlockField("icon", "Icon", "select", "check",
                       options=(("check", "Tick"), ("truck", "Delivery"), ("refund", "Returns"),
                                ("shield", "Guarantee"), ("leaf", "Sustainable"),
                                ("star", "Quality"), ("clock", "Fast"), ("globe", "Worldwide"))),
            _text("title", "Title", "Free delivery"),
            BlockField("body", "Body", "textarea", "On every order over 50."),
            BlockField("align", "Align", "select", "left",
                       options=(("left", "Left"), ("center", "Centre"))),
        ),
    ),
    "gallery": Block(
        type="gallery",
        label="Gallery",
        description="A grid of pictures.",
        group="media",
        icon="image",
        fields=(
            BlockField(
                "items", "Pictures", "list", [],
                fields=(BlockField("image", "Image", "image"), _text("alt", "Alt text")),
            ),
            BlockField("columns", "Per row", "number", 3, minimum=2, maximum=4),
            BlockField("ratio", "Shape", "select", "square",
                       options=(("square", "Square"), ("wide", "16:9"), ("portrait", "4:5"))),
        ),
    ),
    "split_banner": Block(
        type="split_banner",
        label="Split banner",
        description="Two half-width cards, each a picture with a heading over it.",
        group="content",
        icon="layout",
        fields=(
            BlockField(
                "items", "Panels", "list", [],
                fields=(
                    BlockField("image", "Image", "image"),
                    _text("eyebrow", "Small label"),
                    _text("title", "Heading"),
                    _text("label", "Button label"),
                    BlockField("url", "Links to", "url"),
                ),
            ),
            BlockField("height", "Height", "select", "md",
                       options=(("sm", "Short"), ("md", "Medium"), ("lg", "Tall"))),
        ),
    ),
    "editorial": Block(
        type="editorial",
        label="Editorial",
        description="A wide photograph with a card of text sitting over its corner.",
        group="content",
        icon="image",
        fields=(
            BlockField("image", "Image", "image", ""),
            _text("eyebrow", "Small label", "The story"),
            _text("heading", "Heading", "Cut, sewn and checked by hand"),
            BlockField("body", "Body", "textarea",
                       "Six people in one room, and every piece passes all six."),
            _text("cta_label", "Button label", "Read more"),
            BlockField("cta_url", "Button link", "url", "/pages/about"),
            BlockField("side", "Card position", "select", "left",
                       options=(("left", "Left"), ("right", "Right"))),
        ),
    ),
    "steps": Block(
        type="steps",
        label="Steps",
        description="A numbered process — how it is made, how to order, how returns work.",
        group="content",
        icon="check",
        fields=(
            _text("heading", "Heading", "How it works"),
            BlockField(
                "items", "Steps", "list", [],
                fields=(_text("title", "Title"), BlockField("body", "Body", "textarea")),
            ),
            BlockField("layout", "Layout", "select", "row",
                       options=(("row", "Across"), ("stack", "Down the page"))),
        ),
    ),
    "marquee": Block(
        type="marquee",
        label="Marquee",
        description="A line of short phrases scrolling across a band.",
        group="content",
        icon="megaphone",
        fields=(
            BlockField(
                "items", "Phrases", "list", [],
                fields=(_text("text", "Phrase"),),
            ),
            BlockField("background", "Background", "color", ""),
            BlockField("speed", "Speed", "select", "slow",
                       options=(("slow", "Slow"), ("medium", "Medium"), ("fast", "Fast"))),
        ),
    ),
    "announcement": Block(
        type="announcement",
        label="Announcement",
        description="A single line across the top of the page.",
        group="commerce",
        icon="megaphone",
        fields=(
            _text("text", "Message", "Free delivery on orders over 50"),
            BlockField("url", "Links to", "url", ""),
            BlockField("background", "Background", "color", ""),
        ),
    ),

    "navbar": Block(
        type="navbar",
        label="Navbar",
        description="The shop's header — pick a layout, then choose its logo, links, search and cart.",
        group="commerce",
        icon="layout",
        fields=(
            BlockField("variant", "Layout", "select", "classic", options=NAVBAR_VARIANTS),
            # -- brand
            BlockField("logo_mode", "Logo", "select", "auto",
                       "Auto uses your logo from the Site panel, or your store name.",
                       options=(("auto", "Auto"), ("text", "Text only"), ("image", "Custom image"))),
            _text("logo_text", "Logo text", "", "Overrides the store name when Logo is Text only."),
            BlockField("logo_image", "Logo image", "image", "", "Used when Logo is Custom image."),
            BlockField("logo_size", "Logo size", "select", "md",
                       options=(("sm", "Small"), ("md", "Medium"), ("lg", "Large"))),
            # -- links
            BlockField("links_source", "Links", "select", "site",
                       "Site uses the navigation from the Site panel.",
                       options=(("site", "Use site navigation"), ("custom", "Custom links"))),
            BlockField("links", "Custom links", "list", [], "Used when Links is Custom.", fields=(
                _text("label", "Label"),
                BlockField("url", "Link", "url", ""),
            )),
            BlockField("link_style", "Link style", "select", "plain",
                       options=(("plain", "Plain"), ("underline", "Underline on hover"),
                                ("pill", "Pill on hover"))),
            # -- search
            BlockField("show_search", "Show search", "boolean", True),
            BlockField("search_style", "Search style", "select", "icon",
                       options=(("icon", "Icon button"), ("bar", "Search bar"))),
            BlockField("search_icon", "Search icon", "select", "search", options=NAVBAR_SEARCH_ICONS),
            _text("search_placeholder", "Search placeholder", "Search the shop"),
            # -- cart
            BlockField("show_cart", "Show cart", "boolean", True),
            BlockField("cart_icon", "Cart icon", "select", "cart", options=NAVBAR_CART_ICONS),
            BlockField("show_cart_count", "Show item count", "boolean", True),
            # -- more
            BlockField("menu_icon", "Mobile menu icon", "select", "menu", options=NAVBAR_MENU_ICONS),
            BlockField("actions", "Extra icon buttons", "list", [],
                       "Account, wishlist, phone — anything that deserves an icon.", fields=(
                BlockField("icon", "Icon", "select", "user", options=NAVBAR_ACTION_ICONS),
                _text("label", "Label", "", "Read out to screen readers."),
                BlockField("url", "Link", "url", ""),
            )),
            _text("cta_label", "Button label", "", "Leave blank for no button."),
            BlockField("cta_url", "Button links to", "url", ""),
            # -- look
            BlockField("sticky", "Stay at the top when scrolling", "boolean", True),
            BlockField("background", "Background", "select", "blur",
                       options=(("blur", "Frosted"), ("solid", "Solid"),
                                ("transparent", "Transparent"), ("primary", "Brand colour"))),
            BlockField("border", "Edge", "select", "line",
                       options=(("line", "Line"), ("shadow", "Shadow"), ("none", "None"))),
            BlockField("height", "Height", "select", "regular",
                       options=(("compact", "Compact"), ("regular", "Regular"), ("tall", "Tall"))),
            BlockField("width", "Width", "select", "contained",
                       options=(("contained", "Contained"), ("full", "Full width"))),
        ),
    ),

    # -- header parts ---------------------------------------------------------
    #
    # The navbar above is a ready-made bar. These are the pieces it is made of,
    # each a block in its own right: put them in a `header_bar`, in a section,
    # or on their own anywhere on the page. The cart button in particular is
    # not tied to a navbar — set its position to a corner and it floats over
    # every page of the shop.
    "header_bar": Block(
        type="header_bar",
        label="Header bar",
        description="An empty header you fill yourself — logo, links, search, cart, anything.",
        group="commerce",
        icon="layout",
        accepts=FLOW,
        fields=(
            BlockField("direction", "Arrange", "select", "row",
                       options=(("row", "Side by side"), ("column", "Stacked"))),
            BlockField("justify", "Spread", "select", "between",
                       options=(("start", "Start"), ("center", "Centre"), ("end", "End"),
                                ("between", "Space between"), ("around", "Space around"))),
            BlockField("valign", "Vertical", "select", "center",
                       options=(("start", "Top"), ("center", "Middle"), ("end", "Bottom"))),
            _GAP,
            BlockField("wrap", "Wrap onto new lines", "boolean", False),
            BlockField("height", "Height", "select", "regular",
                       options=(("auto", "Fit contents"), ("compact", "Compact"),
                                ("regular", "Regular"), ("tall", "Tall"))),
            BlockField("width", "Width", "select", "contained",
                       options=(("contained", "Contained"), ("full", "Full width"))),
            BlockField("shape", "Shape", "select", "bar",
                       options=(("bar", "Bar"), ("floating", "Floating card"),
                                ("pill", "Floating pill"))),
            BlockField("sticky", "Stay at the top when scrolling", "boolean", True),
            BlockField("background", "Background", "select", "blur",
                       options=(("blur", "Frosted"), ("solid", "Solid"),
                                ("surface", "Surface colour"), ("transparent", "Transparent"),
                                ("primary", "Brand colour"))),
            BlockField("border", "Edge", "select", "line",
                       options=(("line", "Line"), ("shadow", "Shadow"), ("none", "None"))),
        ),
    ),
    "site_logo": Block(
        type="site_logo",
        label="Logo",
        description="Your logo, or your store name. Links home.",
        group="commerce",
        icon="image",
        fields=(
            BlockField("logo_mode", "Show", "select", "auto",
                       "Auto uses your logo from the Site panel, or your store name.",
                       options=(("auto", "Auto"), ("text", "Text only"), ("image", "Custom image"))),
            _text("logo_text", "Text", "", "Overrides the store name when Show is Text only."),
            BlockField("logo_image", "Image", "image", "", "Used when Show is Custom image."),
            BlockField("logo_size", "Size", "select", "md",
                       options=(("sm", "Small"), ("md", "Medium"), ("lg", "Large"), ("xl", "Extra large"))),
        ),
    ),
    "nav_links": Block(
        type="nav_links",
        label="Navigation links",
        description="Your menu. Collapses into a menu button on a phone.",
        group="commerce",
        icon="link",
        fields=(
            BlockField("links_source", "Links", "select", "site",
                       "Site uses the navigation from the Site panel.",
                       options=(("site", "Use site navigation"), ("custom", "Custom links"))),
            BlockField("links", "Custom links", "list", [], "Used when Links is Custom.", fields=(
                _text("label", "Label"),
                BlockField("url", "Link", "url", ""),
            )),
            BlockField("orientation", "Direction", "select", "horizontal",
                       options=(("horizontal", "Across"), ("vertical", "Down"))),
            BlockField("link_style", "Style", "select", "plain",
                       options=(("plain", "Plain"), ("underline", "Underline on hover"),
                                ("pill", "Pill on hover"), ("caps", "Small capitals"))),
            BlockField("text_size", "Text size", "select", "md", options=_STRENGTH),
            BlockField("gap", "Spacing", "select", "md",
                       options=(("sm", "Tight"), ("md", "Normal"), ("lg", "Wide"))),
            BlockField("collapse", "Collapse into a menu button", "select", "mobile",
                       options=(("mobile", "On phones"), ("always", "Always"), ("never", "Never"))),
            BlockField("menu_icon", "Menu icon", "select", "menu", options=NAVBAR_MENU_ICONS),
        ),
    ),
    "search_box": Block(
        type="search_box",
        label="Search",
        description="A search icon or a full search bar.",
        group="commerce",
        icon="search",
        fields=(
            BlockField("search_style", "Style", "select", "bar",
                       "A bar shrinks to an icon on a phone.",
                       options=(("icon", "Icon button"), ("bar", "Search bar"))),
            BlockField("search_icon", "Icon", "select", "search", options=NAVBAR_SEARCH_ICONS),
            _text("search_placeholder", "Placeholder", "Search the shop"),
            BlockField("width", "Bar width", "select", "md",
                       options=(("sm", "Narrow"), ("md", "Medium"), ("lg", "Wide"), ("full", "Fill space"))),
        ),
    ),
    "cart_button": Block(
        type="cart_button",
        label="Cart",
        description="The basket, with its item count. Place it in the header — or float it in a corner of every page.",
        group="commerce",
        icon="cart",
        fields=(
            BlockField("cart_icon", "Icon", "select", "cart", options=NAVBAR_CART_ICONS),
            BlockField("cart_style", "Style", "select", "plain", options=_BUTTON_STYLES),
            BlockField("cart_size", "Size", "select", "md", options=_STRENGTH),
            _text("cart_label", "Label", "", "Text beside the icon, e.g. Cart."),
            BlockField("show_cart_count", "Show item count", "boolean", True),
            BlockField("cart_position", "Position", "select", "inline",
                       "Anything but Inline floats it over the page, outside any bar.",
                       options=(("inline", "Where I put it"), ("bottom-right", "Floating, bottom right"),
                                ("bottom-left", "Floating, bottom left"),
                                ("top-right", "Floating, top right"), ("top-left", "Floating, top left"))),
        ),
    ),
    "icon_button": Block(
        type="icon_button",
        label="Icon button",
        description="An icon that links somewhere — account, wishlist, phone, location.",
        group="commerce",
        icon="link",
        fields=(
            BlockField("icon", "Icon", "select", "user", options=NAVBAR_ACTION_ICONS),
            _text("label", "Label", "", "Read out to screen readers, and shown if enabled."),
            BlockField("show_label", "Show the label", "boolean", False),
            BlockField("url", "Links to", "url", ""),
            BlockField("icon_style", "Style", "select", "plain", options=_BUTTON_STYLES),
            BlockField("icon_size", "Size", "select", "md", options=_STRENGTH),
        ),
    ),
    "flex_space": Block(
        type="flex_space",
        label="Flexible space",
        description="Pushes what follows to the far side of a bar.",
        group="commerce",
        icon="layout",
        fields=(
            BlockField("size", "Width", "select", "grow",
                       options=(("grow", "Fill the gap"), ("sm", "Small"), ("md", "Medium"), ("lg", "Large"))),
        ),
    ),

    # -- primitives: layout -------------------------------------------------
    "box": Block(
        type="box",
        label="Box",
        description="A group with its own background, padding and corners. Make a card of it.",
        group="layout",
        icon="layout",
        accepts=FLOW,
        fields=(
            BlockField("background", "Background", "color", ""),
            BlockField("background_image", "Background image", "image", ""),
            BlockField("overlay", "Darken the image", "boolean", True),
            BlockField("text_color", "Text colour", "color", ""),
            BlockField("padding", "Padding", "select", "md",
                       options=(("none", "None"), ("sm", "Small"), ("md", "Medium"),
                                ("lg", "Large"), ("xl", "Extra large"))),
            _GAP,
            _align(),
            BlockField("valign", "Content position", "select", "top",
                       options=(("top", "Top"), ("center", "Middle"), ("bottom", "Bottom"))),
            BlockField("min_height", "Height", "select", "auto",
                       options=(("auto", "Fits its content"), ("sm", "Short"),
                                ("md", "Medium"), ("lg", "Tall"))),
            BlockField("max_width", "Maximum width", "select", "none",
                       options=(("none", "Fill the space"), ("sm", "Narrow"),
                                ("md", "Medium"), ("lg", "Wide"))),
            BlockField("radius", "Corners", "select", "theme",
                       options=(("none", "Square"), ("theme", "Theme"), ("lg", "Round"))),
            BlockField("border", "Outline", "boolean", False),
            BlockField("shadow", "Shadow", "select", "none",
                       options=(("none", "None"), ("sm", "Soft"), ("lg", "Lifted"))),
            BlockField("url", "Whole box links to", "url", "",
                       "Makes the entire box clickable — a card that goes somewhere."),
            BlockField("hover", "Lift on hover", "boolean", False),
        ),
    ),
    "row": Block(
        type="row",
        label="Row",
        description="Blocks side by side — two buttons, a badge beside a heading.",
        group="layout",
        icon="layout",
        accepts=FLOW,
        fields=(
            _GAP,
            BlockField("justify", "Spread", "select", "start",
                       options=(("start", "Start"), ("center", "Centre"), ("end", "End"),
                                ("between", "Space between"))),
            BlockField("valign", "Vertical", "select", "center",
                       options=(("start", "Top"), ("center", "Middle"), ("end", "Bottom"),
                                ("stretch", "Stretch"))),
            BlockField("wrap", "Wrap onto new lines", "boolean", True),
            BlockField("stack", "Stack on a phone", "boolean", False),
        ),
    ),
    "grid": Block(
        type="grid",
        label="Grid",
        description="Any blocks, in equal columns. Put boxes in it for a row of cards.",
        group="layout",
        icon="layout",
        accepts=FLOW,
        fields=(
            BlockField("columns", "Per row", "number", 3, minimum=1, maximum=6),
            BlockField("tablet_columns", "Per row on a tablet", "number", 2, minimum=1, maximum=4),
            BlockField("mobile_columns", "Per row on a phone", "number", 1, minimum=1, maximum=3),
            _GAP,
            BlockField("valign", "Line up", "select", "stretch",
                       options=(("stretch", "Same height"), ("start", "Top"),
                                ("center", "Middle"), ("end", "Bottom"))),
        ),
    ),

    # -- primitives: text ---------------------------------------------------
    "quote": Block(
        type="quote", label="Quote", description="A pulled quotation, with who said it.",
        group="text", icon="customers",
        fields=(
            BlockField("text", "Quote", "textarea", "The best thing I have bought this year."),
            _text("cite", "Attributed to", ""),
            _text("cite_detail", "Their role or place", ""),
            BlockField("style", "Style", "select", "bar",
                       options=(("bar", "Rule beside"), ("large", "Large, with marks"),
                                ("boxed", "In a panel"), ("plain", "Plain"))),
            _SIZE,
            _align(),
        ),
    ),
    "link": Block(
        type="link", label="Link", description="A line of text that goes somewhere.",
        group="text", icon="link",
        fields=(
            _text("label", "Label", "Read more"),
            BlockField("url", "Link", "url", "/products"),
            BlockField("arrow", "Show an arrow", "boolean", True),
            BlockField("tone", "Colour", "select", "accent", options=_TONES),
            BlockField("underline", "Underline", "select", "hover",
                       options=(("hover", "On hover"), ("always", "Always"), ("never", "Never"))),
            _SIZE,
            _align(right=True),
        ),
    ),
    "badge": Block(
        type="badge", label="Badge", description="A small pill: New, Sale, Limited.",
        group="text", icon="tag",
        fields=(
            _text("text", "Text", "New"),
            BlockField("tone", "Style", "select", "accent",
                       options=(("accent", "Accent"), ("primary", "Filled"), ("soft", "Soft"),
                                ("outline", "Outline"), ("success", "Green"),
                                ("warning", "Amber"), ("danger", "Red"))),
            BlockField("size", "Size", "select", "sm", options=(("sm", "Small"), ("md", "Medium"))),
            BlockField("shape", "Shape", "select", "pill",
                       options=(("pill", "Pill"), ("rounded", "Rounded"), ("square", "Square"))),
            BlockField("uppercase", "Capitals", "boolean", True),
            _align(right=True),
        ),
    ),
    "code": Block(
        type="code", label="Code", description="Preformatted text — a care code, a size chart key.",
        group="text", icon="code",
        fields=(
            _text("label", "Label above", ""),
            BlockField("code", "Text", "textarea", "WASH 30°  ·  DO NOT TUMBLE"),
            BlockField("size", "Size", "select", "md", options=(("sm", "Small"), ("md", "Normal"))),
            BlockField("wrap", "Wrap long lines", "boolean", True),
        ),
    ),
    "list": Block(
        type="list", label="List", description="Bulleted, numbered or ticked points.",
        group="text", icon="check",
        fields=(
            BlockField(
                "items", "Points", "list",
                [{"text": "Made from organic cotton"}, {"text": "Cut and sewn in Portugal"},
                 {"text": "Free repairs for life"}],
                "Add, reorder and remove points here.",
                fields=(BlockField("text", "Point", "textarea"),),
            ),
            BlockField("marker", "Marker", "select", "check",
                       options=(("bullet", "Bullets"), ("number", "Numbers"), ("check", "Ticks"),
                                ("dash", "Dashes"), ("arrow", "Arrows"), ("none", "None"))),
            BlockField("marker_tone", "Marker colour", "select", "accent", options=_TONES),
            _SIZE,
            BlockField("spacing", "Spacing", "select", "md",
                       options=(("sm", "Tight"), ("md", "Normal"), ("lg", "Loose"))),
            BlockField("columns", "Columns", "number", 1, minimum=1, maximum=3),
            BlockField("dividers", "Lines between points", "boolean", False),
        ),
    ),
    "callout": Block(
        type="callout", label="Callout", description="A highlighted note with a title.",
        group="text", icon="megaphone",
        fields=(
            _text("title", "Title", "Good to know"),
            BlockField("body", "Text", "textarea", "Orders placed before 2pm ship the same day."),
            BlockField("tone", "Tone", "select", "neutral",
                       options=(("neutral", "Neutral"), ("info", "Information"),
                                ("success", "Good news"), ("warning", "Warning"),
                                ("danger", "Important"))),
            BlockField("style", "Style", "select", "soft",
                       options=(("soft", "Tinted"), ("outline", "Outlined"), ("bar", "Rule beside"))),
            BlockField("show_icon", "Show an icon", "boolean", True),
            _text("link_label", "Link label", ""),
            BlockField("link_url", "Link", "url", ""),
        ),
    ),
    "accordion": Block(
        type="accordion", label="Accordion",
        description="A question or title that opens to show an answer.",
        group="text", icon="file",
        accepts=FLOW,
        fields=(
            _text("title", "Title", "What is your returns policy?"),
            BlockField("body", "Answer", "textarea",
                       "Thirty days, no questions, and the return label is on us.",
                       "Blocks dragged inside appear beneath this text."),
            BlockField("open", "Open to start with", "boolean", False),
            BlockField("icon", "Icon", "select", "plus",
                       options=(("plus", "Plus"), ("chevron", "Chevron"), ("none", "None"))),
            BlockField("style", "Style", "select", "lines",
                       options=(("lines", "Lines between"), ("boxed", "In a panel"),
                                ("plain", "Plain"))),
            BlockField("title_size", "Title size", "select", "md", options=_SIZE.options),
            BlockField("background", "Panel colour", "color", "", "Used by the panel style."),
        ),
    ),

    # -- primitives: data ---------------------------------------------------
    "table": Block(
        type="table", label="Table", description="Rows and columns — sizes, specifications, prices.",
        group="data", icon="layout",
        fields=(
            BlockField(
                "rows", "Cells", "table",
                [["Size", "Chest", "Length"], ["S", "86 cm", "68 cm"],
                 ["M", "94 cm", "71 cm"], ["L", "102 cm", "74 cm"]],
                "Type into the cells, or paste straight from a spreadsheet.",
            ),
            _text("caption", "Caption", ""),
            BlockField("header_row", "First row is a heading", "boolean", True),
            BlockField("header_column", "First column is a heading", "boolean", False),
            BlockField("lines", "Lines", "select", "rows",
                       options=(("rows", "Between rows"), ("all", "Every cell"), ("none", "None"))),
            BlockField("striped", "Striped rows", "boolean", False),
            BlockField("header_style", "Heading row", "select", "tinted",
                       options=(("tinted", "Tinted"), ("filled", "Filled"), ("plain", "Plain"))),
            BlockField("size", "Size", "select", "md",
                       options=(("sm", "Compact"), ("md", "Normal"), ("lg", "Roomy"))),
            _align(right=True),
            BlockField("outline", "Outline", "boolean", True),
        ),
    ),
    "rating": Block(
        type="rating", label="Rating", description="Stars out of five, with a count.",
        group="data", icon="check",
        fields=(
            BlockField("value", "Stars", "number", 5, minimum=0, maximum=5),
            _text("label", "Beside the stars", "4.9 from 212 reviews"),
            BlockField("tone", "Star colour", "select", "gold",
                       options=(("gold", "Gold"), ("accent", "Accent"), ("text", "Text"))),
            _SIZE,
            _align(right=True),
        ),
    ),

    # -- primitives: media --------------------------------------------------
    "icon": Block(
        type="icon", label="Icon", description="A single glyph.",
        group="media", icon="check",
        fields=(
            BlockField("icon", "Icon", "select", "star", options=ICON_CHOICES),
            BlockField("size", "Size", "select", "md",
                       options=(("sm", "Small"), ("md", "Medium"), ("lg", "Large"), ("xl", "Huge"))),
            BlockField("shape", "Backdrop", "select", "circle",
                       options=(("circle", "Circle"), ("square", "Rounded square"), ("none", "None"))),
            BlockField("tone", "Colour", "select", "accent", options=_TONES),
            _align(right=True),
        ),
    ),
    "avatar": Block(
        type="avatar", label="Avatar", description="A round photo with a name beside it.",
        group="media", icon="customers",
        fields=(
            BlockField("src", "Photo", "image", ""),
            _text("name", "Name", "Ada Lovelace"),
            _text("subtitle", "Beneath the name", "Founder"),
            BlockField("size", "Size", "select", "md",
                       options=(("sm", "Small"), ("md", "Medium"), ("lg", "Large"))),
            BlockField("layout", "Layout", "select", "row",
                       options=(("row", "Name beside"), ("stacked", "Name below"))),
            _align(),
        ),
    ),
    "audio": Block(
        type="audio", label="Audio", description="A sound file with a player.",
        group="media", icon="image",
        fields=(
            BlockField("url", "Audio file URL", "url", "", "An https link to an mp3 or ogg file."),
            _text("title", "Title", ""),
            _text("caption", "Caption"),
            BlockField("loop", "Loop", "boolean", False),
        ),
    ),
    "map": Block(
        type="map", label="Map", description="Where to find you, from an address.",
        group="media", icon="globe",
        fields=(
            _text("address", "Address", "", "As you would type it into a maps search."),
            BlockField("zoom", "Zoom", "number", 15, "1 is the whole world, 20 a single street.",
                       minimum=1, maximum=20),
            BlockField("height", "Height", "select", "md",
                       options=(("sm", "Short"), ("md", "Medium"), ("lg", "Tall"))),
            BlockField("rounded", "Rounded corners", "boolean", True),
        ),
    ),
    "social_links": Block(
        type="social_links", label="Social links", description="Your profiles, as a row of links.",
        group="media", icon="link",
        fields=(
            BlockField(
                "items", "Links", "list",
                [{"label": "Instagram", "url": "https://instagram.com"},
                 {"label": "TikTok", "url": "https://tiktok.com"}],
                fields=(_text("label", "Label"), BlockField("url", "Link", "url")),
            ),
            BlockField("style", "Style", "select", "pill",
                       options=(("pill", "Outlined pills"), ("solid", "Filled pills"),
                                ("text", "Plain text"))),
            BlockField("size", "Size", "select", "md", options=(("sm", "Small"), ("md", "Medium"))),
            _align(right=True),
        ),
    ),
}


def catalogue() -> list[dict[str, Any]]:
    """The registry as props, for the builder's block library and inspector.

    The UI is generated from this — a new block type is one entry here and one
    renderer, with no third place to update.
    """
    return [
        {
            "type": block.type,
            "label": block.label,
            "description": block.description,
            "group": block.group,
            "icon": block.icon,
            "accepts": list(block.accepts),
            "insertable": block.insertable,
            "listed": block.listed,
            # What the Add panel inserts for this type, when that is not a
            # bare block of it. See `COMPOSITIONS`.
            "compose": COMPOSITIONS[block.type]() if block.type in COMPOSITIONS else None,
            "fields": [
                {
                    "name": entry.name,
                    "label": entry.label,
                    "kind": entry.kind,
                    "default": entry.default,
                    "help": entry.help,
                    "options": [{"value": value, "label": label} for value, label in entry.options],
                    "minimum": entry.minimum,
                    "maximum": entry.maximum,
                    "fields": [
                        {
                            "name": sub.name,
                            "label": sub.label,
                            "kind": sub.kind,
                            "default": sub.default,
                            "help": sub.help,
                            "options": [{"value": v, "label": lab} for v, lab in sub.options],
                        }
                        for sub in entry.fields
                    ],
                }
                for entry in (*block.fields, *PLACEMENT_FIELDS)
            ],
        }
        for block in BLOCKS.values()
    ]


def new_block(block_type: str) -> dict[str, Any]:
    """A fresh block of this type, with every field at its default.

    Built on the server rather than in the browser so a block dragged in has
    exactly the shape `sanitise` will accept — the two cannot disagree, because
    they read the same registry.
    """
    spec = BLOCKS.get(block_type)
    if spec is None:
        raise KeyError(f"No block type {block_type!r}.")

    block: dict[str, Any] = {
        "id": f"b_{secrets.token_urlsafe(8)}",
        "type": block_type,
        "props": {entry.name: entry.default for entry in (*spec.fields, *PLACEMENT_FIELDS)},
        "children": [],
    }

    # A `columns` block with no columns is a broken-looking empty row. Give it
    # the number its own default asks for.
    if block_type == "columns":
        count = int(block["props"].get("count") or 2)
        block["children"] = [new_block("column") for _ in range(count)]

    # An accordion's answer is a field; nothing needs to go inside it to start.
    return block


def _with(block: dict[str, Any], **props: Any) -> dict[str, Any]:
    block["props"].update(props)
    return block


def _composed_hero(
    heading: str = "Made to last",
    body: str = "A small collection of things worth keeping.",
    cta_label: str = "Shop the collection",
    cta_url: str = "/products",
) -> dict[str, Any]:
    """A hero as a section of real blocks, each one selectable on the canvas.

    A hero whose heading and button are *props* is a hero whose button cannot
    be clicked, restyled or moved — clicking it selects the whole hero. This is
    the same band built from an eyebrow, a heading, a text and a row of
    buttons, so every part of it is a block like any other.
    """
    section = _with(new_block("section"), padding="xl", min_height="md")
    buttons = _with(new_block("row"), gap="sm")
    buttons["children"] = [
        _with(new_block("button"), label=cta_label, url=cta_url),
        _with(new_block("button"), label="Our story", url="/pages/about", style="outline"),
    ]
    section["children"] = [
        _with(new_block("eyebrow"), text="New collection"),
        _with(new_block("heading"), text=heading, level="h1"),
        _with(new_block("text"), body=body, size="lg"),
        buttons,
    ]
    return section


#: Block types whose Add-panel entry inserts a composition of primitives rather
#: than a bare block of the type. The type itself stays registered, so pages
#: that already hold one still render.
COMPOSITIONS: dict[str, Any] = {
    "hero": lambda: [_composed_hero()],
}


def expand_legacy(tree: Any) -> Any:
    """Replace every props-only `hero` with the same hero built from blocks.

    Run when a page is opened in the editor, so a merchant who clicks the
    button in an old hero gets the button. The published page is untouched
    until they publish: the storefront still renders the old block.
    """
    if not isinstance(tree, list):
        return tree
    out: list[Any] = []
    for block in tree:
        if not isinstance(block, dict):
            out.append(block)
            continue
        if block.get("type") == "hero":
            out.append(_expand_hero(block, nested=True))
            continue
        children = block.get("children")
        if isinstance(children, list) and children:
            block = {**block, "children": expand_legacy(children)}
        out.append(block)
    return out


def _expand_hero(block: dict[str, Any], *, nested: bool) -> dict[str, Any]:
    props = block.get("props") if isinstance(block.get("props"), dict) else {}
    image = str(props.get("image") or "")
    layout = str(props.get("layout") or "left")
    centred = layout == "center"
    align = "center" if centred else "left"

    copy: list[dict[str, Any]] = []
    if props.get("heading"):
        copy.append(_with(new_block("heading"), text=str(props["heading"]), level="h1", align=align))
    if props.get("body"):
        copy.append(_with(new_block("text"), body=str(props["body"]), size="lg", align=align))
    if props.get("cta_label"):
        copy.append(_with(new_block("button"), label=str(props["cta_label"]),
                          url=str(props.get("cta_url") or "/products"), align=align))

    if layout == "split":
        columns = _with(new_block("columns"), count=2, gap="lg")
        left, right = columns["children"]
        left["props"]["align"] = "middle"
        left["children"] = copy
        right["children"] = [_with(new_block("image"), src=image, ratio="wide")]
        body: dict[str, Any] = columns
    else:
        height = {"sm": "sm", "md": "md", "lg": "lg"}.get(str(props.get("height") or "md"), "md")
        body = _with(
            new_block("box"),
            background_image=image,
            overlay=bool(props.get("overlay", True)),
            padding="xl" if image else "none",
            min_height=height if image else "auto",
            align=align,
        )
        body["children"] = copy

    body["id"] = str(block.get("id") or body["id"])
    return body


def sanitise(
    tree: Any,
    *,
    depth: int = 0,
    parent: str | None = None,
    seen: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild a tree from the registry. The only door into a stored page.

    Rebuilds rather than filters: the output contains exactly what the registry
    describes, so nothing unknown can round-trip. See the module docstring for
    why each rule is there.

    Args:
        seen: Ids already used in this tree. Threaded through the recursion
            because a duplicate id is not a syntax problem — both spellings are
            valid — but it makes React's keys collide and the builder's
            selection point at a different block than the one that is
            highlighted. Two blocks claiming one id is a tree where "delete the
            selected block" deletes the wrong one.
    """
    if depth > MAX_DEPTH or not isinstance(tree, list):
        return []

    if seen is None:
        seen = set()

    out: list[dict[str, Any]] = []
    for raw in tree[:MAX_BLOCKS]:
        if not isinstance(raw, dict):
            continue

        block_type = str(raw.get("type") or "")
        spec = BLOCKS.get(block_type)
        if spec is None:
            continue  # unknown type — dropped, never stored

        if parent is not None:
            allowed = BLOCKS[parent].accepts
            if block_type not in allowed:
                continue  # not permitted here

        block_id = str(raw.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", block_id) or block_id in seen:
            block_id = f"b_{secrets.token_urlsafe(8)}"
        seen.add(block_id)

        props: dict[str, Any] = {}
        raw_props = raw.get("props")
        raw_props = raw_props if isinstance(raw_props, dict) else {}
        for entry in spec.fields:
            props[entry.name] = _coerce(entry, raw_props.get(entry.name))

        # The styling classes the builder's Style tab writes.
        #
        # Every block carries this, so it is not in any block's field list —
        # and that is exactly how it got dropped. `sanitise` rebuilds props
        # from the registry, the registry had no `styles`, so every autosave
        # returned a tree with the styling stripped and the client adopted it.
        # From the merchant's side: styles that "randomly disappear" a few
        # seconds after being set.
        #
        # Filtered rather than trusted — see `_safe_classes`.
        props["styles"] = _safe_classes(raw_props.get("styles"))
        for entry in PLACEMENT_FIELDS:
            props[entry.name] = _coerce(entry, raw_props.get(entry.name))

        out.append(
            {
                "id": block_id,
                "type": block_type,
                "props": props,
                "children": (
                    sanitise(
                        raw.get("children"),
                        depth=depth + 1,
                        parent=block_type,
                        seen=seen,
                    )
                    if spec.accepts
                    else []
                ),
            }
        )

    return out


#: A Tailwind-shaped class token. Deliberately narrow: letters, digits and the
#: punctuation Tailwind actually uses — `-`, `_`, `:`, `/`, `.`, `%`, `[`, `]`,
#: `(`, `)`, `,`, `#`, plus the selector characters of its variants: `&`, `*`,
#: `>`, `+`, `~` (`[&_h1]:text-8xl`, `*:rounded-xl`), `@` (container queries)
#: and `!` (important) and `=` (`[&[data-open=true]]`). Quotes, semicolons,
#: braces, `<` and whitespace are never allowed, so a token cannot close the
#: attribute it sits in or open a declaration of its own. Everything else is
#: dropped.
_CLASS_TOKEN = re.compile(r"^[A-Za-z0-9\-_:/.%\[\](),#&*>+~@!=]{1,80}$")

#: Most classes one block may carry. A block with two hundred is either a
#: mistake or an attempt to make a page expensive to render.
MAX_CLASSES = 40


def _safe_classes(value: Any) -> str:
    """A class string, filtered token by token.

    Arbitrary text here reaches a `class` attribute on a public storefront.
    Tailwind's arbitrary-value syntax means the useful characters include
    brackets and parentheses — `bg-[url(...)]` is a real class — which is
    precisely why this cannot be a blanket allow: `bg-[url(javascript:...)]` is
    the same shape. So each token is checked against the alphabet Tailwind
    uses, and anything containing a quote, a space inside brackets, a
    semicolon or a colon-scheme is dropped.

    The result is a class string that can only ever name classes. What those
    classes *do* is Tailwind's business, and Tailwind cannot be made to emit a
    script.
    """
    if isinstance(value, dict):
        # The builder hands styling back as `{className}`.
        value = value.get("className") or value.get("class") or ""
    if not isinstance(value, str):
        return ""

    kept: list[str] = []
    for token in value.split():
        if len(kept) >= MAX_CLASSES:
            break
        if "javascript:" in token.lower() or "data:" in token.lower():
            continue
        if _CLASS_TOKEN.match(token):
            kept.append(token)
    return " ".join(kept)


def _coerce(entry: BlockField, value: Any) -> Any:
    """One field, forced into the shape its declaration promises."""
    kind = entry.kind

    if kind == "boolean":
        return bool(value) if value is not None else bool(entry.default)

    if kind == "number":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return entry.default
        if entry.minimum is not None:
            number = max(entry.minimum, number)
        if entry.maximum is not None:
            number = min(entry.maximum, number)
        return number

    if kind == "select":
        allowed = {value for value, _ in entry.options}
        text = str(value or "")
        return text if text in allowed else entry.default

    if kind == "color":
        text = str(value or "").strip()
        # Hex only. A colour field is exactly where someone tries `url(...)`.
        return text if _COLOR.match(text) else ""

    if kind in ("url", "image"):
        text = str(value or "").strip()
        if not text:
            return ""
        # `javascript:` and `data:` are refused. A merchant's own hero button
        # is still a link their customers click.
        return text[:1000] if _URL.match(text) else ""

    if kind == "list":
        if not isinstance(value, list):
            return []
        rows = []
        for item in value[:20]:
            if not isinstance(item, dict):
                continue
            rows.append({sub.name: _coerce(sub, item.get(sub.name)) for sub in entry.fields})
        return rows

    if kind == "table":
        # Rows of plain-text cells. Capped in both directions and made
        # rectangular, so a renderer never meets a ragged or enormous grid.
        if not isinstance(value, list):
            value = entry.default if isinstance(entry.default, list) else []
        rows = [row for row in value if isinstance(row, list)][:MAX_TABLE_ROWS]
        width = min(max((len(row) for row in rows), default=0), MAX_TABLE_COLUMNS)
        return [
            [_plain_text(cell)[:500] for cell in row[:width]] + [""] * (width - len(row[:width]))
            for row in rows
        ]

    if kind in ("collection", "product"):
        # An id, as a string. Whether it exists is checked when the page is
        # rendered — a merchant deleting a product should not corrupt a page.
        text = str(value or "").strip()
        return text if text.isdigit() else ""

    if kind == "richtext":
        # The one kind whose value is meant to be markup. No field uses it
        # today; if one is added, its renderer owns the sanitising.
        return str(value or "")[:5000]

    if kind == "textarea":
        return _plain_text(value)[:5000]

    return _plain_text(value)[:500]


#: A run of HTML/XML tags. Plain-text fields are rendered as escaped text by
#: every renderer, so a tag that reaches one shows up as literal `<p>` on the
#: canvas rather than doing anything — this strips them back out on save.
_TAGS = re.compile(r"<[^>]*>")


def _plain_text(value: Any) -> str:
    """A text field with any tags removed.

    ChaiBuilder's rich-text control, a paste from a word processor, or a
    copy-paste of a styled selection can all put `<p>…</p>` into a field the
    schema calls plain text. The renderers escape it, so the merchant sees the
    markup instead of their words; dropping the tags here is what keeps the
    field honest.
    """
    text = str(value or "")
    if "<" not in text:
        return text
    return _TAGS.sub("", text)


def default_header() -> list[dict[str, Any]]:
    """What a new header page starts with: a classic bar, built of real parts.

    Composed rather than a single `navbar` block, so every piece — the logo,
    the links, the search, the cart — is selectable and can be moved, restyled
    or deleted on the canvas from the first minute.
    """
    bar = new_block("header_bar")
    bar["children"] = [
        new_block("site_logo"),
        _with(new_block("nav_links"), gap="md"),
        new_block("flex_space"),
        new_block("search_box"),
        new_block("cart_button"),
    ]
    return [bar]


def default_tree(store_name: str) -> list[dict[str, Any]]:
    """The homepage a new store starts with.

    A real page rather than a blank canvas: a merchant opening the builder for
    the first time should see something that already works and edit it, not
    face an empty rectangle and a question.
    """
    hero_section = _composed_hero(
        heading="Things worth keeping",
        body=f"Welcome to {store_name}. A small collection, chosen carefully.",
        cta_label="Shop everything",
    )

    featured_section = new_block("section")
    grid = new_block("product_grid")
    grid["props"].update({"heading": "Featured", "limit": 4, "columns": 4})
    featured_section["children"] = [grid]

    props_section = new_block("section")
    props_section["props"]["background"] = "#f7f4f0"
    value_props = new_block("value_props")
    value_props["props"]["items"] = [
        {"title": "Free delivery over 50", "body": "Tracked, two to three days."},
        {"title": "Guaranteed for life", "body": "We repair it, or we replace it."},
        {"title": "30-day returns", "body": "No questions, no restocking fee."},
    ]
    props_section["children"] = [value_props]

    collections_section = new_block("section")
    collection_list = new_block("collection_list")
    collections_section["children"] = [collection_list]

    return [hero_section, featured_section, props_section, collections_section]


def count_blocks(tree: list[dict[str, Any]]) -> int:
    """How many blocks a tree holds, at every level."""
    total = 0
    for block in tree:
        total += 1
        total += count_blocks(block.get("children") or [])
    return total


def find_block(tree: list[dict[str, Any]], block_id: str) -> dict[str, Any] | None:
    for block in tree:
        if block.get("id") == block_id:
            return block
        found = find_block(block.get("children") or [], block_id)
        if found is not None:
            return found
    return None
