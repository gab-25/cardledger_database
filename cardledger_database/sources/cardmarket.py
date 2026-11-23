import httpx

from ..model import CmProduct, SourceError

PRODUCT_LIST_URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_{kind}_{game}.json"
PRICE_GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_{game}.json"


def fetch_products(client: httpx.Client, cm_game_id: int) -> tuple[list[CmProduct], set[int]]:
    """Every single and non-single of a Cardmarket game (singles first), and the categories of the singles."""
    products: list[CmProduct] = []
    single_category_ids: set[int] = set()
    for kind in ("singles", "nonsingles"):
        name = f"products_{kind}_{cm_game_id}.json"
        try:
            response = client.get(PRODUCT_LIST_URL.format(kind=kind, game=cm_game_id))
            response.raise_for_status()
            for p in response.json()["products"]:
                metacard = p.get("idMetacard")
                product = CmProduct(
                    int(p["idProduct"]), p["name"], int(p["idCategory"]), p.get("categoryName") or "",
                    int(p["idExpansion"]), int(metacard) if metacard is not None else None,
                )
                products.append(product)
                if kind == "singles":
                    single_category_ids.add(product.id_category)
            del response
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SourceError(f"Cardmarket {name}: {type(exc).__name__}: {exc}") from exc
    return products, single_category_ids


def fetch_trends(client: httpx.Client, cm_game_id: int) -> dict[int, float]:
    """{idProduct: trend price} from Cardmarket's public price guide of a game, for the products that have one."""
    name = f"price_guide_{cm_game_id}.json"
    try:
        response = client.get(PRICE_GUIDE_URL.format(game=cm_game_id))
        response.raise_for_status()
        return {int(g["idProduct"]): float(g["trend"]) for g in response.json()["priceGuides"] if g.get("trend")}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"Cardmarket {name}: {type(exc).__name__}: {exc}") from exc
