"""
Data models for one Government Order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class LitigationOfficer:
    officer: str | None = None
    phone: str | None = None
    email: str | None = None
    # True when the same officer is also the Swearing Officer.
    is_swearing_officer: bool = False
    # Set only when a different person is named as Swearing Officer.
    swearing_officer: str | None = None


@dataclass
class IssuingAuthority:
    name: str | None = None
    designation: str | None = None


@dataclass
class GOExtraction:
    """The 8 fields extracted from a Government Order."""

    file_no: str | None = None
    go_number: str | None = None
    go_date: str | None = None
    case_number: str | None = None
    petitioner: str | None = None
    # Respondents as named in the case title ("The State of Karnataka &
    # others") plus the numbered respondents the Advocate appears for.
    respondent: str | None = None
    respondents_represented: list[str] = field(default_factory=list)
    government_advocate: str | None = None
    litigation_officer: LitigationOfficer = field(
        default_factory=LitigationOfficer
    )
    issuing_authority: IssuingAuthority = field(
        default_factory=IssuingAuthority
    )

    # Where each field came from ("rules" or "llm"), for review/debugging.
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing_fields(self) -> list[str]:
        """Top-level fields that are still empty."""
        checks = {
            "file_no": self.file_no,
            "go_number": self.go_number,
            "go_date": self.go_date,
            "case_number": self.case_number,
            "petitioner": self.petitioner,
            "respondent": self.respondent,
            "government_advocate": self.government_advocate,
            "litigation_officer": self.litigation_officer.officer,
            "issuing_authority": self.issuing_authority.name,
        }

        return [name for name, value in checks.items() if not value]


@dataclass
class SuspensionOrder:
    """Fields of an order that suspends, extends or revokes a suspension."""

    # "Suspension", "Suspension extended", "Suspension revoked" or
    # "Other (suspension related)", decided from the content.
    order_type: str | None = None
    order_number: str | None = None
    order_date: str | None = None
    employee_name: str | None = None
    employee_designation: str | None = None
    employee_office: str | None = None
    # Short English summary of why the order was passed.
    reason: str | None = None
    rule_invoked: str | None = None
    # Criminal / Lokayukta case the order refers to, if any.
    case_reference: str | None = None
    effective_from: str | None = None
    issuing_authority: IssuingAuthority = field(
        default_factory=IssuingAuthority
    )

    # Values above are in English; these are the same fields as written
    # in the order (usually Kannada), for checking against the scan.
    original: dict[str, str] = field(default_factory=dict)

    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing_fields(self) -> list[str]:
        """Fields every suspension order should have that are still empty."""
        checks = {
            "order_type": self.order_type,
            "order_number": self.order_number,
            "order_date": self.order_date,
            "employee_name": self.employee_name,
            "employee_designation": self.employee_designation,
            "issuing_authority": (
                self.issuing_authority.name
                or self.issuing_authority.designation
            ),
        }

        return [name for name, value in checks.items() if not value]


@dataclass
class EmployeeElements:
    """The data elements of one employee named in an order."""

    # Element key -> value in English; None when the order does not state it.
    values: dict[str, str | None] = field(default_factory=dict)
    # Same elements as written in the order (usually Kannada).
    original: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {**self.values, "original": self.original}


@dataclass
class DisciplinaryOrder:
    """HRMS2 data elements of an order (go_extractor/data_elements.py)."""

    # Data-element order type, e.g. "Suspension Invoke Order".
    order_type: str
    # Elements of the whole order: key -> value in English, or None.
    values: dict[str, str | None] = field(default_factory=dict)
    # Same elements as written in the order (usually Kannada).
    original: dict[str, str] = field(default_factory=dict)
    # One entry per employee the order is about, in the order named.
    employees: list[EmployeeElements] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    # Set when the order's subject line reads as another type than the
    # one from the file name (the file name's type is kept).
    type_check: str | None = None

    def to_dict(self) -> dict:
        return {
            "order_type": self.order_type,
            "type_check": self.type_check,
            **self.values,
            "employees": [e.to_dict() for e in self.employees],
            "original": self.original,
            "sources": self.sources,
        }

    def missing_fields(self) -> list[str]:
        """Elements every order should have that are still empty."""
        missing = [name for name in ("order_no", "order_date")
                   if not self.values.get(name)]

        for name in ("employee_name", "designation"):
            if not self.employees or any(
                not e.values.get(name) for e in self.employees
            ):
                missing.append(name)

        return missing


@dataclass
class DocumentResult:
    """Everything produced for one uploaded file."""

    filename: str
    fields: GOExtraction | SuspensionOrder | DisciplinaryOrder
    text: str
    ocr_method: str
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    # "advocate" (GO authorising a Government Advocate), "suspension", or
    # a data-element order type such as "Suspension Invoke Order".
    doc_type: str = "advocate"
    pages: int = 0
