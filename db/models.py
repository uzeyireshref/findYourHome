from sqlalchemy import BigInteger, Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from db.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    criteria = relationship("SearchCriteria", back_populates="user")
    seen_listings = relationship("SeenListing", back_populates="user")
    notifications = relationship("NotificationLog", back_populates="user")

class SearchCriteria(Base):
    __tablename__ = "search_criteria"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    city = Column(String)
    district = Column(String)
    min_price = Column(Float, nullable=True)
    max_price = Column(Float, nullable=True)
    min_rooms = Column(Integer, nullable=True)
    max_rooms = Column(Integer, nullable=True)
    max_building_age = Column(Integer, nullable=True)
    listing_type = Column(String, nullable=True)
    property_type = Column(String, nullable=True)
    is_furnished = Column(Boolean, nullable=True)
    seller_type = Column(String, nullable=True)
    extra_notes = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="criteria")

class SeenListing(Base):
    __tablename__ = "seen_listings"
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(String, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    first_seen_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="seen_listings")

class NotificationLog(Base):
    __tablename__ = "notifications_log"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    listing_id = Column(String)
    sent_at = Column(DateTime, default=datetime.utcnow)
    gemini_summary = Column(String, nullable=True)

    user = relationship("User", back_populates="notifications")
