from pydantic import BaseModel
from typing import Optional

class ListingModel(BaseModel):
    listing_id: str
    title: str
    price: float
    district: Optional[str] = None
    room_count: Optional[str] = None
    building_age: Optional[str] = None
    description: Optional[str] = None
    url: str
    is_furnished: Optional[bool] = None
    seller_type: Optional[str] = None
