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
class DocumentResult:
    """Everything produced for one uploaded file."""

    filename: str
    fields: GOExtraction | SuspensionOrder
    text: str
    ocr_method: str
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    # "advocate" (GO authorising a Government Advocate) or "suspension".
    doc_type: str = "advocate"
