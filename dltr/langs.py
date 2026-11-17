"""Languages for translation. NLLB-200 (the 200-language translator) names languages like "eng_Latn"
(language + script); Whisper and langid use two-letter codes ("en"). This maps between them, lists which languages
the AI model (Qwen3) translates well, and guesses the language of a piece of text."""

import re

# Every language NLLB-200 can translate from and to (code -> name), sorted by name.
NLLB = {
    "ace_Arab": "Acehnese (Arabic script)",
    "ace_Latn": "Acehnese (Latin script)",
    "afr_Latn": "Afrikaans",
    "aka_Latn": "Akan",
    "als_Latn": "Albanian",
    "amh_Ethi": "Amharic",
    "arb_Arab": "Arabic",
    "hye_Armn": "Armenian",
    "asm_Beng": "Assamese",
    "ast_Latn": "Asturian",
    "awa_Deva": "Awadhi",
    "azj_Latn": "Azerbaijani",
    "ban_Latn": "Balinese",
    "bam_Latn": "Bambara",
    "bjn_Arab": "Banjar (Arabic script)",
    "bjn_Latn": "Banjar (Latin script)",
    "bak_Cyrl": "Bashkir",
    "eus_Latn": "Basque",
    "bel_Cyrl": "Belarusian",
    "bem_Latn": "Bemba",
    "ben_Beng": "Bengali",
    "bho_Deva": "Bhojpuri",
    "bos_Latn": "Bosnian",
    "bug_Latn": "Buginese",
    "bul_Cyrl": "Bulgarian",
    "mya_Mymr": "Burmese",
    "yue_Hant": "Cantonese",
    "cat_Latn": "Catalan",
    "ceb_Latn": "Cebuano",
    "tzm_Tfng": "Central Atlas Tamazight",
    "ayr_Latn": "Central Aymara",
    "knc_Arab": "Central Kanuri (Arabic script)",
    "knc_Latn": "Central Kanuri (Latin script)",
    "hne_Deva": "Chhattisgarhi",
    "zho_Hans": "Chinese (Simplified)",
    "zho_Hant": "Chinese (Traditional)",
    "cjk_Latn": "Chokwe",
    "crh_Latn": "Crimean Tatar",
    "hrv_Latn": "Croatian",
    "ces_Latn": "Czech",
    "dan_Latn": "Danish",
    "prs_Arab": "Dari",
    "dik_Latn": "Dinka",
    "nld_Latn": "Dutch",
    "dyu_Latn": "Dyula",
    "dzo_Tibt": "Dzongkha",
    "pan_Guru": "Eastern Panjabi",
    "arz_Arab": "Egyptian Arabic",
    "eng_Latn": "English",
    "epo_Latn": "Esperanto",
    "est_Latn": "Estonian",
    "ewe_Latn": "Ewe",
    "fao_Latn": "Faroese",
    "fij_Latn": "Fijian",
    "fin_Latn": "Finnish",
    "fon_Latn": "Fon",
    "fra_Latn": "French",
    "fur_Latn": "Friulian",
    "fuv_Latn": "Fulfulde",
    "glg_Latn": "Galician",
    "lug_Latn": "Ganda",
    "kat_Geor": "Georgian",
    "deu_Latn": "German",
    "ell_Grek": "Greek",
    "grn_Latn": "Guarani",
    "guj_Gujr": "Gujarati",
    "hat_Latn": "Haitian Creole",
    "hau_Latn": "Hausa",
    "heb_Hebr": "Hebrew",
    "hin_Deva": "Hindi",
    "hun_Latn": "Hungarian",
    "isl_Latn": "Icelandic",
    "ibo_Latn": "Igbo",
    "ilo_Latn": "Ilocano",
    "ind_Latn": "Indonesian",
    "gle_Latn": "Irish",
    "ita_Latn": "Italian",
    "jpn_Jpan": "Japanese",
    "jav_Latn": "Javanese",
    "kac_Latn": "Jingpho",
    "kbp_Latn": "KabiyÃ¨",
    "kea_Latn": "Kabuverdianu",
    "kab_Latn": "Kabyle",
    "kam_Latn": "Kamba",
    "kan_Knda": "Kannada",
    "kas_Arab": "Kashmiri (Arabic script)",
    "kas_Deva": "Kashmiri (Devanagari script)",
    "kaz_Cyrl": "Kazakh",
    "khm_Khmr": "Khmer",
    "kon_Latn": "Kikongo",
    "kik_Latn": "Kikuyu",
    "kmb_Latn": "Kimbundu",
    "kin_Latn": "Kinyarwanda",
    "kor_Hang": "Korean",
    "kmr_Latn": "Kurdish (Kurmanji)",
    "ckb_Arab": "Kurdish (Sorani)",
    "kir_Cyrl": "Kyrgyz",
    "lao_Laoo": "Lao",
    "ltg_Latn": "Latgalian",
    "lvs_Latn": "Latvian",
    "lij_Latn": "Ligurian",
    "lim_Latn": "Limburgish",
    "lin_Latn": "Lingala",
    "lit_Latn": "Lithuanian",
    "lmo_Latn": "Lombard",
    "lua_Latn": "Luba-Kasai",
    "luo_Latn": "Luo",
    "ltz_Latn": "Luxembourgish",
    "mkd_Cyrl": "Macedonian",
    "mag_Deva": "Magahi",
    "mai_Deva": "Maithili",
    "plt_Latn": "Malagasy",
    "zsm_Latn": "Malay",
    "mal_Mlym": "Malayalam",
    "mlt_Latn": "Maltese",
    "mri_Latn": "Maori",
    "mar_Deva": "Marathi",
    "mni_Beng": "Meitei (Bengali script)",
    "acm_Arab": "Mesopotamian Arabic",
    "min_Latn": "Minangkabau (Latin script)",
    "lus_Latn": "Mizo",
    "khk_Cyrl": "Mongolian",
    "ary_Arab": "Moroccan Arabic",
    "mos_Latn": "Mossi",
    "ars_Arab": "Najdi Arabic",
    "npi_Deva": "Nepali",
    "apc_Arab": "North Levantine Arabic",
    "nso_Latn": "Northern Sotho",
    "nob_Latn": "Norwegian",
    "nno_Latn": "Norwegian Nynorsk",
    "nus_Latn": "Nuer",
    "nya_Latn": "Nyanja",
    "oci_Latn": "Occitan",
    "ory_Orya": "Odia",
    "gaz_Latn": "Oromo",
    "pag_Latn": "Pangasinan",
    "pap_Latn": "Papiamento",
    "pbt_Arab": "Pashto",
    "pes_Arab": "Persian",
    "pol_Latn": "Polish",
    "por_Latn": "Portuguese",
    "quy_Latn": "Quechua",
    "ron_Latn": "Romanian",
    "run_Latn": "Rundi",
    "rus_Cyrl": "Russian",
    "smo_Latn": "Samoan",
    "sag_Latn": "Sango",
    "san_Deva": "Sanskrit",
    "sat_Beng": "Santali",
    "srd_Latn": "Sardinian",
    "gla_Latn": "Scottish Gaelic",
    "srp_Cyrl": "Serbian",
    "shn_Mymr": "Shan",
    "sna_Latn": "Shona",
    "scn_Latn": "Sicilian",
    "szl_Latn": "Silesian",
    "snd_Arab": "Sindhi",
    "sin_Sinh": "Sinhala",
    "slk_Latn": "Slovak",
    "slv_Latn": "Slovenian",
    "som_Latn": "Somali",
    "azb_Arab": "South Azerbaijani",
    "ajp_Arab": "South Levantine Arabic",
    "sot_Latn": "Southern Sotho",
    "spa_Latn": "Spanish",
    "bod_Tibt": "Standard Tibetan",
    "sun_Latn": "Sundanese",
    "swh_Latn": "Swahili",
    "ssw_Latn": "Swati",
    "swe_Latn": "Swedish",
    "tgl_Latn": "Tagalog",
    "tgk_Cyrl": "Tajik",
    "taq_Latn": "Tamasheq (Latin script)",
    "taq_Tfng": "Tamasheq (Tifinagh script)",
    "tam_Taml": "Tamil",
    "tat_Cyrl": "Tatar",
    "acq_Arab": "Taâ€™izzi-Adeni Arabic",
    "tel_Telu": "Telugu",
    "tha_Thai": "Thai",
    "tir_Ethi": "Tigrinya",
    "tpi_Latn": "Tok Pisin",
    "tso_Latn": "Tsonga",
    "tsn_Latn": "Tswana",
    "tum_Latn": "Tumbuka",
    "aeb_Arab": "Tunisian Arabic",
    "tur_Latn": "Turkish",
    "tuk_Latn": "Turkmen",
    "twi_Latn": "Twi",
    "ukr_Cyrl": "Ukrainian",
    "umb_Latn": "Umbundu",
    "urd_Arab": "Urdu",
    "uig_Arab": "Uyghur",
    "uzn_Latn": "Uzbek",
    "vec_Latn": "Venetian",
    "vie_Latn": "Vietnamese",
    "war_Latn": "Waray",
    "cym_Latn": "Welsh",
    "wol_Latn": "Wolof",
    "xho_Latn": "Xhosa",
    "ydd_Hebr": "Yiddish",
    "yor_Latn": "Yoruba",
    "zul_Latn": "Zulu",
}

