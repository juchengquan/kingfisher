"""Every error a caller can cause, and what it becomes on the wire."""

from __future__ import annotations

#: The library's list, restated. See the module docstring for why it is a copy.
CALLER_FACING_ERRORS = frozenset({
    "CapabilityError", "QuotaExceededError", "SessionBusyError", "SkillError",
    "SubagentError", "UnknownReferenceError", "UnknownSessionError",
    "UnsafeReferenceError", "UploadError",
})


def test_every_caller_facing_error_has_a_status():
    """The half phase 1 could not check yet.

    `CALLER_FACING_ERRORS` says which errors a caller can cause; `errors.STATUS` says
    what each becomes on the wire. Nothing but this keeps them the same set -- and
    the failure is quiet in both directions. An error classified caller-facing but
    absent from the map is a 500 for something the caller could fix; one in the map
    but not classified is a status nobody decided on.
    """
    from kingfisher_service.errors import STATUS

    mapped = {error.__name__ for error in STATUS}

    assert mapped == CALLER_FACING_ERRORS, (
        "every caller-facing error needs a status and code, and nothing else "
        "belongs in the map — a deployment error is a 500 on purpose"
    )


def test_no_two_refusals_share_a_code():
    """The code is what a client branches on, so two refusals answering the same code
    are two things it cannot tell apart.
    """
    from kingfisher_service.errors import CODE_FOR_STATUS, STATUS

    codes = [code for _, code in STATUS.values()] + list(CODE_FOR_STATUS.values())

    assert len(codes) == len(set(codes)), sorted(codes)


#: The library's other list, restated for the same reason as the one above.
#: Only the ones this service names; the rest reach the default 500 and want no
#: code of their own.
NAMED_DEPLOYMENT_ERRORS = frozenset({"AccessError"})


def test_the_two_tables_are_disjoint():
    """One error, one meaning."""
    from kingfisher_service.errors import DEPLOYMENT_STATUS, STATUS

    assert not set(STATUS) & set(DEPLOYMENT_STATUS)


def test_only_deployment_errors_are_named_as_such():
    """The second table is not a way round the first."""
    from kingfisher_service.errors import DEPLOYMENT_STATUS

    named = {error.__name__ for error in DEPLOYMENT_STATUS}

    assert named == NAMED_DEPLOYMENT_ERRORS
    assert not named & CALLER_FACING_ERRORS


def test_every_named_deployment_error_stays_a_5xx():
    """A 4xx here would say the caller sent something wrong, which is the one thing
    these are not.
    """
    from kingfisher_service.errors import DEPLOYMENT_STATUS

    assert all(status >= 500 for status, _ in DEPLOYMENT_STATUS.values())
