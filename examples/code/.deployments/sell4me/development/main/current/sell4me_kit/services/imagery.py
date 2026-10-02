"""Photographs for the templates.

Every template ships with real pictures, because a shop template without them
is a wireframe: a merchant cannot tell whether a layout works from three grey
rectangles, and the first thing they do with a wireframe is close it.

**Where they come from.** Unsplash, by photo id, served from their CDN with the
sizing and format left to them. Their licence permits commercial use without
attribution and their guidelines expect exactly this kind of hotlinking, so it
is a legitimate arrangement rather than a borrowed one. The cost is honest and
worth stating: a published page makes a request to a CDN we do not run, and if
Unsplash is slow that page is slow. Every one is a normal image field, so a
merchant replaces it with their own in two clicks — and should, because
photographs of the thing you actually sell outsell stock photography.

**How they are chosen.** Not one pool. Ten templates drawing from one bag of
pictures look like ten arrangements of the same shop; a linen shop and a tool
shop need different photographs before they need different fonts. So imagery
is grouped into *sets* — a mood, not a subject — and a template names one. The
set then supplies every slot the template has: a wide hero, a tall portrait, a
close detail, a lifestyle frame, and a handful for galleries.

The URL builder asks for exactly the size the slot renders at. A 2400px hero
in a 300px thumbnail is four megabytes to draw a postage stamp.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SETS", "ImagerySet", "hero", "portrait", "square", "url", "wide"]

_BASE = "https://images.unsplash.com/photo-{id}"

#: The sizes the slots actually render at, so nothing is fetched larger than it
#: is drawn. `q=72` is where the compression stops being visible on a
#: photograph and starts being visible on the bill.
_SIZES = {
    "hero": "w=2000&h=1100&fit=crop&q=72",
    "wide": "w=1400&h=800&fit=crop&q=72",
    "portrait": "w=900&h=1200&fit=crop&q=72",
    "square": "w=900&h=900&fit=crop&q=72",
    "thumb": "w=500&h=500&fit=crop&q=70",
}


def url(photo_id: str, slot: str = "wide") -> str:
    """One photograph, at the size the slot draws it."""
    return f"{_BASE.format(id=photo_id)}?auto=format&{_SIZES.get(slot, _SIZES['wide'])}"


def hero(photo_id: str) -> str:
    return url(photo_id, "hero")


def wide(photo_id: str) -> str:
    return url(photo_id, "wide")


def portrait(photo_id: str) -> str:
    return url(photo_id, "portrait")


def square(photo_id: str) -> str:
    return url(photo_id, "square")


@dataclass(frozen=True, slots=True)
class ImagerySet:
    """The pictures one template draws from.

    Named slots rather than a list, so a preset can ask for "the tall one"
    and get something that works tall in every set it might be rendered with.
    """

    key: str
    #: Wide, and able to carry text over it.
    hero: str
    #: Tall. Beside a column of copy.
    portrait: str
    #: Close. A material, a texture, a hand at work.
    detail: str
    #: A room, a table, a person. Context rather than product.
    lifestyle: str
    #: A second hero, for a page that is not the homepage.
    secondary: str
    #: Four for a gallery or a lookbook.
    gallery: tuple[str, str, str, str]


SETS: dict[str, ImagerySet] = {
    # Warm, natural light, linen and wood. The safe default.
    "studio": ImagerySet(
        key="studio",
        hero="1441984904996-e0b6ba687e04",
        portrait="1489987707025-afc232f7ea0f",
        detail="1556905055-8f358a7a47b2",
        lifestyle="1522708323590-d24dbb6b0267",
        secondary="1567016432779-094069958ea5",
        gallery=(
            "1523381210434-271e8be1f52b",
            "1434389677669-e08b4cac3105",
            "1483985988355-763728e1935b",
            "1445205170230-053b83016050",
        ),
    ),
    # Paper, ink, shelves. For shops that write.
    "press": ImagerySet(
        key="press",
        hero="1507842217343-583bb7270b66",
        portrait="1495640388908-05fa85288e61",
        detail="1544716278-ca5e3f4abd8c",
        lifestyle="1524995997946-a1c2e315a42f",
        secondary="1481627834876-b7833e8f5570",
        gallery=(
            "1512820790803-83ca734da794",
            "1519682337058-a94d519337bc",
            "1497633762265-9d179a990aa6",
            "1526243741027-444d633d7365",
        ),
    ),
    # Pale, soft, botanical. Skincare and flowers.
    "bloom": ImagerySet(
        key="bloom",
        hero="1596462502278-27bfdc403348",
        portrait="1571781926291-c477ebfd024b",
        detail="1512207736890-6ffed8a84e8d",
        lifestyle="1487412720507-e7ab37603c6f",
        secondary="1522335789203-aabd1fc54bc9",
        gallery=(
            "1519681393784-d120267933ba",
            "1465146344425-f00d5f5c8f07",
            "1490750967868-88aa4486c946",
            "1502741224143-90386d7f8c82",
        ),
    ),
    # Steel, workbenches, grease. Tools and parts.
    "workshop": ImagerySet(
        key="workshop",
        hero="1504328345606-18bbc8c9d7d1",
        portrait="1530124566582-a618bc2615dc",
        detail="1581092918056-0c4c3acd3789",
        lifestyle="1516822003754-cca485356ecb",
        secondary="1517420704952-d9f39e95b43e",
        gallery=(
            "1572981779307-38b8cabb2407",
            "1618044733300-9472054094ee",
            "1581092160562-40aa08e78837",
            "1595246140625-573b715d11dc",
        ),
    ),
    # Marble, gold, low light. Jewellery and tailoring.
    "atelier": ImagerySet(
        key="atelier",
        hero="1515562141207-7a88fb7ce338",
        portrait="1490481651871-ab68de25d43d",
        detail="1611591437281-460bfbe1220a",
        lifestyle="1469334031218-e382a71b716b",
        secondary="1573408301185-9146fe634ad0",
        gallery=(
            "1599643478518-a784e5dc4c8f",
            "1535632066927-ab7c9ab60908",
            "1602173574767-37ac01994b2a",
            "1584302179602-e4c3d3fd629d",
        ),
    ),
    # Bright, busy, plentiful. A wide catalogue.
    "market": ImagerySet(
        key="market",
        hero="1578916171728-46686eac8d58",
        portrait="1542838132-92c53300491e",
        detail="1550989460-0adf9ea622e2",
        lifestyle="1441986300917-64674bd600d8",
        secondary="1534452203293-494d7ddbf7e0",
        gallery=(
            "1506617564039-2f3b650b7010",
            "1607083206869-4c7672e72a8a",
            "1584680226833-0d680d0a0794",
            "1595535873420-a599195b3f4a",
        ),
    ),
    # Green, outdoors, unhurried. Plants and refills.
    "grove": ImagerySet(
        key="grove",
        hero="1466692476868-aef1dfb1e735",
        portrait="1416879595882-3373a0480b5b",
        detail="1502082553048-f009c37129b9",
        lifestyle="1485955900006-10f4d324d411",
        secondary="1501004318641-b39e6451bec6",
        gallery=(
            "1462530260150-162092dbf011",
            "1518495973542-4542c06a5843",
            "1504198322253-cfa87a0ff25f",
            "1509937528035-ad76254b0356",
        ),
    ),
    # High contrast, product-forward. One line sold hard.
    "signal": ImagerySet(
        key="signal",
        hero="1526170375885-4d8ecf77b99f",
        portrait="1523275335684-37898b6baf30",
        detail="1505740420928-5e560c06d30e",
        lifestyle="1498049794561-7780e7231661",
        secondary="1560343090-f0409e92791a",
        gallery=(
            "1546435770-a3e426bf472b",
            "1572635196237-14b3f281503f",
            "1585386959984-a4155224a1ad",
            "1491933382434-500287f9b54b",
        ),
    ),
    # Crates, racks, film grain. Vintage and records.
    "archive": ImagerySet(
        key="archive",
        hero="1470225620780-dba8ba36b745",
        portrait="1493225457124-a3eb161ffa5f",
        detail="1458560871784-56d23406c091",
        lifestyle="1483412033650-1015ddeb83d1",
        secondary="1514320291840-2e0a9bf2a9ae",
        gallery=(
            "1461360370896-922624d12aa1",
            "1507838153414-b4b713384a76",
            "1471478331149-c72f17e33c73",
            "1524650359799-842906ca1c06",
        ),
    ),
    # Deep blue, gold, evening. Spirits and watches.
    "midnight": ImagerySet(
        key="midnight",
        hero="1470337458703-46ad1756a187",
        portrait="1519671482749-fd09be7ccebf",
        detail="1524594152303-9fd13543fe6e",
        lifestyle="1514362545857-3bc16c4c7d1b",
        secondary="1551024709-8f23befc6f87",
        gallery=(
            "1470337458703-46ad1756a187",
            "1536935338788-846bb9981813",
            "1547595628-c61a29f496f0",
            "1516450360452-9312f5e86fc7",
        ),
    ),
    # Dark plates, hard light, one plate at a time. Kitchens and meal brands.
    "kitchen": ImagerySet(
        key="kitchen",
        hero="1571091718767-18b5b1457add",
        portrait="1546069901-ba9599a7e63c",
        detail="1466637574441-749b8f19452f",
        lifestyle="1504674900247-0877df9cc836",
        secondary="1544025162-d76694265947",
        gallery=(
            "1476224203421-9ac39bcb3327",
            "1414235077428-338989a2e8c0",
            "1495474472287-4d71bcdd2085",
            "1467003909585-2f8a72700288",
        ),
    ),
    # Soft light, linen and wood toys. Nurseries and small children.
    "nursery": ImagerySet(
        key="nursery",
        hero="1586102728466-46b99b3bc411",
        portrait="1543346242-2b8e41fb91ca",
        detail="1505043203398-7e4c111acbfa",
        lifestyle="1704241135858-2e10153afaec",
        secondary="1770831208117-c0dd9469e95d",
        gallery=(
            "1766918780916-228d10b071be",
            "1631541911156-0b44a12a7770",
            "1542901689-8917f44e3541",
            "1642685464968-5d85bb7cffe0",
        ),
    ),
    # Dark rooms, hard shadow, one machine at a time. Training gear.
    "pulse": ImagerySet(
        key="pulse",
        hero="1770513649465-2c60c8039806",
        portrait="1614367674345-f414b2be3e5b",
        detail="1672344048213-76b6e77304bd",
        lifestyle="1637430308606-86576d8fef3c",
        secondary="1613845205719-8c87760ab728",
        gallery=(
            "1734630341082-0fec0e10126c",
            "1695892046204-ec2962b26b48",
            "1648235692910-947cb90ddd97",
            "1676655079738-af54dfd6318e",
        ),
    ),
    # Pink on pink, flat and bright. Cosmetics.
    "gloss": ImagerySet(
        key="gloss",
        hero="1583209814683-c023dd293cc6",
        portrait="1631214524115-9942bf927d4a",
        detail="1538022890810-8f4000ca9a10",
        lifestyle="1676570092589-a6c09ecbb373",
        secondary="1643123158477-0c3f990b299d",
        gallery=(
            "1631730486572-226d1f595b68",
            "1723150512429-bfa92988d845",
            "1631730486784-5456119f69ae",
            "1706067003154-bf1a01c58bbb",
        ),
    ),
    # Warm rooms, one dog at a time. Pet shops.
    "paws": ImagerySet(
        key="paws",
        hero="1581888227599-779811939961",
        portrait="1591946614720-90a587da4a36",
        detail="1608096299210-db7e38487075",
        lifestyle="1600352712371-15fd49ca42b5",
        secondary="1583337130417-3346a1be7dee",
        gallery=(
            "1583511655826-05700d52f4d9",
            "1547525623-c7d42c20284c",
            "1630361102412-bd7ed8710307",
            "1618173745201-8e3bf8978acc",
        ),
    ),
    # Warm neutrals, gallery walls, one room at a time. Furniture and interiors.
    "haus": ImagerySet(
        key="haus",
        hero="1615876234886-fd9a39fda97f",
        portrait="1616486338812-3dadae4b4ace",
        detail="1616137148650-4aa14651e02b",
        lifestyle="1632119580908-ae947d4c7691",
        secondary="1600210491369-e753d80a41f3",
        gallery=(
            "1554995207-c18c203602cb",
            "1550581190-9c1c48d21d6c",
            "1615529179035-e760f6a2dcee",
            "1673563932832-a0c9e0ed26f8",
        ),
    ),
    # Dark rooms, one red light. Audio and music gear.
    "current": ImagerySet(
        key="current",
        hero="1790042200763-d715bb455b9a",
        portrait="1761005654142-819960af0658",
        detail="1712932115370-4f9d354ced6b",
        lifestyle="1655931546417-18e16c015812",
        secondary="1761005653885-b3d8b04f47c5",
        gallery=(
            "1761005654126-6d512251d7a3",
            "1761005654347-f5907893edb7",
            "1761005653827-9cd95fa1faee",
            "1655931546470-7b2e167b5fef",
        ),
    ),
    # Primary colour, flat and bright. Toys and games.
    "playroom": ImagerySet(
        key="playroom",
        hero="1655087751207-1020c89f7eee",
        portrait="1621452689618-9b4e935ff588",
        detail="1560859251-d563a49c5e4a",
        lifestyle="1545558014-8692077e9b5c",
        secondary="1618842676088-c4d48a6a7c9d",
        gallery=(
            "1553158399-3796bdbc82fd",
            "1696527014341-a874bd839540",
            "1751110479291-36300997a064",
            "1696527018042-54d665bb6fba",
        ),
    ),
    # Pastel and ribbon, held up to a window. Gifts, party and paper goods.
    "confetti": ImagerySet(
        key="confetti",
        hero="1787319313547-9e5efaaa11ef",
        portrait="1646181908244-c1f9f6fff579",
        detail="1765167029919-69048f9a8069",
        lifestyle="1707944145479-12755f0434d8",
        secondary="1646182504823-a02b768e28b5",
        gallery=(
            "1764389814703-9c3699580712",
            "1523754569648-43ccb845c9d8",
            "1646181930254-7b455b85d369",
            "1513151233558-d860c5398176",
        ),
    ),
    # Wide skies, one tent at a time. Camping and outdoor gear.
    "basecamp": ImagerySet(
        key="basecamp",
        hero="1510312305653-8ed496efae75",
        portrait="1532339142463-fd0a8979791a",
        detail="1624923686627-514dd5e57bae",
        lifestyle="1508873696983-2dfd5898f08b",
        secondary="1504280390367-361c6d9f38f4",
        gallery=(
            "1471115853179-bb1d604434e0",
            "1464547323744-4edd0cd0c746",
            "1628087235616-4e146afcd061",
            "1621519994490-b87b9401599e",
        ),
    ),
    # White desks, one device at a time. Minimal electronics.
    "chrome": ImagerySet(
        key="chrome",
        hero="1505209487757-5114235191e5",
        portrait="1548960254-456846b00986",
        detail="1608377205627-b3b6fd76f18c",
        lifestyle="1656259145847-81bcdac8f0f3",
        secondary="1752223638233-4c9545333f89",
        gallery=(
            "1595161397851-cb282659df5e",
            "1616175304583-ed54838016f3",
            "1640643640029-fc9d3f99e811",
            "1598061403733-a0d8eb6bd569",
        ),
    ),
    # Oatmeal and clay, rooms shown softly. Furniture and interiors, unhurried.
    "linen": ImagerySet(
        key="linen",
        hero="1632119580908-ae947d4c7691",
        portrait="1616486338812-3dadae4b4ace",
        detail="1616137148650-4aa14651e02b",
        lifestyle="1462530260150-162092dbf011",
        secondary="1600210491369-e753d80a41f3",
        gallery=(
            "1554995207-c18c203602cb",
            "1518495973542-4542c06a5843",
            "1615529179035-e760f6a2dcee",
            "1509937528035-ad76254b0356",
        ),
    ),
    # Black ground, one hard accent. Gaming gear and audio.
    "neon": ImagerySet(
        key="neon",
        hero="1526170375885-4d8ecf77b99f",
        portrait="1761005654142-819960af0658",
        detail="1712932115370-4f9d354ced6b",
        lifestyle="1655931546417-18e16c015812",
        secondary="1560343090-f0409e92791a",
        gallery=(
            "1546435770-a3e426bf472b",
            "1761005654126-6d512251d7a3",
            "1585386959984-a4155224a1ad",
            "1655931546470-7b2e167b5fef",
        ),
    ),
    # Terracotta plates, warm hard light. Bakeries and hand-made food.
    "terra": ImagerySet(
        key="terra",
        hero="1571091718767-18b5b1457add",
        portrait="1546069901-ba9599a7e63c",
        detail="1512207736890-6ffed8a84e8d",
        lifestyle="1504674900247-0877df9cc836",
        secondary="1487412720507-e7ab37603c6f",
        gallery=(
            "1476224203421-9ac39bcb3327",
            "1490750967868-88aa4486c946",
            "1495474472287-4d71bcdd2085",
            "1502741224143-90386d7f8c82",
        ),
    ),
    # Marble and deep plum, low light. Jewellery and occasionwear.
    "velvet": ImagerySet(
        key="velvet",
        hero="1515562141207-7a88fb7ce338",
        portrait="1519671482749-fd09be7ccebf",
        detail="1611591437281-460bfbe1220a",
        lifestyle="1514362545857-3bc16c4c7d1b",
        secondary="1573408301185-9146fe634ad0",
        gallery=(
            "1599643478518-a784e5dc4c8f",
            "1536935338788-846bb9981813",
            "1602173574767-37ac01994b2a",
            "1516450360452-9312f5e86fc7",
        ),
    ),
    # Green and wide sky. Gardens, plants and time outdoors.
    "meadow": ImagerySet(
        key="meadow",
        hero="1466692476868-aef1dfb1e735",
        portrait="1416879595882-3373a0480b5b",
        detail="1502082553048-f009c37129b9",
        lifestyle="1508873696983-2dfd5898f08b",
        secondary="1504280390367-361c6d9f38f4",
        gallery=(
            "1471115853179-bb1d604434e0",
            "1464547323744-4edd0cd0c746",
            "1504198322253-cfa87a0ff25f",
            "1621519994490-b87b9401599e",
        ),
    ),
    # Primary colour, flat and cheerful. Toys, games and things for kids.
    "arcade": ImagerySet(
        key="arcade",
        hero="1655087751207-1020c89f7eee",
        portrait="1621452689618-9b4e935ff588",
        detail="1560859251-d563a49c5e4a",
        lifestyle="1707944145479-12755f0434d8",
        secondary="1646182504823-a02b768e28b5",
        gallery=(
            "1553158399-3796bdbc82fd",
            "1764389814703-9c3699580712",
            "1751110479291-36300997a064",
            "1646181930254-7b455b85d369",
        ),
    ),
    # Steel and shadow, one machine at a time. Parts, tools and hardware.
    "slate": ImagerySet(
        key="slate",
        hero="1504328345606-18bbc8c9d7d1",
        portrait="1530124566582-a618bc2615dc",
        detail="1581092918056-0c4c3acd3789",
        lifestyle="1656259145847-81bcdac8f0f3",
        secondary="1752223638233-4c9545333f89",
        gallery=(
            "1572981779307-38b8cabb2407",
            "1595161397851-cb282659df5e",
            "1581092160562-40aa08e78837",
            "1640643640029-fc9d3f99e811",
        ),
    ),
    # Warm coral, soft and flat. Skincare and cosmetics.
    "coral": ImagerySet(
        key="coral",
        hero="1583209814683-c023dd293cc6",
        portrait="1631214524115-9942bf927d4a",
        detail="1538022890810-8f4000ca9a10",
        lifestyle="1571781926291-c477ebfd024b",
        secondary="1522335789203-aabd1fc54bc9",
        gallery=(
            "1631730486572-226d1f595b68",
            "1519681393784-d120267933ba",
            "1465146344425-f00d5f5c8f07",
            "1706067003154-bf1a01c58bbb",
        ),
    ),
}


def set_for(key: str) -> ImagerySet:
    """One imagery set, falling back to the neutral one.

    A template naming a set that does not exist should render with pictures
    rather than fail — the fallback is the least opinionated of the ten.
    """
    return SETS.get(key, SETS["studio"])