# Whisper / ISO 639-1 code -> NLLB code (for transcripts, whose language Whisper already detected).
ISO_TO_NLLB = {
    "en": "eng_Latn", "zh": "zho_Hans", "yue": "yue_Hant", "ms": "zsm_Latn", "ta": "tam_Taml", "id": "ind_Latn",
    "ja": "jpn_Jpan", "ko": "kor_Hang", "th": "tha_Thai", "vi": "vie_Latn", "tl": "tgl_Latn", "hi": "hin_Deva",
    "bn": "ben_Beng", "ar": "arb_Arab", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn", "it": "ita_Latn",
    "pt": "por_Latn", "ru": "rus_Cyrl", "nl": "nld_Latn", "tr": "tur_Latn", "pl": "pol_Latn", "uk": "ukr_Cyrl",
    "sv": "swe_Latn", "no": "nob_Latn", "nn": "nno_Latn", "da": "dan_Latn", "fi": "fin_Latn", "el": "ell_Grek",
    "he": "heb_Hebr", "fa": "pes_Arab", "ur": "urd_Arab", "cs": "ces_Latn", "ro": "ron_Latn", "hu": "hun_Latn",
    "ca": "cat_Latn", "hr": "hrv_Latn", "bg": "bul_Cyrl", "lt": "lit_Latn", "mi": "mri_Latn", "ml": "mal_Mlym",
    "cy": "cym_Latn", "sk": "slk_Latn", "te": "tel_Telu", "lv": "lvs_Latn", "sr": "srp_Cyrl", "az": "azj_Latn",
    "sl": "slv_Latn", "kn": "kan_Knda", "et": "est_Latn", "mk": "mkd_Cyrl", "eu": "eus_Latn", "is": "isl_Latn",
    "hy": "hye_Armn", "ne": "npi_Deva", "mn": "khk_Cyrl", "bs": "bos_Latn", "kk": "kaz_Cyrl", "sq": "als_Latn",
    "sw": "swh_Latn", "gl": "glg_Latn", "mr": "mar_Deva", "pa": "pan_Guru", "si": "sin_Sinh", "km": "khm_Khmr",
    "sn": "sna_Latn", "yo": "yor_Latn", "so": "som_Latn", "af": "afr_Latn", "oc": "oci_Latn", "ka": "kat_Geor",
    "be": "bel_Cyrl", "tg": "tgk_Cyrl", "sd": "snd_Arab", "gu": "guj_Gujr", "am": "amh_Ethi", "yi": "ydd_Hebr",
    "lo": "lao_Laoo", "uz": "uzn_Latn", "fo": "fao_Latn", "ht": "hat_Latn", "ps": "pbt_Arab", "tk": "tuk_Latn",
    "mt": "mlt_Latn", "lb": "ltz_Latn", "my": "mya_Mymr", "bo": "bod_Tibt", "mg": "plt_Latn", "as": "asm_Beng",
    "tt": "tat_Cyrl", "ln": "lin_Latn", "ha": "hau_Latn", "ba": "bak_Cyrl", "jw": "jav_Latn", "jv": "jav_Latn",
    "su": "sun_Latn", "sa": "san_Deva", "ga": "gle_Latn", "ky": "kir_Cyrl", "ku": "kmr_Latn", "zu": "zul_Latn",
    "xh": "xho_Latn", "ig": "ibo_Latn", "rw": "kin_Latn", "or": "ory_Orya", "ug": "uig_Arab", "lg": "lug_Latn",
}
NLLB_TO_ISO = {v: k for k, v in ISO_TO_NLLB.items() if k not in ("jw", "nn")}
NLLB_TO_ISO.update({"zho_Hant": "zh", "zho_Hans": "zh"})

