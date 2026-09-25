from dataclasses import dataclass

@dataclass
class Product:
    id: str
    title: str
    price: float
    stock: int

