"""Historical Projection matching imports keep the retrieval-owned implementation."""

from fetchcore.projection.semantics import match_projection_record as package_match
from fetchcore.projection_semantics import match_projection_record as historical_match
from fetchcore.retrieval.projection_matching import match_projection_record


def test_historical_projection_matching_imports_share_owner() -> None:
    assert package_match is historical_match is match_projection_record