# Languages the AI model (Qwen3) translates well. For the rest NLLB-200 is used.
AI_GOOD = {"eng_Latn", "zho_Hans", "zho_Hant", "yue_Hant", "jpn_Jpan", "kor_Hang", "zsm_Latn", "ind_Latn", "tha_Thai",
           "vie_Latn", "tgl_Latn", "spa_Latn", "fra_Latn", "deu_Latn", "ita_Latn", "por_Latn", "rus_Cyrl", "nld_Latn",
           "tur_Latn", "pol_Latn", "ukr_Cyrl", "arb_Arab", "hin_Deva", "swe_Latn", "dan_Latn", "nob_Latn", "fin_Latn",
           "ces_Latn", "ell_Grek", "heb_Hebr", "ron_Latn", "hun_Latn", "pes_Arab"}

# How the AI model is told which language to write.
AI_NAME = {"zho_Hans": "Simplified Chinese", "zho_Hant": "Traditional Chinese",
           "yue_Hant": "Cantonese (written colloquial Cantonese in Traditional Chinese characters)",
           "zsm_Latn": "Malay", "ind_Latn": "Indonesian", "nob_Latn": "Norwegian (Bokmal)", "pes_Arab": "Persian"}


def name(code):
    return NLLB.get(code, code)


def choice(code):
    """Text shown in the language lists: 'Japanese (jpn_Jpan)'."""
    return f"{name(code)} ({code})"


