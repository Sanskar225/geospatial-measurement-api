from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from shapely.geometry.base import BaseGeometry

@dataclass
class ParsedFeature:
    feature_id: str
    geometry: BaseGeometry
    properties: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ParsedDataset:
    features: List[ParsedFeature]
    crs: str
    file_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)
