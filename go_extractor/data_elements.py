"""
HRMS2 disciplinary / suspension data elements
(HRMS2_Disciplinary_Suspension_Data_Elements_Ver2.0.xlsx).

Each order type gets the Common and Disciplinary elements (applies to
ALL) plus the elements marked for it. Values are extracted only when the
document states them; anything not present stays blank, never inferred.

To add an order type: add its elements below with
applies_to=(<order type>,), map it in ORDER_TYPES and describe it in
ORDER_DESCRIPTIONS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALL = "ALL"
INVOKE = "Suspension Invoke Order"
EXTEND = "Suspension Extend Order"
REVOKE = "Suspension Revoke Order"

# Order type from suspension.py -> data-element order type. Suspension
# orders whose type is not listed ("Other (suspension related)") keep the
# fields in suspension.py.
ORDER_TYPES = {
    "Suspension": INVOKE,
    "Suspension extended": EXTEND,
    "Suspension revoked": REVOKE,
}

# The order type is taken from the file name when it says it
# ("Suspension_Invoke_Orders_739522.pdf"); the content is not used for it.
FILENAME_TYPES = (
    (re.compile(r"invoke", re.I), INVOKE),
    (re.compile(r"exten", re.I), EXTEND),
    (re.compile(r"revoke|revocation", re.I), REVOKE),
)


def type_from_filename(name: str) -> str | None:
    """Data-element order type named in the file name, if any."""
    for words, order_type in FILENAME_TYPES:
        if words.search(name):
            return order_type

    return None


# What each order does, for the LLM prompt.
ORDER_DESCRIPTIONS = {
    INVOKE: "places a government employee under suspension",
    EXTEND: "continues (extends) the suspension of a government employee "
            "who is already suspended",
    REVOKE: "revokes the suspension of a government employee and "
            "reinstates them in service",
}

# Elements that belong to one employee. An order about several employees
# gives one set of these per employee (one Excel row each); every other
# element is the same for the whole order.
EMPLOYEE_KEYS = frozenset({
    "employee_name", "designation", "office_name", "ddo_code",
    "employee_kgid", "dgo_kgid", "cadre", "current_posting",
    "lien_post", "reinstatement_posting_office",
    "reinstatement_posting_office_address",
})

# Checked in code, not left to the model (see order_elements.py).
# Elements kept only when the document uses these words: a Yes / No or a
# charge-sheet number is not accepted from an order that never mentions it.
EVIDENCE = {
    "subsistence_allowance_applicable":
        r"ಜೀವನಾಧಾರ|ಜೀವನ\s*ಭತ್ಯೆ|subsistence",
    "subsistence_allowance": r"ಜೀವನಾಧಾರ|ಜೀವನ\s*ಭತ್ಯೆ|subsistence",
    "charge_given_during_suspension":
        r"ದೋಷಾರೋಪಣ|ಆರೋಪಣ[ಾ]?\s*ಪಟ್ಟಿ|ಆರೋಪ\s*ಪಟ್ಟಿ|ಚಾರ್ಜ್\s*ಶೀಟ್|"
        r"charge\s*-?\s*(sheet|memo)",
    "charge_sheet_no":
        r"ದೋಷಾರೋಪಣ|ಆರೋಪಣ[ಾ]?\s*ಪಟ್ಟಿ|ಆರೋಪ\s*ಪಟ್ಟಿ|ಚಾರ್ಜ್\s*ಶೀಟ್|"
        r"charge\s*-?\s*(sheet|memo)",
    "lien_post": r"ಲೀನ್|ಲಿಯನ್|\blien\b",
    # The suspension itself continued ("ಮುಂದುವರೆಸಿ", continue *it*) or
    # extended, within a few words: not "ನಿಯಂತ್ರಣದಲ್ಲಿ ಮುಂದುವರೆಯುತ್ತೀರಿ"
    # (*you* continue under the same officer).
    "extension_required":
        r"ಅಮಾನತ್ತ?\S*(?:\s+\S+){0,4}?\s+(?:ಮುಂದುವರ[ೆಿ]ಸ|ವಿಸ್ತರ)|"
        r"suspension(?:\s+\S+){0,4}?\s+(?:extend|continu)|"
        r"(?:extension|continuation)\s+of\s+(?:the\s+)?suspension",
    # Only when the order says it was approved: not the signer / its date.
    "approved_by": r"ಅನುಮೋದ|approv",
    "approval_date": r"ಅನುಮೋದ|approv",
    # Only an authority the order calls competent / disciplinary, not
    # whoever signed it.
    "competent_authority":
        r"ಸಕ್ಷಮ|ಶಿಸ್ತು\s*ಪ್ರಾಧಿಕಾರ|competent|disciplinary\s*authorit",
}

# Identifiers: the value must follow its label in the text (within a few
# words), so a vehicle, constable or handwritten number is not taken for
# a KGID.
LABELS = {
    "employee_kgid": r"ಕೆ\.?\s*ಜಿ\.?\s*ಐ\.?\s*ಡಿ|K\.?\s*G\.?\s*I\.?\s*D",
    "dgo_kgid": r"ಕೆ\.?\s*ಜಿ\.?\s*ಐ\.?\s*ಡಿ|K\.?\s*G\.?\s*I\.?\s*D",
    "ddo_code": r"ಡಿ\.?\s*ಡಿ\.?\s*ಒ|ಡಿ\.?\s*ಡಿ\.?\s*ಓ|D\.?\s*D\.?\s*O",
}

# Dates that must be printed near these words (within NEAR_WINDOW
# characters after the date), so the incident or a report date is not
# taken for the date the case was registered.
NEAR = {
    "case_registration_date": r"ದಾಖಲ|regist",
    # A date "from which" it takes effect, not just the order's date.
    "reinstatement_date": r"ರಿಂದ|ಜಾರಿ|from|effect",
    "suspension_effective_to": r"ರಿಂದ|ಜಾರಿ|ವರೆಗೆ|from|effect|till|until",
}
NEAR_WINDOW = 150

# Values that must not be the number printed after these labels: the
# order book number (ಓಬಿ ಸಂಖ್ಯೆ) is not a case number.
NOT_AFTER = {
    "disciplinary_case_no": r"ಓ\.?\s*ಬಿ|\bO\.?\s*B\b",
}

# Kinds of value, each checked differently (see order_elements.py):
#   text   copied from the document, given in English too
#   list   several items copied from the document (references, copy to)
#   date   DD.MM.YYYY, must be a date in the document
#   yes_no "Yes" / "No", only when the document says so
#   label  chosen by the model from the description (document type)


@dataclass(frozen=True)
class DataElement:
    number: int
    key: str
    group: str
    label: str
    category: str
    description: str
    applies_to: tuple[str, ...] = (ALL,)
    kind: str = "text"
    # Dates only: "With immediate effect" is also a valid value.
    immediate: bool = False
    # Extra guidance for the LLM (Kannada wording, what not to take).
    hint: str = ""

    @property
    def per_employee(self) -> bool:
        return self.key in EMPLOYEE_KEYS

    @property
    def evidence(self) -> str | None:
        return EVIDENCE.get(self.key)

    @property
    def label_words(self) -> str | None:
        return LABELS.get(self.key)

    @property
    def near_words(self) -> str | None:
        return NEAR.get(self.key)

    @property
    def not_after(self) -> str | None:
        return NOT_AFTER.get(self.key)


ELEMENTS = (
    # ---------- Common ----------
    DataElement(1, "document_type", "Document", "Document Type", "Common",
                "Notice / Order / Suspension Order / Charge Sheet / etc.",
                kind="label",
                hint="The kind of document this is, in English, e.g. "
                     "'Suspension Order'"),
    DataElement(2, "employee_name", "Employee", "Employee Name", "Common",
                "Auto-populated from Employee Master",
                hint="Name of the employee the order is about, without "
                     "ಶ್ರೀ / ಶ್ರೀಮತಿ / Sri / Smt"),
    DataElement(3, "designation", "Employee", "Designation", "Common",
                "Designation at the time of action",
                hint="That employee's post, as written right after the "
                     "employee's name (e.g. ಎಎಸ್‌ಐ, ಸಿ.ಪಿ.ಸಿ.16778, "
                     "ಎಪಿಸಿ-328, ಅಡುಗೆಯವರು). Not the unit or office "
                     "(e.g. ಡಿಎಆರ್), not the post of another officer named "
                     "in the order"),
    DataElement(4, "office_name", "Employee", "Office Name", "Common",
                "Employee's office",
                hint="Office / police station / school where the employee "
                     "works"),
    DataElement(5, "ddo_code", "Employee", "DDO Code", "Common",
                "Financial / Payroll linkage",
                hint="Drawing and Disbursing Officer code, only if a DDO "
                     "code is printed"),
    DataElement(6, "employee_kgid", "Employee", "Employee KGID", "Common",
                "Employee identifier",
                hint="KGID number of the employee, only a number the "
                     "text labels as KGID (ಕೆಜಿಐಡಿ / KGID No.)"),
    DataElement(7, "disciplinary_authority_name", "Authority",
                "Disciplinary Authority Name", "Common",
                "Authority issuing / initiating the action",
                hint="Name and post of the officer who signed / issued "
                     "this order (printed under the signature)"),
    DataElement(8, "order_no", "Document", "Order No.", "Common",
                "Order / document reference number",
                hint="Number of this order (after ಸಂಖ್ಯೆ / ಸಂ: / No. at "
                     "the top or under the ಆದೇಶ heading). Not the number of "
                     "a referenced letter"),
    DataElement(9, "order_date", "Document", "Order Date", "Common",
                "Date of order", kind="date",
                hint="Date of this order (the ದಿನಾಂಕ next to its number or "
                     "at its signature). Never the date of a referenced "
                     "letter or of an event"),
    DataElement(10, "reference", "Document", "Reference", "Common",
                "Reference(s) cited in the document", kind="list",
                hint="Each reference cited (under ಉಲ್ಲೇಖ / ಓದಲಾಗಿದೆ / Ref / "
                     "Read), one list item each"),
    DataElement(11, "subject", "Document", "Subject", "Common",
                "Subject / title of the document",
                hint="The subject line (after ವಿಷಯ / Sub), without the "
                     "word ವಿಷಯ"),
    DataElement(12, "copy_to", "Distribution", "Copy To", "Common",
                "Recipients / authorities to whom copy is sent",
                kind="list",
                hint="Each recipient under ಪ್ರತಿಯನ್ನು / ಪ್ರತಿ / Copy to, one "
                     "list item each"),

    # ---------- Disciplinary ----------
    DataElement(13, "disciplinary_case_no", "Case", "Disciplinary Case No.",
                "Disciplinary",
                "Unique disciplinary case / proceeding reference",
                hint="Number of the disciplinary case / proceeding / "
                     "departmental enquiry against the employee, or of the "
                     "criminal / Lokayukta case (ಪ್ರಕರಣ ಸಂಖ್ಯೆ / ಮೊ.ಸಂ / "
                     "ಅಪರಾಧ ಸಂಖ್ಯೆ) if that is the only case named. Not the "
                     "number of a report, letter or order in the "
                     "references, not this order's own number, not the "
                     "order book number (ಓಬಿ ಸಂಖ್ಯೆ)"),
    DataElement(14, "case_registration_date", "Case",
                "Case Registration Date", "Disciplinary",
                "Date the case was registered", kind="date",
                hint="Date that case was registered (ದಾಖಲಾದ / "
                     "ದಾಖಲಿಸಿದ ದಿನಾಂಕ), not the date of the incident"),
    DataElement(15, "case_type", "Case", "Case Type", "Disciplinary",
                "Type of disciplinary proceeding",
                hint="Type of case / proceeding as the order names it (e.g. "
                     "Lokayukta trap case, criminal case, departmental "
                     "enquiry)"),
    DataElement(16, "nature_of_disciplinary_action", "Case",
                "Nature of Disciplinary Action", "Disciplinary",
                "Nature / category of action",
                hint="The action this order takes, as the order words it "
                     "(e.g. placed under suspension pending enquiry)"),
    DataElement(17, "dgo_kgid", "Employee", "DGO KGID", "Disciplinary",
                "HRMS employee identifier",
                hint="KGID of the Delinquent Government Official, only a "
                     "number the text labels as KGID"),
    DataElement(18, "department", "Employee", "Department", "Disciplinary",
                "Employee's department",
                hint="Department the employee belongs to, as the order "
                     "names it (letterhead, office heading or text, e.g. "
                     "ಪೊಲೀಸ್ ಇಲಾಖೆ)"),
    DataElement(19, "cadre", "Employee", "Cadre", "Disciplinary",
                "Employee cadre",
                hint="Cadre / group of the employee's post, only if the "
                     "order states it (ವೃಂದ / Group A-D)"),
    DataElement(20, "current_posting", "Employee", "Current Posting",
                "Disciplinary", "Current posting / place of work",
                hint="Place where the employee is working, as the order "
                     "states it (often the same as the office)"),
    DataElement(21, "competent_authority", "Authority",
                "Competent Authority", "Disciplinary",
                "Authority competent to take action",
                hint="The authority the order names as competent / "
                     "appointing / disciplinary authority (ಸಕ್ಷಮ "
                     "ಪ್ರಾಧಿಕಾರ / ಶಿಸ್ತು ಪ್ರಾಧಿಕಾರಿಗಳು, often printed under "
                     "the signature)"),
    DataElement(22, "applicable_rule", "Legal", "Applicable Rule",
                "Disciplinary",
                "Applicable service / disciplinary rule",
                hint="Name of the rules book under which this order is "
                     "passed (e.g. ಕರ್ನಾಟಕ ನಾಗರಿಕ ಸೇವಾ (ವರ್ಗೀಕರಣ, ನಿಯಂತ್ರಣ "
                     "ಮತ್ತು ಮೇಲ್ಮನವಿ) ನಿಯಮಗಳು 1957). If the order names none, "
                     "the service / conduct rules it says the employee "
                     "broke (e.g. ಕರ್ನಾಟಕ ನಾಗರಿಕ ಸೇವಾ (ನಡತೆ) ನಿಯಮಗಳು 2021). "
                     "Never the pay rules for subsistence allowance "
                     "(ಕೆ.ಸಿ.ಎಸ್.ಆರ್ ನಿಯಮ 98)"),
    DataElement(23, "rule_section", "Legal", "Rule / Section",
                "Disciplinary", "Specific rule, section or provision",
                hint="The rule number / sub-rule of the Applicable Rule "
                     "that the order cites (e.g. ನಿಯಮ 10(1)(ಡಿ))"),
    DataElement(24, "approved_by", "Workflow", "Approved By",
                "Disciplinary", "Approving authority / user",
                hint="Who approved the order, only if the document says "
                     "it was approved by someone (ಅನುಮೋದನೆ)"),
    DataElement(25, "approval_date", "Workflow", "Approval Date",
                "Disciplinary", "Date of approval", kind="date",
                hint="Date of that approval, only if printed"),

    # ---------- Suspension Invoke Order ----------
    DataElement(26, "suspension_order_no", "Suspension",
                "Suspension Order No.", "Suspension",
                "Suspension order reference", applies_to=(INVOKE,),
                hint="Number of the order that suspends the employee; "
                     "when this order is itself the suspension order, its "
                     "own number"),
    DataElement(27, "suspension_order_date", "Suspension",
                "Suspension Order Date", "Suspension",
                "Date of suspension order", applies_to=(INVOKE,),
                kind="date",
                hint="Date of that order; when this order is itself the "
                     "suspension order, its own date"),
    DataElement(28, "suspension_effective_from", "Suspension",
                "Suspension Effective From", "Suspension",
                "Start date of suspension", applies_to=(INVOKE,),
                kind="date", immediate=True,
                hint="Date from which the suspension takes effect, or "
                     "'With immediate effect' if it says ತಕ್ಷಣದಿಂದ / "
                     "immediately"),
    DataElement(29, "reason_for_suspension", "Suspension",
                "Reason for Suspension", "Suspension",
                "Reason for placing employee under suspension",
                applies_to=(INVOKE,),
                hint="The reason the order gives for the suspension: "
                     "the one sentence or phrase that states what the "
                     "employee did, not the whole account of the incident"),
    DataElement(30, "lien_post", "Suspension", "Lien Post", "Suspension",
                "Substantive / lien post during suspension",
                applies_to=(INVOKE,),
                hint="Post / office where the employee's lien is kept "
                     "during suspension (ಲೀನ್ / ಲಿಯನ್), only if stated"),
    DataElement(31, "subsistence_allowance_applicable", "Suspension",
                "Subsistence Allowance Applicable", "Suspension",
                "Yes / No", applies_to=(INVOKE,), kind="yes_no",
                hint="'Yes' if the order grants subsistence allowance "
                     "(ಜೀವನಾಧಾರ ಭತ್ಯೆ), 'No' if it says none is payable, "
                     "null if it does not mention it"),
    DataElement(32, "charge_given_during_suspension", "Suspension",
                "Charge Given During Suspension", "Suspension",
                "Whether charge/charge sheet was issued during suspension "
                "— Yes/No",
                applies_to=(INVOKE,), kind="yes_no",
                hint="'Yes' if the order says a charge sheet / charge memo "
                     "(ದೋಷಾರೋಪಣಾ ಪಟ್ಟಿ) has been issued, 'No' if it says "
                     "none has, null if it does not mention it. A list of "
                     "allegations (ಆರೋಪಗಳು) in this order is not a charge "
                     "sheet"),
    DataElement(33, "charge_sheet_no", "Suspension",
                "Charge Sheet / Charge Memo No.", "Suspension",
                "Reference number of charge sheet issued during suspension",
                applies_to=(INVOKE,),
                hint="Number of that charge sheet / charge memo, only if "
                     "printed"),
)




# ---------- Suspension Extend Order ----------

EXTEND_ELEMENTS = (
    DataElement(26, "extension_required", "Suspension",
                "Extension Required", "Suspension", "Yes / No",
                applies_to=(EXTEND,), kind="yes_no",
                hint="'Yes' if the order continues / extends the "
                     "suspension (ಮುಂದುವರೆಸಿ / ವಿಸ್ತರಿಸಿ), 'No' if it "
                     "says the suspension is not extended, null if it "
                     "says neither"),
    DataElement(27, "extension_from", "Suspension", "Extension From",
                "Suspension", "Start date of extension",
                applies_to=(EXTEND,), kind="date",
                hint="Date from which the suspension is continued"),
    DataElement(28, "extension_to", "Suspension", "Extension To",
                "Suspension", "End date of extension",
                applies_to=(EXTEND,), kind="date",
                hint="Date until which the suspension is continued, only "
                     "if a date is printed (not 'until further orders')"),
    DataElement(29, "subsistence_allowance", "Suspension",
                "Subsistance Allowance", "Suspension",
                "Subsistance Allowance percentage",
                applies_to=(EXTEND,),
                hint="Percentage of subsistence allowance (ಜೀವನಾಧಾರ "
                     "ಭತ್ಯೆ) the order grants during the extension, as "
                     "written (e.g. ಶೇ 50 / 50%). Its English version is "
                     "the percentage, e.g. '50%'"),
)

# ---------- Suspension Revoke Order ----------

REVOKE_ELEMENTS = (
    DataElement(26, "suspension_effective_to", "Suspension",
                "Suspension Effective To", "Suspension",
                "End date, if specified", applies_to=(REVOKE,),
                kind="date", immediate=True,
                hint="Date on which the suspension ends (is revoked), or "
                     "'With immediate effect' if it says ತಕ್ಷಣದಿಂದ / "
                     "immediately; null if not specified"),
    DataElement(27, "reinstatement_order_no", "Suspension",
                "Reinstatement Order No.", "Suspension",
                "Reference of reinstatement order", applies_to=(REVOKE,),
                hint="Number of the order that reinstates the employee; "
                     "when this order itself reinstates, its own number"),
    DataElement(28, "reinstatement_date", "Suspension",
                "Reinstatement Date", "Suspension",
                "Date of reinstatement", applies_to=(REVOKE,),
                kind="date", immediate=True,
                hint="Date from which the employee is reinstated "
                     "(ಪುನರ್‌ಸ್ಥಾಪಿಸಿ), or 'With immediate effect'"),
    DataElement(29, "reinstatement_posting_office", "Suspension",
                "Reinstatement Posting Office", "Suspension",
                "Office Name Posting after reinstatement",
                applies_to=(REVOKE,),
                hint="Name of the office / post where the employee is "
                     "posted on reinstatement (ಸ್ಥಳ ನಿಯುಕ್ತಿ)"),
    DataElement(30, "reinstatement_posting_office_address", "Suspension",
                "Reinstatement Posting Office Address", "Suspension",
                "Office Address Posting after reinstatement",
                applies_to=(REVOKE,),
                hint="Address of that office, only if an address (place, "
                     "taluk, district) is printed"),
    DataElement(31, "reinstatement_reason", "Suspension",
                "Reinstatement Reason", "Suspension",
                "Reason for reinstatement", applies_to=(REVOKE,),
                hint="Why the order revokes the suspension (e.g. the "
                     "employee's appeal or undertaking, the result of the "
                     "enquiry, a court order), one continuous phrase from "
                     "the text. Not the action itself (revoking / "
                     "reinstating)"),
)

ELEMENTS += EXTEND_ELEMENTS + REVOKE_ELEMENTS


def elements_for(order_type: str) -> list[DataElement]:
    """Elements to extract for an order type (ALL + its own)."""
    return [e for e in ELEMENTS
            if ALL in e.applies_to or order_type in e.applies_to]
