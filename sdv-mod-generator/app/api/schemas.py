"""API request/response schemas."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class GenerateRequest(BaseModel):
    user_id: str
    prompt: str = Field(max_length=10000)
    phase: str | None = None


class BatchGenerateRequest(BaseModel):
    user_id: str
    prompts: list[str] = Field(min_length=1, max_length=10)
    phase: str | None = None


class BatchGenerateItem(BaseModel):
    prompt: str
    request_id: str
    status: Literal["pending", "running", "done", "failed"]
    estimated_seconds: int | None = None


class BatchGenerateResponse(BaseModel):
    batch_id: str
    items: list[BatchGenerateItem]


class GenerateResponse(BaseModel):
    request_id: str
    status: Literal["pending", "running", "done", "failed"]
    estimated_seconds: int | None = Field(default=None, description="Estimated time to completion in seconds")


class ModStatusResponse(BaseModel):
    request_id: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    zip_url: str | None = None
    files_preview: list[str] = Field(default_factory=list)
    t1_errors: list[str] = Field(default_factory=list)
    generators_failed: list[str] = Field(default_factory=list)
    generators_succeeded: list[str] = Field(default_factory=list)
    t2_feedback: str | None = None
    t2_score: int | None = None
    t2_max_score: int | None = None
    t2_pass_threshold: int | None = None
    t2_passed: bool | None = None
    t2_available: bool | None = None
    t2_panel_passed_count: int | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100, description="Pipeline progress percentage")
    current_stage: str | None = Field(default=None, description="Current pipeline stage")
    created_at: datetime


class FilePreviewResponse(BaseModel):
    request_id: str
    files: dict[str, Any]


class HistoryEntry(BaseModel):
    request_id: str
    prompt: str
    status: str
    created_at: datetime


class HistoryResponse(BaseModel):
    user_id: str
    entries: list[HistoryEntry]


class ErrorResponse(BaseModel):
    detail: str
    code: str


class CancellationReasonsListResponse(BaseModel):
    """Response for ``GET /v1/mods/cancellation_reasons``.

    Returns the sorted, deduplicated set of valid cancellation reason
    ids that may appear on a cancelled request's
    :class:`ModStatusResponse.cancellation_reason` field. Mirrors the
    contract of ``storage.redis.KNOWN_CANCELLATION_REASONS`` so clients
    can validate user-supplied or LLM-generated reasons against the
    canonical set without scraping the source.

    This endpoint is the read-side companion of the
    ``GET /v1/mods/{id}/cancellation_reason`` endpoint — together they
    form a complete "what reasons exist, and what was the reason for
    *this* request" pair.
    """

    reasons: list[str] = Field(
        description=(
            "Sorted, deduplicated list of valid cancellation reason ids. "
            "Mirrors storage.redis.KNOWN_CANCELLATION_REASONS."
        ),
    )
    count: int = Field(description="Length of the reasons list (== len(reasons)).")


class CancellationReasonResponse(BaseModel):
    """A standalone view of a request's cancellation reason.

    Returned by ``GET /v1/mods/{id}/cancellation_reason``. Lets a
    caller (e.g. a chat bot or a dashboard) query just the reason
    without paying for the full status payload. ``status`` is always
    ``"cancelled"`` for a successful response; a 400 is returned
    otherwise.
    """
    request_id: str = Field(description="Server-generated id; matches the create-request id.")
    status: Literal["cancelled"] = Field(
        description="Always 'cancelled' for a successful response; 400 otherwise.",
    )
    cancellation_reason: str | None = Field(
        description=(
            "Why the request was cancelled. One of the known reasons "
            "in storage.redis.KNOWN_CANCELLATION_REASONS, or null for "
            "cancellations recorded before the reason field existed."
        ),
    )


class ModMetadataResponse(BaseModel):
    """Mod packaging metadata — schema + generator version info.

    Returned by ``GET /v1/mods/{id}/metadata``. Exposes the parsed
    contents of the packaged ``metadata.json`` and ``version.json`` for
    a completed request. Useful for downstream tools that want to
    verify schema compatibility or the generator build that produced
    the mod without downloading the whole zip.

    Both fields default to empty dicts so the response is well-typed
    even for older zips (pre-``version.json``) or for requests that
    exist but aren't packaged yet — the endpoint then returns 200
    with ``metadata={} version={}`` rather than 404.
    """
    request_id: str = Field(description="Server-generated id; matches the create-request id.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed contents of the packaged metadata.json (schema/version info).",
    )
    version: dict[str, Any] = Field(
        default_factory=dict,
        description="Parsed contents of the packaged version.json (generator build info).",
    )


class ModSummaryResponse(BaseModel):
    """Human-readable text summary of a mod request.

    Returned by ``GET /v1/mods/{id}/summary``. Aggregates the
    request's pipeline status, feature name (from the manifest), file
    count, generator list, and T1/T2 quality-gate outcomes into a
    single short text block in the ``summary`` field.

    Designed for chat-bots (``!summary``-style commands) and
    dashboards: one round-trip gives you the text to display.
    Numeric fields are also exposed separately so callers that
    want to render their own UI don't have to parse the text.
    """
    request_id: str = Field(description="Server-generated id; matches the create-request id.")
    status: str = Field(description="Current pipeline status (matches ModStatusResponse.status).")
    feature_name: str | None = Field(
        default=None,
        description="Human-readable feature name from the packaged manifest, if available.",
    )
    mod_id: str | None = Field(
        default=None,
        description="Manifest UniqueID for the mod; matches the public mod folder id.",
    )
    file_count: int = Field(default=0, ge=0, description="Number of generated files.")
    generator_count: int = Field(default=0, ge=0, description="Number of generators that ran.")
    generators: list[str] = Field(
        default_factory=list,
        description="Names of generators that produced output, in execution order.",
    )
    t1_status: str = Field(
        default="unknown",
        description="T1 gate verdict ('passed'/'failed'/'running'/'pending'/'unknown').",
    )
    t1_error_count: int = Field(default=0, ge=0, description="Number of distinct T1 errors reported.")
    t2_status: str = Field(
        default="unknown",
        description="T2 gate verdict ('passed'/'failed'/'unknown').",
    )
    t2_score: int | None = Field(default=None, description="Average T2 score (0-10); None when judges skipped.")
    t2_max_score: int | None = Field(default=None, description="Maximum possible T2 score (always 10).")
    t2_passed: bool | None = Field(default=None, description="True if T2 gate passed; None when skipped.")
    cancellation_reason: str | None = Field(
        default=None,
        description="Why a cancelled request was cancelled; surfaced here so the summary text can mention it without a second round-trip.",
    )
    created_at: str | None = Field(
        default=None,
        description="UTC timestamp the request was created (ISO-8601 string in JSON).",
    )
    summary: str = Field(description="Human-readable text summary.")


class TimelineStage(BaseModel):
    """A single stage of the mod generation pipeline in execution order.

    Returned by ``GET /v1/mods/{id}/timeline``. Each stage carries a
    short id (matching the orchestrator's internal ``status`` names
    like ``routing``/``generating``/``validating``/``reviewing``/
    ``packaging``), a human-readable ``label``, a ``reached`` flag
    (``True`` once the pipeline has moved past this stage, ``False``
    while the request is still earlier), and an ISO-8601 ``at``
    timestamp representing when the stage was entered (or
    ``None`` when not yet reached).

    ``at`` is **derived** from existing Redis state (``created_at``
    plus ``duration_seconds`` plus the live ``status`` field) so the
    endpoint does not require any new fields to be written by the
    pipeline. As a result the timestamps are best-effort
    approximations of when each stage *would have* run, not exact
    measurements — callers that need exact per-stage timing should
    add explicit stage logging to the orchestrator.
    """
    stage: str = Field(
        description="Short stage id (e.g. 'routing', 'generating', 'validating')."
    )
    label: str = Field(
        description="Human-readable label for the stage (e.g. 'Routing', 'Generating')."
    )
    reached: bool = Field(
        description="True once the pipeline has moved past this stage."
    )
    current: bool = Field(
        description="True if the pipeline is currently in this stage."
    )
    at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp at which the stage was entered. "
            "Approximated from created_at + duration_seconds for "
            "completed stages; None while a stage has not yet been "
            "reached."
        ),
    )


class ModTimelineResponse(BaseModel):
    """Response for ``GET /v1/mods/{id}/timeline``.

    Surfaces the per-stage execution order, which stage is currently
    active, and approximate timestamps so operators and chat bots can
    show the user "where it is right now" without re-parsing the full
    status payload. The endpoint is read-only and has no side
    effects — it only reads Redis pipeline state plus the DB
    fallback path used by ``GET /v1/mods/{id}``.

    All ``*_at`` fields are derived from existing state and are
    best-effort approximations, not exact measurements. The
    ``stages`` list is always returned in pipeline execution order
    (routing -> generating -> validating -> reviewing -> packaging ->
    done) so callers can render a fixed-length progress bar without
    re-sorting.
    """
    request_id: str = Field(description="Server-generated id; matches the {id} path parameter.")
    status: str = Field(
        description=(
            "Current pipeline status. One of the standard status "
            "ids (pending, routing, generating, t1_gating, t2_gating, "
            "packaging, done, failed, cancelled)."
        ),
    )
    started_at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp at which the request was "
            "received. Mirrors the ``created_at`` field on "
            "ModStatusResponse."
        ),
    )
    completed_at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp at which the request reached a "
            "terminal status (done/failed/cancelled). None while the "
            "request is still in flight."
        ),
    )
    progress_percent: int = Field(
        ge=0, le=100,
        description="Pipeline progress percentage, derived from status.",
    )
    current_stage: str = Field(
        description=(
            "Id of the currently-active stage (matches a ``stage`` "
            "field on one of the entries in ``stages``)."
        ),
    )
    current_stage_label: str = Field(
        description="Human-readable label for the current stage."
    )
    stages: list[TimelineStage] = Field(
        description=(
            "Pipeline stages in execution order. Each entry has a "
            "``reached`` and ``current`` flag plus an optional ``at`` "
            "timestamp."
        ),
    )


class GeneratorInfo(BaseModel):
    """Information about a single generator exposed via the API."""
    name: str = Field(description="Generator class/function name (stable identifier).")
    phase: str = Field(description="Phase this generator belongs to (e.g. 'shop_channel').")
    game: str = Field(description="Game pack this generator is registered for.")
    execution_position: int = Field(
        description="0-based position in the phase's execution order.",
    )


class GeneratorsResponse(BaseModel):
    """Response for ``GET /v1/mods/generators`` — lists all generators for a (game, phase) pair."""
    game: str = Field(description="Game pack the listed generators belong to.")
    phase: str = Field(description="Phase the listed generators belong to.")
    generators: list[GeneratorInfo] = Field(
        description="Generators in execution order (matches execution_position 0..N)."
    )


class PhaseInfo(BaseModel):
    """Information about a single phase in a game pack."""
    phase: str = Field(description="Phase id (e.g. 'shop_channel', 'weather_event').")
    generator_count: int = Field(ge=0, description="Number of generators registered for this phase.")
    execution_order: list[str] = Field(
        default_factory=list,
        description="Generator names in execution order.",
    )


class PackInfo(BaseModel):
    """Information about a single registered game pack."""
    game_id: str = Field(description="Stable game identifier (e.g. 'stardew_valley').")
    display_name: str = Field(description="Human-readable name for the pack (UI/dashboard use).")
    mod_format: str = Field(description="Target mod format (e.g. 'Content Patcher 1.29').")
    phases: list[PhaseInfo] = Field(description="Phases registered for this pack, in any order.")


class PhasesResponse(BaseModel):
    """Response for ``GET /v1/mods/phases`` — lists all registered game packs and their phases.

    The top-level ``packs`` field is the per-pack breakdown (each pack
    carries its own phase list with generator counts). The flat
    ``phases`` field is the sorted, deduplicated union of every phase
    id across all registered packs — the canonical list clients should
    use to validate a ``phase`` parameter before calling
    ``POST /v1/mods/generate``.
    """

    packs: list[PackInfo] = Field(
        description="All registered packs, in registration order.",
    )
    phases: list[str] = Field(
        default_factory=list,
        description=(
            "Sorted, deduplicated list of every known phase id across "
            "all registered packs."
        ),
    )


class KnownPhasesResponse(BaseModel):
    """Response for ``GET /v1/mods/phases/known`` — flat list of all known phase ids.

    A thin alias for the ``phases`` field of :class:`PhasesResponse`,
    exposed as its own endpoint so clients that only need the canonical
    phase list (e.g. to validate a ``phase`` parameter before calling
    ``POST /v1/mods/generate``, or to populate a dropdown in a UI) can
    do so without paying the per-pack serialization cost of
    ``GET /v1/mods/phases``.

    The flat list is the sorted, deduplicated union of every phase id
    across all registered packs — same data ``PhasesResponse.phases``
    exposes, but without the ``packs`` breakdown.
    """

    phases: list[str] = Field(
        description=(
            "Sorted, deduplicated list of every known phase id across "
            "all registered packs."
        ),
    )
    count: int = Field(
        ge=0,
        description="Length of the phases list (== len(phases)).",
    )


class PacksResponse(BaseModel):
    """Response for ``GET /v1/packs`` — list of registered game packs.

    Thin alias for the ``packs`` field of :class:`PhasesResponse`,
    exposed as its own endpoint so clients that only need the pack
    registry (a web UI showing "this server supports the following N
    packs", an integration test that wants to assert which packs
    registered, a Discord bot populating a ``/pack-info`` autocomplete)
    can do so without paying the per-phase serialization cost of
    ``GET /v1/mods/phases``.

    Mirrors the existing ``GET /v1/mods/phases/known`` endpoint which
    exposes the ``phases`` field of :func:`list_phases` as its own
    endpoint — together they complete the read-only phase / pack
    registry family.

    The endpoint does NOT take any query parameters and does NOT
    require an API key — same convention as ``/v1/mods/phases`` and
    ``/v1/mods/phases/known``. Read-only: no DB / Redis state, no side
    effects — purely a static introspection endpoint over the registered
    :class:`GamePack` registry.

    ``count`` mirrors the ``count`` field on :class:`KnownPhasesResponse`
    so the two thin-alias endpoints share the same envelope convention.
    Clients can render "N packs registered" without computing
    ``len(packs)`` themselves and the value stays stable across pack
    registration orderings.

    Adapted from the discord-ops-hardening branch's ``PacksResponse``
    (source bundle line 710-742). The shape is byte-identical to the
    branch's contract — same ``packs`` / ``count`` pair, same
    :class:`PackInfo` per pack — so a client written against the
    branch's response can switch to master without any code change.
    """

    packs: list[PackInfo] = Field(
        description=(
            "All registered packs, in registration order. Same shape "
            "as the ``packs`` field of :class:`PhasesResponse`."
        ),
    )
    count: int = Field(
        ge=0,
        description=(
            "Length of the packs list (== len(packs)). Mirrors the "
            "count field on KnownPhasesResponse so the two thin-alias "
            "endpoints share one envelope convention."
        ),
    )


class RoutePreviewResponse(BaseModel):
    """Response for ``GET /v1/route_preview`` — dry-run routing result.

    Mirrors the tuple returned by :func:`orchestrator.router.route`
    without actually starting a generation. Lets clients (chat bots,
    web UIs, integration tests) see *which* phase + game + generator
    pipeline would be selected for a given prompt before they pay the
    full generation cost via ``POST /v1/mods/generate``.

    The ``confidence`` field follows the same heuristic the
    orchestrator uses internally (matched-keyword length / 16.0,
    clamped to 1.0). A ``matched_keyword`` of ``""`` means the router
    fell back to the default phase because no keyword matched.

    Adapted from the discord-ops-hardening branch's
    ``RoutePreviewResponse`` (source bundle lines 745-803). The wire
    shape is byte-identical to the branch's contract — same six
    fields (``prompt`` / ``game`` / ``phase`` / ``generators`` /
    ``confidence`` / ``matched_keyword``) plus the optional
    ``locales`` echo — so a client written against the branch's
    response can switch to master without any code change.

    Note: the ``locales`` field has ``default_factory=list`` so a
    caller that does not pass the ``locales`` query parameter gets
    an empty list echoed back (zero-cost path). When ``locales``
    is provided, the v38 handler splits the comma-separated string
    and dedupes the entries — but does NOT validate the BCP-47
    shape. That validator (``_validate_locales_field``) lives on
    the branch but was deemed out of scope for the v38 first cut;
    adding it is a v39+ follow-up.
    """

    prompt: str = Field(
        description=(
            "Echo of the prompt the router was asked to route. "
            "Useful for logging and debugging without a separate "
            "request echo."
        ),
    )
    game: str = Field(
        description=(
            "Resolved game pack id (e.g. 'stardew_valley'). Defaults "
            "to the fallback pack when the prompt matches no game "
            "keyword."
        ),
    )
    phase: str = Field(
        description=(
            "Resolved phase id within the game pack (e.g. "
            "'shop_channel', 'texture'). Defaults to the pack's "
            "fallback phase when no phase keyword matched."
        ),
    )
    generators: list[str] = Field(
        default_factory=list,
        description=(
            "Generator names the orchestrator would run, in "
            "execution order. Defaults to an empty list so partial "
            "constructions are always valid."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Routing confidence in [0.0, 1.0]. 0.0 means no keyword "
            "matched (fallback path). 1.0 means a long, unambiguous "
            "keyword matched."
        ),
    )
    matched_keyword: str = Field(
        description=(
            "The keyword string that triggered the phase match. "
            "Empty string when the router fell back to the default "
            "phase."
        ),
    )
    locales: list[str] = Field(
        default_factory=list,
        description=(
            "Echo of the (optional) ``locales`` query parameter after "
            "split + dedup. Lets a caller preview exactly which locale "
            "codes the server would accept for a matching ``POST "
            "/v1/mods/generate`` call. Empty list (the default) means "
            "the caller did not pass the ``locales`` query parameter. "
            "v38 first cut does NOT validate the BCP-47 shape — the "
            "handler splits and dedupes but does not enforce the "
            "_validate_locale_code / _MAX_LOCALES_PER_PACK cap from "
            "the branch's ``_validate_locales_field`` helper."
        ),
    )


def _truncate_prompt(value: str | None) -> str | None:
    """Truncate a prompt to 200 characters at the schema boundary.

    Used as a ``field_validator`` target on :attr:`ModListItem.prompt`
    so the cap is enforced whether the field is populated directly by
    a route or by ``model_validate(row)`` in a test. The cap keeps a
    multi-KB prompt from blowing up the JSON envelope on the listing
    endpoint.
    """
    if value is None:
        return None
    return value[:200]


class ModListItem(BaseModel):
    """A single row in the ``GET /v1/mods`` listing.

    Mirrors :class:`ModStatusResponse` but trimmed for list views.
    ``feature`` mirrors ``phase`` (the orchestrator's term) under the
    public-facing name used elsewhere in the API; both fields are
    populated from the same DB column. ``has_zip`` is a denormalized
    flag derived from the LEFT JOIN'd ``mod_outputs.zip_key``: ``True``
    iff a zip has been uploaded for this request.
    """

    request_id: str = Field(description="Server-generated id of the request.")
    user_id: str | None = Field(
        default=None,
        description="Owning user; None when the request was created without a user scope (rare).",
    )
    status: str = Field(description="Current pipeline status (see ModStatusResponse.status for the canonical set).")
    phase: str | None = Field(
        default=None,
        description="Phase used for generation (orchestrator-internal name; new code should prefer 'feature').",
    )
    feature: str | None = Field(
        default=None,
        description="Public-facing feature name (same value as 'phase', client-facing alias).",
    )
    prompt: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Truncated to 200 characters at the schema boundary. "
            "The full prompt is available via GET /v1/mods/{id}."
        ),
    )
    created_at: datetime = Field(description="UTC timestamp the request was created.")
    updated_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the last status update; None if the row has never been touched after creation.",
    )
    has_zip: bool = Field(
        default=False,
        description=(
            "True iff a mod_outputs row exists for this request "
            "(i.e. the pipeline finished packaging the zip)."
        ),
    )

    @field_validator("prompt")
    @classmethod
    def _truncate_prompt_field(cls, v: str | None) -> str | None:
        return _truncate_prompt(v)


class ModListResponse(BaseModel):
    """Response for ``GET /v1/mods``.

    Returns a page of mod requests with optional ``user_id`` and
    ``status`` query filters, plus pagination metadata (``limit``,
    ``offset``, ``has_more``) and a real ``total`` count of all rows
    matching the filters (not just the current page). ``has_more`` is
    the canonical "is there another page?" flag, computed as
    ``offset + len(items) < total``. ``filters`` echoes back the
    filters that were actually applied so a caller can verify their
    query string was honored.
    """

    items: list[ModListItem] = Field(
        description="Page of mod requests matching the filters; len(items) <= limit."
    )
    total: int = Field(ge=0, description="Total rows matching the filters (>= len(items)).")
    limit: int = Field(ge=0, description="The limit that was applied (post-validation).")
    offset: int = Field(
        default=0,
        ge=0,
        description="The offset that was applied (0 = first page).",
    )
    has_more: bool = Field(
        default=False,
        description=(
            "True iff there is at least one more row after this page "
            "(i.e. ``offset + len(items) < total``)."
        ),
    )
    filters: dict[str, str | None] = Field(
        default_factory=dict,
        description=(
            "Echo of the query-string filters that were actually applied. "
            "Useful for verifying the request was honored."
        ),
    )


class StatusBreakdown(BaseModel):
    """Counts of mod requests grouped by status.

    Returned by ``GET /v1/mods/stats`` — one row per status that has at
    least one matching request, plus ``count`` so callers can sanity-check
    the sum without a second round-trip. A status with zero rows is
    omitted from the list (not included as ``count=0``) because the
    canonical set of statuses is already documented at
    ``/v1/mods/cancellation_reasons`` and the listing's full status list.
    """

    status: str = Field(description="Pipeline status name (matches ModStatusResponse.status).")
    count: int = Field(ge=0, description="Number of mod requests in this status.")


class PhaseBreakdown(BaseModel):
    """Counts of mod requests grouped by phase.

    Returned by ``GET /v1/mods/stats`` — one row per phase that has at
    least one matching request. ``phase`` is the phase id (e.g.
    ``shop_channel``, ``weather_event``); requests with ``phase IS NULL``
    are surfaced under the synthetic key ``__none__`` so callers can
    distinguish "no phase set" from "phase key with zero rows".
    """

    phase: str = Field(description="Phase id, or '__none__' for requests with no phase set.")
    count: int = Field(ge=0, description="Number of mod requests in this phase.")


class StatsResponse(BaseModel):
    """Response for ``GET /v1/mods/stats`` — aggregate operator view.

    Pure read-only counters intended for an admin / operator dashboard.
    The endpoint is intentionally cheap: both breakdowns are computed
    in a single ``GROUP BY`` query each, so the response scales with
    the number of distinct (status, phase) values, not the total row
    count.

    The companion endpoint ``GET /v1/mods`` (the v7 listing) returns
    per-request rows for the same table; this endpoint is the
    aggregate view of the same data. The two together cover the
    "list / summarize" operator use case.
    """

    total: int = Field(
        ge=0,
        description="Total number of mod_requests rows in the database.",
    )
    by_status: list[StatusBreakdown] = Field(
        default_factory=list,
        description=(
            "Counts grouped by status, sorted by count descending. Statuses "
            "with zero rows are omitted (the canonical status set is "
            "documented elsewhere)."
        ),
    )
    by_phase: list[PhaseBreakdown] = Field(
        default_factory=list,
        description=(
            "Counts grouped by phase, sorted by count descending. Requests "
            "with no phase are grouped under '__none__'."
        ),
    )
    generated_at: datetime = Field(
        description="Server-side timestamp at which the stats were computed (UTC).",
    )


class FeatureFlagValue(BaseModel):
    """A single feature flag and its current on/off state.

    Returned by ``GET /v1/feature_flags`` for each entry in the
    registered feature-flag registry. ``name`` is the stable,
    snake_case flag identifier (the same key the orchestrator passes
    to ``orchestrator.feature_flags.is_enabled()``), and ``enabled``
    mirrors the live boolean value at the moment the endpoint was
    called.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagValue`` (source bundle line 1061-1089) for
    master's ``orchestrator.feature_flags`` symbol set — master's
    module exposes ``is_enabled()`` (resolves ``_overrides`` first,
    then ``_DEFAULT_FLAGS``) rather than a single ``_FLAGS`` dict.
    The response shape is byte-identical to the branch's so the
    API contract stays stable for clients.

    This model is intentionally tiny — the endpoint exists to
    surface runtime state, not to expose the registry's internals
    (e.g. rollout owner, default value, environment override). If
    we ever need to surface that metadata, add a separate model;
    do not overload this one.
    """

    name: str = Field(
        description=(
            "Stable snake_case flag identifier (e.g. "
            "'t2_three_judge_panel', "
            "'discord_dm_notifier', "
            "'security_headers_middleware'). "
            "Matches the keys of "
            "orchestrator.feature_flags._DEFAULT_FLAGS."
        ),
    )
    enabled: bool = Field(
        description=(
            "Whether the flag is currently on. Mirrors the live "
            "value of orchestrator.feature_flags.is_enabled(name) "
            "at the moment the endpoint was called — overrides "
            "win over the registry default."
        ),
    )


class FeatureFlagsResponse(BaseModel):
    """Response for ``GET /v1/feature_flags`` — registry snapshot.

    Lists every flag currently registered in
    ``orchestrator.feature_flags._DEFAULT_FLAGS`` together with
    its live value, so operators can verify which features are
    actually enabled in a running process without parsing logs.

    ``flags`` is sorted by ``name`` (the registry's
    :func:`orchestrator.feature_flags.known_flags` helper returns
    a sorted tuple, and the route re-sorts defensively before
    building the response) so callers can rely on a stable order
    for snapshot tests and dashboard rendering.

    ``count`` is the length of ``flags`` and is included so callers
    can sanity-check the response without a separate ``len()``
    call after JSON parsing. The endpoint is intentionally
    read-only and has no side effects — it does not consult Redis,
    PostgreSQL, or any external config store, and is safe to poll
    freely.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagsResponse`` (source bundle line 1092-1121). The
    response shape is byte-identical to the branch's so a
    dashboard written against the branch contract still works
    against the master module.
    """

    flags: list[FeatureFlagValue] = Field(
        description=(
            "Every registered feature flag and its current value, "
            "sorted by name. Mirrors "
            "orchestrator.feature_flags.known_flags() in order, "
            "and "
            "orchestrator.feature_flags.is_enabled(name) in "
            "contents (override > default)."
        ),
    )
    count: int = Field(
        ge=0,
        description="Length of the flags list (== len(flags)).",
    )


class FlagHistoryEntry(BaseModel):
    """One row in the in-memory feature-flag override audit log.

    Returned by ``GET /v1/feature_flags/history`` for every recorded
    :func:`orchestrator.feature_flags.record_override` event. The
    fields mirror the :class:`orchestrator.feature_flags.FlagOverride`
    frozen dataclass one-for-one so the API and the in-process audit
    log cannot drift — any change to ``FlagOverride``'s shape must
    also update this model, and vice versa.

    The audit log is process-local and bounded by
    ``orchestrator.feature_flags._HISTORY_LIMIT`` (default 100 rows,
    a ``deque`` ring buffer). It resets on every process restart and
    is intentionally NOT persisted to Redis, the DB, or any external
    store. That is the right trade-off for a P5-staged rollout: the
    recent operator activity is what matters (last few hours, last
    few days), and persisting everything would conflate the runtime
    audit trail with the deployment-change history that already lives
    in git + a future change-log endpoint. If long-term persistence
    becomes necessary, the cleanest path is to subscribe a sink to
    the ``feature_flag.override_recorded`` log event and let that
    sink write to whatever store the operator chooses — the
    in-memory log stays fast and bounded, and the durable copy
    lives wherever the operator wants it.

    **Adaptation vs. the discord-ops-hardening branch** (source
    bundle line 1198-1266 ``FlagHistoryEntry``): the source's
    schema carried ``flag_name`` / ``previous_value`` / ``new_value``
    / ``changed_at`` / ``no_op`` because the branch's history was
    a list of dicts emitted by a separate ``record_flag_change``
    helper that pre-computed the previous value, a UTC timestamp,
    and a no-op marker. Master's cleanroom port (see
    ``orchestrator/feature_flags.py``) collapses the audit path
    into a single :class:`FlagOverride` frozen dataclass that
    stores ``name`` / ``value`` / ``reason`` / ``actor`` — the
    previous value, the timestamp, and the no-op flag are NOT
    retained because (a) ``set_flag`` already returns the previous
    value to the caller, (b) ordering is implicit in the deque's
    insertion order so a timestamp is redundant for an
    in-memory-only log, and (c) a no-op write is just another
    append with ``value`` equal to the prior ``value`` on that
    flag, which the operator can infer from the order. The
    resulting response is byte-smaller than the branch's but
    carries the audit information that is actually durable in the
    master module (who, what, why) — and the field names match
    ``FlagOverride`` so a caller introspecting one can introspect
    the other without a translation layer.
    """

    name: str = Field(
        description=(
            "Stable snake_case flag identifier that was overridden "
            "(e.g. 't2_three_judge_panel', 'security_headers_middleware'). "
            "Matches ``orchestrator.feature_flags.FlagOverride.name`` "
            "and the keys of ``orchestrator.feature_flags._DEFAULT_FLAGS``."
        ),
    )
    value: bool = Field(
        description=(
            "The flag's value after the override was applied. Matches "
            "``orchestrator.feature_flags.FlagOverride.value``. For a "
            "no-op write (the operator flipped a flag that was already "
            "in the requested state), this equals the value before the "
            "call — the caller can detect a no-op by comparing the "
            "current ``value`` against the most recent prior ``value`` "
            "on the same ``name`` in the history page."
        ),
    )
    reason: str = Field(
        description=(
            "Free-text justification supplied by the override "
            "(e.g. 'set_flag', 'pin_flag', 'manual rollback'). "
            "Matches ``orchestrator.feature_flags.FlagOverride.reason``. "
            "Empty string when the override path did not supply one."
        ),
    )
    actor: str = Field(
        description=(
            "Handle of the operator or service that issued the override "
            "(defaults to ``\"system\"`` for automated rollouts). Matches "
            "``orchestrator.feature_flags.FlagOverride.actor``."
        ),
    )


class FlagHistoryResponse(BaseModel):
    """Response for ``GET /v1/feature_flags/history`` — audit-log page.

    Surfaces the in-memory override audit log so operators can answer
    "who flipped which flag when" without scraping structlog output or
    grepping a log shipper. The endpoint is read-only and mirrors
    exactly what :func:`orchestrator.feature_flags.get_history` returns
    — there is no transformation, no server-side filtering beyond
    the optional ``flag_name`` query parameter, and no enrichment.

    ``entries`` is ordered **newest-first** (matching
    :func:`orchestrator.feature_flags.get_history` — the deque is
    reversed after the optional filter so the most recent operator
    activity surfaces at the top of the page). The endpoint clamps
    ``entries`` to the LAST ``limit`` rows (default 100, max 1000) but
    ``total`` reflects the FULL count BEFORE the limit is applied — so
    a caller can detect that the history has wrapped or grown past the
    page size. (A future ``before`` cursor would unlock true
    pagination; this version only supports the ``flag_name``-filter
    paged view.)

    ``total`` is included even when the response is empty (== 0) so
    clients can assert "the log is genuinely empty" vs. "the filter
    matched nothing" without an extra round-trip.

    Adapted from the discord-ops-hardening branch's
    ``FlagHistoryResponse`` (source bundle line 1269-1309). The
    response shape diverges from the branch's in two places: (1)
    the inner entries are :class:`FlagHistoryEntry` rather than the
    branch's flag-name/previous/new/timestamp/no-op dict (see
    :class:`FlagHistoryEntry`'s docstring for why), and (2) the
    sort order is newest-first to match
    :func:`orchestrator.feature_flags.get_history`'s contract (the
    branch returned oldest-first because the branch's
    ``record_flag_change`` helper appended in chronological order
    and the handler sliced the last N rows for the "most recent
    activity visible" property). The total / limit / filter
    contract is otherwise byte-identical so a dashboard that
    consumes the branch's response will work with only a sort
    flip.
    """

    entries: list[FlagHistoryEntry] = Field(
        description=(
            "Audit-log rows that matched the request, newest-first. "
            "Empty list if the history is empty or the ``flag_name`` "
            "filter matched no rows. The list is the LAST ``limit`` "
            "rows of the filtered set (most recent operator activity "
            "surfaces at index 0)."
        ),
    )
    total: int = Field(
        ge=0,
        description=(
            "Total number of rows that matched the request, BEFORE the "
            "``limit`` clamp is applied. Equal to ``len(entries)`` when "
            "the matching set was small enough to fit in one page, "
            "larger otherwise."
        ),
    )


class FeatureFlagUpdate(BaseModel):
    """Request body for ``POST /v1/feature_flags/{name}`` — toggle a flag.

    The endpoint accepts the desired ``enabled`` state as a JSON
    body rather than a query parameter so callers don't have to
    URL-encode booleans, and so the schema is trivially extensible
    later (e.g. adding an ``expires_at`` for timed toggles would
    not require a new route or query string).

    ``name`` is duplicated in the body even though it is also in
    the URL path — the duplication is deliberate. It lets a client
    construct the body once and POST it to the same route for
    different flags without rebuilding the request, and it makes
    the body self-describing in logs and traces where the URL is
    stripped. The route ignores the body's ``name`` and uses the
    path parameter as the source of truth.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagUpdate`` (source bundle line 1124-1156). The
    field names and shapes are byte-identical to the branch's so
    a client written against the branch contract works against
    master without any code change.
    """

    name: str = Field(
        description=(
            "Stable snake_case flag identifier. Must match the "
            "{name} path parameter; the route uses the path "
            "parameter as the source of truth and treats the body's "
            "name as a self-describing label only."
        ),
    )
    enabled: bool = Field(
        description=(
            "Desired new value of the flag. True turns the flag "
            "on, False turns it off. The change is recorded in the "
            "audit log event 'feature_flag.override_recorded' "
            "(via the underlying ``record_override`` helper) and "
            "in the structured 'feature_flag.changed' log event."
        ),
    )


class FeatureFlagChangeResponse(BaseModel):
    """Response for ``POST /v1/feature_flags/{name}`` — toggle confirmation.

    Mirrors the request shape but adds ``previous_value`` so the
    caller can confirm what the flag was set to before the change
    took effect. This is essential for an audit endpoint: an
    operator who toggles a flag from off to on wants to see the
    "was off / is on" pair, not just "is on".

    The fields mirror ``FeatureFlagValue`` plus ``previous_value``
    rather than nesting ``FeatureFlagValue`` so the response shape
    stays flat — easier to log, easier to grep, easier to render
    in a single-line terminal table.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagChangeResponse`` (source bundle line 1159-1195).
    The field names and shapes are byte-identical to the
    branch's so the wire shape is unchanged.
    """

    name: str = Field(
        description=(
            "The flag that was updated. Matches the {name} path "
            "parameter that was POSTed."
        ),
    )
    enabled: bool = Field(
        description=(
            "The new value of the flag after the change. Mirrors "
            "orchestrator.feature_flags.is_enabled(name) at the "
            "moment the response was built."
        ),
    )
    previous_value: bool = Field(
        description=(
            "The value of the flag immediately before the change. "
            "Equals ``enabled`` if the operator's request was a "
            "no-op (the registry was already in the requested "
            "state), in which case ``feature_flag.override_recorded`` "
            "is still emitted for audit-trail completeness."
        ),
    )


class FeatureFlagRollbackResponse(BaseModel):
    """Response for ``POST /v1/feature_flags/{name}/rollback``.

    The v40 companion to :class:`FeatureFlagChangeResponse` (the
    toggle endpoint's response): surfaces the result of a
    rollback so the operator can confirm what was undone, what
    the flag is now, and which audit-log entry was the source of
    truth for the restored value. Together with the v15 GET
    (registry snapshot), v16 POST (toggle), and v17 GET (audit
    log) endpoints, this closes the operator-dashboard loop on a
    single flag: read, write, audit, undo.

    Field semantics:

    - ``name`` — the flag that was rolled back. Matches the
      ``{name}`` path parameter that was POSTed.
    - ``rolled_back_from`` — the value of the flag immediately
      before the rollback took effect. This is what the
      ``set_flag`` call inside the rollback helper observed as the
      "previous value" at write time, and it is the value the
      flag held at the end of the most recent real mutation.
    - ``rolled_back_to`` — the value of the flag after the
      rollback. Equals the ``previous_value`` of the most recent
      non-no-op entry in the audit log (i.e. the value the flag
      held BEFORE the change that is now being undone). On a
      second consecutive rollback (undoing the undo), this is the
      value the flag had before the first rollback.
    - ``restored_entry_index`` — the position (0-indexed, in the
      ascending-sorted history) of the audit-log entry whose
      ``previous_value`` was re-applied. Surfaced so dashboards
      can render "rolled back change #N from the audit log".
      ``-1`` when the helper was unable to find a rollbackable
      entry (kept in-band rather than ``None`` so the model stays
      a flat typed structure with every field guaranteed to be a
      primitive).
    - ``history_size_at_rollback`` — the size of the in-memory
      audit log at the moment the rollback was recorded.
      Snapshot here (not at response-build time in the route) so
      the value is consistent with the restored ``_overrides[name]``
      even if another request interleaves a mutation between the
      helper call and the route's response construction.

    The model is intentionally a flat record (no nested
    ``FeatureFlagValue``) so a dashboard can render the four
    audit-relevant fields as a single terminal table row, and so
    the JSON shape matches the ``rollback_flag()`` helper's
    return value one-to-one — no field-by-field copy in the
    route.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagRollbackResponse`` (source bundle line
    1312-1405). Field names and types are byte-identical to the
    branch's; the only adaptation is the docstring's
    "Adapted from..." provenance paragraph and a reference to
    master's ``_DEFAULT_FLAGS`` / ``_overrides`` symbol names
    (the branch had a single ``_FLAGS`` dict). The wire shape is
    byte-identical to the branch's contract.
    """

    name: str = Field(
        description=(
            "The flag that was rolled back. Matches the {name} "
            "path parameter that was POSTed."
        ),
    )
    rolled_back_from: bool = Field(
        description=(
            "Value of the flag immediately BEFORE the rollback "
            "took effect. Equals the ``new_value`` of the most "
            "recent real change to this flag in the audit log."
        ),
    )
    rolled_back_to: bool = Field(
        description=(
            "Value of the flag AFTER the rollback. Equals the "
            "``previous_value`` of the most recent real change to "
            "this flag (i.e. what the flag held before that "
            "change happened). On a double-rollback, this is the "
            "value the flag held before the first rollback."
        ),
    )
    restored_entry_index: int = Field(
        ge=-1,
        description=(
            "Index (0-based, ascending-sorted history) of the "
            "audit-log entry whose ``previous_value`` was "
            "re-applied by this rollback. -1 when no real change "
            "exists to roll back to (the route surfaces this as a "
            "409 Conflict before reaching the response model)."
        ),
    )
    history_size_at_rollback: int = Field(
        ge=0,
        description=(
            "Size of the in-memory audit log at the moment the "
            "rollback was recorded. Includes the rollback's own "
            "audit-log entry. Useful for dashboards that want to "
            "render the rollback as 'change N of M'."
        ),
    )


class FeatureFlagPinResponse(BaseModel):
    """Response for ``POST /v1/feature_flags/{name}/pin`` and ``/unpin``.

    The v41 companion to :class:`FeatureFlagRollbackResponse` (the
    rollback endpoint's response) and :class:`FeatureFlagChangeResponse`
    (the toggle endpoint's response): surfaces the result of a
    pin/unpin so the operator can confirm what lock state the flag
    is now in, whether the call was a no-op (``already_pinned`` /
    ``was_pinned``), and the flag's current value.

    The two endpoints share a single response model because their
    shapes are identical at the wire level — only the boolean
    sentinel differs (``already_pinned`` vs. ``was_pinned``). A
    single model keeps the API contract flat and lets the same
    parser code on the client side handle both endpoints.

    Field semantics:

    - ``name`` — the flag that was pinned or unpinned. Matches
      the ``{name}`` path parameter that was POSTed.
    - ``pinned`` — the new lock state of the flag after the call.
      Always ``True`` for the ``/pin`` endpoint, always ``False``
      for the ``/unpin`` endpoint. Surfaced so a single
      dashboard renderer can branch on the value rather than
      re-deriving it from which endpoint was called.
    - ``already_pinned`` (pin endpoint) — ``True`` when the flag
      was already locked and the call was a no-op, ``False``
      when the pin actually transitioned. The pin endpoint
      intentionally returns 200 with ``already_pinned=True``
      rather than 4xx because pinning a locked flag is a
      legitimate operator pattern (idempotent lock).
    - ``was_pinned`` (unpin endpoint) — ``True`` when the call
      actually removed a pin, ``False`` when the flag was not
      pinned and the call was a no-op. Mirrors the pin
      endpoint's idempotent contract.
    - ``current_value`` — the flag's current boolean value at the
      moment the response was built. Mirrors
      :func:`orchestrator.feature_flags.is_enabled(name)` for
      override > default resolution. Useful for dashboards that
      want to render "pinned at value V" without a second round
      trip to ``GET /v1/feature_flags``.

    The model is intentionally a flat record (no nested
    ``FeatureFlagValue``) so a dashboard can render the four
    audit-relevant fields as a single terminal table row, and so
    the JSON shape matches the ``pin_flag()`` /
    ``unpin_flag()`` helpers' return values one-to-one — no
    field-by-field copy in the route.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagPinResponse`` (source bundle line
    1648-1710). Field names and types are byte-identical to the
    branch's; the only adaptation is the docstring's
    "Adapted from..." provenance paragraph and a reference to
    master's ``_DEFAULT_FLAGS`` / ``_overrides`` symbol names
    (the branch had a single ``_FLAGS`` dict). The wire shape is
    byte-identical to the branch's contract.
    """

    name: str = Field(
        description=(
            "The flag that was pinned or unpinned. Matches the "
            "{name} path parameter that was POSTed."
        ),
    )
    pinned: bool = Field(
        description=(
            "The new lock state of the flag after the call. "
            "Always True for /pin, always False for /unpin. "
            "Surface value is the post-call state, not the "
            "pre-call state."
        ),
    )
    already_pinned: bool = Field(
        description=(
            "Pin endpoint only: True when the flag was already "
            "locked and the call was a no-op. False when the pin "
            "actually transitioned from unlocked to locked. "
            "Always False on the unpin endpoint (the unpin "
            "endpoint sets was_pinned instead)."
        ),
    )
    was_pinned: bool = Field(
        description=(
            "Unpin endpoint only: True when the call actually "
            "removed a pin. False when the flag was not pinned "
            "and the call was a no-op. Always False on the "
            "pin endpoint (the pin endpoint sets "
            "already_pinned instead)."
        ),
    )
    current_value: bool = Field(
        description=(
            "Current boolean value of the flag at the moment "
            "the response was built. Mirrors "
            "orchestrator.feature_flags.is_enabled(name) for "
            "override > default resolution."
        ),
    )


class FeatureFlagPinStateResponse(BaseModel):
    """Response for ``GET /v1/feature_flags/{name}/pin`` — pin state snapshot.

    The v43 read-only companion to v41's :class:`FeatureFlagPinResponse`
    (returned by ``POST /v1/feature_flags/{name}/pin`` and
    ``/unpin``). Where the POST endpoints have a side effect (toggling
    the pin set), this GET is pure inspection: it surfaces the
    current pin state of a flag without mutating any registry, so an
    operator dashboard can poll the live "is this flag locked?"
    view as often as it likes without worrying about duplicate
    toggles or audit-log pollution.

    Field semantics:

    - ``name`` — the flag whose pin state was queried. Matches the
      ``{name}`` path parameter of the GET request. Echoed back so a
      caller polling many flags in parallel can route the response
      without relying on its own request bookkeeping.
    - ``pinned`` — whether the flag is currently in the
      ``_locked_pins`` set. ``True`` if the flag is locked (a
      subsequent ``set_flag`` call would raise
      :class:`FlagPinnedError`); ``False`` if the flag is mutable.
    - ``current_value`` — the flag's value in the ``_FLAGS``
      registry at the moment the response was built. Surfaced so a
      dashboard can render "pinned at on" / "pinned at off" without
      a second round-trip to ``GET /v1/feature_flags``.
    - ``known`` — ``True`` if the flag is registered in
      ``_DEFAULT_FLAGS`` ∪ ``_overrides``; ``False`` if the URL path
      contains a typo or a stale name. The 200 response always sets
      this to ``True`` (unknown flags surface via a 404
      ``HTTPException`` instead), so the value is informational
      rather than a sentinel. Included for symmetry with the
      route's 200/404 contract.

    Status code mapping:

    - ``200 OK`` — the flag is known; the response carries the
      full snapshot above.
    - ``404 Not Found`` — the flag is not registered. Mirrors the
      v41 (and v18) contract: a typo in the path fails closed and
      surfaces ``{"detail": "Unknown feature flag: '<name>'"}``.

    The endpoint is unauthenticated by design, matching the
    v15/v16/v17/v18/v41/v42 pin/rollback/toggle/history/registry
    siblings, and the scope is in-memory (mirrors ``_FLAGS`` /
    ``_overrides`` / ``_locked_pins`` / ``_history``) — the response
    reflects exactly what the orchestrator would use to gate a real
    call site one line later in the same process.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagPinStateResponse`` (source bundle line 1499-1582).
    Field names and types are byte-identical to the branch's; the
    only adaptation is the docstring's "Adapted from..." provenance
    paragraph and a reference to master's ``_DEFAULT_FLAGS`` /
    ``_overrides`` / ``_locked_pins`` symbol names (the branch had
    a single ``_FLAGS`` dict and a single ``_PINNED_FLAGS`` set).
    The wire shape is byte-identical to the branch's contract.
    """

    name: str = Field(
        description=(
            "The flag whose pin state was queried. Matches the "
            "{name} path parameter of the GET request."
        ),
    )
    pinned: bool = Field(
        description=(
            "True iff the flag is currently in _locked_pins "
            "(i.e. a subsequent set_flag call would raise "
            "FlagPinnedError). False if the flag is mutable."
        ),
    )
    current_value: bool = Field(
        description=(
            "The flag's value in the _FLAGS registry at the "
            "moment the response was built. Mirrors what the "
            "orchestrator would use to gate a real call site "
            "one line later in the same process."
        ),
    )
    known: bool = Field(
        description=(
            "True if the flag is registered in _DEFAULT_FLAGS or "
            "_overrides. The 200 response always sets this to True; "
            "unknown flags are surfaced via a 404 HTTPException "
            "instead. Included for symmetry with the route's "
            "200/404 contract and to make the success response "
            "self-describing for dashboards that render this "
            "field verbatim."
        ),
    )


class FeatureFlagPinSummary(BaseModel):
    """A single pinned feature flag, returned in the ``pins`` list.

    Response item for ``GET /v1/feature_flags/pins`` — surfaces the
    current on/off value of one pinned flag so an operator dashboard
    can render a flat table of "what's locked, and what state is it
    locked at?" without making a follow-up call to
    ``GET /v1/feature_flags``.

    The shape mirrors the v15 :class:`FeatureFlagValue` model
    (``name`` + ``enabled``) but is named ``FeatureFlagPinSummary``
    so dashboards can render the two collections differently — a
    pin summary is the operator's "what's locked right now?" view
    while :class:`FeatureFlagValue` is the registry's "what exists?"
    view. Keeping the two as separate Pydantic models also lets
    future fields land on one without polluting the other.

    Field semantics:

    - ``name`` — the stable snake_case flag identifier. Matches a
      member of ``_locked_pins`` (and a key in ``_DEFAULT_FLAGS``
      or ``_overrides`` — the route's source list).
    - ``current_value`` — the flag's value at the moment the
      response was built. Mirrors
      :func:`orchestrator.feature_flags.is_enabled`. Surfaced so a
      dashboard can render "pinned at on" / "pinned at off"
      without a second round-trip to ``GET /v1/feature_flags``.

    The model is intentionally minimal (no ``pinned`` boolean —
    every flag in this list is pinned by construction) so dashboards
    can render the two fields as a single terminal table row and
    so the JSON shape matches the ``get_pinned_flags()`` +
    ``is_enabled()`` pair exactly with no field-by-field copy.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagPinSummary`` (source bundle line 1585-1636). Field
    names and types are byte-identical to the branch's; the only
    adaptation is the docstring's "Adapted from..." provenance
    paragraph and a reference to master's ``_DEFAULT_FLAGS`` /
    ``_overrides`` / ``_locked_pins`` symbol names (the branch had
    a single ``_FLAGS`` dict and a single ``_PINNED_FLAGS`` set).
    The wire shape is byte-identical to the branch's contract.
    """

    name: str = Field(
        description=(
            "Stable snake_case flag identifier (e.g. "
            "'t2_three_judge_panel', 'discord_dm_notifier', "
            "'security_headers_middleware'). Matches a member "
            "of orchestrator.feature_flags._locked_pins and a key "
            "in _DEFAULT_FLAGS or _overrides."
        ),
    )
    current_value: bool = Field(
        description=(
            "Whether the flag is currently on. Mirrors "
            "orchestrator.feature_flags.is_enabled(name) at the "
            "moment the response was built. Surfaced so the "
            "dashboard can render 'pinned at on' / 'pinned at off' "
            "without a second round-trip to GET /v1/feature_flags."
        ),
    )


class FeatureFlagPinsResponse(BaseModel):
    """Response for ``GET /v1/feature_flags/pins`` — collection of pinned flags.

    The v44 collection-level companion to v43's
    :class:`FeatureFlagPinStateResponse` (which surfaces the pin
    state of a *single* flag). Where v43 answers the dashboard's
    "is *this* flag locked?" question, this endpoint answers
    "which flags are locked *right now*?" — a flat, sorted list
    so the operator can render the entire locked-set in one place
    without a loop over the v15 ``GET /v1/feature_flags`` response.

    Together with the v41 POST ``/pin`` / ``/unpin`` endpoints and
    v43's GET ``/{name}/pin``, the four endpoints complete the
    pin-state surface:

    - ``POST /pin`` — pin a flag (idempotent: 200 even on re-pin).
    - ``POST /unpin`` — unpin a flag (idempotent: 200 even on
      un-unpin).
    - ``GET /{name}/pin`` — single-flag pin state snapshot.
    - ``GET /pins`` — *this endpoint.* Collection-level view of
      every currently-pinned flag, sorted by ``name``.

    ``pins`` is sorted by ``name`` (the
    :func:`orchestrator.feature_flags.get_pinned_flags` helper
    returns a sorted tuple, and the route joins values without
    re-sorting to preserve the contract) so callers can rely on
    a stable order for snapshot tests and dashboard rendering.

    ``count`` is the length of ``pins`` and is included so callers
    can sanity-check the response without a separate ``len()``
    call after JSON parsing. The endpoint is intentionally
    read-only and has no side effects — it does not consult Redis,
    PostgreSQL, or any external config store, and is safe to poll
    freely.

    Status code mapping:

    - ``200 OK`` — always 200. The collection is allowed to be
      empty (no flags are currently pinned); the response shape
      is ``{"pins": [], "count": 0}`` rather than a 404 so
      dashboards can render an empty "no flags pinned" state
      without special-casing the error path.

    The endpoint is unauthenticated by design, matching the
    v15/v16/v17/v18/v41/v42/v43 pin/rollback/toggle/history/
    registry siblings, and the scope is in-memory (mirrors
    ``_DEFAULT_FLAGS`` / ``_overrides`` / ``_locked_pins`` /
    ``_history``) — the response reflects exactly what the
    orchestrator would use to gate a real call site one line
    later in the same process.

    Adapted from the discord-ops-hardening branch's
    ``FeatureFlagPinsResponse`` (source bundle line 1639-1698).
    Field names and types are byte-identical to the branch's; the
    only adaptation is the docstring's "Adapted from..." provenance
    paragraph and a reference to master's ``_DEFAULT_FLAGS`` /
    ``_overrides`` / ``_locked_pins`` symbol names (the branch had
    a single ``_FLAGS`` dict and a single ``_PINNED_FLAGS`` set).
    The wire shape is byte-identical to the branch's contract.
    """

    pins: list[FeatureFlagPinSummary] = Field(
        description=(
            "Every currently-pinned feature flag, sorted by name. "
            "Mirrors orchestrator.feature_flags.get_pinned_flags() "
            "in order. Each entry carries the flag's name and its "
            "current on/off value so dashboards can render the "
            "locked-set without a second round-trip to "
            "GET /v1/feature_flags."
        ),
    )
    count: int = Field(
        description=(
            "Length of the ``pins`` list. Surfaced so callers can "
            "sanity-check the response without a separate len() "
            "call after JSON parsing. Equals 0 when no flags are "
            "currently pinned (the response is still 200 OK in "
            "that case — empty-set is not an error)."
        ),
    )


class T2JudgeIteration(BaseModel):
    """One iteration of the T2 judge panel for a mod request.

    Round v52 (Feature — ``GET /v1/mods/{id}/t2_judges``). The
    per-iteration T2 payload that ``orchestrator.pipeline.node_t2_gate``
    appends to ``PipelineState.t2_judge_results``. The model mirrors
    the JSON shape that is persisted to Redis under
    ``pipeline:<request_id>`` so the API layer can validate each
    entry without re-running the gate.

    Fields mirror the dict shape exactly so the model can be built
    via ``T2JudgeIteration(**entry)`` from the raw Redis payload:

    - ``iteration`` — 1-based iteration index (matches
      ``PipelineState.t2_iterations`` after the gate ran)
    - ``score`` — the integer panel average in ``[0, 10]``. ``None``
      when the gate errored and a numeric score could not be
      computed.
    - ``feedback`` — the human-readable aggregated feedback string
      (empty string mirrors an empty panel verdict).
    - ``passed`` — whether the panel passed the threshold this
      iteration. ``True`` when the panel errored and the pipeline
      fell back to advisory-shipping the output.
    - ``panel_scores`` — the per-judge scores (1 judge when the
      ``t2_three_judge_panel`` feature flag is off, 3 otherwise).
    - ``panel_passed_count`` — number of judges with score ``>=
      T2_PASS_THRESHOLD`` for this iteration. ``0`` is a valid
      value (every judge voted below threshold).
    """

    iteration: int = Field(
        ge=1,
        description=(
            "1-based iteration index for this T2 run. Matches the "
            "value of ``PipelineState.t2_iterations`` after the "
            "gate ran."
        ),
    )
    score: int | None = Field(
        default=None,
        ge=0,
        le=10,
        description=(
            "Integer panel-average score in [0, 10]. ``None`` when "
            "the gate errored and a numeric score could not be "
            "computed."
        ),
    )
    feedback: str = Field(
        default="",
        description=(
            "Human-readable aggregation of the per-judge verdicts "
            "for this iteration. Empty string when the gate errored."
        ),
    )
    passed: bool = Field(
        description=(
            "Whether the panel passed the T2 pass threshold for "
            "this iteration. ``True`` when the gate errored and the "
            "pipeline fell back to advisory-shipping the output."
        ),
    )
    panel_scores: list[int] = Field(
        default_factory=list,
        description=(
            "Per-judge scores for this iteration. 1 entry when the "
            "``t2_three_judge_panel`` flag is off, 3 entries when "
            "on."
        ),
    )
    panel_passed_count: int = Field(
        ge=0,
        description=(
            "Number of judges whose score was ``>= T2_PASS_THRESHOLD`` "
            "for this iteration. ``0`` is a valid value (every "
            "judge voted below threshold)."
        ),
    )


class T2JudgesResponse(BaseModel):
    """Response envelope for ``GET /v1/mods/{id}/t2_judges``.

    Round v52 (Feature — per-iteration T2 history endpoint).
    Surfaces the full T2 retry history for a request, complementing
    the single-pass ``t2_score`` / ``t2_feedback`` fields on
    :class:`ModStatusResponse`. The endpoint is intentionally
    cache-first (Redis) because per-iteration data is NOT persisted
    to ``mod_outputs`` — only the final score + feedback is. A
    Redis miss therefore surfaces ``iterations=[]`` and
    ``source="none"`` rather than a 404, because the request itself
    still exists in Postgres (just not in the live pipeline cache
    anymore, either because the pipeline never ran T2 or because
    the 24h TTL expired).

    The ``source`` field tells operators exactly where the response
    came from:

    - ``"redis"`` — the pipeline state was in Redis (live or
      recently completed); ``iterations`` reflects whatever was
      found there.
    - ``"db_unavailable"`` — Redis was unreachable, but the request
      was confirmed to exist in ``mod_outputs``. ``iterations`` is
      empty (per-iteration history is Redis-only) but the request
      is not 404.
    - ``"none"`` — neither Redis nor the DB has the request, or
      the request never ran T2. ``iterations`` is empty.
    """

    request_id: str = Field(
        description="The mod request this iteration history is for.",
    )
    iterations: list[T2JudgeIteration] = Field(
        default_factory=list,
        description=(
            "Per-iteration T2 payloads, ordered ascending by "
            "``iteration`` (i.e. earliest retry first). Empty when "
            "the request never ran T2, when the Redis pipeline "
            "state has expired, or when Redis was unreachable "
            "and the DB fallback confirmed existence but has no "
            "per-iteration history."
        ),
    )
    final_score: int | None = Field(
        default=None,
        description=(
            "Echo of the final T2 panel-average score from the "
            "Redis pipeline state. ``None`` when not available "
            "(Redis miss, request never ran T2, or the gate "
            "errored). Matches ``ModStatusResponse.t2_score``."
        ),
    )
    final_passed: bool | None = Field(
        default=None,
        description=(
            "Echo of the final T2 pass/fail verdict from the Redis "
            "pipeline state. ``None`` when not available. Matches "
            "``ModStatusResponse.t2_passed``."
        ),
    )
    t2_available: bool = Field(
        default=False,
        description=(
            "``True`` when the T2 panel produced a real numeric "
            "score at least once. ``False`` when the gate was "
            "skipped (no LLM provider) or errored for every "
            "iteration. Matches ``ModStatusResponse.t2_available``."
        ),
    )
    source: Literal["redis", "db_unavailable", "none"] = Field(
        default="none",
        description=(
            "Where the response payload came from. ``redis`` "
            "(happy path), ``db_unavailable`` (Redis unreachable "
            "but request exists in DB), or ``none`` (request not "
            "found or never ran T2). Defaults to ``\"none\"`` so a "
            "minimal constructor (``T2JudgesResponse(request_id=...)``) "
            "round-trips cleanly."
        ),
    )


class PhaseEstimate(BaseModel):
    """Per-phase seconds estimate returned by ``GET /v1/estimates``.

    Each entry mirrors a row of ``app.estimation._PHASE_SECONDS`` —
    the canonical seconds budget for generating a mod of that phase.
    Surfaced as a structured object (rather than a bare
    ``dict[str, int]``) so the JSON schema is self-describing for
    clients and ``mypy`` can verify the contract on the server side.

    The ``seconds`` value is always > 0 and matches what
    :func:`app.estimation.estimate_seconds` returns when the
    corresponding phase's keyword is matched in a prompt. Unknown
    phases use the ``default_seconds`` field on the parent
    :class:`EstimatesResponse` envelope rather than receiving a
    row of their own.
    """

    phase: str = Field(
        description=(
            "Phase id (e.g. 'shop_channel', 'weather_event'). Matches "
            "the keys of app.estimation._PHASE_SECONDS exactly."
        ),
    )
    seconds: int = Field(
        ge=1,
        description=(
            "Estimated generation time in seconds for a single mod "
            "of this phase. Matches app.estimation._PHASE_SECONDS[phase]."
        ),
    )


class EstimatesResponse(BaseModel):
    """Response for ``GET /v1/estimates`` — full per-phase estimate table.

    Mirrors :data:`app.estimation._PHASE_SECONDS` as a list of
    :class:`PhaseEstimate` objects so clients (chat bots, dashboards,
    integration tests) can render a phase→seconds table without
    scraping the source or hard-coding the values. The endpoint is
    intentionally read-only and has no side effects — no LLM call,
    no DB read, no Redis hit, just an in-memory dict export.

    The ``default_seconds`` field is surfaced alongside the per-phase
    rows because it is the contract that
    :func:`app.estimation.estimate_seconds` falls back to when no
    phase keyword matches — clients that need to reproduce the
    heuristic locally can read both the rows and the default from
    this one endpoint.

    Entries are sorted by ``phase`` (the same canonical order as
    ``/v1/mods/phases/known``) so callers can rely on a stable
    iteration order for snapshot tests and UI rendering.
    """

    estimates: list[PhaseEstimate] = Field(
        description=(
            "Per-phase seconds estimate, sorted by phase. Mirrors "
            "app.estimation._PHASE_SECONDS; length == count."
        ),
    )
    default_seconds: int = Field(
        ge=1,
        description=(
            "Fallback estimate used when no phase keyword matches "
            "the prompt. Matches app.estimation._DEFAULT_SECONDS."
        ),
    )
    count: int = Field(
        ge=0,
        description="Length of the estimates list (== len(estimates)).",
    )


class PhaseEstimateResponse(BaseModel):
    """Response for ``GET /v1/estimates/{phase}`` — single-phase lookup.

    Lets a caller ask "how long will a *shop_channel* mod take?"
    without parsing the full table. The ``matched`` flag tells the
    caller whether the requested phase was in the canonical table
    (``True``) or whether the server returned the default estimate
    (``False``) so the client can decide whether to show "unknown"
    or just the fallback value.

    When ``matched`` is ``False``, ``seconds == default_seconds`` and
    ``phase`` echoes the requested phase id verbatim (the canonical
    table does not contain it). This is the same contract as
    :func:`app.estimation.estimate_seconds_for_phase` returning
    ``_DEFAULT_SECONDS`` for an unknown phase.
    """

    phase: str = Field(
        description=(
            "Phase id the caller asked about (echoed back). May be a "
            "phase the canonical table does not contain — in that "
            "case ``matched`` is False and ``seconds`` is the default."
        ),
    )
    seconds: int = Field(
        ge=1,
        description=(
            "Estimated generation time in seconds. Equals "
            "app.estimation._PHASE_SECONDS[phase] when ``matched`` "
            "is True; equals ``default_seconds`` otherwise."
        ),
    )
    default_seconds: int = Field(
        ge=1,
        description=(
            "Fallback estimate used when the phase is unknown. Mirrors "
            "app.estimation._DEFAULT_SECONDS so a client can render "
            "'this phase is unknown' without a second round-trip."
        ),
    )
    matched: bool = Field(
        description=(
            "True iff the phase id was found in "
            "app.estimation._PHASE_SECONDS. False signals that "
            "``seconds`` is the fallback default."
        ),
    )


class PromptEstimateResponse(BaseModel):
    """Response for ``GET /v1/estimate?prompt=...`` — prompt-keyed estimate.

    Lets a UI render "this will take ~60 seconds" before the user
    submits a full generation request. The endpoint composes two
    already-existing helpers:

    * :func:`orchestrator.router.route` resolves the *phase* the
      orchestrator would pick for the prompt (same path the real
      pipeline takes, including the longest-keyword-wins heuristic).
    * :func:`app.estimation.estimate_seconds_for_phase` returns the
      canonical seconds budget for that phase.

    ``seconds`` is always > 0 — the route guarantees it by passing the
    raw prompt through the estimation helper, which falls back to the
    documented ``_DEFAULT_SECONDS`` (90s) when the phase is unknown.
    The ``matched`` flag is ``True`` iff the resolved phase id is in
    the canonical phase table (i.e. the caller can rely on ``seconds``
    being a tuned estimate rather than the fallback default).

    Mirrors the JSON contract of :class:`PhaseEstimateResponse`
    (phase-keyed lookup) and adds an echoed ``prompt`` field so the
    caller can correlate the response with the request without keeping
    a separate id.
    """

    prompt: str = Field(
        description=(
            "Echo of the (trimmed) prompt the server estimated. Useful "
            "for log correlation and for UIs that render multiple "
            "estimate rows in a list without tracking the request "
            "separately."
        ),
    )
    phase: str = Field(
        description=(
            "Phase id the orchestrator would select for this prompt "
            "(e.g. ``shop_channel``, ``weather_event``). Falls back "
            "to the default phase when no keyword matches."
        ),
    )
    seconds: int = Field(
        ge=1,
        description=(
            "Estimated generation time in seconds. Equals "
            "``app.estimation._PHASE_SECONDS[phase]`` when "
            "``matched`` is True; equals ``default_seconds`` otherwise."
        ),
    )
    default_seconds: int = Field(
        ge=1,
        description=(
            "Fallback estimate surfaced so clients can render 'this "
            "phase is unknown — defaulting to 90s' without a second "
            "round-trip. Mirrors ``app.estimation._DEFAULT_SECONDS``."
        ),
    )
    matched: bool = Field(
        description=(
            "True iff the resolved phase is in the canonical phase "
            "table (i.e. ``seconds`` is a tuned estimate rather than "
            "the fallback default)."
        ),
    )
    game: str = Field(
        default="stardew_valley",
        description=(
            "Game pack the resolved phase belongs to. Mirrors the "
            "``game`` field on ``RoutePreviewResponse`` so the two "
            "preview-style endpoints share a vocabulary. Defaults to "
            "``\"stardew_valley\"`` because the router's fallback "
            "pack is stardew_valley."
        ),
    )


class BatchPromptEstimateItem(BaseModel):
    """One row of the ``POST /v1/estimate/batch`` response.

    Mirrors :class:`PromptEstimateResponse` minus the echoed prompt
    (which is already in the request body) — the batch variant keeps
    the per-row payload small so a UI rendering 10 cards pays ~9
    fewer bytes per row.
    """

    phase: str = Field(description="Resolved phase id for this prompt.")
    seconds: int = Field(
        ge=1,
        description="Estimated generation time in seconds for this prompt.",
    )
    default_seconds: int = Field(
        ge=1,
        description="Fallback estimate (mirrors ``app.estimation._DEFAULT_SECONDS``).",
    )
    matched: bool = Field(
        description=(
            "True iff the resolved phase has a tuned estimate "
            "(not a fallback to ``default_seconds``)."
        ),
    )
    game: str = Field(
        default="stardew_valley",
        description="Game pack the resolved phase belongs to.",
    )


class BatchPromptEstimateRequest(BaseModel):
    """Request body for ``POST /v1/estimate/batch``.

    Accepts 1-20 prompts per batch — small enough to fit in a single
    UI frame for a "what can I generate" card grid, large enough to
    amortise the routing cost on the server. Each prompt gets its own
    per-item estimate in the response, in the same order as the
    request.
    """

    prompts: list[str] = Field(
        min_length=1,
        max_length=20,
        description=(
            "1-20 prompts to estimate in one round-trip. Order is "
            "preserved in the response so callers can render the "
            "results as a list of cards without re-sorting. Each "
            "prompt is trimmed at the schema boundary; empty after "
            "trim is rejected with a 422 before the handler runs."
        ),
    )

    @field_validator("prompts")
    @classmethod
    def _validate_prompts(cls, v: list[str]) -> list[str]:
        # Reuse the same trim + null-byte-rejection rule as
        # ``GenerateRequest._validate_prompt`` so the batch endpoint
        # and the single-prompt endpoint have identical input
        # hygiene. Per-item validation surfaces as a 422 for the
        # whole batch — partial successes would force the caller
        # to walk two parallel lists.
        cleaned: list[str] = []
        for raw in v:
            stripped = raw.strip()
            if not stripped:
                raise ValueError(
                    "every prompt must be non-empty after trim"
                )
            if "\x00" in stripped:
                raise ValueError("prompt must not contain null bytes")
            cleaned.append(stripped)
        return cleaned


class BatchPromptEstimateResponse(BaseModel):
    """Response envelope for ``POST /v1/estimate/batch``.

    ``estimates`` is parallel to the request ``prompts`` list — the
    i-th element is the estimate for the i-th prompt. ``count`` is
    ``len(estimates)`` so callers can sanity-check without a second
    ``len()`` call.
    """

    estimates: list[BatchPromptEstimateItem] = Field(
        description=(
            "Per-prompt estimates, in the same order as the request "
            "``prompts`` list. Length == ``count``."
        ),
    )
    count: int = Field(
        ge=0,
        description="Length of the estimates list (== len(estimates)).",
    )
    default_seconds: int = Field(
        ge=1,
        description=(
            "Echo of the fallback estimate (``app.estimation._DEFAULT_SECONDS``) "
            "so a UI can render the fallback value next to each card "
            "without re-asking the server."
        ),
    )


class PhaseDetailResponse(BaseModel):
    """Response for ``GET /v1/mods/phases/{phase_id}`` — single-phase detail.

    Additive alongside the existing ``GET /v1/mods/phases`` (which lists
    every pack + phase) and ``GET /v1/mods/phases/known`` (which returns
    the flat phase list). Lets a caller fetch the *detail* for one phase
    id without parsing the full table — same pattern as
    :class:`PhaseEstimateResponse` for the estimate table.

    The ``game_id`` / ``display_name`` / ``mod_format`` triple is the
    owning :class:`generators.packs.GamePack` manifest — useful for a web
    UI that needs to render "this phase belongs to <pack> / <format>".
    When the phase is not registered with any pack the ``matched`` flag
    is ``False`` and these fields are empty strings (mirrors the
    ``PhaseEstimateResponse.matched=False`` graceful-degrade shape).
    """

    phase: str = Field(
        description=(
            "Phase id the caller asked about (echoed back). May be a "
            "phase no registered pack exposes — in that case ``matched`` "
            "is False and ``execution_order`` is the empty list."
        ),
    )
    matched: bool = Field(
        description=(
            "True iff the phase id was found in at least one "
            "registered pack's ``list_phases()``. False signals that "
            "the caller asked about a phase the server has not "
            "registered (graceful 200, not a 404 — same contract as "
            "PhaseEstimateResponse for unknown estimates)."
        ),
    )
    game_id: str = Field(
        default="",
        description=(
            "Owning game pack id (e.g. 'stardew_valley'). Empty "
            "string when ``matched`` is False."
        ),
    )
    display_name: str = Field(
        default="",
        description=(
            "Owning game pack display name (e.g. 'Stardew Valley'). "
            "Empty string when ``matched`` is False."
        ),
    )
    mod_format: str = Field(
        default="",
        description=(
            "Owning game pack mod format string (e.g. 'Content "
            "Patcher 1.29'). Empty string when ``matched`` is False."
        ),
    )
    generator_count: int = Field(
        ge=0,
        description=(
            "Length of ``execution_order`` (== the number of "
            "generators the pipeline would run for this phase). 0 "
            "when ``matched`` is False."
        ),
    )
    execution_order: list[str] = Field(
        default_factory=list,
        description=(
            "Generator class/function names in the order the "
            "pipeline would execute them. Empty list when "
            "``matched`` is False or when the registered pack "
            "fails to resolve the phase (defensive default that "
            "matches the ``GET /v1/mods/phases`` handler)."
        ),
    )
    estimated_seconds: int = Field(
        ge=1,
        description=(
            "Tuned generation estimate from "
            ":func:`app.estimation.estimate_seconds_for_phase`. "
            "Equals ``app.estimation._DEFAULT_SECONDS`` (90) when "
            "the phase is not in the canonical ``_PHASE_SECONDS`` "
            "table — the same fallback the single-phase estimate "
            "endpoint uses."
        ),
    )
    default_seconds: int = Field(
        ge=1,
        description=(
            "Fallback estimate used when the phase has no tuned "
            "estimate. Mirrors ``app.estimation._DEFAULT_SECONDS`` "
            "so a client can render 'no specific estimate — "
            "default is 90s' without a second round-trip."
        ),
    )


class LogEntry(BaseModel):
    """One entry in a request's status log stream.

    The shape mirrors a structlog event dict so a client can
    render the entries verbatim. ``extras`` is a free-form
    ``dict[str, Any]`` so context-bound fields (e.g.
    ``request_id``, ``phase``, ``generator``) ride along
    without us enumerating them. Field ``extras`` is the only
    non-reserved field on the entry — the reserved fields are
    always present (timestamp/level/event/message) and read
    straight off the model.

    ``timestamp`` is ISO-8601 UTC; clients should treat it as
    a string for display purposes rather than parse and re-emit
    it (the on-the-wire format is the canonical form).
    """

    timestamp: str
    level: str = Field(description="One of INFO|WARNING|ERROR|DEBUG.")
    event: str = Field(description="dot.case event name, e.g. \"pipeline.routing\".")
    message: str = Field(description="Human-readable single-line message.")
    extras: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form context fields (request_id, phase, "
            "generator, etc.) as recorded by the writer. Empty "
            "when the entry had no extra context."
        ),
    )

    @field_validator("level")
    @classmethod
    def _validate_level(cls, value: str) -> str:
        if value not in {"INFO", "WARNING", "ERROR", "DEBUG"}:
            # Accept any other level string but log-equivalent of a
            # soft warning is to keep it as-is so callers can spot
            # future additions without a schema bump. The route
            # layer is the right place to reject; here we just
            # normalize empty values.
            if not value:
                return "INFO"
        return value


class ModLogsResponse(BaseModel):
    """Response for ``GET /v1/mods/{request_id}/logs``.

    ``entries`` is newest-first. ``count`` echoes ``len(entries)``
    so a caller can sanity-check against the server without
    iterating the list (the limit query param may have produced
    fewer entries than exist on the server).

    ``source`` tells the caller where the data came from so the
    client can render an "expiring" banner when ``source`` is
    ``db_unavailable`` (the request is in the DB but the Redis
    stream has aged out).
    """

    request_id: str
    entries: list[LogEntry] = Field(default_factory=list)
    count: int = Field(ge=0, description="Number of entries returned in this response.")
    limit: int = Field(ge=1, description="The clamped limit applied to this request.")
    source: Literal["redis", "db_unavailable"] = Field(
        description=(
            "\"redis\" when the entries came from the live Redis "
            "log stream; \"db_unavailable\" when the request "
            "exists in the DB but the Redis stream has expired "
            "or was never written to. Always \"redis\" when "
            "``entries`` is non-empty."
        ),
    )
