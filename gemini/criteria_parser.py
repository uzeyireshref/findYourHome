import os
import json
import unicodedata
import google.generativeai as genai
from pydantic import BaseModel, Field
from typing import Optional

class SearchCriteriaSchema(BaseModel):
    city: Optional[str] = Field(None, description="Şehir adı, örneğin: İstanbul")
    district: Optional[str] = Field(None, description="İlçe adı, örneğin: Kadıköy")
    min_price: Optional[float] = Field(None, description="Minimum bütçe/fiyat")
    max_price: Optional[float] = Field(None, description="Maksimum bütçe/fiyat")
    min_rooms: Optional[int] = Field(None, description="Minimum oda sayısı (örnek: 3+1 için 3)")
    max_rooms: Optional[int] = Field(None, description="Maksimum oda sayısı")
    max_building_age: Optional[int] = Field(None, description="Maksimum bina yaşı")
    listing_type: Optional[str] = Field(None, description="'kiralik', 'satilik' veya 'gunluk-kiralik'")
    property_type: Optional[str] = Field(None, description="'daire', 'villa', 'residence', 'mustakil-ev' vb.")
    is_furnished: Optional[bool] = Field(None, description="TRUE eşyalı (mobilyalı), FALSE eşyasız, NULL belirtilmemiş demek.")
    seller_type: Optional[str] = Field(None, description="'sahibinden', 'emlak' (veya ofis, acente) vb. belirtilmemişse null")
    extra_notes: Optional[str] = Field(None, description="Diğer talepler: güney cephe, asansörlü vb.")

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

    return criteria

async def parse_user_request(text_input: str) -> dict:
    """
    Sends the user's free-text request to Gemini and returns parsed criteria JSON.
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not set.")
        
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    
    prompt = f"""
Aşağıdaki emlak arama metnini analiz et ve JSON objesi olarak gerekli kriterleri çıkar.
Veri bulunmuyorsa o alan için değer olarak "null" kullan.
Yanıtın sadece JSON metni olsun, başına sonuna markdown ekleme (```json vb. KULLANMA).

JSON Şeması:
{{
  "city": "string/null",
  "district": "string/null (birden fazla ilçe/bölge belirtilmişse aralarına virgül koyarak yaz: Kadıköy, Üsküdar vb.)",
  "min_price": float/null,
  "max_price": float/null,
  "min_rooms": int/null (sadece oda sayısı, salon olmadan),
  "max_rooms": int/null,
  "max_building_age": int/null,
  "listing_type": "string/null ('kiralik', 'satilik' veya 'gunluk-kiralik'; belirtilmemişse kiralik)",
  "property_type": "string/null ('daire', 'villa', 'residence', 'mustakil-ev' vb.; belirtilmemişse daire)",
  "is_furnished": boolean/null (eşyalı isteniyorsa true, eşyasız isteniyorsa false, fark etmezse null),
  "seller_type": "string/null ('sahibinden' veya 'emlak', kısıtlanmamışsa null)",
  "extra_notes": "string/null (diğer özel isteklerin tümü buraya)"
}}

Kullanıcı Metni: "{text_input}"
    """
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = await model.generate_content_async(prompt)
    
    # Text temizliği (Olası markdown kod bloğu sınırlarını kaldırma)
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
        raise ValueError(f"Gemini'den dönen yanıt ayrıştırılamadı. Hata: {{e}}\nYanıt: {{response.text}}")
