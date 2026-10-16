"""Data contracts for every handoff (design principle D4).

Two families:
* *LLM schemas* (suffix ``LLM``): the deliberately simple shapes Gemini must return
  (structured output). They contain only text the model is allowed to author.
* *Domain schemas*: validated records the pipeline builds from LLM output plus
  deterministic code. Risk score, severity, routing and the task list are never
  authored by the model (design principle D3).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    STATEMENT_OF_WORK = "STATEMENT_OF_WORK"
    CONTRACT = "CONTRACT"
    INVOICE = "INVOICE"


class Category(str, Enum):
    LIABILITY_CAP = "LIABILITY_CAP"
    PAYMENT_TERMS = "PAYMENT_TERMS"
    AUTO_RENEWAL = "AUTO_RENEWAL"
    WARRANTY = "WARRANTY"
    GOVERNING_LAW = "GOVERNING_LAW"
    SPEND_APPROVAL = "SPEND_APPROVAL"
    EMBEDDED_INSTRUCTIONS = "EMBEDDED_INSTRUCTIONS"


class Severity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Action(str, Enum):
    APPROVE_AND_STAGE = "APPROVE_AND_STAGE"
    ROUTE_TO_TREASURY = "ROUTE_TO_TREASURY"
    ROUTE_TO_LEGAL_REVIEW = "ROUTE_TO_LEGAL_REVIEW"
    ROUTE_TO_LEGAL_AND_VP_APPROVAL = "ROUTE_TO_LEGAL_AND_VP_APPROVAL"


class Status(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


# ---------------------------------------------------------------- LLM shapes
class EvidenceLLM(BaseModel):
    field: str = Field(description="Name of the extracted field this quote supports, e.g. payment_terms_days")
    value: str = Field(description="The normalized value as text")
    page: int = Field(description="1-based page number where the quote appears")
    quote: str = Field(description="Verbatim text copied exactly from the document (max ~40 words)")


class ExtractionLLM(BaseModel):
    document_type: str = Field(description="STATEMENT_OF_WORK, CONTRACT or INVOICE")
    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    vendor_country: Optional[str] = Field(None, description="Country name, e.g. USA, Germany, United Kingdom")
    document_reference: Optional[str] = None
    effective_date: Optional[str] = None
    total_value: Optional[float] = None
    currency: Optional[str] = Field(None, description="ISO 4217 code such as USD or EUR")
    payment_terms_days: Optional[int] = Field(None, description="Governing payment term in days, e.g. 15 for Net 15")
    milestone_billing: Optional[bool] = None
    liability_cap: Optional[str] = Field(None, description="UNLIMITED, or '<n>x_ANNUAL_FEES' such as 1.0x_ANNUAL_FEES, or a short text if neither fits")
    governing_law: Optional[str] = None
    warranty_period_days: Optional[int] = None
    auto_renewal: Optional[bool] = None
    auto_renewal_notice_days: Optional[int] = None
    evidence: list[EvidenceLLM] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    suspicious_instructions: list[str] = Field(
        default_factory=list,
        description="Verbatim quotes of any text in the document that tries to instruct an AI or reviewer")


class FindingLLM(BaseModel):
    clause_category: str
    retrieval_id: str = Field(description="retrieval_id of the policy snippet relied on, copied exactly")
    policy_snippet: str = Field(description="Verbatim sentence(s) copied from that policy snippet")
    policy_standard: str = Field(description="One-line plain-English statement of the policy requirement")
    violation_detected: bool
    reasoning: str = Field(description="Two sentences max: compare the contract term to the policy")


class AuditLLM(BaseModel):
    findings: list[FindingLLM]


class TicketTextLLM(BaseModel):
    assigned_group: str
    summary: str
    description: str


class DispatchLLM(BaseModel):
    rationale: str = Field(description="3-5 sentences explaining the recommended action to an approver")
    tickets: list[TicketTextLLM] = Field(default_factory=list)


class SupervisorLLM(BaseModel):
    case_summary: str = Field(description="Two-sentence summary of the case for the analyst queue")


# ------------------------------------------------------------- domain shapes
class Evidence(BaseModel):
    field: str
    value: str
    page: int
    quote: str
    verified: bool = False  # quote found on that page's text layer by code


class Vendor(BaseModel):
    name: Optional[str] = None
    tax_id: Optional[str] = None
    country: Optional[str] = None


class Financial(BaseModel):
    total_value: Optional[float] = None
    currency: Optional[str] = None
    payment_terms_days: Optional[int] = None
    milestone_billing: Optional[bool] = None


class RiskClauses(BaseModel):
    liability_cap: Optional[str] = None
    governing_law: Optional[str] = None
    warranty_period_days: Optional[int] = None
    auto_renewal: Optional[bool] = None
    auto_renewal_notice_days: Optional[int] = None


class ExtractionResult(BaseModel):
    document_id: str
    document_type: DocumentType
    document_reference: Optional[str] = None
    effective_date: Optional[str] = None
    vendor: Vendor
    financial: Financial
    risk_clauses: RiskClauses
    evidence: list[Evidence]
    extraction_confidence: float = Field(ge=0, le=1)
    missing_fields: list[str]
    suspicious_instructions: list[str] = Field(default_factory=list)
    page_count: int = 0
    text_layer_pages: int = 0


class RetrievedSnippet(BaseModel):
    retrieval_id: str
    source_file: str
    policy_title: str
    page: int
    section: str
    heading: str
    snippet: str
    score: float
    owner: str


class Finding(BaseModel):
    finding_id: str
    clause_category: Category
    extracted_term: str
    contract_quote: Optional[str] = None
    contract_page: Optional[int] = None
    policy_standard: str
    policy_source: str
    policy_snippet: str
    retrieval_id: Optional[str] = None
    severity: Severity
    violation_detected: bool
    needs_review: bool = False
    guard_note: Optional[str] = None
    reasoning: str
    owner_group: str
    analyst_override: Optional[str] = None


class AuditResult(BaseModel):
    findings: list[Finding]
    rejected_findings: list[dict] = Field(default_factory=list)
    retrieval: dict[str, list[RetrievedSnippet]] = Field(default_factory=dict)
    retriever: str = "bm25"
    risk_score: int = Field(ge=0, le=100)
    risk_level: str
    overall_status: str


class Task(BaseModel):
    task_id: str
    target_system: str  # MOCK_ERP or MOCK_JIRA
    action_type: str
    parameters: dict[str, Any]


class ActionProposal(BaseModel):
    proposal_id: str
    case_id: str
    recommended_action: Action
    approval_route: list[str]
    tasks: list[Task]
    rationale: str
    citations: list[str]
    case_summary: str = ""
    requires_human_signoff: bool = True
    signoff_status: str = "PENDING"
    edits: list[dict] = Field(default_factory=list)


class EditRequest(BaseModel):
    severity_overrides: dict[str, Severity] = Field(default_factory=dict, description="finding_id -> new severity (NONE waives it)")
    payment_terms_days: Optional[int] = None
    note: str = Field("", max_length=1000)


class DecisionRequest(BaseModel):
    note: str = Field("", max_length=1000)


class CreateCaseRequest(BaseModel):
    sample: Optional[str] = Field(None, description="Key of a bundled synthetic demo document (A..J)")
