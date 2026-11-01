from typing import Literal
from uuid import UUID
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvironmentInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=2048)
    region: Literal["local", "domestic", "international", "custom"] = "local"
    stage: Literal["local", "test", "prod"] = "local"
    network_scope: Literal["local", "internal", "public"] = "local"
    credential_ref: UUID | None = None
    read_timeout: float = Field(default=120, ge=1, le=300)
    total_timeout: float = Field(default=600, ge=1, le=600)
    variables: dict[str, str] = Field(default_factory=dict, max_length=128)
    secret_variables: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("variables")
    @classmethod
    def bounded_variables(cls, value):
        if any(
            not re.fullmatch(r"[^{}\s:]{1,128}", k) or len(v) > 8192
            for k, v in value.items()
        ):
            raise ValueError("变量名或变量值超出限制")
        if (
            sum(len(k.encode()) + len(v.encode()) for k, v in value.items())
            > 128 * 1024
        ):
            raise ValueError("环境变量总大小超过128 KiB")
        return value

    @field_validator("name")
    @classmethod
    def nonblank(cls, v):
        if not v.strip():
            raise ValueError("名称不能为空")
        return v.strip()


class EnvironmentUpdate(EnvironmentInput):
    revision: int = Field(ge=1)


class EnvironmentConnection(StrictModel):
    network_scope: Literal["local", "internal", "public"] = "local"
    credential_ref: UUID | None = None
    read_timeout: float = Field(default=120, ge=1, le=300)
    total_timeout: float = Field(default=600, ge=1, le=600)


class EnvironmentGroupUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    region: Literal["local", "domestic", "international", "custom"] = "local"
    stage: Literal["local", "test", "prod"] = "local"
    variables: dict[str, str] = Field(default_factory=dict, max_length=128)
    secret_variables: list[str] = Field(default_factory=list, max_length=128)
    connections: dict[str, EnvironmentConnection] = Field(min_length=1, max_length=128)
    revisions: dict[UUID, int] = Field(max_length=128)
    revision: int = Field(ge=0)
    resolved_conflicts: list[str] = Field(default_factory=list, max_length=128)

    _bounded = field_validator("variables")(EnvironmentInput.bounded_variables.__func__)


class EnvironmentGroupArchive(StrictModel):
    revisions: dict[UUID, int] = Field(max_length=128)
    revision: int = Field(ge=0)


class RunInput(StrictModel):
    case_id: UUID | None = None
    environment_id: UUID
    submission_key: UUID
    name: str = Field(default="未命名请求", max_length=120)
    entry_type: Literal["raw", "qizhi", "ibrain", "aibots", "vendor"] = "raw"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "POST"
    path: str = Field(default="/", max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict, max_length=64)
    body: str = Field(default="", max_length=8 * 1024 * 1024)
    body_type: Literal["json", "text", "none"] = "json"
    stream: bool = False
    expected_status: int | None = Field(default=None, ge=100, le=599)
    contains: str = Field(default="", max_length=512)
    resource_bindings: list["ResourceBinding"] = Field(
        default_factory=list, max_length=16
    )


class CredentialInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4096)
    header: str = Field(default="Authorization", pattern=r"^[A-Za-z0-9-]{1,80}$")
    prefix: str = Field(default="Bearer ", max_length=32)

    @field_validator("value", "prefix")
    @classmethod
    def no_newlines(cls, v):
        if "\r" in v or "\n" in v:
            raise ValueError("凭证不可含换行")
        return v


class PairInput(StrictModel):
    code: str = Field(min_length=20, max_length=128)


class OpenAPIImport(StrictModel):
    document: dict
    source_id: str = Field(default="apipost-ai-services", min_length=1, max_length=128)


class ResourceAddress(StrictModel):
    id: UUID | None = None
    purpose: Literal["internal", "external", "third_party"]
    url: str | None = Field(default=None, min_length=8, max_length=8192)
    network_scope: Literal["local", "internal", "public"] = "public"
    credential_ref: UUID | None = None
    trusted: bool = False


class ResourceInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["image", "video", "audio"]
    tags: list[str] = Field(default_factory=list, max_length=20)
    addresses: list[ResourceAddress] = Field(default_factory=list, max_length=8)
    revision: int | None = Field(default=None, ge=1)


class ResourceDownload(StrictModel):
    revision: int = Field(ge=1)
    address_id: UUID


class ResourceBinding(StrictModel):
    resource_id: UUID
    revision: int = Field(ge=1)
    pointer: str = Field(min_length=2, max_length=512)
    mode: Literal["auto", "url", "internal", "external", "third_party", "base64"] = "auto"


class CaseSave(StrictModel):
    revision: int | None = Field(default=None, ge=1)
    request: dict
    group: str = Field(default="我的用例", max_length=200)


class TaxonomyCommand(StrictModel):
    revision: int = Field(ge=0)
    action: Literal["create", "rename", "delete"]
    kind: Literal["module", "business"]
    id: str | None = Field(default=None, max_length=512)
    module_id: str | None = Field(default=None, max_length=512)
    name: str = Field(default="", max_length=200)


class CaseMembership(StrictModel):
    module_id: str = Field(min_length=1, max_length=512)
    business_id: str | None = Field(default=None, max_length=512)


class CaseClassification(StrictModel):
    revision: int = Field(ge=0)
    case_revision: int = Field(ge=1)
    memberships: list[CaseMembership] = Field(min_length=1, max_length=32)


class ResponseExampleSave(StrictModel):
    run_id: UUID
