"""Skills: registering what a workspace's `skills/` directory provides."""

#: The built-in a skill is opened with: the model is shown an index, and reads the skill it
#: picks. Here, not in `core.agent.prompting` beside the other built-ins' names: the
#: definition readers refuse by this name, and nothing in `core.definitions` imports from
#: `core.agent`, which is built from it.
READ_FILE = "read_file"
