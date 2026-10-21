"""YouTube library for Sterling TV: latest uploads from official channels and their live streams.

Only public data, only from the fixed channel list below, so the route that serves it can be public
without becoming a general-purpose fetcher. Uploads come from YouTube's channel feeds (no API key);
live status comes from each channel's /live page, whose canonical link names the live video.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import httpx

VOD_TTL = 30 * 60
LIVE_TTL = 10 * 60
MAX_FEED_BYTES = 1_000_000
HEAD_BYTES = 1_200_000
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+1",
}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "m": "http://search.yahoo.com/mrss/"}


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    lang: str
    country: str = "IN"


# Library rows, in display order. Every channel is the official account of the rights holder.
LIBRARY: dict[str, tuple[str, list[Channel]]] = {
    "documentaries": ("Documentaries", [
        Channel("UCW39zufHfsuGgpLviKh297Q", "DW Documentary", "English", "DE"),
        Channel("UCijcd0GR0fkxCAZwkiuWqtQ", "Free Documentary", "English", "DE"),
        Channel("UC_g32e3JeECjEuRbk-loywg", "Free High-Quality Documentaries", "English", "US"),
        Channel("UCu4XcDBdnZkV6-5z2f16M0g", "Real Stories", "English", "GB"),
        Channel("UCpVm7bg6pXKo1Pr6k5kxG9A", "National Geographic", "English", "US"),
        Channel("UCwmZiChSryoWQCZMIQezgTg", "BBC Earth", "English", "GB"),
        Channel("UCWqPRUsJlZaDp-PVbqEch9g", "Smithsonian Channel", "English", "US"),
        Channel("UCn8zNIfYAQNdrFRrr8oibKw", "VICE", "English", "US"),
        Channel("UC56-aeQA38z72h9pQGj-MOg", "Animal Planet India", "Hindi"),
        Channel("UC6n8I1UDTKP1IWjQMg6_TwA", "The B1M", "English", "GB"),
    ]),
    "movies": ("Full movies", [
        Channel("UCyoXW-Dse7fURq30EWl_CUA", "Goldmines", "Hindi"),
        Channel("UCBOmfqgTZi7yDp4-3Lr_3lA", "Shemaroo Movies", "Hindi"),
        Channel("UCEKWXRsfUHkan-D_ljU8Asw", "Rajshri", "Hindi"),
        Channel("UCYauDsl-rswjQGpA0_-Z9Pw", "Ultra Movie Parlour", "Hindi"),
        Channel("UC3ar28GS6o1p0m_wabfk2zw", "Pen Movies", "Hindi"),
        Channel("UCObyBrdrtQ20BU9PxHQDY9w", "Pen Multiplex", "Hindi"),
        Channel("UCAr27NDctKdLpIzzVvJlhFg", "Sri Balaji Video", "Telugu"),
        Channel("UCX_uPA_dGf7wXjuMEaSKLJA", "Aditya Movies", "Telugu"),
        Channel("UCoy3dQzEdq1y2zMnT4pdj3Q", "Sri Balaji Movies", "Telugu"),
        Channel("UCintIUOJEktQBfhEI9XXpuw", "Telugu Filmnagar", "Telugu"),
        Channel("UC26UNezdPZfGkRnxX3fVWGA", "Sun NXT Malayalam", "Malayalam"),
        Channel("UCXokGzAS1tSy5NJUpAigu_w", "SVF Movies", "Bengali"),
        Channel("UC8IHAQMuiJdY6ALuhG7iU8Q", "FilmRise Movies", "English", "US"),
        Channel("UCVFYikepF-avelvuIaQ_lHA", "Popcornflix", "English", "US"),
    ]),
    "web_series": ("Web series", [
        Channel("UCNJcSUSzUeFm8W9P7UUlSeQ", "The Viral Fever", "Hindi"),
        Channel("UC1yr6_1-dXRPi7P-q2Ku5Yw", "Qtiyapa", "Hindi"),
        Channel("UC7IMq6lLHbptAnSucW1pClA", "FilterCopy", "Hindi"),
        Channel("UCdxbhKxr8pyWTx1ExCSmJRw", "Girliyapa", "Hindi"),
        Channel("UCTlnaHHQ75zlDg_fLr7tGEg", "The Timeliners", "Hindi"),
        Channel("UCqwUrj10mAEsqezcItqvwEw", "BB Ki Vines", "Hindi"),
        Channel("UC_vcKmg67vjMP7ciLnSxSHQ", "Amit Bhadana", "Hindi"),
        Channel("UCn2wC753SXLd2BJYLss_jlA", "Put Chutney", "Tamil"),
        Channel("UCr0_xzS43ijsp0ab4XUGjkQ", "Hoichoi", "Bengali"),
    ]),
    "tv_shows": ("TV shows", [
        Channel("UCSjPe5kinQtwcyHcFJyyMfw", "Doordarshan National", "Hindi"),
        Channel("UC6-F5tO8uklgE9Zy8IvbdFw", "Sony SAB", "Hindi"),
        Channel("UCpEhnqL0y41EpW2TvWAHD7Q", "SET India", "Hindi"),
        Channel("UC-LPIU24bQXVljUXivKEeRQ", "StarPlus", "Hindi"),
        Channel("UCppHT7SZKKvar4Oc9J4oljQ", "Zee TV", "Hindi"),
        Channel("UC55IWqFLDH1Xp7iu1_xknRA", "Colors TV", "Hindi"),
        Channel("UCnoqvTW4YZExfDeq7_Wmd-w", "ETV Jabardasth", "Telugu"),
        Channel("UCE8FZJKzY53hrncyM_n0QWA", "ETV Win", "Telugu"),
        Channel("UCBnxEdpoZwstJqC1yZpOjRA", "Sun TV", "Tamil"),
        Channel("UCvrhwpnp2DHYQ1CbXby9ypQ", "Vijay Television", "Tamil"),
    ]),
    "trailers": ("New on streaming", [
        Channel("UCWOA1ZGywLbqmigxE4Qlvuw", "Netflix", "English", "US"),
        Channel("UCQJWtTnAHhEG5w4uN0udnUQ", "Prime Video", "English", "US"),
        Channel("UC4zWG9LccdWGUlF77LZ8toA", "Prime Video India", "Hindi"),
        Channel("UCOQNJjhXwvAScuELTT_i7cQ", "Sony LIV", "Hindi"),
        Channel("UCXOgAl4w-FQero1ERbGHpXQ", "ZEE5", "Hindi"),
        Channel("UCials1wQnEN_NykYZr1048w", "Sun NXT", "Tamil"),
        Channel("UC617yLdi31EfTbgWfxTWXCg", "ETV WIN", "Telugu"),
    ]),
    "science": ("Science & space", [
        Channel("UCsXVk37bltHxD1rDPwtNM8Q", "Kurzgesagt", "English", "DE"),
        Channel("UCHnyfMqiRRG1u-2MsSQLbXA", "Veritasium", "English", "US"),
        Channel("UCLA_DiR1FfKNvjuUpBHmylQ", "NASA", "English", "US"),
        Channel("UCvJiYiBUbw4tmpRSZT2r1Hw", "Science Channel", "English", "US"),
        Channel("UCHJuQZuzapBh-CuhRYxIZrg", "Insider", "English", "US"),
    ]),
    "kids": ("Kids", [
        Channel("UCbCmjCuTUZos6Inko4u57UQ", "Cocomelon", "English", "US"),
        Channel("UCBnZ16ahKA2DZ_T5W0FPUXg", "ChuChu TV", "English"),
        Channel("UCAOtE1V7Ots4DjM8JLlrYgg", "Peppa Pig", "English", "GB"),
    ]),
    "podcasts": ("Podcasts", [
        Channel("UCPxMZIFE856tbTfdkdjzTSQ", "The Ranveer Show (BeerBiceps)", "Hindi"),
        Channel("UCzwCEE_PchiBULMnAJqhGVg", "Figuring Out with Raj Shamani", "Hindi"),
        Channel("UC47GlIglvmEUVpXSMHH51vw", "FiguringOut", "Hindi"),
        Channel("UCYalWwW79BdxmMTKUyBFj5g", "Shubhankar Mishra", "Hindi"),
        Channel("UCFPg96eW9CVNW-dkGNS0ulA", "People by WTF (Nikhil Kamath)", "English"),
        Channel("UCu7af8z6YlAOAIGAS6XE3Rw", "TRS Clips", "Hindi"),
        Channel("UCSHZKyawb77ixDdsGog4iWA", "Lex Fridman", "English", "US"),
        Channel("UC2D2CMWXMOVWx7giW1n3LIg", "Huberman Lab", "English", "US"),
        Channel("UCGq-a57w-aPwyi3pW7XLiHw", "The Diary Of A CEO", "English", "GB"),
    ]),
    "music_hindi": ("Music · Hindi", [
        Channel("UCq-Fj5jknLsUf-MWSy4_brA", "T-Series", "Hindi"),
        Channel("UCFFbwnve3yF62-tVXkTyHqg", "Zee Music Company", "Hindi"),
        Channel("UC56gTxNs4f9xZ7Pa2i5xNzg", "Sony Music India", "Hindi"),
        Channel("UCJrDMFOdv1I2k8n9oK_V21w", "Tips Official", "Hindi"),
        Channel("UC5XiydmXHj2IIN0oIjyoYAw", "Music India", "Hindi"),
    ]),
    "music_punjabi": ("Music · Punjabi", [
        Channel("UCOsyDsO5tIt-VZ1iwjdQmew", "Speed Records", "Punjabi"),
        Channel("UCxQRUQmsAHA5GMAI4nddx_w", "White Hill Music", "Punjabi"),
    ]),
    "music_telugu": ("Music · Telugu", [
        Channel("UCNApqoVYJbYSrni4YsbXzyQ", "Aditya Music", "Telugu"),
        Channel("UCWqyzn3cDkRDh3kRGWrIQwA", "Mango Music", "Telugu"),
        Channel("UCyhgNoFwssv3hG-gc29sEnw", "Lahari Music Telugu", "Telugu"),
        Channel("UCnJjcn5FrgrOEp5_N45ZLEQ", "T-Series Telugu", "Telugu"),
    ]),
    "music_tamil": ("Music · Tamil", [
        Channel("UCTNtRdBAiZtHP9w7JinzfUg", "Sony Music South", "Tamil"),
        Channel("UCAEv0ANkT221wXsTnxFnBsQ", "T-Series Tamil", "Tamil"),
        Channel("UCLsSLka8jODBozvi5VTQeaQ", "Zee Music South", "Tamil"),
    ]),
    "music_regional": ("Music · Kannada, Bengali, Marathi, Gujarati", [
        Channel("UCLtCejNl8eAg4PO_9lf2TIg", "Anand Audio", "Kannada"),
        Channel("UCCPPUrQOQ12l9Xr-B7A351Q", "SVF Music", "Bengali"),
        Channel("UCe6txxh-HwjtdOlmXL1md_Q", "Amara Muzik Bengali", "Bengali"),
        Channel("UCe7UKRSJM6ITMfXX-CbET0w", "Everest Marathi", "Marathi"),
        Channel("UCf40kH47yvowumzzK9LVKKg", "Zee Music Marathi", "Marathi"),
        Channel("UCCo_LMj-m3iGSSuytYq9n6Q", "T-Series Marathi", "Marathi"),
        Channel("UCev6abkwjdHj_dB3rquFfbQ", "T-Series Gujarati", "Gujarati"),
    ]),
    "music_international": ("Music · International & K-pop", [
        Channel("UC2pmfLm7iq6Ov1UwYrWYkZA", "Vevo", "English", "US"),
        Channel("UCe4LM_eKc9ywRmVuBm5pjQg", "Atlantic Records", "English", "US"),
        Channel("UCDj64sior23IP_7xLe8R7sg", "Warner Records", "English", "US"),
        Channel("UCweOkPb1wVVH0Q0Tlj4a5Pw", "1theK", "Korean", "KR"),
        Channel("UC3IZKseVpdzPSBaWxBxundA", "HYBE LABELS", "Korean", "KR"),
    ]),
    "comedy": ("Comedy & stand-up", [
        Channel("UCkS7Vxu4PjM99w0Is6idjcg", "Zakir Khan", "Hindi"),
        Channel("UCzNq9i-DlDDBLjPerVzJW-A", "Kenny Sebastian", "English"),
        Channel("UCObk_g1hQBy0RKKriVX_zOQ", "Netflix Is A Joke", "English", "US"),
    ]),
    "food": ("Food & cooking", [
        Channel("UCChqsCRFePrP2X897iQkyAA", "Kabita's Kitchen", "Hindi"),
        Channel("UCEHCDn_BBnk3uTK1M64ptyw", "Chef Ranveer Brar", "Hindi"),
        Channel("UCIEv3lZ_tNXHzL3ox-_uUGQ", "Gordon Ramsay", "English", "GB"),
    ]),
    "travel": ("Travel", [
        Channel("UCl5dXugC3XZeDVsDkTaWJ4g", "Mountain Trekker", "Hindi"),
        Channel("UCt_NLJ4McJlCyYM-dSPRo7Q", "Lost LeBlanc", "English", "CA"),
    ]),
    "sports": ("Sports highlights", [
        Channel("UCt2JXOLNxqry7B_4rRZME3Q", "ICC", "English"),
        Channel("UCmqfX0S3x0I3uwLkPdpX03w", "Star Sports", "Hindi"),
        Channel("UCB_qr75-ydFVKSF9Dmo6izg", "Formula 1", "English", "GB"),
        Channel("UCWJ2lWNubArHWmf3FIHbfcQ", "NBA", "English", "US"),
        Channel("UCpcTrCXblq78GZrTUTLWeBw", "FIFA", "English", "CH"),
        Channel("UCJ5v_MCY6GNUBTO8-D3XoAg", "WWE", "English", "US"),
        Channel("UCvgfXK4nTYKudb0rFR6noLA", "UFC", "English", "US"),
        Channel("UCTl3QQTvqHFjurroKxexy2Q", "Olympics", "English", "CH"),
    ]),
    "tech": ("Tech", [
        Channel("UCBJycsmduvYEL83R_U4JriQ", "Marques Brownlee", "English", "US"),
        Channel("UCXuqSBlHAE6Xw-yeJA0Tunw", "Linus Tech Tips", "English", "CA"),
        Channel("UCOhHO2ICt0ti9KAh-QHvttQ", "Technical Guruji", "Hindi"),
    ]),
    "learning": ("Learning & talks", [
        Channel("UCAuUUnT6oDeKwE6v1NGQxug", "TED", "English", "US"),
        Channel("UCsT0YIqwnpJCM-mx7-gSA4Q", "TEDx Talks", "English", "US"),
        Channel("UC4a-Gbdw7vOaccHmFo40b9g", "Khan Academy", "English", "US"),
    ]),
    "devotional": ("Devotional", [
        Channel("UC7ZivIYRB0fMSGh-THcTYbw", "Shemaroo Bhakti", "Hindi"),
        Channel("UCaayLD9i5x4MmIoVZxXSv_g", "T-Series Bhakti Sagar", "Hindi"),
        Channel("UCn9WB2Eb1QRSYkFxuPQHkxg", "Times Music Spiritual", "Hindi"),
        Channel("UCSzOZ97LOpU-_AVlGfmD4rQ", "Bhakthi TV", "Telugu"),
    ]),
}

# 24/7 and frequent live channels: (channel, genre). Only those live right now are returned.
LIVE: list[tuple[Channel, str]] = [
    *((Channel(i, n, l, c), "News") for i, n, l, c in [
        ("UCknLrEdhRCp1aegoMqRaCZg", "DW News", "English", "DE"),
        ("UCNye-wNBqNL5ZzHSJj3l8Bg", "Al Jazeera English", "English", "QA"),
        ("UCCCPCZNChQdGa9EkATeye4g", "France 24", "French", "FR"),
        ("UCoMdktPbSTixAyNGwb-UYkQ", "Sky News", "English", "GB"),
        ("UC16niRr50-MSBwiO3YDb3RA", "BBC News", "English", "GB"),
        ("UCBi2mrWuNuyYy4gbM6fU18Q", "ABC News", "English", "US"),
        ("UCeY0bbntWzzVIaj2z3QigXg", "NBC News", "English", "US"),
        ("UC8p1vwvWtl6T73JiExfWs1g", "CBS News", "English", "US"),
        ("UC83jt4dlz1Gjl58fzQrrKZg", "CNA", "English", "SG"),
        ("UCSrZ3UV4jOidv8ppoVuvW9Q", "euronews", "English", "FR"),
        ("UC7fWeaHhqgM4Ry-RMpM2YYw", "TRT World", "English", "TR"),
        ("UC0vn8ISa4LKMunLbzaXLnOQ", "GB News", "English", "GB"),
        ("UCSPEjw8F2nQDtmUKPFNF7_A", "NHK World-Japan", "English", "JP"),
        ("UCgrNz-aDmcr2uuto8_DL2jg", "CGTN", "English", "CN"),
        ("UC5iLnYoF4Ryb63YdGD9RfWQ", "ABC Australia", "English", "AU"),
        ("UCuFFtHWoLl5fauMMD5Ww2jA", "CBC News", "English", "CA"),
        ("UChLtXXpo4Ge1ReTEboVvTDg", "Global News", "English", "CA"),
        ("UC_gUM8rL-Lrg6O3adPW9K1g", "WION", "English", "IN"),
        ("UCZFMm1mMw0F81Z37aaEzTUA", "NDTV 24x7", "English", "IN"),
        ("UCYPvAwZP8pZhSMW8qs7cVCw", "India Today", "English", "IN"),
        ("UCef1-8eOpJgud7szVPlZQAQ", "CNN-News18", "English", "IN"),
        ("UC6RJ7-PaXg6TIH2BzZfTV7w", "Times Now", "English", "IN"),
        ("UCWCEYVwSqr7Epo6sSCfUgiw", "Mirror Now", "English", "IN"),
        ("UCt4t-jeY85JegMlZ-E5UWtA", "Aaj Tak", "Hindi", "IN"),
        ("UCIvaYmXn910QMdemBG3v1pQ", "Zee News", "Hindi", "IN"),
        ("UCttspZesZIDEwwpVIgoZtWQ", "India TV", "Hindi", "IN"),
        ("UC9CYT9gSNLevX5ey2_6CK0Q", "NDTV India", "Hindi", "IN"),
        ("UCPP3etACgdUWvizcES1dJ8Q", "News18 India", "Hindi", "IN"),
        ("UCKVM5FMqX90AquMlhzNP82Q", "ETV Bharat", "Hindi", "IN"),
        ("UCPXTXMecYqnRKNdqdVOGSFg", "TV9 Telugu", "Telugu", "IN"),
        ("UCumtYpCY26F6Jr3satUgMvA", "NTV Telugu", "Telugu", "IN"),
        ("UCJi8M0hRKjz8SLPvJKEVTOg", "ETV Andhra Pradesh", "Telugu", "IN"),
        ("UCZ9m4KOh8Ei60428xeGYDCQ", "Sakshi TV", "Telugu", "IN"),
        ("UC_2irx_BQR7RsBKmUV9fePQ", "ABN Telugu", "Telugu", "IN"),
        ("UCAR3h_9fLV82N2FH4cE4RKw", "TV5 News", "Telugu", "IN"),
        ("UCfymZbh17_3T_UhgjkQ9fRQ", "10TV News", "Telugu", "IN"),
        ("UCDCMjD1XIAsCZsYHNMGVcog", "V6 News", "Telugu", "IN"),
        ("UCoBjSqfZgAtcjTGO9xb24hA", "India Today Telugu", "Telugu", "IN"),
        ("UCmyKnNRH0wH-r8I-ceP-dsg", "Puthiya Thalaimurai", "Tamil", "IN"),
        ("UC8Z-VjXBtDJTvq6aqkIskPg", "Polimer News", "Tamil", "IN"),
        ("UC-JFyL0zDFOsPMpuWu39rPA", "Thanthi TV", "Tamil", "IN"),
        ("UCat88i6_rELqI_prwvjspRA", "News18 Tamil Nadu", "Tamil", "IN"),
        ("UCf8w5m0YsRa8MHQ5bwSGmbw", "Asianet News", "Malayalam", "IN"),
        ("UCP0uG-mcMImgKnJz-VjJZmQ", "Manorama News", "Malayalam", "IN"),
        ("UC-f7r46JhYv78q5pGrO6ivA", "MediaOne", "Malayalam", "IN"),
        ("UCup3etEdjyF1L3sRbU-rKLw", "24 News", "Malayalam", "IN"),
        ("UC-mMi78WJST4N5o8_i1FoXw", "News18 Kerala", "Malayalam", "IN"),
        ("UCFx1nseXKTc1Culiu3neeSQ", "Reporter TV", "Malayalam", "IN"),
        ("UC8dnBi4WUErqYQHZ4PfsLTg", "TV9 Kannada", "Kannada", "IN"),
        ("UCa-vioGhe2btBcZneaPonKA", "News18 Kannada", "Kannada", "IN"),
        ("UCvBwvQgs4T1MnSkHPoZCJig", "Suvarna News", "Kannada", "IN"),
        ("UCH7nv1A9xIrAifZJNvt7cgA", "ABP Majha", "Marathi", "IN"),
        ("UCdF5Q5QVbYstYrTfpgUl0ZA", "Zee 24 Ghanta", "Bengali", "IN"),
        ("UCbf0XHULBkTfv2hBjaaDw9Q", "News18 Bangla", "Bengali", "IN"),
        ("UCdSo1hkqcvVeQlfJkmYAUIQ", "Sandesh News", "Gujarati", "IN"),
        ("UCQLEbraENUGWh6p1Rv664rQ", "PTC News", "Punjabi", "IN"),
        ("UCT4Jg8h03dD0iN3Pb5L0PMA", "DW Español", "Spanish", "DE"),
        ("UCUdOoVWuWmgo1wByzcsyKDQ", "France 24 Español", "Spanish", "FR"),
        ("UCajrNVKvG2xVlVcSr3GcsDA", "Telemundo", "Spanish", "US"),
        ("UCdTyuXgmJkG_O8_75eqej-w", "France 24 Arabic", "Arabic", "FR"),
        ("UCMIgOXM2JEQ2Pv2d0_PVfcg", "DW Deutsch", "German", "DE"),
        ("UC5NOEUbkLheQcaaRldYW5GA", "tagesschau", "German", "DE"),
        ("UCYLksysPyWbObO2GKlqtKpA", "WELT", "German", "DE"),
        ("UCGCZAYq5Xxojl_tSXcVJhiQ", "ANN News", "Japanese", "JP"),
        ("UChlgI3UHCOnwUGzWzbJ3H5w", "YTN", "Korean", "KR"),
    ]),
    *((Channel(i, n, l, c), "Business") for i, n, l, c in [
        ("UCIALMKvObZNtJ6AmdCLP7Lg", "Bloomberg Television", "English", "US"),
        ("UChqUTb7kYRX8-EiaN3XFrSQ", "Reuters", "English", "GB"),
    ]),
    *((Channel(i, n, l, c), "Music") for i, n, l, c in [
        ("UCSJ4gkVC6NrvII8umztf0Ow", "Lofi Girl", "English", "FR"),
        ("UCOxqgCwgOqC2lMqC5PYz_Dg", "Chillhop Music", "English", "NL"),
        ("UCJ6td3C9QlPO9O_J5dF4ZzA", "Monstercat", "English", "CA"),
    ]),
    *((Channel(i, n, l, c), "Devotional") for i, n, l, c in [
        ("UCS2Y83GD-fc7qqgNW5uj41g", "SVBC TTD", "Telugu", "IN"),
        ("UCTk9aE-fuOxEj4XpqND_zQA", "Bhakti TV Live", "Telugu", "IN"),
        ("UCSzOZ97LOpU-_AVlGfmD4rQ", "Bhakthi TV", "Telugu", "IN"),
    ]),
    (Channel("UCLA_DiR1FfKNvjuUpBHmylQ", "NASA", "English", "US"), "Science"),
]

_VIDEO_ID = re.compile(r"^[\w-]{11}$")
_CANONICAL = re.compile(rb'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"')
_ANY_CANONICAL = re.compile(rb'<link rel="canonical" href="[^"]+"')
_TITLE = re.compile(rb'<meta name="title" content="([^"]{1,300})"')
ARCHIVE_PER_CATEGORY = 1500
_cache: dict[str, tuple[float, object]] = {}


def _archive_path() -> Path:
    """Next to the Sterling database, so it survives restarts; STERLING_YOUTUBE_ARCHIVE overrides."""
    override = os.environ.get("STERLING_YOUTUBE_ARCHIVE")
    if override:
        return Path(override)
    from app.services import db
    return Path(db._DB_PATH).resolve().parent / "youtube_library_archive.json"


def _load_archive() -> dict[str, list[dict]]:
    try:
        data = json.loads(_archive_path().read_text())
        return {k: v for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def merge_archive(archive: dict[str, list[dict]], key: str, fresh: list[dict]) -> list[dict]:
    """Every video ever seen for a category, newest first. Feeds only list 15 uploads per channel,
    so this is how the collection reaches back in time: it grows on every refresh."""
    by_id = {v["id"]: v for v in archive.get(key, []) if isinstance(v, dict) and _VIDEO_ID.match(str(v.get("id", "")))}
    for video in fresh:
        by_id[video["id"]] = {**by_id.get(video["id"], {}), **video}
    rows = sorted(by_id.values(), key=lambda v: str(v.get("published", "")), reverse=True)[:ARCHIVE_PER_CATEGORY]
    archive[key] = rows
    return rows


def _save_archive(archive: dict[str, list[dict]]) -> None:
    path = _archive_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(archive, separators=(",", ":")))
        tmp.replace(path)
    except OSError:
        pass
_locks: dict[str, asyncio.Lock] = {}


def parse_feed(xml: str, channel: Channel, category: str) -> list[dict]:
    """Uploads from one channel feed; Shorts are dropped (their link is /shorts/)."""
    root = ElementTree.fromstring(xml)
    out = []
    for entry in root.findall("a:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS)
        link = entry.find("a:link", NS)
        href = link.get("href", "") if link is not None else ""
        if not _VIDEO_ID.match(vid) or "/shorts/" in href:
            continue
        stats = entry.find("m:group/m:community/m:statistics", NS)
        out.append({
            "id": vid,
            "title": (entry.findtext("a:title", default="", namespaces=NS) or "").strip()[:200],
            "channel": channel.name,
            "channelId": channel.id,
            "lang": channel.lang,
            "country": channel.country,
            "category": category,
            "published": entry.findtext("a:published", default="", namespaces=NS),
            "views": int(stats.get("views", "0")) if stats is not None and stats.get("views", "").isdigit() else 0,
        })
    return out


async def _feed(client: httpx.AsyncClient, channel: Channel, category: str) -> list[dict]:
    try:
        response = await client.get("https://www.youtube.com/feeds/videos.xml",
                                     params={"channel_id": channel.id}, timeout=12)
        if response.status_code != 200 or len(response.content) > MAX_FEED_BYTES:
            return []
        return parse_feed(response.text, channel, category)
    except (httpx.HTTPError, ElementTree.ParseError):
        return []


async def _live(client: httpx.AsyncClient, channel: Channel, genre: str) -> dict | None:
    """The channel's current live video, read from the head of its /live page."""
    try:
        head = b""
        async with client.stream("GET", f"https://www.youtube.com/channel/{channel.id}/live",
                                 headers=HEADERS, timeout=15, follow_redirects=True) as response:
            found = -1
            async for chunk in response.aiter_bytes():
                head += chunk
                if found < 0:
                    canonical = _ANY_CANONICAL.search(head)
                    found = canonical.end() if canonical else -1
                # The live flag follows the canonical link by about 35 KB; stop shortly after it.
                if (found >= 0 and (b'"isLive":true' in head[found:] or len(head) > found + 120_000)) \
                        or len(head) > HEAD_BYTES:
                    break
        match = _CANONICAL.search(head)
        if not match or b'"isLive":true' not in head:
            return None
        title = _TITLE.search(head)
        return {"id": match.group(1).decode(), "name": channel.name, "channelId": channel.id,
                "title": title.group(1).decode("utf-8", "replace") if title else channel.name,
                "lang": channel.lang, "country": channel.country, "genre": genre}
    except httpx.HTTPError:
        return None


