"""Enumerations shared by models, state machine and API."""

from __future__ import annotations

from enum import Enum


class ProjectState(str, Enum):
    idea = "idea"
    researching = "researching"
    script_draft = "script_draft"
    fact_check = "fact_check"
    storyboard = "storyboard"
    assets = "assets"
    voice = "voice"
    rendering = "rendering"
    quality_control = "quality_control"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    rejected = "rejected"
    completed = "completed"
    failed = "failed"


class VideoFormat(str, Enum):
    long = "long"
    short = "short"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    needs_human = "needs_human"


class ApprovalStage(str, Enum):
    idea = "idea"
    script = "script"
    claims = "claims"
    storyboard = "storyboard"
    final = "final"


class ApprovalDecision(str, Enum):
    approve = "approve"
    reject = "reject"
    changes_requested = "changes_requested"


class ClaimKind(str, Enum):
    fact = "fact"
    estimate = "estimate"
    opinion = "opinion"
    inference = "inference"


class SectionRole(str, Enum):
    hook = "hook"
    promise = "promise"
    context = "context"
    development = "development"
    reveal = "reveal"
    conclusion = "conclusion"
    cta = "cta"


class AssetType(str, Enum):
    stock_footage = "stock_footage"
    photo = "photo"
    historical_archive = "historical_archive"
    public_domain_image = "public_domain_image"
    authorized_screenshot = "authorized_screenshot"
    chart = "chart"
    map = "map"
    timeline = "timeline"
    animated_text = "animated_text"
    motion_graphics = "motion_graphics"
    generated_image = "generated_image"
    image_to_video = "image_to_video"
    text_to_video = "text_to_video"
    abstract_broll = "abstract_broll"


class AssetKind(str, Enum):
    video = "video"
    image = "image"
    graphic = "graphic"
    audio = "audio"


class AssetLicense(str, Enum):
    pexels = "pexels"
    pixabay = "pixabay"
    cc0 = "cc0"
    cc_by = "cc-by"
    cc_by_sa = "cc-by-sa"
    public_domain = "public-domain"
    generated = "generated"
    own = "own"
    unknown = "unknown"


class AssetStatus(str, Enum):
    candidate = "candidate"
    selected = "selected"
    rejected = "rejected"
    removed = "removed"


class ScriptStatus(str, Enum):
    draft = "draft"
    reviewed = "reviewed"
    approved = "approved"


class CostUnit(str, Enum):
    tokens = "tokens"
    characters = "characters"
    requests = "requests"
    seconds = "seconds"
    eur = "eur"
