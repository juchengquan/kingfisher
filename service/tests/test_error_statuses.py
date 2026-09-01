"""Every error a caller can cause, and what it becomes on the wire.

Lived in the library's `test_architecture` until the service became its own
distribution. It has to be here now, for the same reason `test_wire_vocabulary`
does: the library may not import this package, so the side that can see both
sets is this one.

`CALLER_FACING_ERRORS` is copied rather than imported, and that is the cost of
the split being real. The library asserts the same set against its own error
classes, so a new error still fails there first -- what would go unnoticed is
adding one to *that* list and not to this file, which is why the two names are
identical and this note exists.
"""

from __future__ import annotations

#: The library's list, restated. See the module docstring for why it is a copy.
CALLER_FACING_ERRORS = frozenset({
    "CapabilityError", "QuotaExceededError", "SessionBusyError", "SkillError",
    "SubagentError", "UnknownReferenceError", "UnknownSessionError",
    "UnsafeReferenceError", "UploadError",
})


def test_every_caller_facing_error_has_a_status():
    """The half phase 1 could not check yet.

    `CALLER_FACING_ERRORS` says which errors a caller can cause;
    `errors.STATUS` says what each becomes on the wire. Nothing but this keeps
    them the same set -- and the failure is quiet in both directions. An error
    classified caller-facing but absent from the map is a 500 for something the
    caller could fix; one in the map but not classified is a status nobody
    decided on.
    """
    from kingfisher_service.errors import STATUS

    mapped = {error.__name__ for error in STATUS}

    assert mapped == CALLER_FACING_ERRORS, (
        "every caller-facing error needs a status and code, and nothing else "
        "belongs in the map — a deployment error is a 500 on purpose"
    )


def test_no_two_refusals_share_a_code():
    """The code is what a client branches on, so two refusals answering the
    same code are two things it cannot tell apart. Statuses may repeat --
    `bad_reference`, `bad_skill` and `bad_subagent` are all 400 -- which is
    exactly why the code carries the meaning."""
    from kingfisher_service.errors import CODE_FOR_STATUS, STATUS

    codes = [code for _, code in STATUS.values()] + list(CODE_FOR_STATUS.values())

    assert len(codes) == len(set(codes)), sorted(codes)


#: The library's other list, restated for the same reason as the one above.
#: Only the ones this service names; the rest reach the default 500 and want no
#: code of their own.
NAMED_DEPLOYMENT_ERRORS = frozenset({"AccessError"})


def test_the_two_tables_are_disjoint():
    """One error, one meaning. An error in both would be a caller-facing thing
    a caller cannot cause, which is the contradiction each table exists to
    keep out of the other."""
    from kingfisher_service.errors import DEPLOYMENT_STATUS, STATUS

    assert not set(STATUS) & set(DEPLOYMENT_STATUS)


def test_only_deployment_errors_are_named_as_such():
    """The second table is not a way round the first. Anything in it must be an
    error the caller cannot cause -- otherwise it belongs above, with a status
    somebody decided on and a rule holding them together."""
    from kingfisher_service.errors import DEPLOYMENT_STATUS

    named = {error.__name__ for error in DEPLOYMENT_STATUS}

    assert named == NAMED_DEPLOYMENT_ERRORS
    assert not named & CALLER_FACING_ERRORS


def test_every_named_deployment_error_stays_a_5xx():
    """A 4xx here would say the caller sent something wrong, which is the one
    thing these are not."""
    from kingfisher_service.errors import DEPLOYMENT_STATUS

    assert all(status >= 500 for status, _ in DEPLOYMENT_STATUS.values())