async def _cached(key: str, ttl: int, build):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        value = await build()
        # Keep serving the last good copy when YouTube is unreachable.
        if value or not hit:
            _cache[key] = (time.time(), value)
        return value if value or not hit else hit[1]


async def _gather(jobs, limit: int = 8):
    gate = asyncio.Semaphore(limit)

    async def run(job):
        async with gate:
            return await job

    return await asyncio.gather(*(run(job) for job in jobs))


async def library() -> dict:
    async def build_vod():
        async with httpx.AsyncClient(headers=HEADERS) as client:
            jobs = [(key, channel) for key, (_, channels) in LIBRARY.items() for channel in channels]
            results = await _gather(_feed(client, channel, key) for key, channel in jobs)
        archive = await asyncio.to_thread(_load_archive)
        rows = []
        for key, (label, _) in LIBRARY.items():
            fresh = [v for (k, _), batch in zip(jobs, results) if k == key for v in batch]
            videos = merge_archive(archive, key, fresh)
            if videos:
                rows.append({"id": key, "label": label, "videos": videos})
        await asyncio.to_thread(_save_archive, archive)
        return rows

    async def build_live():
        async with httpx.AsyncClient(headers=HEADERS) as client:
            found = await _gather((_live(client, channel, genre) for channel, genre in LIVE), limit=6)
        return [row for row in found if row]

    vod, live = await asyncio.gather(_cached("vod", VOD_TTL, build_vod), _cached("live", LIVE_TTL, build_live))
    return {"generated": int(time.time()), "categories": vod, "live": live}