def from_choice(text):
    m = re.search(r"\(([a-z]{3}_[A-Z][a-z]{3})\)\s*$", text or "")
    return m.group(1) if m else None


def from_iso(iso):
    return ISO_TO_NLLB.get((iso or "").lower())


def ai_name(code):
    return AI_NAME.get(code) or name(code)


# A few characters that only appear in Traditional (left) or only in Simplified (right) Chinese.
_TRAD = set("這們個說時來為會國對開關學問題還發進過裡後麼樣點東車長門買賣實歡體聽讀寫話電視網頁")
_SIMP = set("这们个说时来为会国对开关学问题还发进过里后么样点东车长门买卖实欢体听读写话电视网页")
_CANTO = re.compile("[嘅唔係咗冇佢喺啲嚟咁噉嘢乜嗰邊度睇攞俾畀咪喇囉嚿]")


def chinese_variant(text):
    if _CANTO.search(text):
        return "yue_Hant"
    t = sum(c in _TRAD for c in text)
    s = sum(c in _SIMP for c in text)
    return "zho_Hant" if t > s else "zho_Hans"


_lid = None
COMMON = ["en", "zh", "ms", "id", "ta", "ja", "ko", "th", "vi", "tl", "hi", "bn", "ar", "es", "fr", "de", "it", "pt",
          "ru", "nl", "tr", "pl", "uk", "sv", "no", "da", "fi", "el", "he", "fa", "ur", "cs", "ro", "hu", "ca", "hr",
          "bg", "lt", "ml", "te", "kn", "mr", "gu", "pa", "si", "ne", "km", "lo", "my", "sw", "af", "sk", "sl", "sr",
          "lv", "et", "is", "ga", "cy", "eu", "gl", "hy", "ka", "kk", "uz", "mn", "am", "ps", "az", "sq", "mk", "be"]


def detect(text, fallback="eng_Latn"):
    """Best guess of the language of a piece of text (NLLB code)."""
    global _lid
    text = (text or "").strip()
    if not text:
        return fallback
    if re.search(r"[぀-ヿ]", text):            # kana -> Japanese
        return "jpn_Jpan"
    if re.search(r"[가-힯]", text):            # hangul -> Korean
        return "kor_Hang"
    if re.search(r"[一-鿿]", text):
        han = len(re.findall(r"[一-鿿]", text))
        if han >= max(2, len(re.sub(r"\s", "", text)) * 0.3):
            return chinese_variant(text)
    try:
        if _lid is None:
            from langid.langid import LanguageIdentifier, model
            _lid = LanguageIdentifier.from_modelstring(model, norm_probs=True)
            # Only widely used languages: otherwise casual Malay often comes out as Javanese, etc.
            _lid.set_languages([c for c in COMMON if c in _lid.nb_classes])
        iso, prob = _lid.classify(text)
    except Exception:
        return fallback
    if len(text) < 25 and prob < 0.9:
        return fallback                                  # too short to be sure
    if iso == "zh":
        return chinese_variant(text)
    return from_iso(iso) or fallback
