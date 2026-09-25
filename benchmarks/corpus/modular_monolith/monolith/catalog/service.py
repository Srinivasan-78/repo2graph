from monolith.catalog.models import Product

class CatalogService:
    def __init__(self):
        self.products = {
            "prod-1": Product(id="prod-1", title="Mechanical Keyboard", price=120.0, stock=50),
            "prod-2": Product(id="prod-2", title="UltraWide Monitor", price=450.0, stock=20),
        }

    def get_product(self, product_id: str) -> Product | None:
        return self.products.get(product_id)

    def reserve_stock(self, product_id: str, quantity: int) -> bool:
        prod = self.get_product(product_id)
        if prod and prod.stock >= quantity:
            prod.stock -= quantity
            return True
        return False

