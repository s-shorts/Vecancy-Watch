import os
import re
from pathlib import Path

APP_NAME = "VacancyWatch"
PKG_DIR = Path(__file__).resolve().parent
SOURCES_CSV = PKG_DIR / "data" / "sources.csv"
NATIONAL = "All India"


def data_dir() -> Path:
    d = Path(os.environ.get("VACANCYWATCH_HOME", Path.home() / ".vacancywatch"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "vacancywatch.db"


STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
]
UNION_TERRITORIES = [
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]

# Each profession defines the phrases that make a notice relevant.
# Only Assistant Professor is enabled for now; the others are placeholders for later.
PROFESSIONS = {
    "assistant_professor": {
        "label": "Assistant Professor",
        "available": True,
        "description": "Teaching faculty posts at government universities, colleges and institutes.",
        "patterns": [
            r"assistant\s*professors?",
            r"asst\.?\s*prof",
            r"asstt\.?\s*prof",
            r"faculty\s+(?:recruitment|positions?|posts?|vacanc\w*|advertisement|requirement)",
            r"(?:recruitment|appointment|requirement|selection)\s+(?:of|for|to)\s+(?:the\s+)?(?:posts?\s+of\s+)?(?:regular\s+|contractual\s+|guest\s+)?(?:teaching|faculty)",
            r"teaching\s+(?:posts?|positions?|faculty|staff\s+recruitment|vacanc\w*)",
            r"guest\s+(?:faculty|lecturers?)",
            r"सहायक\s*(?:आचार्य|प्राध्यापक|प्रोफेसर|प्रोफ़ेसर)",
            r"असिस्टेंट\s*प्रो(?:फेसर|फ़ेसर)",
        ],
    },
    "associate_professor": {"label": "Associate Professor", "available": False,
                            "description": "Coming soon", "patterns": []},
    "professor": {"label": "Professor", "available": False, "description": "Coming soon", "patterns": []},
    "librarian": {"label": "Librarian", "available": False, "description": "Coming soon", "patterns": []},
}


# Subjects you can focus on. "patterns" are matched against the notice and the full notification text.
SUBJECTS = {
    "computer_science": {
        "label": "Computer Science",
        "patterns": [r"computer\s*sc(?:ience|\.)?", r"(?-i:\bC\.?S\.?E\b)", r"computer\s*(?:science\s*(?:&|and)\s*)?engineering",
                     r"computer\s*applications?", r"(?-i:\bM\.?C\.?A\b)", r"(?-i:\bB\.?C\.?A\b)", r"comp\.?\s*sc",
                     r"कम्प्यूटर|कंप्यूटर|संगणक"],
    },
    "data_science": {
        "label": "Data Science",
        "patterns": [r"data\s*science", r"data\s*analytics", r"artificial\s*intelligence", r"machine\s*learning",
                     r"(?-i:\bAI\s*(?:&|and|/)\s*(?:DS|ML)\b)", r"(?-i:\bAI-?ML\b|\bAI-?DS\b)", r"डाटा\s*साइंस|डेटा\s*विज्ञान"],
    },
    "software_engineering": {
        "label": "Software Engineering",
        "patterns": [r"software\s*engineering", r"software\s*(?:development|technology)"],
    },
    "information_technology": {
        "label": "Information Technology",
        "patterns": [r"information\s*technology", r"(?-i:\bI\.T\.?)(?=\s|$|[,)])", r"(?<![A-Za-z])(?-i:IT)(?=\s*(?:department|dept|&|and|\)|,|$))",
                     r"सूचना\s*प्रौद्योगिकी"],
    },
}
DEFAULT_SUBJECTS = list(SUBJECTS)

# Other common subjects - used only to list which subjects a notice mentions.
OTHER_SUBJECTS = [
    "Mathematics", "Statistics", "Physics", "Chemistry", "Botany", "Zoology", "Biotechnology", "Biochemistry",
    "Microbiology", "Electronics", "Commerce", "Economics", "Management", "Business Administration", "Law",
    "Political Science", "Sociology", "History", "Ancient History", "Medieval History", "Geography", "Psychology",
    "Philosophy", "Education", "Physical Education", "English", "Hindi", "Sanskrit", "Urdu", "Music",
    "Fine Arts", "Home Science", "Library Science", "Journalism", "Social Work", "Defence Studies", "Geology",
    "Agriculture", "Pharmacy", "Mechanical Engineering", "Civil Engineering", "Electrical Engineering",
    "Electronics and Communication", "Anthropology", "Environmental Science",
]


def compiled_subjects(keys, custom=()):
    out = {}
    for k in keys or []:
        if k in SUBJECTS:
            out[SUBJECTS[k]["label"]] = [re.compile(p, re.I) for p in SUBJECTS[k]["patterns"]]
    for c in custom or []:
        c = c.strip()
        if c:
            out[c] = [re.compile(r"\b" + re.escape(c).replace(r"\ ", r"\s*") + r"\b", re.I)]
    return out


def compiled_patterns(profession: str):
    prof = PROFESSIONS.get(profession) or PROFESSIONS["assistant_professor"]
    return [re.compile(p, re.I) for p in prof["patterns"]]


DEFAULT_SETTINGS = {
    "profession": None,
    "states": [],
    "include_national": True,
    "include_colleges": True,          # constituent / govt colleges found on university websites
    "subjects": DEFAULT_SUBJECTS,
    "custom_subjects": [],
    "scan_interval_hours": 6,
    "reminder_days": [7, 3, 1],
    "reminder_scope": "all",          # "all" or "starred"
    "notify_likely_open": True,       # alert for notices without a clear deadline
    "notify_desktop": True,
    "telegram_enabled": False,
    "telegram_token": "",
    "telegram_chat_id": "",
    "email_enabled": False,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "email_to": "",
    "last_scan": None,
}
