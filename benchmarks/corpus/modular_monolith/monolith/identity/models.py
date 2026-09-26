from dataclasses import dataclass


@dataclass
class Account:
    id: str
    email: str
    name: str
    verified: bool = False
