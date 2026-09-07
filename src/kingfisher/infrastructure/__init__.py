"""The infrastructure layer: everything with a foreign system on the far side.

deepagents and LangChain, the filesystem, sqlite, the model endpoints. The adapters
here satisfy `domain.ports` by shape and translate at the boundary, so a foreign type
is named in this layer or nowhere.
"""
