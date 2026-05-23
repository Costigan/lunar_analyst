from __future__ import annotations

from abc import ABC, abstractmethod

from backend.contracts.decorators import contract
from backend.contracts.models import Product, RegisterProductRequest


class ProductService(ABC):
    @contract(
        name="ProductService.register_product",
        request_type=RegisterProductRequest,
        response_type=Product,
        description="Register a scenario product and file metadata.",
    )
    @abstractmethod
    def register_product(self, request: RegisterProductRequest) -> Product:
        raise NotImplementedError

    @contract(
        name="ProductService.get_product",
        request_type=None,
        response_type=Product,
        description="Get one product by ID.",
    )
    @abstractmethod
    def get_product(self, product_id: str) -> Product:
        raise NotImplementedError

    @contract(
        name="ProductService.list_products",
        request_type=None,
        response_type=list[Product],
        description="List products for a scenario.",
    )
    @abstractmethod
    def list_products(self, scenario_id: str) -> list[Product]:
        raise NotImplementedError
