import os
import json
import google.generativeai as genai

async def analyze_listing_with_gemini(listing_title: str, listing_details: str, extra_notes: str) -> dict:
    """
    Kullanıcının ekstra kriterleriyle (extra_notes) ilan verisini kıyaslar ve 
    [uygun_mu, skor, özet] dönen bir JSON döndürür.
    """
    if not extra_notes:
        return {"uygun": True, "skor": 100, "ozet": "Özel bir kriter (extra_notes) belirtilmediği için ilanın temel özelliklere uyması yeterli görüldü."}
        
    prompt = f"""
Kullanıcı şu emlak özelliklerini arıyor (ekstra beklentiler):
"{extra_notes}"

Aşağıdaki İlanı İncele:
İlan Başlığı: {listing_title}
İlan Detayları/Açıklaması: {listing_details}

Bu ilan kullanıcının özel isteklerine (ekstra beklentilerine) uyuyor mu?
Sonucu mutlak JSON olarak döndür. JSON Şeması:
{{
  "uygun": true veya false,
  "skor": 0-100 arası integer (100 tam uyum),
  "ozet": "İlanın bu taleplere neden uygun olduğu veya olmadığı kısaca"
}}
SADECE JSON YANITI VER, MARKDOWN KULLANMA.
"""
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = await model.generate_content_async(prompt)
    
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
        return data
    except Exception as e:
        return {"uygun": False, "skor": 0, "ozet": f"Analiz hatası: {str(e)}"}
