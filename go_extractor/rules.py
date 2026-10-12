"""
Rule-based field extraction for Karnataka Government Orders that authorise
a Government Advocate to appear in a court case.

These GOs follow a fixed template, so each field is anchored on its
standard wording ("GOVERNMENT ORDER NO", "filed by ... V/S ...",
"is authorized to appear", "shall be the Litigation Conducting Officer",
"BY ORDER AND IN THE NAME OF THE GOVERNOR ..."). Kannada labels are
matched where the value itself is unambiguous (numbers, dates). Anything
the rules cannot find is left None for the LLM fallback.
"""

from __future__ import annotations

import re

from go_extractor.models import GOExtraction, IssuingAuthority, LitigationOfficer

# ==========================================================
# HELPERS
# ==========================================================

_KANNADA_DIGITS = str.maketrans("೦೧೨೩೪೫೬೭೮೯", "0123456789")

I = re.IGNORECASE
S = re.DOTALL

DATE = (
    r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}"
)


def prepare(text: str) -> str:
    """Kannada digits -> ASCII, so numbers match one pattern."""
    return text.translate(_KANNADA_DIGITS)


def flatten(text: str) -> str:
    """Whole text on one line, for phrases that wrap across lines."""
    return re.sub(r"\s+", " ", text).strip()


def tidy(value: str | None) -> str | None:
    if not value:
        return None

    value = re.sub(r"\s+", " ", value).strip(" ,;:-")

    return value or None


def title_name(name: str) -> str:
    """'FAIROZA H. UKKALI' -> 'Fairoza H. Ukkali' (mixed case is kept)."""
    if name.upper() != name:
        return name

    return " ".join(
        word if re.fullmatch(r"[A-Z]\.?", word) else word.capitalize()
        for word in name.split()
    )


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


# ==========================================================
# 1. FILE NO.
# ==========================================================

_FILE_NO = re.compile(
    r"(?:\bfile|ಕಡತ)\s*(?:No|Number|ಸಂಖ್ಯೆ)\.?\s*[:.\-]?\s*"
    r"(?P<v>[A-Z0-9][A-Z0-9 /\-().]{2,50}?)"
    r"(?=\s+(?:received|dated|dt|from|of|on|ಸ್ವೀಕರಿಸಿದ|ದಿನಾಂಕ)\b|[,;\n]|\s{2}|$)",
    I,
)


def file_no(text: str) -> str | None:
    match = _FILE_NO.search(text)
    return tidy(match.group("v")) if match else None


# ==========================================================
# 2. GOVT. ORDER NO. & DATE
# ==========================================================

_GO_NO = re.compile(
    r"(?:GOVERNMENT\s+ORDER\s+N[O0]|G\.?\s*O\.?\s*(?:\(?Ms\.?\)?\s*)?N[O0]"
    r"|ಸರ್ಕಾರಿ\s+ಆದೇಶ\s+ಸಂಖ್ಯೆ|ಸರ್ಕಾರದ\s+ಆದೇಶ\s+ಸಂಖ್ಯೆ)"
    r"\.?\s*[:.\-]?\s*"
    r"(?P<v>[^\n,]+?)"
    r"(?=\s*(?:,|\n|\bBENGALURU\b|\bBANGALORE\b|\bDATED\b|\bdt\b|ಬೆಂಗಳೂರು|ದಿನಾಂಕ|$))",
    I,
)

_DATED = re.compile(
    rf"(?:\bDATED|\bdt|ದಿನಾಂಕ)\.?\s*[:.\-]?\s*(?P<d>{DATE})",
    I,
)


def go_number_and_date(text: str) -> tuple[str | None, str | None]:
    number_match = _GO_NO.search(text)
    number = tidy(number_match.group("v")) if number_match else None

    # The GO's own date follows its number ("BENGALURU, DATED:07.07.2025").
    start = number_match.end() if number_match else 0
    date_match = _DATED.search(text, start, start + 150) or _DATED.search(text)

    date = None

    if date_match:
        date = re.sub(r"\s*([./-])\s*", r"\1", date_match.group("d")).strip()

    return number, date


# ==========================================================
# 3. CASE NO.
# ==========================================================

_CASE_TYPES = (
    r"W\.?\s*P\.?\s*H\.?\s*C|W\.?\s*P|W\.?\s*A|C\.?\s*C\.?\s*C|R\.?\s*F\.?\s*A"
    r"|R\.?\s*S\.?\s*A|M\.?\s*F\.?\s*A|C\.?\s*R\.?\s*P|Crl\.?\s*R\.?\s*P"
    r"|Crl\.?\s*P|Crl\.?\s*A|S\.?\s*L\.?\s*P|O\.?\s*S|C\.?\s*P|R\.?\s*P"
    r"|Writ\s+Petition|Writ\s+Appeal|Contempt\s+Petition|Review\s+Petition"
)

