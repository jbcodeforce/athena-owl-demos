"""
Copyright 2024 Athena Decision Systems
@author Jerome Boyer
"""
from dataclasses import dataclass, asdict
import json
from .ComplaintHandling_generated_model import *


class InsuranceClientRepositoryInterface:

    def add_client(self,client: Client):
        pass

    def get_client(self,id: int) -> Client:
        return None
    
    def get_client_by_name(self,name: str) -> Client:
        return None
    
    def get_client_by_name_json(self, name: str) -> str:
        return None
    
    def get_client_json(self, id: int) -> str:
        return None
    
    def get_all_clients_json(self) -> str:
        return None
    
    def get_all_clients(self) -> list[Client]:
        return None