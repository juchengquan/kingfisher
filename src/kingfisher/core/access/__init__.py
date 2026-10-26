"""Who reaches what: what a request may use, the source ids a caller holds, and the rule
the definitions apply to both."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

#: What other folders of the package take from this one, and the module defining each.
#: Lazy, never an eager import: every module here loads this file first, so an eager
#: re-export would have each of them load all the others, the shape
#: `test_no_module_in_the_package_can_reach_itself` refuses.
_EXPORTS = {
    "ALL": "kingfisher.core.access.capabilities",
    "Capabilities": "kingfisher.core.access.capabilities",
    "CapabilityError": "kingfisher.core.access.capabilities",
    "SEPARATOR": "kingfisher.core.access.capabilities",
    "Selection": "kingfisher.core.access.capabilities",
    "UNRESTRICTED": "kingfisher.core.access.capabilities",
    "approved_middleware": "kingfisher.core.access.capabilities",
    "approved_settings": "kingfisher.core.access.capabilities",
    "bare_name": "kingfisher.core.access.capabilities",
    "belongs_in": "kingfisher.core.access.capabilities",
    "ceiling": "kingfisher.core.access.capabilities",
    "narrowed": "kingfisher.core.access.capabilities",
    "refuse_ungranted_endpoint": "kingfisher.core.access.capabilities",
    "refuse_ungranted_models": "kingfisher.core.access.capabilities",
    "refuse_unoffered": "kingfisher.core.access.capabilities",
    "refuse_unprovided_wants": "kingfisher.core.access.capabilities",
    "refuse_written_wants": "kingfisher.core.access.capabilities",
    "unoffered": "kingfisher.core.access.capabilities",
    "withheld": "kingfisher.core.access.capabilities",
    "audit": "kingfisher.core.access.checks",
    "caller_holds": "kingfisher.core.access.checks",
    "collected": "kingfisher.core.access.checks",
    "held_by": "kingfisher.core.access.checks",
    "walked": "kingfisher.core.access.checks",
    "activated": "kingfisher.core.access.reach",
    "handed_work_by": "kingfisher.core.access.reach",
    "narrowed_for": "kingfisher.core.access.reach",
    "offers": "kingfisher.core.access.reach",
    "reaches": "kingfisher.core.access.reach",
    "stated": "kingfisher.core.access.reach",
    "within_reach": "kingfisher.core.access.reach",
    "AccessReport": "kingfisher.core.access.source_ids",
    "Audience": "kingfisher.core.access.source_ids",
    "CallerError": "kingfisher.core.access.source_ids",
    "DATAZONE_PREFIX": "kingfisher.core.access.source_ids",
    "Datazone": "kingfisher.core.access.source_ids",
    "ExtraDatazone": "kingfisher.core.access.source_ids",
    "Held": "kingfisher.core.access.source_ids",
    "Opened": "kingfisher.core.access.source_ids",
    "Opening": "kingfisher.core.access.source_ids",
    "Stated": "kingfisher.core.access.source_ids",
    "Vocabulary": "kingfisher.core.access.source_ids",
    "jointly": "kingfisher.core.access.source_ids",
    "listed": "kingfisher.core.access.source_ids",
    "mentioned": "kingfisher.core.access.source_ids",
    "spell": "kingfisher.core.access.source_ids",
    "unlisted": "kingfisher.core.access.source_ids",
    "with_datazone": "kingfisher.core.access.source_ids",
    "zone_in": "kingfisher.core.access.source_ids",
    "zones_held": "kingfisher.core.access.source_ids",
    "zoned": "kingfisher.core.access.source_ids",
}

if TYPE_CHECKING:
    from kingfisher.core.access.capabilities import ALL as ALL
    from kingfisher.core.access.capabilities import SEPARATOR as SEPARATOR
    from kingfisher.core.access.capabilities import UNRESTRICTED as UNRESTRICTED
    from kingfisher.core.access.capabilities import Capabilities as Capabilities
    from kingfisher.core.access.capabilities import CapabilityError as CapabilityError
    from kingfisher.core.access.capabilities import Selection as Selection
    from kingfisher.core.access.capabilities import approved_middleware as approved_middleware
    from kingfisher.core.access.capabilities import approved_settings as approved_settings
    from kingfisher.core.access.capabilities import bare_name as bare_name
    from kingfisher.core.access.capabilities import belongs_in as belongs_in
    from kingfisher.core.access.capabilities import ceiling as ceiling
    from kingfisher.core.access.capabilities import narrowed as narrowed
    from kingfisher.core.access.capabilities import (
        refuse_ungranted_endpoint as refuse_ungranted_endpoint,
    )
    from kingfisher.core.access.capabilities import (
        refuse_ungranted_models as refuse_ungranted_models,
    )
    from kingfisher.core.access.capabilities import refuse_unoffered as refuse_unoffered
    from kingfisher.core.access.capabilities import (
        refuse_unprovided_wants as refuse_unprovided_wants,
    )
    from kingfisher.core.access.capabilities import refuse_written_wants as refuse_written_wants
    from kingfisher.core.access.capabilities import unoffered as unoffered
    from kingfisher.core.access.capabilities import withheld as withheld
    from kingfisher.core.access.checks import audit as audit
    from kingfisher.core.access.checks import caller_holds as caller_holds
    from kingfisher.core.access.checks import collected as collected
    from kingfisher.core.access.checks import held_by as held_by
    from kingfisher.core.access.checks import walked as walked
    from kingfisher.core.access.reach import activated as activated
    from kingfisher.core.access.reach import handed_work_by as handed_work_by
    from kingfisher.core.access.reach import narrowed_for as narrowed_for
    from kingfisher.core.access.reach import offers as offers
    from kingfisher.core.access.reach import reaches as reaches
    from kingfisher.core.access.reach import stated as stated
    from kingfisher.core.access.reach import within_reach as within_reach
    from kingfisher.core.access.source_ids import DATAZONE_PREFIX as DATAZONE_PREFIX
    from kingfisher.core.access.source_ids import AccessReport as AccessReport
    from kingfisher.core.access.source_ids import Audience as Audience
    from kingfisher.core.access.source_ids import CallerError as CallerError
    from kingfisher.core.access.source_ids import Datazone as Datazone
    from kingfisher.core.access.source_ids import ExtraDatazone as ExtraDatazone
    from kingfisher.core.access.source_ids import Held as Held
    from kingfisher.core.access.source_ids import Opened as Opened
    from kingfisher.core.access.source_ids import Opening as Opening
    from kingfisher.core.access.source_ids import Stated as Stated
    from kingfisher.core.access.source_ids import Vocabulary as Vocabulary
    from kingfisher.core.access.source_ids import jointly as jointly
    from kingfisher.core.access.source_ids import listed as listed
    from kingfisher.core.access.source_ids import mentioned as mentioned
    from kingfisher.core.access.source_ids import spell as spell
    from kingfisher.core.access.source_ids import unlisted as unlisted
    from kingfisher.core.access.source_ids import with_datazone as with_datazone
    from kingfisher.core.access.source_ids import zone_in as zone_in
    from kingfisher.core.access.source_ids import zoned as zoned
    from kingfisher.core.access.source_ids import zones_held as zones_held


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export."""
    try:
        module = _EXPORTS[name]
    except KeyError:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from None

    value = getattr(import_module(module), name)
    globals()[name] = value  # resolved once: later lookups skip __getattr__
    return value