_CASE_NO = re.compile(
    rf"\b(?P<type>{_CASE_TYPES})\.?\s*(?:\(\s*[A-Z\-/ ]+\s*\)\s*)?"
    r"No[s]?\.?\s*[:.]?\s*(?P<num>\d+)\s*/\s*(?P<year>(?:19|20)\d{2})",
    I,
)

_CASE_NO_KN = re.compile(
    r"(?P<type>ರಿಟ್\s*(?:ಅರ್ಜಿ|ಮೇಲ್ಮನವಿ)|ರಿಟ್\s*ಪಿಟಿಷನ್)\s*ಸಂಖ್ಯೆ\s*[:.\-]?\s*"
    r"(?P<num>\d+)\s*/\s*(?P<year>(?:19|20)\d{2})",
)


def case_number(text: str) -> str | None:
    found: list[str] = []

    for match in list(_CASE_NO.finditer(text)) + list(_CASE_NO_KN.finditer(text)):
        case_type = re.sub(r"\s+", "", match.group("type"))

        if re.fullmatch(r"[A-Za-z.]+", case_type):
            label = f"{case_type.rstrip('.')}.No."
        else:
            label = f"{tidy(match.group('type'))} ಸಂಖ್ಯೆ"

        value = f"{label} {match.group('num')}/{match.group('year')}"

        if value not in found:
            found.append(value)

    return ", ".join(found) or None


# ==========================================================
# 4 + 5. PETITIONER / RESPONDENT
# ==========================================================

_PARTIES = re.compile(
    r"(?:filed|preferred|instituted)\s+by\s+(?:the\s+petitioners?\s+)?"
    r"(?P<pet>.+?)\s+(?:V\s*/\s*S|Vs|Versus|V|against)\.?\s+"
    r"(?P<resp>.+?)"
    r"(?=\s+(?:on\s+the\s+file|on\s+the\s+Hon|before\s+the|in\s+the\s+Hon|pending)"
    r"|[.;]\s|$)",
    I,
)

_REPRESENTED_HEADER = re.compile(
    r"on\s+behalf\s+of\s+the\s+following\s+Respondents?", I
)

_REPRESENTED_INLINE = re.compile(
    r"on\s+behalf\s+of\s+(?:the\s+)?(?P<v>Respondents?\s+No[s]?\.?\s*"
    r"\d+(?:\s*(?:,|&|and|to)\s*\d+)*)",
    I,
)

_ITEM = re.compile(r"^\s*\(?(\d{1,2})\s*[).]\s*(.*)$")


def parties(text: str) -> tuple[str | None, str | None]:
    match = _PARTIES.search(flatten(text))

    if not match:
        return None, None

    return tidy(match.group("pet")), tidy(match.group("resp"))


def respondents_represented(text: str) -> list[str]:
    """Numbered respondents listed after 'on behalf of the following Respondent'."""
    header = _REPRESENTED_HEADER.search(text)

    if not header:
        inline = _REPRESENTED_INLINE.search(flatten(text))
        return [tidy(inline.group("v"))] if inline else []

    items: list[str] = []
    current: list[str] | None = None
    blank_after_item = False

    for line in text[header.end():].splitlines()[1:]:
        if not line.strip():
            if current is not None:
                blank_after_item = True
            continue

        item = _ITEM.match(line)

        if item:
            if current is not None:
                items.append(tidy(" ".join(current)))
            current = [item.group(2)]
            blank_after_item = False
        elif (current is not None and current[-1].rstrip().endswith(".")
              and line[:1].isupper()):
            break  # OCR without blank lines: a new sentence ends the list
        elif current is not None and not blank_after_item:
            current.append(line)
        elif current is not None:
            break  # a new paragraph after the list ends it
        elif items or len(line) > 3:
            break

    if current is not None:
        items.append(tidy(" ".join(current)))

    return [item.rstrip(".") for item in items if item]


# ==========================================================
# 6. GOVERNMENT ADVOCATE
# ==========================================================

_GA_ROLE = (
    r"(?:(?:Additional|Addl\.?|Senior|Special|Deputy|Asst\.?|Assistant)\s+)*"
    r"(?:Government\s+(?:Advocate|Pleader)|Advocate\s+General"
    r"|State\s+Public\s+Prosecutor|Public\s+Prosecutor)"
)

_GA = re.compile(
    r"(?P<ga>(?:(?:Sri|Smt|Shri|Kum|Dr)\.?\s+[A-Z][A-Za-z. ]{2,40}?,\s+)?"
    rf"{_GA_ROLE}.{{0,160}}?)"
    r"\s+(?:is|are)\s+(?:hereby\s+)?"
    r"(?:authori[sz]ed|directed|permitted|instructed|requested)\s+to\s+appear",
    I,
)


def government_advocate(text: str) -> str | None:
    match = _GA.search(flatten(text))
    return tidy(match.group("ga")) if match else None


# ==========================================================
# 7. LITIGATION CONDUCTING OFFICER (+ SWEARING OFFICER)
# ==========================================================

