"""Built-in repository knowledge-pack registry for the runtime composition root."""

from __future__ import annotations

from fetchcore.repository.knowledge_pack import RepositoryKnowledgePack
from fetchcore.repository.sap_commerce_pack import SapCommerceKnowledgePack


def default_repository_knowledge_packs() -> tuple[RepositoryKnowledgePack, ...]:
    """Return built-in packs without coupling RepositoryIndexer to any domain."""

    return (SapCommerceKnowledgePack(),)
