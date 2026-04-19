from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User, SearchCriteria, SeenListing, NotificationLog

async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int):
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()

async def create_user(session: AsyncSession, telegram_id: int, username: str = None):
    db_user = User(telegram_id=telegram_id, username=username)
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user

async def get_or_create_user(session: AsyncSession, telegram_id: int, username: str = None):
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        user = await create_user(session, telegram_id, username)
    return user

async def add_search_criteria(session: AsyncSession, user_id: int, criteria_data: dict):
    allowed_fields = set(SearchCriteria.__table__.columns.keys()) - {"id", "user_id"}
    clean_data = {key: value for key, value in criteria_data.items() if key in allowed_fields}
    criteria = SearchCriteria(user_id=user_id, **clean_data)
    session.add(criteria)
    await session.commit()
    await session.refresh(criteria)
    return criteria

async def get_active_criteria(session: AsyncSession):
    result = await session.execute(select(SearchCriteria).where(SearchCriteria.is_active == True))
    return result.scalars().all()

async def check_if_listing_seen(session: AsyncSession, user_id: int, listing_id: str):
    result = await session.execute(
        select(SeenListing).where(
            SeenListing.user_id == user_id, 
            SeenListing.listing_id == listing_id
        )
    )
    return result.scalar_one_or_none() is not None

async def mark_listing_as_seen(session: AsyncSession, user_id: int, listing_id: str):
    seen = SeenListing(user_id=user_id, listing_id=listing_id)
    session.add(seen)
    await session.commit()

async def log_notification(session: AsyncSession, user_id: int, listing_id: str, summary: str):
    log = NotificationLog(user_id=user_id, listing_id=listing_id, gemini_summary=summary)
    session.add(log)
    await session.commit()
