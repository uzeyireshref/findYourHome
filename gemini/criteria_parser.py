import json
import os
import re
import unicodedata
from typing import Optional

from google import genai
from pydantic import BaseModel, Field


class SearchCriteriaSchema(BaseModel):
    city: Optional[str] = Field(None, description="City name, for example Istanbul")
    district: Optional[str] = Field(None, description="District names separated by commas")
    min_price: Optional[float] = Field(None, description="Minimum budget/price")
    max_price: Optional[float] = Field(None, description="Maximum budget/price")
    min_rooms: Optional[int] = Field(None, description="Minimum room number before +, e.g. 3 for 3+1")
    max_rooms: Optional[int] = Field(None, description="Maximum room number before +")
    max_building_age: Optional[int] = Field(None, description="Maximum building age")
    listing_type: Optional[str] = Field(None, description="'kiralik', 'satilik' or 'gunluk-kiralik'")
    property_type: Optional[str] = Field(None, description="'daire', 'villa', 'residence', 'mustakil-ev', etc.")
    is_furnished: Optional[bool] = Field(None, description="True furnished, false unfurnished, null unspecified")
    seller_type: Optional[str] = Field(None, description="'sahibinden' or 'emlak'")
    extra_notes: Optional[str] = Field(None, description="Other user requests")


_TR_TRANSLATION = str.maketrans({
    "\u00c7": "c", "\u00e7": "c",
    "\u011e": "g", "\u011f": "g",
    "\u0130": "i", "\u0131": "i",
    "\u00d6": "o", "\u00f6": "o",
    "\u015e": "s", "\u015f": "s",
    "\u00dc": "u", "\u00fc": "u",
})


def _normalize_text(value: str) -> str:
    value = value or ""
    try:
        repaired = value.encode("latin1").decode("utf-8")
        if repaired != value:
            value = f"{value} {repaired}"
    except UnicodeError:
        pass

    value = value.translate(_TR_TRANSLATION)
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return " ".join(value.lower().split())


def _price_to_number(value: str, multiplier_hint: str = "") -> float | None:
    try:
        number = float(value.replace(".", "").replace(",", "."))
    except ValueError:
        return None

    if multiplier_hint in {"bin", "k"} or number < 1000:
        number *= 1000
    return number


def _apply_numeric_overrides(criteria: dict, text_input: str) -> dict:
    normalized = _normalize_text(text_input)

    range_match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:bin|k)?\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(bin|k)?",
        normalized,
    )
    if range_match:
        unit = range_match.group(3) or "bin"
        min_price = _price_to_number(range_match.group(1), unit)
        max_price = _price_to_number(range_match.group(2), unit)
        if min_price is not None and max_price is not None:
            criteria["min_price"] = min_price
            criteria["max_price"] = max_price

    max_match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(bin|k)?\s*(?:tl)?\s*(?:alti|altinda|kadar|max|maksimum|en fazla)",
        normalized,
    )
    if max_match:
        price = _price_to_number(max_match.group(1), max_match.group(2) or "bin")
        if price is not None:
            criteria["max_price"] = price

    min_match = re.search(
        r"(?:en az|min|minimum)\s*(\d+(?:[.,]\d+)?)\s*(bin|k)?\s*(?:tl)?",
        normalized,
    )
    if min_match and "oda" not in normalized[min_match.start():min_match.end() + 10]:
        price = _price_to_number(min_match.group(1), min_match.group(2) or "bin")
        if price is not None:
            criteria["min_price"] = price

    exact_room = re.search(r"(\d+)\s*\+\s*\d+", normalized)
    if exact_room:
        room_count = int(exact_room.group(1))
        prefix = normalized[: exact_room.start()]
        if "en az" in prefix or "minimum" in prefix or "min" in prefix:
            criteria["min_rooms"] = room_count
        else:
            criteria["min_rooms"] = room_count
            criteria["max_rooms"] = room_count

    min_room = re.search(r"(?:en az|min|minimum)\s*(\d+)\s*(?:oda|odali)", normalized)
    if min_room:
        criteria["min_rooms"] = int(min_room.group(1))

    return criteria


def _apply_keyword_overrides(criteria: dict, text_input: str) -> dict:
    normalized = _normalize_text(text_input)

    if any(token in normalized for token in ("gunluk kiralik", "gunluk", "kisa donem")):
        criteria["listing_type"] = "gunluk-kiralik"
    elif "satilik" in normalized:
        criteria["listing_type"] = "satilik"
    elif "kiralik" in normalized or not criteria.get("listing_type"):
        criteria["listing_type"] = "kiralik"

    property_aliases = (
        ("mustakil ev", "mustakil-ev"),
        ("mustakil", "mustakil-ev"),
        ("residence", "residence"),
        ("rezidans", "residence"),
        ("villa", "villa"),
        ("yazlik", "yazlik"),
        ("daire", "daire"),
        ("ev", "daire"),
    )
    if not criteria.get("property_type"):
        criteria["property_type"] = "daire"
    for token, property_type in property_aliases:
        if token in normalized:
            criteria["property_type"] = property_type
            break

    if any(token in normalized for token in ("esyasiz", "esya yok", "mobilyasiz")):
        criteria["is_furnished"] = False
    elif any(token in normalized for token in ("esyali", "mobilyali", "full esya", "esya dahil")):
        criteria["is_furnished"] = True

    if "sahibinden" in normalized or "ev sahibinden" in normalized:
        criteria["seller_type"] = "sahibinden"
    elif any(token in normalized for token in ("emlakci", "emlak ofisi", "emlak firmasi", "acente", "danisman")):
        criteria["seller_type"] = "emlak"

    return _apply_numeric_overrides(criteria, text_input)


async def parse_user_request(text_input: str) -> dict:
    """
    Sends the user's free-text request to Gemini and returns parsed criteria JSON.
    Deterministic keyword/number overrides then correct common LLM misses.
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not set.")

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    prompt = f"""
Analyze this Turkish real estate search request and return only a JSON object.
Use null for fields that are not specified. Do not wrap the response in markdown.

Schema:
{{
  "city": "string/null",
  "district": "string/null, comma-separated if multiple districts are mentioned",
  "min_price": float/null,
  "max_price": float/null,
  "min_rooms": int/null,
  "max_rooms": int/null,
  "max_building_age": int/null,
  "listing_type": "kiralik/satilik/gunluk-kiralik/null",
  "property_type": "daire/villa/residence/mustakil-ev/null",
  "is_furnished": boolean/null,
  "seller_type": "sahibinden/emlak/null",
  "extra_notes": "string/null"
}}

User text: "{text_input}"
"""

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    cleaned_res = response.text.strip()
    if cleaned_res.startswith("```json"):
        cleaned_res = cleaned_res.replace("```json\n", "", 1)
    if cleaned_res.startswith("```"):
        cleaned_res = cleaned_res.replace("```\n", "", 1)
    if cleaned_res.endswith("```"):
        cleaned_res = cleaned_res[::-1].replace("```", "", 1)[::-1]
    cleaned_res = cleaned_res.strip()

    try:
        data = json.loads(cleaned_res)
        validated_data = SearchCriteriaSchema(**data)
        return _apply_keyword_overrides(validated_data.model_dump(), text_input)
    except Exception as e:
        raise ValueError(f"Gemini parsing failed. Error: {e}\nResponse: {response.text}")
