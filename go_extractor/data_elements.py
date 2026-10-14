"""
HRMS2 disciplinary / suspension data elements
(HRMS2_Disciplinary_Suspension_Data_Elements_Ver2.0.xlsx).

Each order type gets the Common and Disciplinary elements (applies to
ALL) plus the elements marked for it. Values are extracted only when the
document states them; anything not present stays blank, never inferred.

To add an order type (e.g. Suspension Extend / Revoke Order): add its
elements below with applies_to=(<order type>,) and map it in ORDER_TYPES.
"""

from __future__ import annotations

from dataclasses import dataclass

ALL = "ALL"
INVOKE = "Suspension Invoke Order"

# Order type from suspension.py -> data-element order type. Suspension
# orders whose type is not listed keep the fields in suspension.py.
ORDER_TYPES = {
    "Suspension": INVOKE,
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
    # Extra guidance for the LLM (Kannada wording, what not to take).
    hint: str = ""


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
                hint="That employee's post (job title)"),
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
                hint="KGID number of the employee (ಕೆಜಿಐಡಿ / KGID No.), "
                     "only if printed"),
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
                     "ಅಪರಾಧ ಸಂಖ್ಯೆ) if that is the only case named"),
    DataElement(14, "case_registration_date", "Case",
                "Case Registration Date", "Disciplinary",
                "Date the case was registered", kind="date",
                hint="Date that case was registered (ದಾಖಲಾದ ದಿನಾಂಕ)"),
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
                hint="KGID of the Delinquent Government Official, only if "
                     "printed"),
    DataElement(18, "department", "Employee", "Department", "Disciplinary",
                "Employee's department",
                hint="Department the employee belongs to (ಇಲಾಖೆ), as "
                     "written"),
    DataElement(19, "cadre", "Employee", "Cadre", "Disciplinary",
                "Employee cadre",
                hint="Cadre / group of the employee's post, only if the "
                     "order states it (ವೃಂದ / Group A-D)"),
    DataElement(20, "current_posting", "Employee", "Current Posting",
                "Disciplinary", "Current posting / place of work",
                hint="Place where the employee is currently working"),
    DataElement(21, "competent_authority", "Authority",
                "Competent Authority", "Disciplinary",
                "Authority competent to take action",
                hint="The authority the order names as competent / "
                     "appointing / disciplinary authority (ಸಕ್ಷಮ "
                     "ಪ್ರಾಧಿಕಾರ / ಶಿಸ್ತು ಪ್ರಾಧಿಕಾರ)"),
    DataElement(22, "applicable_rule", "Legal", "Applicable Rule",
                "Disciplinary",
                "Applicable service / disciplinary rule",
                hint="Name of the rules book (e.g. ಕರ್ನಾಟಕ ನಾಗರಿಕ ಸೇವಾ "
                     "(ವರ್ಗೀಕರಣ, ನಿಯಂತ್ರಣ ಮತ್ತು ಮೇಲ್ಮನವಿ) ನಿಯಮಗಳು 1957)"),
    DataElement(23, "rule_section", "Legal", "Rule / Section",
                "Disciplinary", "Specific rule, section or provision",
                hint="The rule number / sub-rule under which this order is "
                     "passed (e.g. ನಿಯಮ 10(1)(ಡಿ))"),
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
                hint="Number of the order that suspends the employee"),
    DataElement(27, "suspension_order_date", "Suspension",
                "Suspension Order Date", "Suspension",
                "Date of suspension order", applies_to=(INVOKE,),
                kind="date"),
    DataElement(28, "suspension_effective_from", "Suspension",
                "Suspension Effective From", "Suspension",
                "Start date of suspension", applies_to=(INVOKE,),
                kind="date",
                hint="Date from which the suspension takes effect, or "
                     "'With immediate effect' if it says ತಕ್ಷಣದಿಂದ / "
                     "immediately"),
    DataElement(29, "reason_for_suspension", "Suspension",
                "Reason for Suspension", "Suspension",
                "Reason for placing employee under suspension",
                applies_to=(INVOKE,),
                hint="The reason the order gives for the suspension, one "
                     "continuous phrase from the text"),
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
                     "none has, null if it does not mention it"),
    DataElement(33, "charge_sheet_no", "Suspension",
                "Charge Sheet / Charge Memo No.", "Suspension",
                "Reference number of charge sheet issued during suspension",
                applies_to=(INVOKE,),
                hint="Number of that charge sheet / charge memo, only if "
                     "printed"),
)


def elements_for(order_type: str) -> list[DataElement]:
    """Elements to extract for an order type (ALL + its own)."""
    return [e for e in ELEMENTS
            if ALL in e.applies_to or order_type in e.applies_to]
