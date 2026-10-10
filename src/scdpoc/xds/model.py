"""XDS.b constants (ITI TF-3) and the metadata model used by the demonstrator."""
from __future__ import annotations

from dataclasses import dataclass, field

NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "wsa": "http://www.w3.org/2005/08/addressing",
    "xds": "urn:ihe:iti:xds-b:2007",
    "lcm": "urn:oasis:names:tc:ebxml-regrep:xsd:lcm:3.0",
    "rim": "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0",
    "rs": "urn:oasis:names:tc:ebxml-regrep:xsd:rs:3.0",
    "query": "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0",
    "xop": "http://www.w3.org/2004/08/xop/include",
}

# SOAP actions
ITI41 = "urn:ihe:iti:2007:ProvideAndRegisterDocumentSet-b"
ITI18 = "urn:ihe:iti:2007:RegistryStoredQuery"
ITI43 = "urn:ihe:iti:2007:RetrieveDocumentSet"
ITI57 = "urn:ihe:iti:2010:UpdateDocumentSet"          # XDS Metadata Update: Update Document Set

# Object and classification identifiers
DOC_ENTRY_STABLE = "urn:uuid:7edca82f-054d-47f2-a032-9b2a5b5186c1"
SUBMISSION_SET_NODE = "urn:uuid:a54d6aa5-d40d-43f9-88c5-b4633d873bdd"

DE_CLASS = "urn:uuid:41a5887f-8865-4c09-adf7-e362475b143a"
DE_CONFIDENTIALITY = "urn:uuid:f4f85eac-e6cb-4883-b524-f2705394840f"
DE_FORMAT = "urn:uuid:a09d5840-386c-46f2-b5ad-9c3699a4309d"
DE_FACILITY = "urn:uuid:f33fb8ac-18af-42cc-ae0e-ed0b0bdb91e1"
DE_PRACTICE = "urn:uuid:cccf5598-8b07-4b77-a05e-ae952c785ead"
DE_TYPE = "urn:uuid:f0306f51-975f-434e-a61c-c59651d33983"
DE_AUTHOR = "urn:uuid:93606bcf-9494-43ec-9b4e-a7748d1a838d"
DE_PATIENT_ID = "urn:uuid:58a6f841-87b3-4a3e-92fd-a8ffeff98427"
DE_UNIQUE_ID = "urn:uuid:2e82c1f6-a085-4c72-9da3-8640a32e42ab"

SS_AUTHOR = "urn:uuid:a7058bb9-b4e4-4307-ba5b-e3f0ab85e12d"
SS_CONTENT_TYPE = "urn:uuid:aa543740-bdda-424e-8c96-df4873be8500"
SS_PATIENT_ID = "urn:uuid:6b5aea1a-874d-4603-a4bc-96a0a7b38446"
SS_SOURCE_ID = "urn:uuid:554ac39e-e3fe-47fe-b233-965d2a147832"
SS_UNIQUE_ID = "urn:uuid:96fdda7c-d067-4183-912e-bf5ee74998a8"

CODE_SCHEMES = {
    "classCode": DE_CLASS, "confidentialityCode": DE_CONFIDENTIALITY, "formatCode": DE_FORMAT,
    "healthcareFacilityTypeCode": DE_FACILITY, "practiceSettingCode": DE_PRACTICE, "typeCode": DE_TYPE,
}

ASSOC_HAS_MEMBER = "urn:oasis:names:tc:ebxml-regrep:AssociationType:HasMember"
ASSOC_RPLC = "urn:ihe:iti:2007:AssociationType:RPLC"
ASSOC_XFRM = "urn:ihe:iti:2007:AssociationType:XFRM"
ASSOC_APND = "urn:ihe:iti:2007:AssociationType:APND"
ASSOC_UPDATE_AVAILABILITY = "urn:ihe:iti:2010:AssociationType:UpdateAvailabilityStatus"

STATUS_APPROVED = "urn:oasis:names:tc:ebxml-regrep:StatusType:Approved"
STATUS_DEPRECATED = "urn:oasis:names:tc:ebxml-regrep:StatusType:Deprecated"
RESPONSE_SUCCESS = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Success"
RESPONSE_FAILURE = "urn:oasis:names:tc:ebxml-regrep:ResponseStatusType:Failure"
SEVERITY_ERROR = "urn:oasis:names:tc:ebxml-regrep:ErrorSeverityType:Error"

SQ_FIND_DOCUMENTS = "urn:uuid:14d4debf-8f97-4251-9a74-a90016b0af0d"
SQ_GET_DOCUMENTS = "urn:uuid:5c4f972b-d56b-40ac-a5fc-c8ca9b40b9d4"
SQ_GET_RELATED = "urn:uuid:d90e5407-b356-4d91-a89f-873917b4b0e6"


@dataclass
class Code:
    code: str
    scheme: str
    display: str = ""

    @classmethod
    def from_cfg(cls, d: dict) -> Code:
        return cls(d["code"], d["scheme"], d.get("display", ""))


@dataclass
class DocumentEntry:
    entry_uuid: str
    unique_id: str
    patient_id: str                 # CX: value^^^&oid&ISO
    mime_type: str
    title: str
    creation_time: str              # YYYYMMDDHHMMSS (UTC)
    language: str
    codes: dict[str, Code]          # keyed by classCode, typeCode, formatCode, ...
    author_institution: str = ""
    author_person: str = ""
    author_role: str = ""
    source_patient_id: str = ""
    hash: str = ""
    size: int | None = None
    repository_unique_id: str = ""
    status: str = STATUS_APPROVED
    comments: str = ""
    legal_authenticator: str = ""


@dataclass
class SubmissionSet:
    entry_uuid: str
    unique_id: str
    source_id: str
    patient_id: str
    submission_time: str
    content_type: Code
    author_institution: str = ""
    author_person: str = ""


@dataclass
class Association:
    entry_uuid: str
    type: str
    source: str
    target: str
    slots: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Submission:
    submission_set: SubmissionSet
    documents: list[DocumentEntry]
    associations: list[Association]
    contents: dict[str, bytes] = field(default_factory=dict)   # entry_uuid -> document bytes


class XdsError(Exception):
    def __init__(self, code: str, message: str, context: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context
