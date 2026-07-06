"""票种特定字段抽取策略。各票种模块统一暴露 ``extract(...)`` 函数。

路由通过 ``scheme_extractors.<mod>.extract`` 形式直接索引；
具体映射见 ``field_candidates._extract_schema_specific_candidates``。
"""

from . import (
    air_ticket,
    machine_invoice,
    medical_receipt,
    metro_quota,
    railway,
    road_bus,
    tax_payment,
    taxi,
    water_passenger,
)

__all__ = [
    "air_ticket",
    "machine_invoice",
    "medical_receipt",
    "metro_quota",
    "railway",
    "road_bus",
    "tax_payment",
    "taxi",
    "water_passenger",
]
