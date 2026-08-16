"""The infrastructure layer: everything with a foreign system on the far side.

deepagents and LangChain, the filesystem, sqlite, the model endpoints. The
adapters here satisfy `domain.ports` by shape and translate at the boundary, so
a foreign type is named in this layer or nowhere.

That is a job rather than a restriction: a test asserts this layer *does* import
foreign packages and *does* touch the world, because if it stopped, the coupling
would not have gone away — it would have moved somewhere less visible.
"""