_LCO_PHRASE = re.compile(
    r"\s+(?:shall\s+be|is\s+(?:hereby\s+)?(?:appointed|nominated|designated)\s+as)"
    r"\s+(?:the\s+)?Litigation\s+Conducting\s+Officer"
    r"(?P<sw>\s*(?:and|&)\s*(?:the\s+)?Swearing\s+Officer)?",
    I,
)

_SWEARING = re.compile(
    r"(?:^|(?<=\.\s))(?P<v>[^.]{5,250}?)\s+shall\s+be\s+the\s+Swearing\s+Officer",
    I,
)

_PHONE = re.compile(
    r"(?:Phone|Ph|Tel|Telephone|Mobile|Mob|ದೂರವಾಣಿ)\.?\s*(?:No\.?)?\s*[:.\-]?\s*"
    r"(?P<v>\+?\d[\d\-\s/]{5,}\d)",
    I,
)

_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")

_CONTACT_BRACKET = re.compile(
    r"\((?=[^)]*(?:Phone|\bPh\b|Mob|Tel|E-?mail|@))[^)]*\)", I
)

# Sentence end: ". " + capital, but not after abbreviations like No. / Dr.
_SENTENCE_END = re.compile(
    r"(?<!\bNo)(?<!\bDr)(?<!\bSri)(?<!\bSmt)(?<!\bM\.S)(?<!\b[A-Z])\.\s+(?=[A-Z])"
)


def litigation_officer(text: str) -> LitigationOfficer:
    result = LitigationOfficer()

    for paragraph in paragraphs(text):
        flat = flatten(paragraph)
        match = _LCO_PHRASE.search(flat)

        if not match:
            continue

        before = flat[:match.start()]

        phone = _PHONE.search(before)
        email = _EMAIL.search(before)
        result.phone = tidy(phone.group("v")) if phone else None
        result.email = email.group(0) if email else None

        # Drop the "(Phone No... Email Id...)" part (but keep brackets that
        # belong to the title, like "(University-2)"), then keep only the
        # sentence that names the officer.
        officer = _CONTACT_BRACKET.sub(" ", before)
        officer = _SENTENCE_END.split(officer)[-1]

        result.officer = tidy(officer)
        result.is_swearing_officer = bool(match.group("sw"))
        break

    if not result.is_swearing_officer:
        swearing = _SWEARING.search(flatten(text))

        if swearing:
            result.swearing_officer = tidy(
                _CONTACT_BRACKET.sub(" ", swearing.group("v"))
            )

    return result


# ==========================================================
# 8. ISSUING AUTHORITY
# ==========================================================

_SIGN_OFF = re.compile(
    r"(?:IN\s+THE\s+NAME\s+OF\s+THE\s+GOVERNOR\s+OF\s+KARNATAKA"
    r"|ರಾಜ್ಯಪಾಲರ\s+ಆದೇಶಾನುಸಾರ[^\n]*)",
    I,
)

_END_OF_SIGNATURE = re.compile(
    r"^(?:To\b|To:|ಗೆ\b|ಇವರಿಗೆ|Copy\s+to|ಪ್ರತಿ|\(?\d+\s*[).])",
    I,
)


def issuing_authority(text: str) -> IssuingAuthority:
    sign_off = _SIGN_OFF.search(text)

    if not sign_off:
        return IssuingAuthority()

    lines = [line.strip() for line in text[sign_off.end():].splitlines()]
    lines = [line for line in lines if line]

    name = None
    designation: list[str] = []

    for line in lines[:8]:
        if _END_OF_SIGNATURE.match(line):
            break

        if name is None:
            bracketed = re.search(r"\(([^)]{3,60})\)", line)

            if bracketed:
                name = bracketed.group(1)
                continue

            # A signature scrawl OCR'd as junk: skip short noise lines.
            if len(re.sub(r"[^A-Za-zಀ-೿]", "", line)) < 4:
                continue

            name = line
            continue

        # The name read a second time from the signature: "(FAIROZA H. UKNALI)"
        if not designation and re.fullmatch(r"\([^)]{3,60}\)", line):
            continue

        designation.append(line)

    return IssuingAuthority(
        name=title_name(tidy(name)) if name else None,
        designation=tidy(" ".join(designation)),
    )


# ==========================================================
# ALL FIELDS
# ==========================================================


def extract(text: str) -> GOExtraction:
    text = prepare(text)

    go_number, go_date = go_number_and_date(text)
    petitioner, respondent = parties(text)

    result = GOExtraction(
        file_no=file_no(text),
        go_number=go_number,
        go_date=go_date,
        case_number=case_number(text),
        petitioner=petitioner,
        respondent=respondent,
        respondents_represented=respondents_represented(text),
        government_advocate=government_advocate(text),
        litigation_officer=litigation_officer(text),
        issuing_authority=issuing_authority(text),
    )

    for name, value in result.to_dict().items():
        if name == "sources":
            continue
        if isinstance(value, dict):
            value = any(value.values())
        if value:
            result.sources[name] = "rules"

    return result
